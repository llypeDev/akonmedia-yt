"""Who may see and change media and playlists.

The visibility rules live here and nowhere else, the same way role checks live
in ``apps.rbac.permissions``. Views, templates and the API ask these helpers
instead of comparing ``visibility`` strings themselves.

The three levels mean:

``public``
    Listed in the library, search and channel pages; anyone may open it.
``unlisted``
    Not listed anywhere, but anyone holding the link may open or embed it.
``private``
    Only the owner and curators.

A media item is only ever shown to other people once it is ``ready``. Lives use
the same three levels; they have no ready state to wait for.
"""
from django.db.models import Q

from apps.rbac import permissions as policy

from .models import Media, Playlist

LINK_VISIBILITY = (Media.Visibility.PUBLIC, Media.Visibility.UNLISTED)


def _field(prefix: str, name: str) -> str:
    return f"{prefix}{name}" if prefix else name


def listed_q(prefix: str = "") -> Q:
    """Media that may appear in public listings."""
    return Q(**{
        _field(prefix, "visibility"): Media.Visibility.PUBLIC,
        _field(prefix, "state"): Media.State.READY,
    })


def link_q(prefix: str = "") -> Q:
    """Media that anyone holding the link may open."""
    return Q(**{
        _field(prefix, "visibility__in"): LINK_VISIBILITY,
        _field(prefix, "state"): Media.State.READY,
    })


def viewable_q(user, prefix: str = "") -> Q:
    """Everything ``user`` may open, as a filter usable through relations."""
    if user is not None and user.is_authenticated:
        if policy.can_curate_others(user):
            return Q()
        return link_q(prefix) | Q(**{_field(prefix, "owner"): user})
    return link_q(prefix)


def listed_media():
    return Media.objects.filter(listed_q()).select_related("owner", "category")


def can_view_media(user, media: Media) -> bool:
    if media.state == Media.State.READY and media.visibility in LINK_VISIBILITY:
        return True
    return can_edit_media(user, media)


def can_edit_media(user, media: Media) -> bool:
    if user is None or not user.is_authenticated:
        return False
    return media.owner_id == user.id or policy.can_curate_others(user)


def can_view_playlist(user, playlist: Playlist) -> bool:
    if playlist.visibility in LINK_VISIBILITY:
        return True
    return can_edit_playlist(user, playlist)


def can_edit_playlist(user, playlist: Playlist) -> bool:
    if user is None or not user.is_authenticated:
        return False
    return playlist.owner_id == user.id or policy.can_curate_others(user)


# ------------------------------------------------------------------ lives
# Duck-typed on ``owner_id`` and ``visibility`` so this module needs no import
# from apps.live, which itself depends on apps.media.


def live_listed_q(prefix: str = "") -> Q:
    """Lives that may appear in public listings, whatever their status."""
    return Q(**{_field(prefix, "visibility"): Media.Visibility.PUBLIC})


def can_view_live(user, live) -> bool:
    if live.visibility in LINK_VISIBILITY:
        return True
    return can_edit_live(user, live)


def can_edit_live(user, live) -> bool:
    if user is None or not user.is_authenticated:
        return False
    return live.owner_id == user.id or policy.can_curate_others(user)


def can_moderate_live_chat(user, live) -> bool:
    if user is None or not user.is_authenticated:
        return False
    return live.owner_id == user.id or policy.can_moderate_comments(user)
