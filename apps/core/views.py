"""Public pages and operational endpoints."""
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import F, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.media.models import Comment, Media
from apps.rbac import permissions as policy

PAGE_SIZE = 12


def visible_media(user):
    """Everything the current visitor is allowed to see, newest first."""
    queryset = Media.objects.select_related("owner", "category")
    published = Q(visibility=Media.Visibility.PUBLIC, state=Media.State.READY)
    if user.is_authenticated:
        if policy.can_curate_others(user):
            return queryset
        return queryset.filter(Q(owner=user) | published)
    return queryset.filter(published)


def can_view(user, media: Media) -> bool:
    if media.visibility == Media.Visibility.PUBLIC and media.state == Media.State.READY:
        return True
    if not user.is_authenticated:
        return False
    return media.owner_id == user.id or policy.can_curate_others(user)


def index(request):
    kind = request.GET.get("kind", "")
    queryset = visible_media(request.user)

    if kind in Media.Kind.values:
        queryset = queryset.filter(kind=kind)
    else:
        kind = ""

    paginator = Paginator(queryset, PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "core/index.html",
        {"page_obj": page_obj, "kind": kind, "kinds": Media.Kind.choices},
    )


def media_detail(request, uid: str):
    media = get_object_or_404(
        Media.objects.select_related("owner", "category").prefetch_related(
            "chapters", "subtitles", "comments"
        ),
        uid=uid,
    )
    if not can_view(request.user, media):
        raise Http404("media not found")

    if request.method == "POST":
        return _add_comment(request, media)

    is_owner = request.user.is_authenticated and media.owner_id == request.user.id
    if not is_owner:
        Media.objects.filter(pk=media.pk).update(views_count=F("views_count") + 1)
        media.views_count += 1

    comments = (
        Comment.objects.filter(media=media, is_hidden=False)
        .select_related("author")
        .order_by("created_at")
    )

    return render(
        request,
        "core/media_detail.html",
        {"media": media, "comments": comments, "is_owner": is_owner},
    )


@require_POST
@login_required
def _add_comment(request, media: Media):
    body = (request.POST.get("body") or "").strip()
    if not body:
        return redirect("core:media", uid=media.uid)

    Comment.objects.create(
        media=media,
        author=request.user,
        body=body[:4000],
    )
    return redirect("core:media", uid=media.uid)


def search(request):
    query = (request.GET.get("q") or "").strip()
    results = visible_media(request.user).none()

    if query:
        results = visible_media(request.user).filter(
            Q(title__icontains=query) | Q(description__icontains=query)
        )

    paginator = Paginator(results, PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "core/search.html",
        {"query": query, "page_obj": page_obj, "count": paginator.count},
    )


def _flag(raw: str | None, default: bool) -> bool:
    if raw is None or raw == "":
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def embed(request, uid: str):
    """Bare player for iframes: no header, no navigation, nothing to click away."""
    media = get_object_or_404(Media.objects.select_related("owner"), uid=uid)
    if not can_view(request.user, media):
        raise Http404("media not found")

    try:
        start = max(0, int(float(request.GET.get("start", 0))))
    except (TypeError, ValueError):
        start = 0

    context = {
        "media": media,
        "autoplay": _flag(request.GET.get("autoplay"), False),
        "controls": _flag(request.GET.get("controls"), True),
        "show_title": _flag(request.GET.get("title"), False),
        "start": start,
        "is_public_media": media.visibility == Media.Visibility.PUBLIC
        and media.state == Media.State.READY,
    }
    response = render(request, "core/embed.html", context)
    # Embeds are designed to live inside other sites.
    response["X-Frame-Options"] = "ALLOWALL"
    return response


@login_required
def upload(request):
    return render(request, "core/upload.html")


@never_cache
def health(request):
    """Liveness probe. Deliberately dumb: it must answer even when the
    database is down, so orchestration can tell "starting" from "crashed"."""
    return JsonResponse({"status": "ok", "service": "akonmedia"})
