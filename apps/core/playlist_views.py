"""Playlist pages: list, create, view, and reorder."""
import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Max, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.media import access
from apps.media.models import Media, Playlist, PlaylistItem

from .views import playlist_entries, with_covers


def _can_edit(user, playlist: Playlist) -> bool:
    return access.can_edit_playlist(user, playlist)


def _can_view(user, playlist: Playlist) -> bool:
    return access.can_view_playlist(user, playlist)


def _counted(queryset, user):
    return queryset.select_related("owner").annotate(
        item_count=Count(
            "entries",
            filter=Q(entries__media__state=Media.State.READY)
            & access.viewable_q(user, "entries__media__"),
        )
    )


def playlists(request):
    if request.method == "POST":
        return _create(request)

    user = request.user
    mine = []
    others = Playlist.objects.filter(visibility=Media.Visibility.PUBLIC)
    if user.is_authenticated:
        mine = with_covers(_counted(Playlist.objects.filter(owner=user), user), user)
        others = others.exclude(owner=user)
    others = with_covers(_counted(others, user).order_by("-created_at")[:60], user)

    return render(
        request,
        "core/playlists.html",
        {"my_playlists": mine, "playlists": others, "visibilities": Media.Visibility.choices},
    )


@login_required
@require_POST
def _create(request):
    title = (request.POST.get("title") or "").strip()
    if not title:
        messages.error(request, _("A playlist needs a title."))
        return redirect("core:playlists")

    visibility = request.POST.get("visibility", Media.Visibility.PUBLIC)
    if visibility not in Media.Visibility.values:
        visibility = Media.Visibility.PRIVATE

    playlist = Playlist.objects.create(
        title=title[:255],
        description=(request.POST.get("description") or "")[:2000],
        visibility=visibility,
        owner=request.user,
    )
    messages.success(
        request, _("Playlist “%(title)s” created. Add something to it.") % {"title": playlist.title}
    )
    return redirect("core:playlist_edit", uid=playlist.uid)


def playlist_detail(request, uid: str):
    playlist = get_object_or_404(Playlist.objects.select_related("owner"), uid=uid)
    if not _can_view(request.user, playlist):
        raise Http404("playlist not found")

    entries = list(playlist_entries(playlist, request.user))
    total_seconds = sum(entry.media.duration_seconds or 0 for entry in entries)
    return render(
        request,
        "core/playlist_detail.html",
        {
            "playlist": playlist,
            "entries": entries,
            "total_seconds": total_seconds,
            "cover": entries[0].media if entries else None,
            "can_edit": _can_edit(request.user, playlist),
            "share_url": request.build_absolute_uri(),
        },
    )


@login_required
def playlist_edit(request, uid: str):
    playlist = get_object_or_404(Playlist.objects.select_related("owner"), uid=uid)
    if not _can_edit(request.user, playlist):
        raise Http404("playlist not found")

    if request.method == "POST":
        return _mutate(request, playlist)

    entries = playlist.entries.select_related("media").order_by("position", "id")
    in_list = set(entries.values_list("media_id", flat=True))
    suggestions = (
        Media.objects.filter(state=Media.State.READY)
        .filter(Q(owner=request.user) | access.listed_q())
        .exclude(pk__in=in_list)
        .order_by("-created_at")[:200]
    )
    return render(
        request,
        "core/playlist_edit.html",
        {
            "playlist": playlist,
            "entries": entries,
            "suggestions": suggestions,
            "visibilities": Media.Visibility.choices,
        },
    )


UID_IN_TEXT = re.compile(r"(?:/(?:view|embed)/)?([0-9a-z]{6,16})/?(?:[?#].*)?$")


def _uid_from(raw: str) -> str:
    """Accept a bare identifier or a pasted link to the media page."""
    raw = (raw or "").strip()
    match = UID_IN_TEXT.search(raw)
    return match.group(1) if match else raw


@require_POST
def _mutate(request, playlist: Playlist):
    action = request.POST.get("action", "")

    if action == "add":
        media = Media.objects.filter(uid=_uid_from(request.POST.get("media_uid", ""))).first()
        if media is None or not access.can_view_media(request.user, media):
            # Somebody else's private item is reported exactly like a typo.
            messages.error(request, _("No media with that identifier."))
        elif playlist.entries.filter(media=media).exists():
            messages.info(request, _("Already in this playlist."))
        else:
            last = playlist.entries.aggregate(Max("position"))["position__max"]
            PlaylistItem.objects.create(
                playlist=playlist, media=media, position=0 if last is None else last + 1
            )
            messages.success(request, _("Added to the playlist."))

    elif action == "remove":
        playlist.entries.filter(pk=request.POST.get("item_id") or 0).delete()
        _renumber(playlist)

    elif action == "move":
        _move(playlist, request.POST.get("item_id"), request.POST.get("direction"))

    elif action == "rename":
        title = (request.POST.get("title") or "").strip()
        if title:
            playlist.title = title[:255]
        playlist.description = (request.POST.get("description") or "")[:2000]
        visibility = request.POST.get("visibility")
        if visibility in Media.Visibility.values:
            playlist.visibility = visibility
        playlist.save(update_fields=["title", "description", "visibility"])
        messages.success(request, _("Playlist details saved."))

    elif action == "delete":
        title = playlist.title
        playlist.delete()
        messages.success(request, _("Playlist “%(title)s” deleted.") % {"title": title})
        return redirect("core:playlists")

    return redirect("core:playlist_edit", uid=playlist.uid)


def _renumber(playlist: Playlist) -> None:
    for index, item in enumerate(playlist.entries.order_by("position", "id")):
        if item.position != index:
            PlaylistItem.objects.filter(pk=item.pk).update(position=index)


def _move(playlist: Playlist, item_id, direction) -> None:
    items = list(playlist.entries.order_by("position", "id"))
    try:
        index = next(i for i, item in enumerate(items) if str(item.pk) == str(item_id))
    except StopIteration:
        return

    target = index - 1 if direction == "up" else index + 1
    if not 0 <= target < len(items):
        return

    items[index], items[target] = items[target], items[index]
    for position, item in enumerate(items):
        PlaylistItem.objects.filter(pk=item.pk).update(position=position)
