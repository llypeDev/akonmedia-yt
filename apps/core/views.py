"""Public pages and operational endpoints."""
import json
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Count, Exists, F, OuterRef, Prefetch, Q, Sum
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.live.models import Live
from apps.media import access, pipeline
from apps.media.models import Category, Comment, Media, Playlist, PlaylistItem
from apps.rbac import permissions as policy
from apps.users.models import User

PAGE_SIZE = 24

SORTS = {
    "latest": ("-created_at",),
    "popular": ("-views_count", "-created_at"),
    "oldest": ("created_at",),
    "title": ("title",),
}
SORT_LABELS = [
    ("latest", gettext_lazy("Latest")),
    ("popular", gettext_lazy("Most viewed")),
    ("oldest", gettext_lazy("Oldest")),
    ("title", gettext_lazy("A–Z")),
]
KIND_TITLES = {
    "video": gettext_lazy("Videos"),
    "audio": gettext_lazy("Audio"),
    "image": gettext_lazy("Images"),
    "document": gettext_lazy("Documents"),
}


def visible_media(user):
    """Everything the visitor may find through listings: public and ready.

    Owners reach their own unpublished items through the studio, and curators
    through the manage page, so listings look the same for everybody.
    """
    return access.listed_media()


def can_view(user, media: Media) -> bool:
    return access.can_view_media(user, media)


def _sorted(queryset, sort: str):
    if sort not in SORTS:
        sort = "latest"
    return queryset.order_by(*SORTS[sort]), sort


def _safe_next(request, fallback: str) -> str:
    target = request.POST.get("next") or request.GET.get("next") or ""
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return fallback


# --------------------------------------------------------------------------
# library
# --------------------------------------------------------------------------

