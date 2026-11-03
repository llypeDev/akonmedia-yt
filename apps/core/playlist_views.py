"""Playlist pages: list, create, view, and reorder."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Max, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.media.models import Media, Playlist, PlaylistItem
from apps.rbac import permissions as policy


def _visible_playlists(user):
    queryset = Playlist.objects.select_related("owner")
    public = Q(visibility=Media.Visibility.PUBLIC)
    if user.is_authenticated:
        if policy.can_curate_others(user):
            return queryset
        return queryset.filter(Q(owner=user) | public)
    return queryset.filter(public)


def _can_edit(user, playlist: Playlist) -> bool:
    if not user.is_authenticated:
        return False
    return playlist.owner_id == user.id or policy.can_curate_others(user)


def _can_view(user, playlist: Playlist) -> bool:
    if playlist.visibility == Media.Visibility.PUBLIC:
        return True
    return _can_edit(user, playlist)


def playlists(request):
    if request.method == "POST":
        return _create(request)
    items = _visible_playlists(request.user)
    return render(request, "core/playlists.html", {"playlists": items})


@login_required
@require_POST
def _create(request):
    title = (request.POST.get("title") or "").strip()
    if not title:
        messages.error(request, "A playlist needs a title.")
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
    return redirect("core:playlist_edit", uid=playlist.uid)


def playlist_detail(request, uid: str):
    playlist = get_object_or_404(Playlist.objects.select_related("owner"), uid=uid)
    if not _can_view(request.user, playlist):
        raise Http404("playlist not found")

    entries = (
        playlist.entries.select_related("media", "media__owner")
        .filter(media__state=Media.State.READY)
        .order_by("position", "id")
    )
    return render(
        request,
        "core/playlist_detail.html",
        {"playlist": playlist, "entries": entries, "can_edit": _can_edit(request.user, playlist)},
    )


@login_required
def playlist_edit(request, uid: str):
    playlist = get_object_or_404(Playlist.objects.select_related("owner"), uid=uid)
    if not _can_edit(request.user, playlist):
        raise Http404("playlist not found")

    if request.method == "POST":
        return _mutate(request, playlist)

    entries = playlist.entries.select_related("media").order_by("position", "id")
    return render(
        request,
        "core/playlist_edit.html",
        {"playlist": playlist, "entries": entries},
    )


@require_POST
def _mutate(request, playlist: Playlist):
    action = request.POST.get("action", "")

    if action == "add":
        media = Media.objects.filter(uid=request.POST.get("media_uid", "")).first()
        if media is None:
            messages.error(request, "No media with that identifier.")
        elif playlist.entries.filter(media=media).exists():
            messages.info(request, "Already in this playlist.")
        else:
            last = playlist.entries.aggregate(Max("position"))["position__max"] or 0
            PlaylistItem.objects.create(playlist=playlist, media=media, position=last + 1)

    elif action == "remove":
        playlist.entries.filter(pk=request.POST.get("item_id")).delete()
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
