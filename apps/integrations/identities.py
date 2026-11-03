"""The QQplay account behind a person of a site.

Accounts are never matched by email address: a site could otherwise name any
address it likes, an administrator's included, and speak as that account.
Each person of each site gets an account of their own, with an address no
mailbox answers and no password.
"""
from __future__ import annotations

import hashlib

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.rbac.roles import Role
from apps.users.models import User

from .models import ExternalIdentity
from .tokens import Person

#: The domain of the accounts made for people of a site (RFC 2606: it never
#: resolves, so nothing is ever sent there).
ACCOUNT_DOMAIN = "integrations.qqplay.invalid"


def account_email(integration, external_id: str) -> str:
    digest = hashlib.sha256(f"{integration.pk}:{external_id}".encode()).hexdigest()[:20]
    return f"site-{digest}@{ACCOUNT_DOMAIN}"


def sync(person: Person) -> ExternalIdentity:
    """The person's identity, created on their first visit and kept up to
    date with what the site says about them on every later one."""
    integration = person.integration
    identity = (
        ExternalIdentity.objects.select_related("user")
        .filter(integration=integration, external_id=person.external_id)
        .first()
    )
    if identity is None:
        try:
            with transaction.atomic():
                user = User(
                    email=account_email(integration, person.external_id),
                    display_name=person.name,
                    role=Role.MEMBER,
                )
                user.set_unusable_password()
                user.save()
                identity = ExternalIdentity.objects.create(
                    integration=integration, external_id=person.external_id, user=user,
                    name=person.name, email=person.email, is_moderator=person.moderator,
                )
        except IntegrityError:  # the same person's first two requests at once
            identity = ExternalIdentity.objects.select_related("user").get(
                integration=integration, external_id=person.external_id
            )

    changed = []
    for field, value in (("name", person.name), ("email", person.email), ("is_moderator", person.moderator)):
        if getattr(identity, field) != value:
            setattr(identity, field, value)
            changed.append(field)
    picture_changed = identity.picture_url != person.picture
    if picture_changed:
        identity.picture_url = person.picture
        changed.append("picture_url")
    identity.last_seen_at = timezone.now()
    identity.save(update_fields=changed + ["last_seen_at"])

    user = identity.user
    if user.display_name != person.name:
        user.display_name = person.name
        user.save(update_fields=["display_name"])

    if picture_changed:
        from .tasks import fetch_picture  # the task module imports this one

        transaction.on_commit(lambda: fetch_picture.delay(identity.pk))
    return identity
