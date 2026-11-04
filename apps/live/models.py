"""Live streams.

A live is an event. Someone creates it in the studio, points an encoder such as
OBS at the RTMP ingest with the stream key, and viewers watch the HLS that the
ingest server writes. When it ends, the recording becomes an ordinary media
item and goes through the same pipeline as an upload.

Three identifiers, on purpose:

``uid``
    The page address (``/live/<uid>/``), like a media item's.
``playback_id``
    The stream's path on the ingest server. It is only handed to people allowed
    to watch, so a private live's stream address cannot be derived from its
    page address.
``stream_key``
    The secret the encoder sends with ``?key=``. It never appears in any URL a
    viewer sees.
"""
import secrets
import uuid

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.media.models import Category, Media, new_uid


def parse_groups(value) -> list[str]:
    """'Turma A, turma-b' or ['Turma A'] -> ['turma a', 'turma-b']: classes compare
    without case and surrounding spaces."""
    items = value if isinstance(value, (list, tuple)) else str(value or "").split(",")
    seen = []
    for item in items:
        name = str(item).strip().lower()[:60]
        if name and name not in seen:
            seen.append(name)
    return seen[:50]


def new_playback_id() -> str:
    return new_uid(20)


def new_stream_key() -> str:
    return secrets.token_urlsafe(24)


class Live(models.Model):
    class Status(models.TextChoices):
        SCHEDULED = "scheduled", _("Scheduled")
        LIVE = "live", _("Live")
        #: The encoder dropped; the live ends unless it comes back in time.
        INTERRUPTED = "interrupted", _("Interrupted")
        ENDED = "ended", _("Ended")

    ON_AIR = (Status.LIVE, Status.INTERRUPTED)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    uid = models.CharField(
        max_length=16, unique=True, default=new_uid, editable=False,
        verbose_name=_("public id"),
    )
    playback_id = models.CharField(
        max_length=32, unique=True, default=new_playback_id, editable=False,
        verbose_name=_("playback id"),
    )
    stream_key = models.CharField(
        max_length=64, unique=True, default=new_stream_key,
        verbose_name=_("stream key"),
    )

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="lives",
        verbose_name=_("owner"),
    )
    title = models.CharField(_("title"), max_length=255)
    description = models.TextField(_("description"), blank=True)
    visibility = models.CharField(
        _("visibility"),
        max_length=16,
        choices=Media.Visibility.choices,
        default=Media.Visibility.PUBLIC,
    )
    category = models.ForeignKey(
        Category,
        verbose_name=_("category"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="lives",
    )
    scheduled_for = models.DateTimeField(_("starts at"), null=True, blank=True)
    record = models.BooleanField(_("publish the recording"), default=True)
    chat_enabled = models.BooleanField(_("chat"), default=True)
    #: Seconds a person waits between two messages (0: only the usual pause).
    slow_mode_seconds = models.PositiveSmallIntegerField(default=0, verbose_name=_("slow mode"))
    pinned_message = models.ForeignKey(
        "ChatMessage", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
        verbose_name=_("pinned message"),
    )
    #: Classes of a school site (apps.integrations) that may watch this live
    #: when it is private, separated by commas. Empty: all of the site's people.
    groups = models.CharField(max_length=500, blank=True, verbose_name=_("school classes"))

    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.SCHEDULED,
        verbose_name=_("status"),
    )
    #: An encoder is connected right now.
    publishing = models.BooleanField(default=False, verbose_name=_("encoder connected"))
    #: Encoder connections so far. Each one leaves one recording file, so the
    #: recording is complete once there are as many files as sessions.
    sessions = models.PositiveIntegerField(default=0, verbose_name=_("encoder sessions"))
    started_at = models.DateTimeField(null=True, blank=True, verbose_name=_("started at"))
    interrupted_at = models.DateTimeField(null=True, blank=True, verbose_name=_("interrupted at"))
    ended_at = models.DateTimeField(null=True, blank=True, verbose_name=_("ended at"))
    peak_viewers = models.PositiveIntegerField(default=0, verbose_name=_("peak viewers"))
    #: Goes up by one with every change to the chat: a message, a reaction,
    #: a hidden or pinned message, an answered question, its settings. A page
    #: that has seen this number has the chat up to date, and the chat as of
    #: a number is computed once and shared by every page (apps.live.chat).
    chat_version = models.PositiveIntegerField(default=0, editable=False, verbose_name=_("chat version"))

    recording = models.OneToOneField(
        Media, null=True, blank=True, on_delete=models.SET_NULL, related_name="live_source",
        verbose_name=_("recording"),
    )
    #: Set once the end of the live has been handled, with a recording or not.
    finalized_at = models.DateTimeField(
        null=True, blank=True,
        verbose_name=_("recording published at"),
    )
    recording_error = models.CharField(
        max_length=500, blank=True,
        verbose_name=_("recording error"),
    )

    created_at = models.DateTimeField(default=timezone.now, verbose_name=_("created at"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("updated at"))

    class Meta:
        verbose_name = _("live")
        verbose_name_plural = _("lives")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "visibility"]),
            models.Index(fields=["owner", "-created_at"]),
        ]

    def __str__(self) -> str:
        return self.title

    def get_absolute_url(self) -> str:
        return reverse("live:watch", args=[self.uid])

    @property
    def group_list(self) -> list[str]:
        return parse_groups(self.groups)

    @property
    def is_on_air(self) -> bool:
        return self.status in self.ON_AIR

    @property
    def is_ended(self) -> bool:
        return self.status == self.Status.ENDED

    @property
    def hls_url(self) -> str:
        return f"{settings.LIVE_HLS_URL.rstrip('/')}/{self.playback_id}/index.m3u8"

    @property
    def ingest_url(self) -> str:
        return settings.LIVE_RTMP_URL.rstrip("/")

    @property
    def encoder_key(self) -> str:
        """What goes into the encoder's "stream key" box, next to ``ingest_url``."""
        return f"{self.playback_id}?key={self.stream_key}"

    @property
    def duration_seconds(self) -> float:
        if not self.started_at:
            return 0.0
        end = self.ended_at or timezone.now()
        return max(0.0, (end - self.started_at).total_seconds())


