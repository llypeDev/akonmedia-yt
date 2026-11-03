"""Media domain models.

The schema is the contract that survives every rewrite, so it is kept explicit
and documented: a media item owns its renditions, its subtitles, its chapters
and its comments.
"""
import secrets
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


def new_uid(length: int = 12) -> str:
    """Short public identifier used in URLs instead of the primary key."""
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def media_upload_path(instance, filename: str) -> str:
    return f"media/{timezone.now():%Y/%m}/{instance.uid}/{filename}"


def poster_upload_path(instance, filename: str) -> str:
    return f"media/{timezone.now():%Y/%m}/{instance.uid}/poster{_suffix(filename)}"


def _suffix(filename: str) -> str:
    _, dot, ext = filename.rpartition(".")
    return f".{ext.lower()}" if dot and len(ext) <= 5 else ""


class Category(models.Model):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    description = models.TextField(blank=True)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children"
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "categories"

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:140]
        super().save(*args, **kwargs)


class Media(models.Model):
    class Kind(models.TextChoices):
        VIDEO = "video", "Video"
        AUDIO = "audio", "Audio"
        IMAGE = "image", "Image"
        DOCUMENT = "document", "Document"

    class State(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    class Visibility(models.TextChoices):
        PUBLIC = "public", "Public"
        UNLISTED = "unlisted", "Unlisted"
        PRIVATE = "private", "Private"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    uid = models.CharField(max_length=16, unique=True, default=new_uid, editable=False)

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.VIDEO)
    state = models.CharField(max_length=16, choices=State.choices, default=State.PENDING)
    visibility = models.CharField(
        max_length=16, choices=Visibility.choices, default=Visibility.PUBLIC
    )

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="media"
    )
    category = models.ForeignKey(
        Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="media"
    )

    file = models.FileField(upload_to=media_upload_path, max_length=500)
    original_name = models.CharField(max_length=255, blank=True)
    size_bytes = models.BigIntegerField(default=0)
    duration_seconds = models.FloatField(default=0)
    width = models.PositiveIntegerField(default=0)
    height = models.PositiveIntegerField(default=0)
    poster = models.ImageField(upload_to=poster_upload_path, max_length=500, blank=True)
    hls_playlist = models.CharField(max_length=500, blank=True)
    #: Sprite sheet of thumbnails for the seek bar, relative to MEDIA_ROOT.
    storyboard = models.CharField(max_length=500, blank=True)
    #: interval, cols, rows, width, height and count of the storyboard tiles.
    storyboard_meta = models.JSONField(default=dict, blank=True)

    views_count = models.PositiveIntegerField(default=0)
    failure_reason = models.TextField(blank=True)

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    published_at = models.DateTimeField(null=True, blank=True)

    #: Identifier from an earlier deployment, used to make imports idempotent.
    legacy_id = models.CharField(max_length=120, null=True, blank=True, unique=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["state", "visibility"]),
            models.Index(fields=["kind", "visibility"]),
            models.Index(fields=["owner", "-created_at"]),
        ]
        verbose_name_plural = "media"

    def __str__(self) -> str:
        return self.title

    @property
    def is_ready(self) -> bool:
        return self.state == self.State.READY

    @property
    def is_public(self) -> bool:
        return self.visibility == self.Visibility.PUBLIC and self.is_ready

    @property
    def duration_label(self) -> str:
        total = int(self.duration_seconds or 0)
        hours, rest = divmod(total, 3600)
        minutes, seconds = divmod(rest, 60)
        return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"

    @property
    def hls_url(self) -> str:
        """Public URL of the HLS playlist, or an empty string when not packaged."""
        return _media_url(self.hls_playlist)

    @property
    def storyboard_url(self) -> str:
        return _media_url(self.storyboard)

    @property
    def storyboard_config(self) -> dict:
        """What the player needs to show seek previews, or ``{}``."""
        meta = self.storyboard_meta or {}
        if not self.storyboard or not meta.get("count"):
            return {}
        return {"src": self.storyboard_url, **meta}

    @property
    def thumbnail_url(self) -> str:
        """Best still picture for cards: the poster, or the image itself."""
        if self.poster:
            return self.poster.url
        if self.kind == self.Kind.IMAGE and self.file:
            return self.file.url
        return ""

    @property
    def resolution_label(self) -> str:
        if not self.height:
            return ""
        if self.kind == self.Kind.VIDEO:
            for label, minimum in (("4K", 2000), ("1440p", 1400), ("1080p", 1000),
                                   ("720p", 700), ("480p", 460), ("360p", 340)):
                if self.height >= minimum:
                    return label
            return f"{self.height}p"
        return f"{self.width}×{self.height}"

    def media_directory(self):
        """The per-item folder holding the original and everything derived
        from it, or ``None`` when that cannot be established safely."""
        from pathlib import Path

        if not self.file or not self.file.name:
            return None
        root = Path(settings.MEDIA_ROOT).resolve()
        try:
            folder = Path(self.file.path).resolve().parent
        except (NotImplementedError, ValueError):
            return None
        # Only ever the folder named after this item, somewhere under media/.
        if folder.name != self.uid or root not in folder.parents:
            return None
        if (root / "media") not in folder.parents:
            return None
        return folder


