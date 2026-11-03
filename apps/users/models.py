"""Account model.

Email is the identifier; there is no username field anywhere in the system.
The privilege level lives on the account as ``role`` and is interpreted by
``apps.rbac``, so no view ever compares against a literal role string.
"""
import uuid

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.rbac.roles import Role

from . import avatars
from .managers import UserManager


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    email = models.EmailField(_("email address"), unique=True)
    display_name = models.CharField(max_length=120, blank=True, verbose_name=_("display name"))
    role = models.CharField(
        max_length=16,
        choices=Role.choices,
        default=Role.MEMBER,
        help_text=_("Determines what the account may do across the portal."),
        verbose_name=_("role"),
    )

    is_active = models.BooleanField(default=True, verbose_name=_("active"))
    is_staff = models.BooleanField(
        default=False,
        verbose_name=_("admin access"),
        help_text=_("Can sign in to the Admin."),
    )
    date_joined = models.DateTimeField(default=timezone.now, verbose_name=_("date joined"))

    #: Empty for the initials, a gallery character's key, or "photo" for
    #: ``avatar_photo``. See ``apps/users/avatars.py``.
    avatar = models.CharField(
        max_length=32, blank=True, default="", choices=avatars.choices,
        verbose_name=_("profile picture"),
    )
    avatar_photo = models.ImageField(
        upload_to="avatars/", blank=True, verbose_name=_("profile photo"),
        help_text=_("Uploaded on the account page, where it is cropped and resized."),
    )

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")
        ordering = ["-date_joined"]

    def __str__(self) -> str:
        return self.display_name or self.email

    def save(self, *args, **kwargs):
        # Store addresses in a single case so "A@x.com" and "a@x.com" cannot
        # become two accounts.
        if self.email:
            self.email = self.email.strip().lower()
        super().save(*args, **kwargs)

    def get_full_name(self) -> str:
        return self.display_name or self.email

    def get_short_name(self) -> str:
        return (self.display_name or self.email).split("@")[0]

    @property
    def avatar_url(self) -> str:
        """The picture shown for the account, or "" when it shows its initials."""
        return avatars.image_url(self)

    @property
    def handle(self) -> str:
        """Stable, URL-safe label derived from the address."""
        return self.email.split("@")[0]


@receiver(post_delete, sender=User)
def _delete_avatar_photo(sender, instance, **kwargs):
    """A deleted account's photo is deleted with it."""
    if instance.avatar_photo:
        instance.avatar_photo.delete(save=False)
