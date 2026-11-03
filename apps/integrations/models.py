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
import secrets
import uuid
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
    parts = urlsplit(value if "://" in value else f"https://{value}")
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    port = f":{parts.port}" if parts.port and parts.port not in (80, 443) else ""
    return f"{parts.scheme}://{parts.hostname.lower()}{port}"


class Integration(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="integrations",
        verbose_name=_("owner"),
    )
    name = models.CharField(max_length=120, verbose_name=_("name"))
    key = models.CharField(max_length=40, unique=True, default=new_key, editable=False, verbose_name=_("key"))
    secret = models.CharField(max_length=80, default=new_secret, editable=False, verbose_name=_("secret"))
    #: One origin per line (https://escola.com.br). Embeds only sign people in
    #: on pages from these sites.
    origins = models.TextField(blank=True, verbose_name=_("allowed sites"))
    is_active = models.BooleanField(default=True, verbose_name=_("active"))
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

    def allows_origin(self, origin: str) -> bool:
        return normalize_origin(origin) in self.origin_list

    @property
    def secret_version(self) -> str:
        """Changes when the secret is replaced, which signs everyone out."""
        return hashlib.sha256(self.secret.encode()).hexdigest()[:10]

    def rotate_secret(self) -> None:
        self.secret = new_secret()
        self.save(update_fields=["secret"])

    def grants_live(self, live) -> bool:
        """The site's people may see this live even when it is private."""
        return self.is_active and live.owner_id == self.owner_id


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
