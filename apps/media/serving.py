"""Serve uploaded media with byte-range support.

Django's ``static.serve`` answers 200 with the whole file regardless of the
``Range`` header. Browsers need 206 to seek inside a progressive file, and iOS
Safari refuses to play video without it. This view implements the subset of
RFC 7233 that matters: a single range, open-ended ranges and suffix ranges.

Not a replacement for a real web server — for production put nginx, or object
storage plus a CDN, in front of the media directory.
"""
import mimetypes
import os
import re

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse
from django.utils._os import safe_join

RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")

# Python's mimetypes does not know these on every platform.
EXTRA_TYPES = {
    ".m3u8": "application/vnd.apple.mpegurl",
    ".ts": "video/mp2t",
    ".vtt": "text/vtt; charset=utf-8",
    ".m4s": "video/iso.segment",
}


def content_type_for(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in EXTRA_TYPES:
        return EXTRA_TYPES[ext]
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


class _LimitedReader:
    """Hands out at most ``length`` bytes, so a partial response stops there."""

    def __init__(self, handle, length: int):
        self.handle = handle
        self.remaining = length

    def read(self, size: int = -1) -> bytes:
        if self.remaining <= 0:
            return b""
        if size is None or size < 0:
            size = self.remaining
        chunk = self.handle.read(min(size, self.remaining))
        self.remaining -= len(chunk)
        return chunk

    def close(self) -> None:
        self.handle.close()


def serve_media(request, path: str):
    try:
        full_path = safe_join(str(settings.MEDIA_ROOT), path)
    except Exception as exc:  # noqa: BLE001 - anything odd here is a 404
        raise Http404("media not found") from exc

    if not os.path.isfile(full_path):
        raise Http404("media not found")

    size = os.path.getsize(full_path)
    content_type = content_type_for(full_path)

    header = request.headers.get("Range", "").strip()
    match = RANGE_RE.match(header) if header else None

    if match is None:
        response = FileResponse(open(full_path, "rb"), content_type=content_type)
        response["Accept-Ranges"] = "bytes"
        response["Content-Length"] = str(size)
        return response

    first, last = match.group(1), match.group(2)

    if first == "" and last == "":
        response = FileResponse(open(full_path, "rb"), content_type=content_type)
        response["Accept-Ranges"] = "bytes"
        response["Content-Length"] = str(size)
        return response

    if first == "":
        # Suffix range: the last N bytes.
        length = min(int(last), size)
        start = size - length
        end = size - 1
    else:
        start = int(first)
        if start >= size:
            unsatisfiable = HttpResponse(status=416)
            unsatisfiable["Content-Range"] = f"bytes */{size}"
            return unsatisfiable
        end = min(int(last), size - 1) if last else size - 1

    length = end - start + 1

    handle = open(full_path, "rb")
    handle.seek(start)

    response = FileResponse(_LimitedReader(handle, length), content_type=content_type)
    response.status_code = 206
    response["Accept-Ranges"] = "bytes"
    response["Content-Range"] = f"bytes {start}-{end}/{size}"
    response["Content-Length"] = str(length)
    return response
