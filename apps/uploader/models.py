"""Resumable chunked upload.

A browser splits a large file into numbered chunks. The server appends them to a
single part file in order, so a dropped connection only costs the current chunk.
The session is the unit of recovery: closing the tab and reopening it asks the
server which chunks landed.
"""
import uuid
from pathlib import Path

from django.conf import settings
from django.db import models
from django.utils import timezone


class UploadSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="upload_sessions"
    )
    filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=120, blank=True)
    total_size = models.BigIntegerField(default=0)
    chunk_size = models.PositiveIntegerField(default=5 * 1024 * 1024)
    next_index = models.PositiveIntegerField(default=0)
    received_bytes = models.BigIntegerField(default=0)
    completed = models.BooleanField(default=False)
    media_uid = models.CharField(max_length=16, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.filename} ({self.received_bytes}/{self.total_size})"

    @property
    def part_path(self) -> Path:
        root = Path(settings.MEDIA_ROOT) / "uploads" / str(self.id)
        root.mkdir(parents=True, exist_ok=True)
        return root / "part.bin"

    @property
    def expected_chunks(self) -> int:
        if not self.chunk_size:
            return 0
        return (self.total_size + self.chunk_size - 1) // self.chunk_size

    @property
    def progress(self) -> float:
        if not self.total_size:
            return 0.0
        return round(min(1.0, self.received_bytes / self.total_size), 4)
