from django.contrib import admin

from .models import Category, Chapter, Comment, Media, Playlist, PlaylistItem, Subtitle


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "parent"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ("name",)}


class ChapterInline(admin.TabularInline):
    model = Chapter
    extra = 0


class SubtitleInline(admin.TabularInline):
    model = Subtitle
    extra = 0


@admin.register(Media)
class MediaAdmin(admin.ModelAdmin):
    list_display = ["title", "kind", "state", "visibility", "owner", "created_at"]
    list_filter = ["kind", "state", "visibility", "created_at"]
    search_fields = ["title", "uid", "owner__email"]
    readonly_fields = ["uid", "size_bytes", "duration_seconds", "width", "height", "views_count"]
    raw_id_fields = ["owner", "category"]
    inlines = [ChapterInline, SubtitleInline]
    date_hierarchy = "created_at"


class PlaylistItemInline(admin.TabularInline):
    model = PlaylistItem
    extra = 0
    raw_id_fields = ["media"]


@admin.register(Playlist)
class PlaylistAdmin(admin.ModelAdmin):
    list_display = ["title", "owner", "visibility", "created_at"]
    search_fields = ["title", "owner__email"]
    raw_id_fields = ["owner"]
    inlines = [PlaylistItemInline]


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ["media", "author", "timestamp", "is_hidden", "created_at"]
    list_filter = ["is_hidden", "created_at"]
    search_fields = ["body", "author__email", "media__title"]
    raw_id_fields = ["media", "author", "parent"]
