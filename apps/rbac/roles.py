"""The five account levels, ordered from least to most privileged."""
from django.db import models


class Role(models.TextChoices):
    MEMBER = "member", "Member"
    ADVANCED = "advanced", "Advanced member"
    EDITOR = "editor", "Editor"
    MANAGER = "manager", "Manager"
    ADMIN = "admin", "Administrator"


#: Higher wins. Kept explicit so a new role cannot silently outrank an old one.
RANK = {
    Role.MEMBER: 10,
    Role.ADVANCED: 20,
    Role.EDITOR: 30,
    Role.MANAGER: 40,
    Role.ADMIN: 50,
}
