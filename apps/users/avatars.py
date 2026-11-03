"""Profile pictures: a character from the gallery, an uploaded photo, or the
initials on a colour of the account's own (the default).

``User.avatar`` says which: a character's key, ``"photo"`` or empty. The photo
itself is ``User.avatar_photo``, kept when someone switches to a character so
they can switch back without uploading it again.

An uploaded picture is never stored as sent. It is decoded, turned upright,
cropped to a square, scaled down and written again as WebP: what is served is
always a small, plain image, with no metadata (a phone photo's location among
it) and nothing a browser could run.
"""
from __future__ import annotations

import io
import secrets

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.templatetags.static import static
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from PIL import Image, ImageOps, UnidentifiedImageError

#: What ``User.avatar`` holds for the uploaded photo.
PHOTO = "photo"

#: The gallery, in the order the account page shows it. The keys are stored
#: on accounts and name the files in static/avatars/ (built by
#: tools/build_avatars.py): a character may be redrawn, never renamed.
CHARACTERS = {
    "robot": gettext_lazy("Robot"),
    "cat": gettext_lazy("Cat"),
    "bear": gettext_lazy("Bear"),
    "ghost": gettext_lazy("Ghost"),
    "alien": gettext_lazy("Alien"),
    "astronaut": gettext_lazy("Astronaut"),
    "frog": gettext_lazy("Frog"),
    "bunny": gettext_lazy("Bunny"),
    "penguin": gettext_lazy("Penguin"),
    "fox": gettext_lazy("Fox"),
    "slime": gettext_lazy("Slime"),
    "owl": gettext_lazy("Owl"),
    "panda": gettext_lazy("Panda"),
    "chick": gettext_lazy("Chick"),
    "sun": gettext_lazy("Sun"),
    "monster": gettext_lazy("Monster"),
}

#: Largest upload accepted, before it is scaled down.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
#: Refuse pictures bigger than this before decoding them (a few kilobytes of
#: PNG can claim to be gigapixels wide).
MAX_PIXELS = 50_000_000
#: Side of the stored square: sharp at the largest size shown (88px) on a
#: screen with three pixels per point.
SIZE = 320
ACCEPTED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF", "MPO"}


def choices():
    """For the model field and the admin: every value ``avatar`` may hold."""
    return [
        ("", gettext_lazy("Initials")),
        (PHOTO, gettext_lazy("Uploaded photo")),
        *CHARACTERS.items(),
    ]


def character_url(key: str) -> str:
    return static(f"avatars/{key}.svg")


def image_url(user) -> str:
    """The picture to show for this account, or "" for the initials."""
    if user is None:
        return ""
    choice = getattr(user, "avatar", "") or ""
    if choice == PHOTO:
        photo = getattr(user, "avatar_photo", None)
        return photo.url if photo else ""
    if choice in CHARACTERS:
        return character_url(choice)
    return ""


def photo_name() -> str:
    # A new name for every upload: the old address may sit in caches for a
    # while, and must not show the new picture or the other way round.
    return f"{secrets.token_hex(12)}.webp"


def process_photo(upload) -> ContentFile:
    """The upload as the square WebP that gets stored, or ValidationError."""
    if upload.size > MAX_UPLOAD_BYTES:
        raise ValidationError(
            _("The picture is too large: the limit is %(limit)d MB.")
            % {"limit": MAX_UPLOAD_BYTES // (1024 * 1024)}
        )
    unreadable = ValidationError(_("This file is not a picture we can read. Use a JPEG, PNG, WebP or GIF."))
    try:
        upload.seek(0)
        image = Image.open(upload)
        if image.format not in ACCEPTED_FORMATS:
            raise unreadable
        if image.width * image.height > MAX_PIXELS:
            raise ValidationError(_("The picture has too many pixels. Use one under 50 megapixels."))
        image.seek(0)  # the first frame of an animation
        image.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise unreadable from exc

    image = ImageOps.exif_transpose(image)
    has_alpha = image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info)
    image = image.convert("RGBA" if has_alpha else "RGB")
    image = ImageOps.fit(image, (SIZE, SIZE), method=Image.Resampling.LANCZOS, centering=(0.5, 0.42))

    out = io.BytesIO()
    image.save(out, format="WEBP", quality=84, method=6)
    return ContentFile(out.getvalue(), name=photo_name())


def set_photo(user, upload) -> None:
    """Store a new photo for the account and show it. Raises ValidationError."""
    content = process_photo(upload)
    old = user.avatar_photo.name if user.avatar_photo else ""
    user.avatar_photo.save(content.name, content, save=False)
    user.avatar = PHOTO
    user.save(update_fields=["avatar", "avatar_photo"])
    if old and old != user.avatar_photo.name:
        user.avatar_photo.storage.delete(old)


def remove_photo(user) -> None:
    """Delete the account's photo; back to the initials if it was showing."""
    if user.avatar_photo:
        user.avatar_photo.delete(save=False)
    if user.avatar == PHOTO:
        user.avatar = ""
    user.save(update_fields=["avatar", "avatar_photo"])


def choose(user, value: str) -> None:
    """Show a character, the photo or the initials. Raises ValidationError."""
    value = (value or "").strip()
    if value == "initials":
        value = ""
    if value and value != PHOTO and value not in CHARACTERS:
        raise ValidationError(_("Pick one of the pictures shown."))
    if value == PHOTO and not user.avatar_photo:
        raise ValidationError(_("Upload a photo first."))
    user.avatar = value
    user.save(update_fields=["avatar"])
