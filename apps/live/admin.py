from django.contrib import admin

from .models import ChatMessage, Live, LiveRecording


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


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ["live", "author", "body", "created_at", "is_hidden"]
    list_filter = ["is_hidden"]
    search_fields = ["body", "author__email", "live__title"]
