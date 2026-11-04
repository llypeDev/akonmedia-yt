"""Integrations: another site that shows lives and their chat to its own people.

A school's site signs a short token for the student who is signed in there
(see ``tokens.py``); QQplay checks it with the integration's secret and lets
that student into the live's chat under the name and picture the school gave,
with no QQplay account to create and no password to remember.

``Integration``
    One site. Its ``key`` names it in tokens and embed codes and is public;
    its ``secret`` signs the tokens and never leaves the two servers. It
    belongs to a creator: it opens that creator's private lives to the site's
    people, and lets the site's teachers moderate their chat.
``ExternalIdentity``
    A person of that site, by the id the site gave them, and the QQplay
    account their messages and reactions are kept under. That account has no
    password: the only way in is through the site.
"""
import hashlib
import re
import secrets
import uuid
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


def new_key() -> str:
    return "sch_" + secrets.token_hex(8)


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def normalize_origin(value: str) -> str:
    """'https://Escola.com.br/aulas' -> 'https://escola.com.br'; '' if not an origin."""
    value = (value or "").strip()
    if not value:
        return ""
    try:
        parts = urlsplit(value if "://" in value else f"https://{value}")
        port = parts.port
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    # Only what a host name is made of: the origins go into the frames'
    # Content-Security-Policy as they are.
    if not re.fullmatch(r"(\*\.)?[a-z0-9-]+(\.[a-z0-9-]+)*|[0-9a-f.]*:[0-9a-f:.]*", parts.hostname.lower()):
        return ""
    port = f":{port}" if port and port not in (80, 443) else ""
    host = parts.hostname.lower()
    if ":" in host:  # IPv6
        host = f"[{host}]"
    return f"{parts.scheme}://{host}{port}"


class Integration(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="integrations",
        verbose_name=_("owner"),
    )
    name = models.CharField(max_length=120, verbose_name=_("name"))
    key = models.CharField(max_length=40, unique=True, default=new_key, editable=False, verbose_name=_("key"))
    secret = models.CharField(max_length=80, default=new_secret, editable=False, verbose_name=_("secret"))
    #: The secret before the last change, still accepted until a moment, so a
    #: new one can reach the site's server without signing everyone out.
    previous_secret = models.CharField(max_length=80, blank=True, editable=False, verbose_name=_("previous secret"))
    previous_secret_until = models.DateTimeField(null=True, blank=True, verbose_name=_("previous secret valid until"))
    #: One origin per line (https://escola.com.br). Embeds only sign people in
    #: on pages from these sites.
    origins = models.TextField(blank=True, verbose_name=_("allowed sites"))
    is_active = models.BooleanField(default=True, verbose_name=_("active"))
    #: Where QQplay posts the lives' news (live started, ended, recording
    #: ready), signed with the secret (see webhooks.py).
    webhook_url = models.URLField(max_length=500, blank=True, verbose_name=_("webhook address"))
    # How the frames look on the site.
    accent_color = models.CharField(max_length=7, blank=True, verbose_name=_("accent colour"))
    chat_title = models.CharField(max_length=60, blank=True, verbose_name=_("chat title"))
    staff_label = models.CharField(max_length=30, blank=True, verbose_name=_("badge of the host and moderators"))
    hide_branding = models.BooleanField(default=False, verbose_name=_("hide the portal's name"))
    created_at = models.DateTimeField(default=timezone.now, verbose_name=_("created at"))
    last_used_at = models.DateTimeField(null=True, blank=True, verbose_name=_("last used at"))

    class Meta:
        verbose_name = _("integration")
        verbose_name_plural = _("integrations")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.name

    @property
    def origin_list(self) -> list[str]:
        seen = []
        for line in self.origins.splitlines():
            origin = normalize_origin(line)
            if origin and origin not in seen:
                seen.append(origin)
        return seen

    def clean(self):
        bad = [line.strip() for line in self.origins.splitlines() if line.strip() and not normalize_origin(line)]
        if bad:
            raise ValidationError({"origins": _("Not a site address: %(value)s") % {"value": bad[0]}})
        self.origins = "\n".join(self.origin_list)
        if self.accent_color and not re.fullmatch(r"#[0-9a-fA-F]{6}", self.accent_color):
            raise ValidationError({"accent_color": _("A colour like #22d3ee.")})
        if self.webhook_url and urlsplit(self.webhook_url).scheme != "https":
            raise ValidationError({"webhook_url": _("The webhook address must start with https://.")})

    def frame_ancestors(self) -> str:
        """The Content-Security-Policy that lets only the listed sites (and
        the portal itself, for the studio's preview) show its frames."""
        sites = self.origin_list
        return "frame-ancestors 'self' " + " ".join(sites) if sites else "frame-ancestors *"

    @property
    def accent_ink(self) -> str:
        """Black or white, whichever reads better on the accent colour."""
        if not self.accent_color:
            return ""
        r, g, b = (int(self.accent_color[i:i + 2], 16) for i in (1, 3, 5))
        return "#05070c" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#ffffff"

    def allows_origin(self, origin: str) -> bool:
        return normalize_origin(origin) in self.origin_list

    @staticmethod
    def version_of(secret: str) -> str:
        return hashlib.sha256(secret.encode()).hexdigest()[:10]

    @property
    def secret_version(self) -> str:
        """Changes when the secret is replaced, which ends the sessions made
        with the old one once it stops being accepted."""
        return self.version_of(self.secret)

    def secrets_in_force(self, now=None) -> list[str]:
        now = now or timezone.now()
        found = [self.secret]
        if self.previous_secret and self.previous_secret_until and self.previous_secret_until > now:
            found.append(self.previous_secret)
        return found

    def rotate_secret(self, grace_hours: int = 0) -> None:
        """A new secret. The old one keeps working ``grace_hours`` more, or
        stops at once (everyone signed in through the site is signed out)."""
        if grace_hours > 0:
            self.previous_secret = self.secret
            self.previous_secret_until = timezone.now() + timedelta(hours=grace_hours)
        else:
            self.previous_secret = ""
            self.previous_secret_until = None
        self.secret = new_secret()
        self.save(update_fields=["secret", "previous_secret", "previous_secret_until"])

    def grants_live(self, live) -> bool:
        """The site's people may see this live even when it is private."""
        return self.is_active and live.owner_id == self.owner_id

    def grants_media(self, media) -> bool:
        """...and this video of the library, the live's recording included."""
        return self.is_active and media.owner_id == self.owner_id


