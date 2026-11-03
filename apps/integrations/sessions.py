"""Sessions for people signed in through a site.

An embed lives in a frame on another site, where browsers keep QQplay's
cookies away (Safari and Firefox always, Chrome more and more). So the
frame trades the site's token for a session of its own and sends it with
every request in a header. No cookie is involved, which is also why the
embed API needs no CSRF token: a page elsewhere cannot make a browser add
that header.

The session is signed with the portal's SECRET_KEY and names the account,
the integration and the integration secret's version: turning the
integration off, replacing its secret or deactivating the account ends it.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import wraps

from django.contrib.auth.models import AnonymousUser
from django.core import signing
from django.utils.cache import patch_cache_control
from django.views.decorators.csrf import csrf_exempt

from .models import ExternalIdentity, Integration

SALT = "qqplay.integrations.session"
#: A class is a few hours long; the site's script asks for a new token after.
LIFETIME = 12 * 3600
HEADER = "HTTP_AUTHORIZATION"


@dataclass
class EmbedContext:
    """What a request from a site's frame may do, for the live views."""

    integration: Integration
    identity: ExternalIdentity | None

    def grants_live(self, live) -> bool:
        return self.integration.grants_live(live)

    def moderates(self, live) -> bool:
        return bool(self.identity and self.identity.is_moderator and self.grants_live(live))


def issue(identity: ExternalIdentity) -> str:
    integration = identity.integration
    return signing.dumps(
        {"u": str(identity.user_id), "i": str(integration.pk), "v": integration.secret_version}, salt=SALT
    )


def load(token: str) -> ExternalIdentity | None:
    try:
        data = signing.loads(token, salt=SALT, max_age=LIFETIME)
    except signing.BadSignature:
        return None
    identity = (
        ExternalIdentity.objects.select_related("user", "integration")
        .filter(user_id=data.get("u"), integration_id=data.get("i"))
        .first()
    )
    if identity is None or not identity.user.is_active:
        return None
    integration = identity.integration
    if not integration.is_active or integration.secret_version != data.get("v"):
        return None
    return identity


def _bearer(request) -> str:
    value = request.META.get(HEADER, "")
    return value[7:].strip() if value[:7].lower() == "bearer " else ""


def embed_api(view):
    """Run a live view for a site's frame: the person comes from the session
    header and nowhere else (cookies are ignored here), and the site's
    integration may open private lives and moderation to them."""

    @csrf_exempt
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        token = _bearer(request)
        identity = load(token) if token else None
        if identity is not None:
            request.user = identity.user
            request.embed = EmbedContext(identity.integration, identity)
        else:
            request.user = AnonymousUser()
            request.embed = None
        response = view(request, *args, **kwargs)
        patch_cache_control(response, no_store=True)
        if token and identity is None:
            # The page knows to ask its site for a new token.
            response["X-QQplay-Session"] = "expired"
        return response

    return wrapper