def _media_url(relative: str) -> str:
    if not relative:
        return ""
    base = settings.MEDIA_URL
    if not base.startswith(("http://", "https://", "/")):
        base = "/" + base
    return f"{base.rstrip('/')}/{relative.lstrip('/')}"


class Playlist(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    uid = models.CharField(max_length=16, unique=True, default=new_uid, editable=False)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    visibility = models.CharField(
        max_length=16,
        choices=Media.Visibility.choices,
        default=Media.Visibility.PUBLIC,
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="playlists"
    )
    items = models.ManyToManyField(Media, through="PlaylistItem", related_name="playlists")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title


class PlaylistItem(models.Model):
    playlist = models.ForeignKey(Playlist, on_delete=models.CASCADE, related_name="entries")
    media = models.ForeignKey(Media, on_delete=models.CASCADE, related_name="playlist_entries")
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["playlist", "media"], name="unique_media_per_playlist"
            )
        ]


class Comment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    media = models.ForeignKey(Media, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="comments"
    )
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="replies"
    )
    body = models.TextField(max_length=4000)
    # seconds into the video, for comments pinned to a moment
    timestamp = models.FloatField(null=True, blank=True)
    is_hidden = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["media", "created_at"])]

    def __str__(self) -> str:
        return f"{self.author} on {self.media}"


class Subtitle(models.Model):
    media = models.ForeignKey(Media, on_delete=models.CASCADE, related_name="subtitles")
    language = models.CharField(max_length=16)
    label = models.CharField(max_length=120, blank=True)
    file = models.FileField(upload_to="subtitles/%Y/%m/", max_length=500, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["language"]
        constraints = [
            models.UniqueConstraint(
                fields=["media", "language"], name="unique_language_per_media"
            )
        ]

    def __str__(self) -> str:
        return f"{self.media} [{self.language}]"

    @property
    def display_label(self) -> str:
        return self.label or self.language.upper()


class SubtitleCue(models.Model):
    """One timed line. Stored per row so the browser editor can save a single cue."""

    subtitle = models.ForeignKey(Subtitle, on_delete=models.CASCADE, related_name="cues")
    position = models.PositiveIntegerField(default=0)
    start_ms = models.PositiveIntegerField()
    end_ms = models.PositiveIntegerField()
    text = models.TextField(blank=True)

    class Meta:
        ordering = ["position", "start_ms"]
        indexes = [models.Index(fields=["subtitle", "position"])]

    def __str__(self) -> str:
        return f"{self.start_ms}–{self.end_ms}: {self.text[:40]}"

    @property
    def start_label(self) -> str:
        from .subtitles import format_timestamp

        return format_timestamp(self.start_ms)

    @property
    def end_label(self) -> str:
        from .subtitles import format_timestamp

        return format_timestamp(self.end_ms)


class Chapter(models.Model):
    media = models.ForeignKey(Media, on_delete=models.CASCADE, related_name="chapters")
    title = models.CharField(max_length=255)
    start_seconds = models.FloatField()
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["start_seconds", "position"]

    def __str__(self) -> str:
        return f"{self.start_seconds:.0f}s — {self.title}"
