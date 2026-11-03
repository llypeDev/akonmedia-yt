"""Background processing for uploaded media.

The task is written so a failure always leaves the record in a terminal state:
either ``ready`` or ``failed`` with a reason. Nothing stays stuck in
``processing``.
"""
import logging
from pathlib import Path

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from . import pipeline
from .models import Media

logger = logging.getLogger(__name__)


@shared_task(name="media.process", bind=True, max_retries=0)
def process_media(self, media_id: str) -> str:
    try:
        media = Media.objects.get(pk=media_id)
    except Media.DoesNotExist:
        return "missing"

    media.state = Media.State.PROCESSING
    media.failure_reason = ""
    media.save(update_fields=["state", "failure_reason"])

    try:
        _run(media)
    except pipeline.PipelineUnavailable as exc:
        # Nothing is wrong with the file; the worker simply cannot encode.
        media.state = Media.State.FAILED
        media.failure_reason = f"encoder unavailable: {exc}"
        media.save(update_fields=["state", "failure_reason"])
        logger.warning("media %s not processed: %s", media.uid, exc)
        return "unavailable"
    except Exception as exc:  # noqa: BLE001 - the state must not stay pending
        media.state = Media.State.FAILED
        media.failure_reason = f"{type(exc).__name__}: {exc}"[:1000]
        media.save(update_fields=["state", "failure_reason"])
        logger.exception("media %s failed", media.uid)
        return "failed"

    media.state = Media.State.READY
    media.published_at = media.published_at or timezone.now()
    media.save(update_fields=["state", "published_at", "duration_seconds", "width",
                              "height", "poster", "hls_playlist"])
    return "ready"


def _run(media: Media) -> None:
    if not media.file or not media.file.name:
        raise pipeline.PipelineError("no uploaded file attached")

    source = Path(media.file.path)

    if not source.exists():
        raise pipeline.PipelineError("uploaded file is missing on disk")

    if media.kind == Media.Kind.DOCUMENT:
        # Nothing to encode; the file is served as-is.
        return

    if media.kind == Media.Kind.IMAGE:
        media.width, media.height = _image_size(source)
        return

    if not pipeline.available():
        raise pipeline.PipelineUnavailable("ffmpeg and ffprobe are required for video and audio")

    info = pipeline.probe(source)
    media.duration_seconds = info.duration
    media.width = info.width
    media.height = info.height

    poster = source.parent / "poster.jpg"
    pipeline.extract_poster(source, poster, at=min(3.0, max(0.0, info.duration / 3)))
    media.poster.name = str(poster.relative_to(settings.MEDIA_ROOT)).replace("\\", "/")

    if media.kind == Media.Kind.VIDEO:
        master = pipeline.to_hls(
            source,
            source.parent / "hls",
            width=info.width,
            height=info.height,
        )
        media.hls_playlist = str(master.relative_to(settings.MEDIA_ROOT)).replace("\\", "/")


def _image_size(source: Path) -> tuple[int, int]:
    try:
        from PIL import Image  # optional dependency
    except ImportError:
        return 0, 0
    try:
        with Image.open(source) as img:
            return img.width, img.height
    except Exception:  # noqa: BLE001 - a broken image still belongs in the library
        return 0, 0
