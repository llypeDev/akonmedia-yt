"""Housekeeping for abandoned uploads.

A browser that never finishes leaves a part file behind. Celery beat runs
:func:`purge_stale_uploads` every hour; ``manage.py purge_uploads`` does the same
for deployments without beat.
"""
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .models import UploadSession


def purge(max_age_hours: int | None = None) -> int:
    hours = max_age_hours if max_age_hours is not None else settings.UPLOAD_SESSION_TTL_HOURS
    cutoff = timezone.now() - timedelta(hours=hours)
    removed = 0
    for session in UploadSession.objects.filter(completed=False, updated_at__lt=cutoff):
        session.discard_part()
        session.delete()
        removed += 1
    return removed


@shared_task(name="uploads.purge_stale")
def purge_stale_uploads() -> int:
    return purge()
