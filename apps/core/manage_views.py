"""Curation queues for editors and above, account roles for managers."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.media import pipeline
from apps.media.models import Comment, Media, Playlist, Subtitle
from apps.rbac import permissions as policy
from apps.rbac.roles import RANK, Role
from apps.users.models import User

TABS = ("queue", "failed", "comments", "users", "subtitles")


def _require_curator(request):
    if not request.user.is_authenticated or not policy.can_curate_others(request.user):
        raise PermissionDenied("Curation requires editor access")


@login_required
def manage(request):
    _require_curator(request)
    can_manage_users = policy.can_manage_users(request.user)

    queue = Media.objects.select_related("owner").filter(
        state__in=[Media.State.PENDING, Media.State.PROCESSING]
    ).order_by("created_at")
    failed = Media.objects.select_related("owner").filter(state=Media.State.FAILED).order_by(
        "-updated_at"
    )
    hidden = (
        Comment.objects.select_related("author", "media")
        .filter(is_hidden=True)
        .order_by("-created_at")[:50]
    )
    recent_comments = (
        Comment.objects.select_related("author", "media")
        .filter(is_hidden=False)
        .order_by("-created_at")[:30]
    )
    awaiting_subtitles = (
        Subtitle.objects.select_related("media").annotate(cue_count=Count("cues"))
        .order_by("-updated_at")[:20]
    )

    stats = Media.objects.aggregate(
        total=Count("id"),
        ready=Count("id", filter=Q(state=Media.State.READY)),
        size=Sum("size_bytes"),
        views=Sum("views_count"),
    )
    stats.update(
        users=User.objects.count(),
        comments=Comment.objects.count(),
        playlists=Playlist.objects.count(),
        queue=queue.count(),
        failed=failed.count(),
    )

    users = []
    if can_manage_users:
        users = User.objects.annotate(media_count=Count("media")).order_by("-date_joined")[:100]

    tab = request.GET.get("tab", "")
    if tab not in TABS or (tab == "users" and not can_manage_users):
        tab = "failed" if stats["failed"] and not stats["queue"] else "queue"

    return render(
        request,
        "core/manage.html",
        {
            "queue": queue,
            "failed": failed,
            "hidden_comments": hidden,
            "recent_comments": recent_comments,
            "subtitles": awaiting_subtitles,
            "can_manage_users": can_manage_users,
            "users": users,
            "roles": _assignable_roles(request.user),
            "stats": stats,
            "tab": tab,
            "encoder_ready": pipeline.available(),
        },
    )


def _assignable_roles(user):
    """Roles this account may hand out: never above its own rank."""
    mine = policy.rank_of(user)
    return [(value, label) for value, label in Role.choices if RANK[Role(value)] <= mine]


@login_required
@require_POST
def manage_action(request):
    _require_curator(request)
    kind = request.POST.get("kind", "")
    action = request.POST.get("action", "")
    target = request.POST.get("id", "")
    tab = request.POST.get("tab", "")

    if kind == "media":
        media = get_object_or_404(Media, pk=target)
        if action == "retry":
            media.state = Media.State.PENDING
            media.failure_reason = ""
            media.save(update_fields=["state", "failure_reason"])
            from apps.media.tasks import process_media

            process_media.delay(str(media.pk))
            messages.success(request, _("Re-queued “%(title)s”.") % {"title": media.title})
        elif action == "delete":
            media.delete()
            messages.success(request, _("Media removed."))

    elif kind == "comment":
        comment = get_object_or_404(Comment, pk=target)
        if action == "unhide":
            comment.is_hidden = False
            comment.save(update_fields=["is_hidden"])
            messages.success(request, _("Comment restored."))
        elif action == "hide":
            comment.is_hidden = True
            comment.save(update_fields=["is_hidden"])
            messages.success(request, _("Comment hidden."))
        elif action == "delete":
            comment.delete()
            messages.success(request, _("Comment deleted."))

    elif kind == "user":
        _change_user(request, target, action)

    url = reverse("core:manage")
    return redirect(f"{url}?tab={tab}" if tab in TABS else url)


def _change_user(request, target, action) -> None:
    if not policy.can_manage_users(request.user):
        raise PermissionDenied("Managing accounts requires manager access")
    account = get_object_or_404(User, pk=target)
    if account.pk == request.user.pk:
        messages.error(request, _("You cannot change your own account here."))
        return
    if policy.rank_of(account) > policy.rank_of(request.user):
        raise PermissionDenied("That account outranks yours")

    if action == "role":
        role = request.POST.get("role", "")
        if role not in dict(_assignable_roles(request.user)):
            raise PermissionDenied("You cannot grant that role")
        account.role = role
        account.save(update_fields=["role"])
        messages.success(
            request,
            _("%(account)s is now %(role)s.")
            % {"account": account, "role": account.get_role_display().lower()},
        )
    elif action in {"deactivate", "activate"}:
        account.is_active = action == "activate"
        account.save(update_fields=["is_active"])
        if account.is_active:
            messages.success(request, _("%(account)s was re-activated.") % {"account": account})
        else:
            messages.success(request, _("%(account)s was deactivated.") % {"account": account})
