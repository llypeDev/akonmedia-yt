"""State changes of a live, in one place.

::

    scheduled ──publish──▶ live ──encoder drops──▶ interrupted ──grace──▶ ended
                             ▲                          │
                             └────── reconnects ────────┘
    any state ──"End live" in the studio──▶ ended

Every transition is a conditional UPDATE, so the ingest server's callbacks,
viewers polling and the housekeeping task can race without undoing each other.
When a live has ended, its recording is finished by ``tasks.finalize_live``.
"""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from . import ingest, signals
from .models import Live, LiveRecording

#: After the last encoder left, how long to wait for recording files whose
#: callback never came before finishing with what is there.
RECORDING_SETTLE_SECONDS = 90


def start_publishing(live: Live) -> bool:
    """An encoder connected with the right key. False once the live is over."""
    now = timezone.now()
    updated = (
        Live.objects.filter(pk=live.pk)
        .exclude(status=Live.Status.ENDED)
        .update(
            status=Live.Status.LIVE,
            publishing=True,
            interrupted_at=None,
            sessions=F("sessions") + 1,
            updated_at=now,
        )
    )
    first = updated and Live.objects.filter(pk=live.pk, started_at__isnull=True).update(started_at=now)
    live.refresh_from_db()
    if first:
        signals.live_started.send(sender=Live, live=live)
    return bool(updated)


def stop_publishing(live: Live) -> None:
    """The encoder disconnected, on purpose or not."""
    now = timezone.now()
    Live.objects.filter(pk=live.pk).update(publishing=False, updated_at=now)
    Live.objects.filter(pk=live.pk, status=Live.Status.LIVE).update(
        status=Live.Status.INTERRUPTED, interrupted_at=now, updated_at=now
    )
    live.refresh_from_db()
    schedule_finalize(live)


def add_recording(live: Live, filename: str) -> None:
    LiveRecording.objects.get_or_create(live=live, filename=filename)
    live.refresh_from_db()
    schedule_finalize(live)


def end(live: Live) -> bool:
    """End a live from the studio. False when it had already ended."""
    now = timezone.now()
    ended = (
        Live.objects.filter(pk=live.pk)
        .exclude(status=Live.Status.ENDED)
        .update(status=Live.Status.ENDED, ended_at=now, updated_at=now)
    )
    live.refresh_from_db()
    if not ended:
        return False
    if live.interrupted_at and not live.publishing:
        # The stream really stopped when the encoder left, not now.
        Live.objects.filter(pk=live.pk).update(ended_at=live.interrupted_at)
        live.ended_at = live.interrupted_at
    if live.publishing:
        # The callbacks that follow (publish done, record done) finish the job.
        ingest.drop_publisher(live.playback_id)
        live.refresh_from_db()
    signals.live_ended.send(sender=Live, live=live)
    schedule_finalize(live)
    return True


def tick(live: Live, now=None) -> Live:
    """Time-based transitions: an interrupted live whose encoder did not come
    back in time ends, and an ended live gets its recording finished."""
    now = now or timezone.now()
    if live.status == Live.Status.INTERRUPTED and not live.publishing:
        cutoff = now - timedelta(seconds=settings.LIVE_RECONNECT_GRACE_SECONDS)
        ended = Live.objects.filter(
            pk=live.pk,
            status=Live.Status.INTERRUPTED,
            publishing=False,
            interrupted_at__lte=cutoff,
        ).update(status=Live.Status.ENDED, ended_at=F("interrupted_at"), updated_at=now)
        if ended:
            live.refresh_from_db()
            signals.live_ended.send(sender=Live, live=live)
    schedule_finalize(live, now=now)
    return live


def recording_complete(live: Live, now=None) -> bool:
    """Every encoder session has its file, or long enough has passed."""
    if live.recordings.count() >= live.sessions:
        return True
    now = now or timezone.now()
    last = live.ended_at or live.interrupted_at or live.updated_at
    return bool(last and now - last >= timedelta(seconds=RECORDING_SETTLE_SECONDS))


def schedule_finalize(live: Live, now=None) -> bool:
    if live.status != Live.Status.ENDED or live.publishing or live.finalized_at:
        return False
    if not recording_complete(live, now):
        return False
    from .tasks import finalize_live

    live_id = str(live.pk)
    transaction.on_commit(lambda: finalize_live.delay(live_id))
    return True
