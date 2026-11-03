from django.contrib import admin

from .models import UploadSession


@admin.register(UploadSession)
class UploadSessionAdmin(admin.ModelAdmin):
    list_display = ["filename", "owner", "received_bytes", "total_size", "completed", "updated_at"]
    list_filter = ["completed", "created_at"]
    search_fields = ["filename", "owner__email", "media_uid"]
    raw_id_fields = ["owner"]
    readonly_fields = [
        "id", "filename", "content_type", "total_size", "chunk_size", "next_index",
        "received_bytes", "completed", "media_uid", "created_at", "updated_at",
    ]
