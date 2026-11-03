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
from django.utils.translation import gettext_lazy as _

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
    name = models.CharField(max_length=120, unique=True, verbose_name=_("name"))
    slug = models.SlugField(max_length=140, unique=True, blank=True, verbose_name=_("address name"))
    description = models.TextField(blank=True, verbose_name=_("description"))
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children",
        verbose_name=_("parent category"),
    )
    created_at = models.DateTimeField(default=timezone.now, verbose_name=_("created at"))

    class Meta:
        verbose_name = _("category")
        verbose_name_plural = _("categories")
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:140]
        super().save(*args, **kwargs)


class Media(models.Model):
    class Kind(models.TextChoices):
        VIDEO = "video", _("Video")
        AUDIO = "audio", _("Audio")
        IMAGE = "image", _("Image")
        DOCUMENT = "document", _("Document")

    class State(models.TextChoices):
        PENDING = "pending", _("Pending")
        PROCESSING = "processing", _("Processing")
        READY = "ready", _("Ready")
        FAILED = "failed", _("Failed")

    class Visibility(models.TextChoices):
        PUBLIC = "public", _("Public")
        UNLISTED = "unlisted", _("Unlisted")
        PRIVATE = "private", _("Private")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    uid = models.CharField(
        max_length=16, unique=True, default=new_uid, editable=False,
        verbose_name=_("public id"),
    )

    title = models.CharField(max_length=255, verbose_name=_("title"))
    description = models.TextField(blank=True, verbose_name=_("description"))
    kind = models.CharField(
        max_length=16, choices=Kind.choices, default=Kind.VIDEO,
        verbose_name=_("type"),
    )
    state = models.CharField(
        max_length=16, choices=State.choices, default=State.PENDING,
        verbose_name=_("processing"),
    )
    visibility = models.CharField(
        max_length=16, choices=Visibility.choices, default=Visibility.PUBLIC,
        verbose_name=_("visibility"),
    )

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="media",
        verbose_name=_("owner"),
    )
    category = models.ForeignKey(
        Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="media",
        verbose_name=_("category"),
    )

    file = models.FileField(upload_to=media_upload_path, max_length=500, verbose_name=_("file"))
    original_name = models.CharField(max_length=255, blank=True, verbose_name=_("original name"))
    size_bytes = models.BigIntegerField(default=0, verbose_name=_("size in bytes"))
    duration_seconds = models.FloatField(default=0, verbose_name=_("duration in seconds"))
    width = models.PositiveIntegerField(default=0, verbose_name=_("width"))
    height = models.PositiveIntegerField(default=0, verbose_name=_("height"))
    poster = models.ImageField(
        upload_to=poster_upload_path, max_length=500, blank=True,
        verbose_name=_("poster"),
    )
    hls_playlist = models.CharField(max_length=500, blank=True, verbose_name=_("HLS playlist"))
    #: Sprite sheet of thumbnails for the seek bar, relative to MEDIA_ROOT.
    storyboard = models.CharField(max_length=500, blank=True, verbose_name=_("storyboard"))
    #: interval, cols, rows, width, height and count of the storyboard tiles.
    storyboard_meta = models.JSONField(
        default=dict, blank=True,
        verbose_name=_("storyboard layout"),
    )

    views_count = models.PositiveIntegerField(default=0, verbose_name=_("views"))
    failure_reason = models.TextField(blank=True, verbose_name=_("failure reason"))

    created_at = models.DateTimeField(default=timezone.now, verbose_name=_("created at"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("updated at"))
    published_at = models.DateTimeField(null=True, blank=True, verbose_name=_("published at"))

    class Meta:
        verbose_name = _("media item")
        verbose_name_plural = _("media items")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["state", "visibility"]),
            models.Index(fields=["kind", "visibility"]),
            models.Index(fields=["owner", "-created_at"]),
        ]

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
    uid = models.CharField(
        max_length=16, unique=True, default=new_uid, editable=False,
        verbose_name=_("public id"),
    )
    title = models.CharField(max_length=255, verbose_name=_("title"))
    description = models.TextField(blank=True, verbose_name=_("description"))
    visibility = models.CharField(
        max_length=16,
        choices=Media.Visibility.choices,
        default=Media.Visibility.PUBLIC,
        verbose_name=_("visibility"),
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="playlists",
        verbose_name=_("owner"),
    )
    items = models.ManyToManyField(
        Media, through="PlaylistItem", related_name="playlists",
        verbose_name=_("items"),
    )
    created_at = models.DateTimeField(default=timezone.now, verbose_name=_("created at"))

    class Meta:
        verbose_name = _("playlist")
        verbose_name_plural = _("playlists")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title


