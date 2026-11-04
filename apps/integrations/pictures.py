"""The picture a site gives for a person: fetched safely (net.py) and then
processed like an uploaded photo."""
from __future__ import annotations

from django.core.files.uploadedfile import SimpleUploadedFile

from apps.users import avatars

from . import net

MAX_BYTES = 5 * 1024 * 1024

PictureError = net.NetError
public_address = net.public_address


def download(url: str) -> bytes:
    answer = net.request("GET", url, headers={"Accept": "image/*"}, max_bytes=MAX_BYTES)
    if answer.status != 200:
        raise PictureError(f"HTTP {answer.status}")
    return answer.body


def apply(identity) -> bool:
    """Show the site's picture for the person, or their initials when the
    site gives none or it cannot be used. True if a picture was stored."""
    user = identity.user
    url = identity.picture_url
    if not url:
        if user.avatar_photo or user.avatar:
            avatars.remove_photo(user)
            if user.avatar:
                user.avatar = ""
                user.save(update_fields=["avatar"])
        return False
    data = download(url)
    upload = SimpleUploadedFile("picture", data, content_type="image/*")
    avatars.set_photo(user, upload)
    return True
