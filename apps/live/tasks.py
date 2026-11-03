"""Background work for lives: turning a recording into a media item, and the
housekeeping that ends lives whose encoder never came back."""
import logging

from celery import shared_task
from django.conf import settings
from django.core.files import File
from django.utils import timezone
from django.utils.text import slugify

from apps.media import pipeline
from apps.media.models import Media

from . import ingest, services
from .models import Live

logger = logging.getLogger(__name__)


@shared_task(name="live.finalize", bind=True, max_retries=0)
def finalize_live(self, live_id: str) -> str:
    """Join the encoder sessions of an ended live and publish them as a video.

    The row is claimed first, so a second call, from a callback or the
    housekeeping task, does nothing.
    """
    claimed = Live.objects.filter(
        pk=live_id,
        status=Live.Status.ENDED,
        publishing=False,
        finalized_at__isnull=True,
    ).update(finalized_at=timezone.now())
    if not claimed:
        return "skipped"

    live = Live.objects.select_related("owner", "category").get(pk=live_id)
    folder = ingest.recordings_dir()
    files = [folder / recording.filename for recording in live.recordings.all()]
    present = [path for path in files if path.is_file() and path.stat().st_size > 0]

    if not live.record or not files:
        ingest.discard(files)
        return "no recording"
    if not present:
        Live.objects.filter(pk=live.pk).update(
            recording_error="The ingest server's recording files are missing."
        )
        return "missing"

    target = folder / f"{live.playback_id}-joined.mp4"
    try:
        ingest.join_recordings(present, target)
        _publish(live, target)
    except Exception as exc:  # noqa: BLE001 - kept on the live for the owner to see
        # The session files stay on disk so the recording can still be rescued.
        Live.objects.filter(pk=live.pk).update(recording_error=f"{type(exc).__name__}: {exc}"[:500])
        logger.exception("live %s: the recording could not be published", live.uid)
        ingest.discard([target])
        return "failed"

    ingest.discard([*present, target])
    return "recorded"


def _publish(live: Live, path) -> Media:
    info = pipeline.probe(path)
    name = (slugify(live.title)[:60] or "live") + ".mp4"
    media = Media(
        title=live.title,
        description=live.description,
        owner=live.owner,
        visibility=live.visibility,
        category=live.category,
        kind=Media.Kind.VIDEO,
        original_name=name,
        size_bytes=path.stat().st_size,
        created_at=live.started_at or timezone.now(),
    )
    with path.open("rb") as handle:
        media.file.save(name, File(handle), save=True)
    Live.objects.filter(pk=live.pk).update(recording=media)

    from apps.media.tasks import process_media

    # Long classes encode slower than real time on small machines: give the
    # task room in proportion to the recording instead of the default hour.
    limit = max(settings.CELERY_TASK_TIME_LIMIT, int(info.duration * 12) + 30 * 60)
    process_media.apply_async((str(media.pk),), time_limit=limit)
    return media


@shared_task(name="live.housekeeping")
def housekeeping() -> int:
    """End lives whose encoder did not come back in time, and finish any
    recording a lost callback left waiting."""
    touched = 0
    pending = Live.objects.filter(status=Live.Status.INTERRUPTED) | Live.objects.filter(
        status=Live.Status.ENDED, publishing=False, finalized_at__isnull=True
    )
    for live in pending:
        services.tick(live)
        touched += 1
    return touched
