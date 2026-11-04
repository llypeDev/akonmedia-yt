"""The token a site signs for the person signed in there.

A JSON Web Token (RFC 7519) signed with HMAC-SHA256 and the integration's
secret, so any JWT library does it in a few lines (examples on the
integration's page in the studio). Header and claims:

    {"alg": "HS256", "typ": "JWT", "kid": "<integration key>"}
    {
      "sub": "123",                         # the person's id on the site (required)
      "name": "Ana Lima",                   # shown in the chat (required)
      "email": "ana@escola.com.br",         # optional, for the admins' reference
      "picture": "https://.../ana.jpg",     # optional, https only
      "moderator": true,                    # optional: may hide chat messages
      "groups": ["turma-a"],                # optional: the person's classes
      "iat": 1767225600,
      "exp": 1767226200                     # required, at most a day ahead
    }

Only HS256 is accepted, whatever the header says, so a token cannot choose a
weaker check for itself.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from django.utils.translation import gettext as _

from apps.live.models import parse_groups

from .models import Integration

#: Longest a token may still be valid for. Sites should sign them for minutes.
MAX_LIFETIME = 24 * 3600
#: Clocks of two servers are never quite equal.
LEEWAY = 120
MAX_TOKEN_LENGTH = 8192
#: Longest an API token (the site's server talking to QQplay) may be valid for.
API_LIFETIME = 3600


class TokenError(Exception):
    """Why a token was refused, in words for the site's developer."""


@dataclass
class Person:
    integration: Integration
    external_id: str
    name: str
    email: str
    picture: str
    moderator: bool
    groups: list


def _b64decode(part: str) -> bytes:
    padding = "=" * (-len(part) % 4)
    return base64.urlsafe_b64decode(part + padding)


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def sign(claims: dict, integration: Integration) -> str:
    """A token as a site would make it: for the studio's preview and tests."""
    header = {"alg": "HS256", "typ": "JWT", "kid": integration.key}
    head = _b64encode(json.dumps(header, separators=(",", ":")).encode())
    body = _b64encode(json.dumps(claims, separators=(",", ":")).encode())
    signature = hmac.new(integration.secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{_b64encode(signature)}"


def _text(value, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _check(token: str, now: float, max_lifetime: int) -> tuple[Integration, dict]:
    """The integration that signed a token and its claims, or TokenError."""
    if not token or len(token) > MAX_TOKEN_LENGTH or token.count(".") != 2:
        raise TokenError(_("This is not a token."))
    head_part, body_part, signature_part = token.split(".")
    try:
        header = json.loads(_b64decode(head_part))
        claims = json.loads(_b64decode(body_part))
        signature = _b64decode(signature_part)
    except (ValueError, TypeError) as exc:
        raise TokenError(_("This is not a token.")) from exc
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise TokenError(_("This is not a token."))
    if header.get("alg") != "HS256":
        raise TokenError(_("Tokens must be signed with HS256."))

    key = header.get("kid") or claims.get("iss")
    integration = Integration.objects.filter(key=key, is_active=True).first() if isinstance(key, str) else None
    if integration is None:
        raise TokenError(_("No active integration has this key."))
    signed = f"{head_part}.{body_part}".encode()
    # The current secret, or the one before it while it is still accepted.
    if not any(
        hmac.compare_digest(hmac.new(secret.encode(), signed, hashlib.sha256).digest(), signature)
        for secret in integration.secrets_in_force()
    ):
        raise TokenError(_("The signature does not match: check the integration's secret."))

    expires = claims.get("exp")
    if not isinstance(expires, (int, float)):
        raise TokenError(_("The token needs an expiry (exp)."))
    if expires < now - LEEWAY:
        raise TokenError(_("The token has expired."))
    if expires > now + max_lifetime + LEEWAY:
        if max_lifetime == MAX_LIFETIME:
            raise TokenError(_("The token is valid for too long: at most a day."))
        raise TokenError(_("The token is valid for too long: at most an hour."))
    not_before = claims.get("nbf")
    if isinstance(not_before, (int, float)) and not_before > now + LEEWAY:
        raise TokenError(_("The token is not valid yet."))
    return integration, claims


def verify_api(token: str, now: float | None = None) -> Integration:
    """The integration whose server sent an API request, or TokenError.

    The same kind of token as for people, made by the site's server with
    ``"scope": "api"`` and no person, valid for an hour at most.
    """
    now = time.time() if now is None else now
    integration, claims = _check(token, now, API_LIFETIME)
    if claims.get("scope") != "api":
        raise TokenError(_("An API token needs \"scope\": \"api\"."))
    return integration


def verify(token: str, now: float | None = None) -> Person:
    """The person a token names, or TokenError."""
    now = time.time() if now is None else now
    integration, claims = _check(token, now, MAX_LIFETIME)
    if claims.get("scope") == "api":
        raise TokenError(_("This is an API token, not a person's."))

    subject = claims.get("sub")
    if isinstance(subject, int):
        subject = str(subject)
    subject = _text(subject, 200)
    if not subject:
        raise TokenError(_("The token needs the person's id (sub)."))
    name = _text(claims.get("name"), 120)
    if not name:
        raise TokenError(_("The token needs the person's name (name)."))
    picture = _text(claims.get("picture"), 1000)
    if picture and urlsplit(picture).scheme != "https":
        picture = ""
    email = _text(claims.get("email"), 254)
    if "@" not in email:
        email = ""
    return Person(
        integration=integration,
        external_id=subject,
        name=name,
        email=email,
        picture=picture,
        moderator=claims.get("moderator") is True,
        groups=parse_groups(claims.get("groups")) if isinstance(claims.get("groups"), (list, str)) else [],
    )
