"""Background processing for uploaded media.

The task is written so a failure always leaves the record in a terminal state:
either ``ready`` or ``failed`` with a reason. Nothing stays stuck in
``processing``.

What each kind gets:

``video``     probe, poster frame, adaptive HLS ladder, seek-preview storyboard
``audio``     probe, embedded cover art or a rendered waveform as the poster
``image``     dimensions and a JPEG thumbnail (EXIF orientation applied)
``document``  a first-page thumbnail for PDFs when poppler is installed

Posters and storyboards are a convenience: when one cannot be produced the
item is still published, because the original remains perfectly playable.
"""
import logging
from pathlib import Path

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from . import pipeline
from .models import Media

logger = logging.getLogger(__name__)

SAVED_FIELDS = [
    "state", "published_at", "duration_seconds", "width", "height", "poster",
    "hls_playlist", "storyboard", "storyboard_meta", "kind",
]


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
    media.save(update_fields=SAVED_FIELDS)
    return "ready"


def _relative(path: Path) -> str:
    return str(path.relative_to(settings.MEDIA_ROOT)).replace("\\", "/")


def _run(media: Media) -> None:
    if not media.file or not media.file.name:
        raise pipeline.PipelineError("no uploaded file attached")

    source = Path(media.file.path)

    if not source.exists():
        raise pipeline.PipelineError("uploaded file is missing on disk")

    folder = source.parent
    poster = folder / "poster.jpg"

    if media.kind == Media.Kind.DOCUMENT:
        _document(media, source, poster)
        return

    if media.kind == Media.Kind.IMAGE:
        _image(media, source, poster)
        return

    if not pipeline.available():
        raise pipeline.PipelineUnavailable("ffmpeg and ffprobe are required for video and audio")

    info = pipeline.probe(source)
    media.duration_seconds = info.duration
    media.width = info.width
    media.height = info.height

    if media.kind == Media.Kind.VIDEO and not info.has_video and info.has_audio:
        # An .mp4 or .webm that only carries sound is music, not a video.
        media.kind = Media.Kind.AUDIO

    if media.kind == Media.Kind.AUDIO:
        _audio(media, source, poster, info)
        return

    pipeline.extract_poster(source, poster, at=min(3.0, max(0.0, info.duration / 3)))
    media.poster.name = _relative(poster)

    master = pipeline.to_hls(
        source,
        folder / "hls",
        width=info.width,
        height=info.height,
    )
    media.hls_playlist = _relative(master)

    try:
        board = folder / "storyboard.jpg"
        media.storyboard_meta = pipeline.storyboard(source, board, info.duration)
        media.storyboard = _relative(board)
    except pipeline.PipelineError as exc:
        logger.warning("media %s: no storyboard (%s)", media.uid, exc)
        media.storyboard, media.storyboard_meta = "", {}


def _audio(media: Media, source: Path, poster: Path, info: pipeline.Probe) -> None:
    media.width = media.height = 0
    made = None
    if info.has_cover:
        try:
            made = pipeline.extract_cover(source, poster)
        except pipeline.PipelineError:
            made = None
    if made is None:
        try:
            made = pipeline.waveform_poster(source, poster)
        except pipeline.PipelineError as exc:
            logger.warning("media %s: no waveform (%s)", media.uid, exc)
    if made is not None:
        media.poster.name = _relative(made)


def _image(media: Media, source: Path, poster: Path) -> None:
    try:
        media.width, media.height = pipeline.image_poster(source, poster)
    except pipeline.PipelineError as exc:
        # A picture Pillow cannot read (SVG, HEIC…) still belongs in the
        # library; the browser may well be able to show it.
        logger.info("media %s: no thumbnail (%s)", media.uid, exc)
        media.width, media.height = 0, 0
        return
    media.poster.name = _relative(poster)


def _document(media: Media, source: Path, poster: Path) -> None:
    if source.suffix.lower() != ".pdf" or not pipeline.pdf_available():
        return
    try:
        pipeline.pdf_poster(source, poster)
    except pipeline.PipelineError as exc:
        logger.info("media %s: no page preview (%s)", media.uid, exc)
        return
    media.poster.name = _relative(poster)