class ExternalIdentity(models.Model):
    integration = models.ForeignKey(
        Integration, on_delete=models.CASCADE, related_name="identities",
        verbose_name=_("integration"),
    )
    #: The person's id on the site, as the site's tokens give it.
    external_id = models.CharField(max_length=200, verbose_name=_("id on the site"))
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="external_identity",
        verbose_name=_("account"),
    )
    name = models.CharField(max_length=120, blank=True, verbose_name=_("name"))
    email = models.EmailField(blank=True, verbose_name=_("email address"))
    picture_url = models.URLField(max_length=1000, blank=True, verbose_name=_("picture address"))
    is_moderator = models.BooleanField(default=False, verbose_name=_("moderator"))
    #: The person's classes on the site, as its tokens give them (lower case).
    groups = models.JSONField(default=list, blank=True, verbose_name=_("classes"))
    #: Blocked by the creator: may not sign in through the site any more.
    is_blocked = models.BooleanField(default=False, verbose_name=_("blocked"))
    created_at = models.DateTimeField(default=timezone.now, verbose_name=_("created at"))
    last_seen_at = models.DateTimeField(null=True, blank=True, verbose_name=_("last seen at"))

    class Meta:
        verbose_name = _("person from an integration")
        verbose_name_plural = _("people from integrations")
        constraints = [
            models.UniqueConstraint(fields=["integration", "external_id"], name="one_account_per_person_per_site")
        ]

    def __str__(self) -> str:
        return f"{self.name or self.external_id} ({self.integration})"

    def in_groups(self, groups) -> bool:
        """Belongs to one of these classes (no classes: any person does)."""
        return not groups or bool(set(groups) & set(self.groups or []))


class WebhookDelivery(models.Model):
    """One news item posted to a site's webhook, kept to show what was sent."""

    integration = models.ForeignKey(
        Integration, on_delete=models.CASCADE, related_name="deliveries", verbose_name=_("integration"),
    )
    event = models.CharField(max_length=40, verbose_name=_("event"))
    #: What the news is about (a live's id), so the same news goes out once.
    subject = models.CharField(max_length=64, blank=True, verbose_name=_("subject"))
    url = models.URLField(max_length=500, verbose_name=_("address"))
    payload = models.JSONField(default=dict, verbose_name=_("content"))
    attempts = models.PositiveSmallIntegerField(default=0, verbose_name=_("attempts"))
    status_code = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name=_("answer code"))
    ok = models.BooleanField(default=False, verbose_name=_("delivered"))
    error = models.CharField(max_length=300, blank=True, verbose_name=_("error"))
    created_at = models.DateTimeField(default=timezone.now, verbose_name=_("created at"))
    delivered_at = models.DateTimeField(null=True, blank=True, verbose_name=_("delivered at"))

    class Meta:
        verbose_name = _("webhook delivery")
        verbose_name_plural = _("webhook deliveries")
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["integration", "event", "subject"])]

    def __str__(self) -> str:
        return f"{self.event} → {self.url}"
