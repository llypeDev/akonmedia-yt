"""Account model.

Email is the identifier; there is no username field anywhere in the system.
The privilege level lives on the account as ``role`` and is interpreted by
``apps.rbac``, so no view ever compares against a literal role string.
"""
import uuid

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone

from apps.rbac.roles import Role

from .managers import UserManager


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    email = models.EmailField("email address", unique=True)
    display_name = models.CharField(max_length=120, blank=True)
    role = models.CharField(
        max_length=16,
        choices=Role.choices,
        default=Role.MEMBER,
        help_text="Determines what the account may do across the portal.",
    )

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        ordering = ["-date_joined"]
        verbose_name = "user"
        verbose_name_plural = "users"

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
    def handle(self) -> str:
        """Stable, URL-safe label derived from the address."""
        return self.email.split("@")[0]
