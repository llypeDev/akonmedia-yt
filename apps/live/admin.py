from django.contrib import admin
from django.db.models import Count
from django.utils.translation import gettext_lazy as _

from .models import ChatMessage, ChatReaction, Live, LiveRecording


class RecordingInline(admin.TabularInline):
    model = LiveRecording
    extra = 0
    readonly_fields = ["filename", "created_at"]


@admin.register(Live)
class LiveAdmin(admin.ModelAdmin):
    list_display = ["title", "owner", "status", "visibility", "scheduled_for", "started_at", "peak_viewers"]
    list_filter = ["status", "visibility"]
    search_fields = ["title", "uid", "owner__email"]
    readonly_fields = ["uid", "playback_id", "started_at", "interrupted_at", "ended_at",
                       "sessions", "publishing", "peak_viewers", "finalized_at", "recording"]
    inlines = [RecordingInline]


class ReactionInline(admin.TabularInline):
    model = ChatReaction
    extra = 0
    fields = ["kind", "user", "created_at"]
    readonly_fields = ["kind", "user", "created_at"]
    can_delete = True

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ["live", "author", "body", "reactions_shown", "created_at", "is_hidden"]
    list_filter = ["is_hidden"]
    search_fields = ["body", "author__email", "live__title"]
    inlines = [ReactionInline]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(reaction_count=Count("reactions"))

    @admin.display(description=_("reactions"), ordering="reaction_count")
    def reactions_shown(self, obj):
        if not obj.reaction_count:
            return "—"
        counts = obj.reactions.values("kind").annotate(n=Count("id"))
        return " · ".join(f"{ChatReaction.EMOJI.get(row['kind'], row['kind'])} {row['n']}" for row in counts)
