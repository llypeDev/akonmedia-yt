"""Curation queues for editors and above."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.media.models import Comment, Media, Subtitle
from apps.rbac import permissions as policy


def _require_curator(request):
    if not request.user.is_authenticated or not policy.can_curate_others(request.user):
        raise PermissionDenied("Curation requires editor access")


@login_required
def manage(request):
    _require_curator(request)

    queue = Media.objects.select_related("owner").filter(
        state__in=[Media.State.PENDING, Media.State.PROCESSING]
    )
    failed = Media.objects.select_related("owner").filter(state=Media.State.FAILED)
    hidden = (
        Comment.objects.select_related("author", "media")
        .filter(is_hidden=True)
        .order_by("-created_at")[:50]
    )
    awaiting_subtitles = (
        Subtitle.objects.select_related("media").order_by("-created_at")[:20]
    )

    return render(
        request,
        "core/manage.html",
        {
            "queue": queue,
            "failed": failed,
            "hidden_comments": hidden,
            "subtitles": awaiting_subtitles,
            "can_manage_users": policy.can_manage_users(request.user),
        },
    )


@login_required
@require_POST
def manage_action(request):
    _require_curator(request)
    kind = request.POST.get("kind", "")
    action = request.POST.get("action", "")
    target = request.POST.get("id", "")

    if kind == "media":
        media = get_object_or_404(Media, pk=target)
        if action == "retry":
            media.state = Media.State.PENDING
            media.failure_reason = ""
            media.save(update_fields=["state", "failure_reason"])
            from apps.media.tasks import process_media

            process_media.delay(str(media.pk))
            messages.success(request, f"Re-queued “{media.title}”.")
        elif action == "delete":
            media.delete()
            messages.success(request, "Media removed.")

    elif kind == "comment":
        comment = get_object_or_404(Comment, pk=target)
        if action == "unhide":
            comment.is_hidden = False
            comment.save(update_fields=["is_hidden"])
        elif action == "hide":
            comment.is_hidden = True
            comment.save(update_fields=["is_hidden"])
        elif action == "delete":
            comment.delete()

    return redirect("core:manage")
