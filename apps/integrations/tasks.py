"""Background work for integrations: the person's picture from their site."""
import logging

from celery import shared_task
from django.core.exceptions import ValidationError

from . import pictures
from .models import ExternalIdentity

logger = logging.getLogger(__name__)


@shared_task(name="integrations.fetch_picture", ignore_result=True)
def fetch_picture(identity_id: int) -> str:
    identity = ExternalIdentity.objects.select_related("user").filter(pk=identity_id).first()
    if identity is None:
        return "gone"
    try:
        return "stored" if pictures.apply(identity) else "cleared"
    except (pictures.PictureError, ValidationError) as exc:
        # The initials stay; the next sign-in with a new address tries again.
        logger.info("picture for %s not used: %s", identity, exc)
        return "failed"