def index(request):
    kind = request.GET.get("kind", "")
    category_slug = request.GET.get("category", "")
    queryset = visible_media(request.user)

    if kind in Media.Kind.values:
        queryset = queryset.filter(kind=kind)
    else:
        kind = ""

    category = None
    if category_slug:
        category = Category.objects.filter(slug=category_slug).first()
        if category is not None:
            queryset = queryset.filter(category=category)

    queryset, sort = _sorted(queryset, request.GET.get("sort", "latest"))
    paginator = Paginator(queryset, PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page"))

    filtered = bool(kind or category or sort != "latest")
    first_page = page_obj.number == 1
    stats = trending = live_now = upcoming_lives = None
    if first_page and not filtered:
        lives = Live.objects.filter(access.live_listed_q()).select_related("owner", "category", "recording")
        live_now = list(lives.filter(status__in=Live.ON_AIR).order_by("-started_at")[:4])
        upcoming_lives = list(
            lives.filter(status=Live.Status.SCHEDULED, scheduled_for__gte=timezone.now() - timedelta(hours=1))
            .order_by("scheduled_for")[:4]
        )
        listed = access.listed_media()
        stats = listed.aggregate(
            items=Count("id"), seconds=Sum("duration_seconds"), views=Sum("views_count")
        )
        stats["hours"] = round((stats["seconds"] or 0) / 3600, 1)
        stats["creators"] = listed.values("owner").distinct().count()
        trending = list(listed.filter(views_count__gt=0).order_by("-views_count", "-created_at")[:4])

    return render(
        request,
        "core/index.html",
        {
            "page_obj": page_obj,
            "kind": kind,
            "kind_title": KIND_TITLES.get(kind, ""),
            "kinds": Media.Kind.choices,
            "category": category,
            "categories": Category.objects.annotate(
                listed=Count("media", filter=access.listed_q("media__"))
            ).filter(listed__gt=0),
            "sort": sort,
            "sorts": SORT_LABELS,
            "stats": stats,
            "trending": trending,
            "live_now": live_now,
            "upcoming_lives": upcoming_lives,
            "show_hero": first_page and not filtered,
        },
    )


def search(request):
    query = (request.GET.get("q") or "").strip()
    kind = request.GET.get("kind", "")
    results = visible_media(request.user).none()

    if query:
        results = visible_media(request.user).filter(
            Q(title__icontains=query)
            | Q(description__icontains=query)
            | Q(category__name__icontains=query)
            | Q(owner__display_name__icontains=query)
        )
        if kind in Media.Kind.values:
            results = results.filter(kind=kind)
        else:
            kind = ""

    results, sort = _sorted(results, request.GET.get("sort", "latest"))
    paginator = Paginator(results, PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "core/search.html",
        {
            "query": query,
            "page_obj": page_obj,
            "count": paginator.count,
            "kind": kind,
            "kinds": Media.Kind.choices,
            "sort": sort,
            "sorts": SORT_LABELS,
        },
    )


def channel(request, user_id):
    owner = get_object_or_404(User, pk=user_id, is_active=True)
    listed = access.listed_media().filter(owner=owner)
    stats = listed.aggregate(items=Count("id"), views=Sum("views_count"))
    queryset, sort = _sorted(listed, request.GET.get("sort", "latest"))
    page_obj = Paginator(queryset, PAGE_SIZE).get_page(request.GET.get("page"))

    playlists = (
        Playlist.objects.filter(owner=owner, visibility=Media.Visibility.PUBLIC)
        .annotate(item_count=Count("entries", filter=access.listed_q("entries__media__")))
        .order_by("-created_at")
    )
    return render(
        request,
        "core/channel.html",
        {
            "owner": owner,
            "page_obj": page_obj,
            "stats": stats,
            "sort": sort,
            "sorts": SORT_LABELS,
            "playlists": with_covers(playlists, request.user),
            "is_self": request.user.is_authenticated and request.user.pk == owner.pk,
        },
    )


def with_covers(playlists, user):
    """Attach the first viewable item to each playlist, for its thumbnail."""
    playlists = list(playlists)
    for playlist in playlists:
        first = (
            playlist.entries.select_related("media")
            .filter(media__state=Media.State.READY)
            .filter(access.viewable_q(user, "media__"))
            .order_by("position", "id")
            .first()
        )
        playlist.cover = first.media if first else None
    return playlists


# --------------------------------------------------------------------------
# media page
# --------------------------------------------------------------------------

def playlist_entries(playlist: Playlist, user):
    """Entries of ``playlist`` that ``user`` may actually open, in order."""
    return (
        playlist.entries.select_related("media", "media__owner")
        .filter(media__state=Media.State.READY)
        .filter(access.viewable_q(user, "media__"))
        .order_by("position", "id")
    )


def _playlist_context(request, media: Media):
    list_uid = request.GET.get("list")
    if not list_uid:
        return None
    playlist = Playlist.objects.select_related("owner").filter(uid=list_uid).first()
    if playlist is None or not access.can_view_playlist(request.user, playlist):
        return None
    entries = list(playlist_entries(playlist, request.user))
    positions = [entry.media_id for entry in entries]
    if media.pk not in positions:
        return None
    index = positions.index(media.pk)
    previous = entries[index - 1].media if index > 0 else None
    following = entries[index + 1].media if index + 1 < len(entries) else None

    def link(item):
        return f"{reverse('core:media', args=[item.uid])}?list={playlist.uid}" if item else ""

    return {
        "playlist": playlist,
        "entries": entries,
        "index": index,
        "position": index + 1,
        "previous": previous,
        "next": following,
        "previous_url": link(previous),
        "next_url": link(following),
    }


def _related(media: Media, limit: int = 10):
    listed = access.listed_media().exclude(pk=media.pk)
    near = Q(owner=media.owner_id)
    if media.category_id:
        near |= Q(category=media.category_id)
    picks = list(listed.filter(near).order_by("-views_count", "-created_at")[:limit])
    if len(picks) < limit:
        seen = [item.pk for item in picks]
        picks += list(listed.exclude(pk__in=seen).order_by("-created_at")[: limit - len(picks)])
    return picks


def media_detail(request, uid: str):
    media = get_object_or_404(
        Media.objects.select_related("owner", "category").prefetch_related("chapters", "subtitles"),
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

    visible = Comment.objects.filter(media=media, is_hidden=False).select_related("author")
    comments = (
        visible.filter(parent__isnull=True)
        .order_by("-created_at")
        .prefetch_related(
            Prefetch("replies", queryset=visible.order_by("created_at"), to_attr="visible_replies")
        )
    )

    chapters = list(media.chapters.all())
    subtitles = list(media.subtitles.all())
    playlist = _playlist_context(request, media)

    user_playlists = []
    if request.user.is_authenticated:
        user_playlists = Playlist.objects.filter(owner=request.user).annotate(
            has_item=Exists(PlaylistItem.objects.filter(playlist=OuterRef("pk"), media=media))
        ).order_by("-created_at")

    page_url = request.build_absolute_uri(reverse("core:media", args=[media.uid]))
    embed_url = request.build_absolute_uri(reverse("core:embed", args=[media.uid]))
    embed_snippet = (
        f'<iframe src="{embed_url}" width="640" height="360" frameborder="0" '
        f'allow="autoplay; fullscreen; picture-in-picture" allowfullscreen '
        f'title="{media.title}"></iframe>'
    )
    renditions = []
    if media.hls_playlist:
        renditions = [f"{height}p" for height, _ in pipeline.plan_renditions(media.width, media.height)]
    extension = media.original_name.rsplit(".", 1)[-1].upper() if "." in media.original_name else ""

    return render(
        request,
        "core/media_detail.html",
        {
            "media": media,
            "comments": comments,
            "comment_count": visible.count(),
            "is_owner": is_owner,
            "can_edit": access.can_edit_media(request.user, media),
            "can_moderate": policy.can_moderate_comments(request.user),
            "subtitles": subtitles,
            "chapters": chapters,
            "chapters_json": json.dumps(
                [{"start": round(c.start_seconds, 3), "title": c.title} for c in chapters]
            ),
            "storyboard_json": json.dumps(media.storyboard_config) if media.storyboard_config else "",
            "related": _related(media),
            "playlist_ctx": playlist,
            "user_playlists": user_playlists,
            "page_url": page_url,
            "embed_url": embed_url,
            "embed_snippet": embed_snippet,
            "renditions": renditions,
            "extension": extension,
            "owner_uploads": access.listed_media().filter(owner=media.owner_id).count(),
            "poll": media.state in (Media.State.PENDING, Media.State.PROCESSING),
        },
    )


def _parse_uuid_pk(model, raw, **filters):
    if not raw:
        return None
    try:
        return model.objects.filter(pk=raw, **filters).first()
    except (ValueError, ValidationError):
        return None


@require_POST
@login_required
def _add_comment(request, media: Media):
    target = request.get_full_path()
    body = (request.POST.get("body") or "").strip()
    if not body:
        return redirect(target)
    if media.state != Media.State.READY:
        messages.error(request, _("Comments open once the media is published."))
        return redirect(target)

    parent = _parse_uuid_pk(
        Comment, request.POST.get("parent"), media=media, is_hidden=False
    )
    if parent is not None and parent.parent_id:
        # Threads are one level deep: a reply to a reply joins the same thread.
        parent = parent.parent

    timestamp = None
    raw_time = request.POST.get("timestamp")
    if raw_time:
        try:
            timestamp = max(0.0, float(raw_time))
        except ValueError:
            timestamp = None
        if timestamp is not None and media.duration_seconds:
            timestamp = min(timestamp, media.duration_seconds)

    comment = Comment.objects.create(
        media=media,
        author=request.user,
        parent=parent,
        body=body[:4000],
        timestamp=timestamp,
    )
    return redirect(f"{target}#c-{comment.pk}")


@login_required
@require_POST
def comment_action(request, comment_id):
    comment = get_object_or_404(Comment.objects.select_related("media"), pk=comment_id)
    action = request.POST.get("action", "")
    user = request.user
    may_moderate = policy.can_moderate_comments(user)

    if action == "delete" and (comment.author_id == user.id or may_moderate):
        comment.delete()
        messages.success(request, _("Comment deleted."))
    elif action in {"hide", "unhide"} and may_moderate:
        comment.is_hidden = action == "hide"
        comment.save(update_fields=["is_hidden"])
        messages.success(
            request, _("Comment hidden.") if comment.is_hidden else _("Comment restored.")
        )
    else:
        raise PermissionDenied("You cannot do that to this comment.")

    fallback = f"{reverse('core:media', args=[comment.media.uid])}#comments"
    return redirect(_safe_next(request, fallback))


@login_required
@require_POST
def add_to_playlist(request, uid: str):
    media = get_object_or_404(Media, uid=uid)
    if not can_view(request.user, media) or media.state != Media.State.READY:
        raise Http404("media not found")

    fallback = reverse("core:media", args=[media.uid])
    new_title = (request.POST.get("new_title") or "").strip()
    if new_title:
        visibility = request.POST.get("visibility", Media.Visibility.PRIVATE)
        if visibility not in Media.Visibility.values:
            visibility = Media.Visibility.PRIVATE
        playlist = Playlist.objects.create(
            owner=request.user, title=new_title[:255], visibility=visibility
        )
    else:
        playlist = get_object_or_404(Playlist, uid=request.POST.get("playlist", ""))
        if not access.can_edit_playlist(request.user, playlist):
            raise Http404("playlist not found")

    existing = playlist.entries.filter(media=media)
    if request.POST.get("action") == "remove":
        existing.delete()
        messages.success(request, _("Removed from “%(title)s”.") % {"title": playlist.title})
    elif existing.exists():
        messages.info(request, _("Already in “%(title)s”.") % {"title": playlist.title})
    else:
        last = playlist.entries.order_by("-position").values_list("position", flat=True).first()
        PlaylistItem.objects.create(
            playlist=playlist, media=media, position=0 if last is None else last + 1
        )
        messages.success(request, _("Added to “%(title)s”.") % {"title": playlist.title})
    return redirect(_safe_next(request, fallback))


# --------------------------------------------------------------------------
# embed and upload
# --------------------------------------------------------------------------

def _flag(raw: str | None, default: bool) -> bool:
    if raw is None or raw == "":
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


EMBED_LAYOUTS = ("player", "comments", "both")


def embed(request, uid: str):
    """Bare player for iframes: no header, no navigation, nothing to click away.

        ?layout=player|comments|both   the comments beside the player, or alone
        ?school=<integration key>      people comment through that site
                                       (apps.integrations); a private video of
                                       the integration's owner shows once they
                                       have signed in
        ?theme=dark|light
    """
    from apps.integrations.models import Integration

    media = get_object_or_404(
        Media.objects.select_related("owner").prefetch_related("subtitles", "chapters"), uid=uid
    )
    layout = request.GET.get("layout", "player")
    layout = layout if layout in EMBED_LAYOUTS else "player"
    theme = request.GET.get("theme", "")
    theme = theme if theme in ("dark", "light") else ""
    school = (request.GET.get("school") or "")[:40]
    integration = Integration.objects.filter(key=school, is_active=True).first() if school else None

    available = can_view(request.user, media)
    awaiting = (
        not available and integration is not None and media.state == Media.State.READY
        and integration.grants_media(media)
    )
    if not (available or awaiting):
        raise Http404("media not found")

    try:
        start = max(0, int(float(request.GET.get("start", 0))))
    except (TypeError, ValueError):
        start = 0

    chapters = list(media.chapters.all())
    context = {
        "media": media,
        "available": available,
        "awaiting": awaiting,
        "layout": layout,
        "theme": theme,
        "integration": integration,
        "autoplay": _flag(request.GET.get("autoplay"), False),
        "controls": _flag(request.GET.get("controls"), True),
        "show_title": _flag(request.GET.get("title"), False),
        "start": start,
        "is_public_media": media.visibility == Media.Visibility.PUBLIC
        and media.state == Media.State.READY,
        "subtitles": list(media.subtitles.all()),
        "chapters_json": json.dumps(
            [{"start": round(c.start_seconds, 3), "title": c.title} for c in chapters]
        ),
        "storyboard_json": json.dumps(media.storyboard_config) if media.storyboard_config else "",
        "page_url": request.build_absolute_uri(reverse("core:media", args=[media.uid])),
    }
    response = render(request, "core/embed.html", context)
    # Embeds are designed to live inside other sites; a school's only in its own.
    response["X-Frame-Options"] = "ALLOWALL"
    response["Content-Security-Policy"] = integration.frame_ancestors() if integration else "frame-ancestors *"
    return response


@login_required
def upload(request):
    return render(
        request,
        "core/upload.html",
        {"categories": Category.objects.order_by("name"), "visibilities": Media.Visibility.choices},
    )


# --------------------------------------------------------------------------
# operations
# --------------------------------------------------------------------------

@never_cache
def health(request):
    """Liveness probe. Deliberately dumb: it must answer even when the
    database is down, so orchestration can tell "starting" from "crashed"."""
    return JsonResponse({"status": "ok", "service": "qqplay"})


#: The readiness checks as the status page names them: title, what depends
#: on it, and an icon from the sprite.
STATUS_CHECKS = {
    "database": (
        gettext_lazy("Database"),
        gettext_lazy("Accounts, media, comments, playlists and lives."),
        "hard-drive",
    ),
    "cache": (
        gettext_lazy("Cache"),
        gettext_lazy("Fast memory shared by the portal's processes, the lives' state included."),
        "zap",
    ),
    "encoder": (
        gettext_lazy("Encoder"),
        gettext_lazy("ffmpeg and ffprobe, which turn uploads into adaptive video, posters and previews."),
        "cpu",
    ),
}


def wants_html(request) -> bool:
    """Whether the client is a browser asking for a page.

    Probes, monitors and scripts send no Accept header, or */*, and keep
    getting JSON; ?format=json forces it in a browser too.
    """
    if request.GET.get("format") == "json":
        return False
    return request.get_preferred_type(["application/json", "text/html"]) == "text/html"


@never_cache
def ready(request):
    """Readiness probe: can this instance actually serve traffic?

    JSON for probes and monitors, a status page for a browser. Both answer 503
    when the instance cannot serve.
    """
    checks = {}
    healthy = True
    try:
        connection.ensure_connection()
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 - report, do not crash the probe
        checks["database"] = f"error: {type(exc).__name__}"
        healthy = False
    try:
        cache.set("qqplay:ready", "1", 5)
        checks["cache"] = "ok" if cache.get("qqplay:ready") == "1" else "error"
    except Exception as exc:  # noqa: BLE001
        checks["cache"] = f"error: {type(exc).__name__}"
    if checks["cache"] != "ok":
        healthy = False
    checks["encoder"] = "ok" if pipeline.available() else "missing"
    status = 200 if healthy else 503

    if wants_html(request):
        response = HttpResponse(_status_page(checks, healthy), status=status)
    else:
        response = JsonResponse(
            {"status": "ok" if healthy else "degraded", "checks": checks}, status=status
        )
    patch_vary_headers(response, ["Accept"])
    return response


def _status_page(checks: dict, healthy: bool) -> str:
    """The status page. Rendered without the request on purpose: no context
    processor, session or user lookup touches the database, so the page still
    shows up, and says what is wrong, when the database is down."""
    rows = []
    for key, result in checks.items():
        title, description, icon = STATUS_CHECKS[key]
        if result == "ok":
            tone, label = "ok", _("Operational")
        elif result == "missing":
            tone, label = "warn", _("Not installed")
        else:
            tone, label = "down", _("Failing")
        detail = result.split(":", 1)[1].strip() if result.startswith("error:") else ""
        rows.append({
            "key": key, "title": title, "description": description, "icon": icon,
            "tone": tone, "label": label, "detail": detail,
        })

    if not healthy:
        overall = "down"
        headline = _("The portal is having trouble")
        summary = _(
            "A service it depends on is not answering. Pages may fail until it comes back; "
            "the details are below."
        )
    elif any(row["tone"] != "ok" for row in rows):
        overall = "warn"
        headline = _("Running, without the encoder")
        summary = _(
            "Browsing and playback work, but new uploads wait in the queue until ffmpeg is "
            "available."
        )
    else:
        overall = "ok"
        headline = _("All systems operational")
        summary = _("The portal is ready for visits, uploads and lives.")

    return render_to_string(
        "core/status.html",
        {
            "PORTAL_NAME": settings.PORTAL_NAME,
            "overall": overall,
            "headline": headline,
            "summary": summary,
            "checks": rows,
            "checked_at": timezone.localtime(),
            "refresh_seconds": 30,
        },
    )