class PlaylistItem(models.Model):
    playlist = models.ForeignKey(
        Playlist, on_delete=models.CASCADE, related_name="entries",
        verbose_name=_("playlist"),
    )
    media = models.ForeignKey(
        Media, on_delete=models.CASCADE, related_name="playlist_entries",
        verbose_name=_("media item"),
    )
    position = models.PositiveIntegerField(default=0, verbose_name=_("position"))

    class Meta:
        verbose_name = _("playlist item")
        verbose_name_plural = _("playlist items")
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["playlist", "media"], name="unique_media_per_playlist"
            )
        ]

    def __str__(self):
        # Shown over each row of the playlist's items in the admin.
        return str(self.media)


class Comment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    media = models.ForeignKey(
        Media, on_delete=models.CASCADE, related_name="comments",
        verbose_name=_("media item"),
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="comments",
        verbose_name=_("author"),
    )
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="replies",
        verbose_name=_("reply to"),
    )
    body = models.TextField(max_length=4000, verbose_name=_("text"))
    # seconds into the video, for comments pinned to a moment
    timestamp = models.FloatField(null=True, blank=True, verbose_name=_("moment in the video"))
    is_hidden = models.BooleanField(default=False, verbose_name=_("hidden"))
    created_at = models.DateTimeField(default=timezone.now, verbose_name=_("created at"))

    class Meta:
        verbose_name = _("comment")
        verbose_name_plural = _("comments")
        ordering = ["created_at"]
        indexes = [models.Index(fields=["media", "created_at"])]

    def __str__(self) -> str:
        return f"{self.author} on {self.media}"


class Subtitle(models.Model):
    media = models.ForeignKey(
        Media, on_delete=models.CASCADE, related_name="subtitles",
        verbose_name=_("media item"),
    )
    language = models.CharField(max_length=16, verbose_name=_("language"))
    label = models.CharField(max_length=120, blank=True, verbose_name=_("label"))
    file = models.FileField(
        upload_to="subtitles/%Y/%m/", max_length=500, blank=True,
        verbose_name=_("file"),
    )
    created_at = models.DateTimeField(default=timezone.now, verbose_name=_("created at"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("updated at"))

    class Meta:
        verbose_name = _("subtitle track")
        verbose_name_plural = _("subtitle tracks")
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

    subtitle = models.ForeignKey(
        Subtitle, on_delete=models.CASCADE, related_name="cues",
        verbose_name=_("subtitle track"),
    )
    position = models.PositiveIntegerField(default=0, verbose_name=_("position"))
    start_ms = models.PositiveIntegerField(verbose_name=_("start (ms)"))
    end_ms = models.PositiveIntegerField(verbose_name=_("end (ms)"))
    text = models.TextField(blank=True, verbose_name=_("text"))

    class Meta:
        verbose_name = _("cue")
        verbose_name_plural = _("cues")
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
    media = models.ForeignKey(
        Media, on_delete=models.CASCADE, related_name="chapters",
        verbose_name=_("media item"),
    )
    title = models.CharField(max_length=255, verbose_name=_("title"))
    start_seconds = models.FloatField(verbose_name=_("start (seconds)"))
    position = models.PositiveIntegerField(default=0, verbose_name=_("position"))

    class Meta:
        verbose_name = _("chapter")
        verbose_name_plural = _("chapters")
        ordering = ["start_seconds", "position"]

    def __str__(self) -> str:
        return f"{self.start_seconds:.0f}s — {self.title}"
