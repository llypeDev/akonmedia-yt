"""Videos of the library, with their comments, in a site's frame.

The same arrangement as the lives (sessions.py): the frame signs the person in
through the site and sends the session in a header. A private video of the
integration's owner opens to the site's people, a live's recording to the
classes the live was for; the site's teachers moderate the comments.
"""
from __future__ import annotations

import json

from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from apps.core.templatetags.akon import avatar
from apps.media import access
from apps.media.models import Comment, Media
from apps.rbac import permissions as policy

#: Longest comment a frame takes.
MAX_LENGTH = 2000
#: Most threads one answer carries, the newest first.
THREADS = 100
RATE_SECONDS = 5


def media_for(request, uid: str) -> Media:
    media = get_object_or_404(Media.objects.select_related("owner"), uid=uid)
    embed = getattr(request, "embed", None)
    granted = embed is not None and media.state == Media.State.READY and embed.grants_media(media)
    if not (access.can_view_media(request.user, media) or granted):
        raise Http404("media not found")
    return media


def moderates(request, media: Media) -> bool:
    embed = getattr(request, "embed", None)
    identity = embed.identity if embed is not None else None
    return bool(identity and identity.is_moderator and embed.grants_media(media))


def _staff(user, media: Media) -> bool:
    if user.pk == media.owner_id or policy.can_moderate_comments(user):
        return True
    try:
        identity = user.external_identity
    except ObjectDoesNotExist:
        return False
    return identity.is_moderator and identity.integration.owner_id == media.owner_id


def _comment(comment: Comment, media: Media, user, moderator: bool) -> dict:
    author = comment.author
    return {
        "id": str(comment.pk),
        "author": author.get_short_name(),
        "avatar": str(avatar(author, "sm")),
        "body": comment.body,
        "at": comment.created_at.isoformat(),
        "timestamp": comment.timestamp,
        "staff": _staff(author, media),
        "mine": bool(user.is_authenticated and comment.author_id == user.pk),
        "can_hide": moderator,
    }


def play_info(media: Media) -> dict:
    return {
        "title": media.title,
        "kind": media.kind,
        "hls": media.hls_url or "",
        "src": media.file.url if media.file else "",
        "poster": media.poster.url if media.poster else "",
        "page": reverse("core:media", args=[media.uid]),
    }


@require_GET
def info(request, uid: str):
    """What the frame needs to play a video it could not show before the
    person was signed in (a private one)."""
    media = media_for(request, uid)
    return JsonResponse(play_info(media))


def _threads(request, media: Media) -> dict:
    user = request.user
    moderator = moderates(request, media)
    tops = list(
        media.comments.filter(parent__isnull=True, is_hidden=False)
        .select_related("author", "author__external_identity__integration")
        .order_by("-created_at")[:THREADS]
    )
    replies = {}
    for reply in (
        media.comments.filter(parent__in=tops, is_hidden=False)
        .select_related("author", "author__external_identity__integration")
        .order_by("created_at")
    ):
        replies.setdefault(reply.parent_id, []).append(_comment(reply, media, user, moderator))
    threads = []
    for top in tops:
        item = _comment(top, media, user, moderator)
        item["replies"] = replies.get(top.pk, [])
        threads.append(item)
    return {
        "comments": threads,
        "count": media.comments.filter(is_hidden=False).count(),
        "can_post": bool(user.is_authenticated and media.state == Media.State.READY),
        "can_moderate": moderator,
    }


def comments(request, uid: str):
    """GET: the threads. POST: a comment, or a reply with ``parent``."""
    media = media_for(request, uid)
    if request.method == "GET":
        return JsonResponse(_threads(request, media))
    if request.method != "POST":
        return JsonResponse({"detail": _("Not allowed.")}, status=405)
    user = request.user
    if not user.is_authenticated:
        return JsonResponse({"detail": _("Sign in to comment.")}, status=403)
    if media.state != Media.State.READY:
        return JsonResponse({"detail": _("Comments open once the media is published.")}, status=400)
    try:
        data = json.loads(request.body or b"{}") if request.content_type == "application/json" else request.POST
    except ValueError:
        data = {}
    body = str(data.get("body") or "").strip()
    if not body:
        return JsonResponse({"detail": _("Write a comment first.")}, status=400)
    if len(body) > MAX_LENGTH:
        return JsonResponse(
            {"detail": _("Comments are limited to %(limit)d characters.") % {"limit": MAX_LENGTH}}, status=400
        )
    parent = None
    if data.get("parent"):
        parent = media.comments.filter(pk=str(data.get("parent")), is_hidden=False, parent__isnull=True).first()
        if parent is None:
            return JsonResponse({"detail": _("That comment is gone.")}, status=400)
    try:
        timestamp = float(data.get("timestamp")) if data.get("timestamp") not in (None, "") else None
    except (TypeError, ValueError):
        timestamp = None
    if timestamp is not None and (timestamp < 0 or timestamp > 24 * 3600):
        timestamp = None
    if not cache.add(f"embed:comment:{user.pk}", 1, timeout=RATE_SECONDS):
        return JsonResponse({"detail": _("Slow down a little: one comment every few seconds.")}, status=429)
    comment = Comment.objects.create(media=media, author=user, parent=parent, body=body, timestamp=timestamp)
    return JsonResponse({"comment": _comment(comment, media, user, moderates(request, media))}, status=201)


@require_POST
def hide(request, uid: str, comment_id):
    media = media_for(request, uid)
    if not moderates(request, media):
        return JsonResponse({"detail": _("You cannot moderate these comments.")}, status=403)
    comment = get_object_or_404(Comment, pk=comment_id, media=media)
    comment.is_hidden = True
    comment.save(update_fields=["is_hidden"])
    return JsonResponse({"hidden": str(comment.pk)})
