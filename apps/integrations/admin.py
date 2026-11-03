from django.contrib import admin

from .models import ExternalIdentity, Integration


class IdentityInline(admin.TabularInline):
    model = ExternalIdentity
    extra = 0
    fields = ["external_id", "name", "email", "is_moderator", "last_seen_at", "user"]
    readonly_fields = ["external_id", "name", "email", "is_moderator", "last_seen_at", "user"]
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Integration)
class IntegrationAdmin(admin.ModelAdmin):
    list_display = ["name", "owner", "key", "is_active", "created_at", "last_used_at"]
    list_filter = ["is_active"]
    search_fields = ["name", "key", "owner__email"]
    # The secret is shown and replaced on the integration's page in the
    # studio, never here.
    fields = ["name", "owner", "key", "origins", "is_active", "created_at", "last_used_at"]
    readonly_fields = ["key", "created_at", "last_used_at"]
    raw_id_fields = ["owner"]
    inlines = [IdentityInline]


@admin.register(ExternalIdentity)
class ExternalIdentityAdmin(admin.ModelAdmin):
    list_display = ["name", "external_id", "integration", "is_moderator", "last_seen_at"]
    list_filter = ["integration", "is_moderator"]
    search_fields = ["name", "external_id", "email"]
    readonly_fields = ["integration", "external_id", "user", "name", "email", "picture_url",
                       "is_moderator", "created_at", "last_seen_at"]

    def has_add_permission(self, request):
        return False
