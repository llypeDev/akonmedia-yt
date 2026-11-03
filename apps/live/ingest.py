"""Everything that touches the ingest server or the files it writes.

The ingest server is nginx with the RTMP module (``docker/live``). It calls the
portal back on every publish (see ``hooks.py``); the portal only calls it to cut
an encoder off when a live is ended from the studio.
"""
import logging
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from django.conf import settings

from apps.media import pipeline

logger = logging.getLogger(__name__)

#: Internal calls never go through a proxy configured for the outside world.
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def application() -> str:
    """The RTMP application name: the last path segment of ``LIVE_RTMP_URL``."""
    path = urllib.parse.urlparse(settings.LIVE_RTMP_URL).path.strip("/")
    return path.rsplit("/", 1)[-1] or "live"


def drop_publisher(playback_id: str) -> bool:
    """Ask the ingest server to disconnect whoever publishes ``playback_id``."""
    base = (settings.LIVE_CONTROL_URL or "").rstrip("/")
    if not base:
        return False
    query = urllib.parse.urlencode({"app": application(), "name": playback_id})
    try:
        with _DIRECT.open(f"{base}/drop/publisher?{query}", timeout=3) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.warning("live %s: could not drop the publisher (%s)", playback_id, exc)
        return False


def recordings_dir() -> Path:
    return Path(settings.LIVE_RECORDINGS_DIR)


def recording_name_ok(playback_id: str, filename: str) -> bool:
    """Only ``<playback id>-<timestamp>.flv``, the name nginx-rtmp gives a
    recording with ``record_unique on``. Anything else is ignored."""
    return bool(re.fullmatch(rf"{re.escape(playback_id)}-\d+\.flv", filename or ""))


def discard(paths) -> None:
    for path in paths:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("could not delete %s (%s)", path, exc)


def _quoted(path: Path) -> str:
    return str(path).replace("'", "'\\''")


def join_recordings(sources: list[Path], target: Path) -> Path:
    """One MP4 from the encoder sessions of a live, in order.

    The sessions share their settings almost always, so the streams are copied
    without re-encoding. When the encoder's settings changed between sessions
    the copy fails, and the files are re-encoded once instead.
    """
    ffmpeg = pipeline._binary("ffmpeg")
    listing = target.with_suffix(".txt")
    listing.write_text("".join(f"file '{_quoted(path)}'\n" for path in sources), encoding="utf-8")
    common = [ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing)]
    try:
        copied = subprocess.run(
            [*common, "-c", "copy", "-movflags", "+faststart", str(target)],
            capture_output=True,
            text=True,
            timeout=60 * 60,
        )
        if copied.returncode == 0 and target.is_file() and target.stat().st_size > 0:
            return target
        logger.info("live recording %s: copy failed, re-encoding (%s)", target.name,
                    copied.stderr.strip()[-200:])
        encoded = subprocess.run(
            [*common, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(target)],
            capture_output=True,
            text=True,
            timeout=6 * 60 * 60,
        )
        if encoded.returncode != 0 or not target.is_file() or not target.stat().st_size:
            raise pipeline.PipelineError(f"joining the recording failed: {encoded.stderr.strip()[-300:]}")
        return target
    finally:
        listing.unlink(missing_ok=True)
