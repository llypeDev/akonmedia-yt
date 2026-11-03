"""The five account levels, ordered from least to most privileged."""
from django.db import models
from django.utils.translation import gettext_lazy as _


class Role(models.TextChoices):
    MEMBER = "member", _("Member")
    ADVANCED = "advanced", _("Advanced member")
    EDITOR = "editor", _("Editor")
    MANAGER = "manager", _("Manager")
    ADMIN = "admin", _("Administrator")


#: Higher wins. Kept explicit so a new role cannot silently outrank an old one.
RANK = {
    Role.MEMBER: 10,
    Role.ADVANCED: 20,
    Role.EDITOR: 30,
    Role.MANAGER: 40,
    Role.ADMIN: 50,
}
