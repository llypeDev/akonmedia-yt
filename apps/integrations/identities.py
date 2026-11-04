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
                    name=person.name, email=person.email, is_moderator=person.moderator, groups=person.groups,
                )
        except IntegrityError:  # the same person's first two requests at once
            identity = ExternalIdentity.objects.select_related("user").get(
                integration=integration, external_id=person.external_id
            )

    changed = []
    for field, value in (
        ("name", person.name), ("email", person.email), ("is_moderator", person.moderator), ("groups", person.groups),
    ):
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


def find(integration, external_id: str) -> ExternalIdentity | None:
    return (
        ExternalIdentity.objects.select_related("user")
        .filter(integration=integration, external_id=str(external_id))
        .first()
    )


def block(identity: ExternalIdentity, blocked: bool = True) -> None:
    """A blocked person's sessions end at once and they cannot sign in again
    through the site, whatever tokens it makes for them."""
    identity.is_blocked = blocked
    identity.save(update_fields=["is_blocked"])


def erase(identity: ExternalIdentity, keep_messages: bool = False) -> None:
    """Forget a person (the right of deletion of the LGPD/GDPR): their name,
    address and picture go. With ``keep_messages`` their messages stay in the
    chats under "Removed person"; otherwise they go too, with their reactions
    and attendance. A later sign-in from the site makes a new, empty account.
    """
    from django.conf import settings
    from django.utils import translation

    from apps.users import avatars

    user = identity.user
    with transaction.atomic():
        identity.delete()
        if not keep_messages:
            if user.avatar_photo:
                user.avatar_photo.delete(save=False)
            user.delete()
            return
        if user.avatar_photo:
            avatars.remove_photo(user)
        # In the portal's language, whoever asked for it.
        with translation.override(settings.LANGUAGE_CODE):
            user.display_name = translation.gettext("Removed person")
        user.email = f"removed-{hashlib.sha256(str(user.pk).encode()).hexdigest()[:20]}@{ACCOUNT_DOMAIN}"
        user.avatar = ""
        user.is_active = False
        user.save(update_fields=["display_name", "email", "avatar", "is_active"])
