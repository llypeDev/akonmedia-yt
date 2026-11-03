from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ["-date_joined"]
    list_display = ["email", "display_name", "role", "is_active", "is_staff", "date_joined"]
    list_filter = ["role", "is_active", "is_staff", "is_superuser"]
    search_fields = ["email", "display_name"]
    readonly_fields = ["id", "date_joined", "last_login", "picture"]

    fieldsets = [
        (None, {"fields": ["id", "email", "password"]}),
        (_("Profile"), {"fields": ["display_name", "role", "picture", "avatar"]}),
        (_("Permissions"), {"fields": ["is_active", "is_staff", "is_superuser", "groups", "user_permissions"]}),
        (_("History"), {"fields": ["date_joined", "last_login"]}),
    ]

    add_fieldsets = [
        (
            None,
            {
                "classes": ["wide"],
                "fields": ["email", "display_name", "role", "password1", "password2"],
            },
        ),
    ]

    @admin.display(description=_("Picture"))
    def picture(self, obj):
        """What the portal shows. A photo is uploaded on the account page,
        where it is cropped and resized; here it can only be switched off."""
        url = obj.avatar_url
        if not url:
            return _("Initials")
        return format_html(
            '<img src="{}" alt="" width="56" height="56" style="border-radius:50%;object-fit:cover">', url
        )