class LiveRecording(models.Model):
    """One file the ingest server wrote: one per encoder connection."""

    live = models.ForeignKey(
        Live, on_delete=models.CASCADE, related_name="recordings",
        verbose_name=_("live"),
    )
    filename = models.CharField(max_length=255, verbose_name=_("file name"))
    created_at = models.DateTimeField(default=timezone.now, verbose_name=_("created at"))

    class Meta:
        verbose_name = _("live recording")
        verbose_name_plural = _("live recordings")
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["live", "filename"], name="live_recording_unique_file"),
        ]

    def __str__(self) -> str:
        return self.filename


class ChatMessage(models.Model):
    live = models.ForeignKey(
        Live, on_delete=models.CASCADE, related_name="messages",
        verbose_name=_("live"),
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="live_messages",
        verbose_name=_("author"),
    )
    body = models.CharField(max_length=500, verbose_name=_("message"))
    created_at = models.DateTimeField(default=timezone.now, verbose_name=_("created at"))
    is_hidden = models.BooleanField(default=False, verbose_name=_("hidden"))
    hidden_at = models.DateTimeField(null=True, blank=True, verbose_name=_("hidden at"))
    #: Sent as a question for the host: listed apart, voted up, answered.
    is_question = models.BooleanField(default=False, verbose_name=_("question"))
    answered_at = models.DateTimeField(null=True, blank=True, verbose_name=_("answered at"))

    class Meta:
        verbose_name = _("chat message")
        verbose_name_plural = _("chat messages")
        ordering = ["id"]
        indexes = [models.Index(fields=["live", "id"])]

    def __str__(self) -> str:
        return self.body[:60]


class ChatReaction(models.Model):
    """One person's reaction to a chat message. A person may leave several
    kinds on the same message, each once."""

    class Kind(models.TextChoices):
        # The labels are what a screen reader says; the pictures are emoji,
        # drawn by the viewer's own system.
        #: A vote for a question, not one of the pictures.
        UP = "up", _("Vote")
        LIKE = "like", _("Like")
        LOVE = "love", _("Love")
        HAHA = "haha", _("Funny")
        WOW = "wow", _("Wow")
        CLAP = "clap", _("Applause")
        FIRE = "fire", _("Fire")

    #: The picture for each kind, in the order the chat offers them.
    EMOJI = {
        Kind.LIKE: "\U0001F44D",
        Kind.LOVE: "\u2764\ufe0f",
        Kind.HAHA: "\U0001F602",
        Kind.WOW: "\U0001F62E",
        Kind.CLAP: "\U0001F44F",
        Kind.FIRE: "\U0001F525",
    }

    message = models.ForeignKey(
        ChatMessage, on_delete=models.CASCADE, related_name="reactions",
        verbose_name=_("chat message"),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="live_reactions",
        verbose_name=_("user"),
    )
    kind = models.CharField(max_length=8, choices=Kind.choices, verbose_name=_("reaction"))
    created_at = models.DateTimeField(default=timezone.now, verbose_name=_("created at"))

    class Meta:
        verbose_name = _("chat reaction")
        verbose_name_plural = _("chat reactions")
        constraints = [
            models.UniqueConstraint(
                fields=["message", "user", "kind"], name="one_reaction_of_a_kind_per_person"
            )
        ]
        indexes = [models.Index(fields=["message", "kind"])]

    def __str__(self) -> str:
        return f"{self.EMOJI.get(self.kind, self.kind)} {self.message_id}"


class ChatMute(models.Model):
    """A person who may not write in a live's chat until a moment."""

    live = models.ForeignKey(Live, on_delete=models.CASCADE, related_name="mutes", verbose_name=_("live"))
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="live_mutes",
        verbose_name=_("user"),
    )
    until = models.DateTimeField(verbose_name=_("until"))
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
        verbose_name=_("by"),
    )
    created_at = models.DateTimeField(default=timezone.now, verbose_name=_("created at"))

    class Meta:
        verbose_name = _("muted person")
        verbose_name_plural = _("muted people")
        constraints = [models.UniqueConstraint(fields=["live", "user"], name="one_mute_per_person_per_live")]

    def __str__(self) -> str:
        return f"{self.user} ({self.live})"


class LiveAttendance(models.Model):
    """How long a signed-in person watched a live while it was on air."""

    live = models.ForeignKey(Live, on_delete=models.CASCADE, related_name="attendance", verbose_name=_("live"))
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="live_attendance",
        verbose_name=_("user"),
    )
    first_seen = models.DateTimeField(default=timezone.now, verbose_name=_("joined at"))
    last_seen = models.DateTimeField(default=timezone.now, verbose_name=_("last seen at"))
    seconds = models.PositiveIntegerField(default=0, verbose_name=_("seconds watched"))

    class Meta:
        verbose_name = _("attendance")
        verbose_name_plural = _("attendance")
        constraints = [models.UniqueConstraint(fields=["live", "user"], name="one_attendance_per_person_per_live")]
        ordering = ["-seconds"]

    def __str__(self) -> str:
        return f"{self.user} ({self.live}): {self.seconds}s"
