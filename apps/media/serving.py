"""Serve uploaded media with byte-range support.

Django's ``static.serve`` answers 200 with the whole file regardless of the
``Range`` header. Browsers need 206 to seek inside a progressive file, and iOS
Safari refuses to play video without it. This view implements the subset of
RFC 7233 that matters: a single range, open-ended ranges and suffix ranges.

Behind a web server that understands ``X-Accel-Redirect`` (Caddy in
``docker-compose.prod.yml``, or nginx), set ``MEDIA_ACCEL_REDIRECT``: this view
still finds the file and decides the headers it goes out with, and the server
sends the bytes, answering ranges and slow connections without holding a
worker of the application.
"""
import mimetypes
import os
import re
from urllib.parse import quote

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
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".m4a": "audio/mp4",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".weba": "audio/webm",
    ".ogv": "video/ogg",
    ".oga": "audio/ogg",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".heic": "image/heic",
}


def content_type_for(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in EXTRA_TYPES:
        return EXTRA_TYPES[ext]
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


#: Types a browser may render inline from our origin. Anything else (HTML,
#: JavaScript, XML…) is downloaded instead, so an upload can never run script
#: with the portal's cookies.
INLINE_PREFIXES = ("video/", "audio/", "image/")
INLINE_TYPES = {
    "application/pdf",
    "application/vnd.apple.mpegurl",
    "text/vtt; charset=utf-8",
}

#: Applied to every media response. Harmless for <video> and <img>, which
#: ignore it, and it neuters an SVG or text file opened directly.
SANDBOX_POLICY = "sandbox; default-src 'none'; img-src 'self' data:; media-src 'self'; style-src 'unsafe-inline'"


def _harden(response, content_type: str, filename: str) -> None:
    response["X-Content-Type-Options"] = "nosniff"
    inline = content_type in INLINE_TYPES or (
        content_type.startswith(INLINE_PREFIXES) and content_type != "image/svg+xml"
    )
    if content_type == "image/svg+xml":
        inline = True  # shown as a picture, but sandboxed below
    if not inline:
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
    if content_type != "application/pdf":
        # Chrome's PDF viewer will not run inside a sandboxed document.
        response["Content-Security-Policy"] = SANDBOX_POLICY


def _hand_over(full_path: str, content_type: str, filename: str) -> HttpResponse:
    """An empty answer telling the web server in front which file to send.

    The server copies the headers set here and handles ``Range`` and
    conditional requests itself, so none of that happens in this process.
    """
    relative = os.path.relpath(full_path, os.path.abspath(str(settings.MEDIA_ROOT)))
    prefix = settings.MEDIA_ACCEL_REDIRECT.rstrip("/")
    response = HttpResponse(content_type=content_type)
    response["X-Accel-Redirect"] = f"{prefix}/{quote(relative.replace(os.sep, '/'))}"
    _harden(response, content_type, filename)
    return response


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

    content_type = content_type_for(full_path)
    filename = os.path.basename(full_path).replace('"', "")

    if settings.MEDIA_ACCEL_REDIRECT:
        return _hand_over(full_path, content_type, filename)

    size = os.path.getsize(full_path)
    header = request.headers.get("Range", "").strip()
    match = RANGE_RE.match(header) if header else None

    if match is None or (match.group(1) == "" and match.group(2) == ""):
        response = FileResponse(open(full_path, "rb"), content_type=content_type)
        response["Accept-Ranges"] = "bytes"
        response["Content-Length"] = str(size)
        _harden(response, content_type, filename)
        return response

    first, last = match.group(1), match.group(2)

    if first == "" and (int(last) == 0 or size == 0):
        unsatisfiable = HttpResponse(status=416)
        unsatisfiable["Content-Range"] = f"bytes */{size}"
        return unsatisfiable

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
        if end < start:
            invalid = HttpResponse(status=416)
            invalid["Content-Range"] = f"bytes */{size}"
            return invalid

    length = end - start + 1

    handle = open(full_path, "rb")
    handle.seek(start)

    response = FileResponse(_LimitedReader(handle, length), content_type=content_type)
    response.status_code = 206
    response["Accept-Ranges"] = "bytes"
    response["Content-Range"] = f"bytes {start}-{end}/{size}"
    response["Content-Length"] = str(length)
    _harden(response, content_type, filename)
    return response
