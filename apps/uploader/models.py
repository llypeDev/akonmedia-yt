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
    def part_dir(self) -> Path:
        return Path(settings.MEDIA_ROOT) / "uploads" / str(self.id)

    @property
    def part_path(self) -> Path:
        root = self.part_dir
        root.mkdir(parents=True, exist_ok=True)
        return root / "part.bin"

    def bytes_on_disk(self) -> int:
        part = self.part_dir / "part.bin"
        return part.stat().st_size if part.exists() else 0

    def realign(self, on_disk: int) -> None:
        """Make the bookkeeping match the bytes that really reached the disk.

        A crash between writing a chunk and saving the row leaves the two out
        of step. Chunks are fixed-size, so the safe point is the last whole
        chunk; anything after it is cut off and sent again.
        """
        if on_disk >= self.total_size:
            aligned = self.total_size
            index = self.expected_chunks
        else:
            index = on_disk // self.chunk_size
            aligned = index * self.chunk_size
        if aligned != on_disk:
            with self.part_path.open("r+b") as handle:
                handle.truncate(aligned)
        self.received_bytes = aligned
        self.next_index = index
        self.save(update_fields=["received_bytes", "next_index", "updated_at"])

    def discard_part(self) -> None:
        import shutil

        shutil.rmtree(self.part_dir, ignore_errors=True)

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
