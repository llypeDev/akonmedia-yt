"""Background work for integrations: the person's picture from their site."""
import logging

from celery import shared_task
from django.core.exceptions import ValidationError
from django.utils import timezone

from . import net, pictures, webhooks
from .models import ExternalIdentity, WebhookDelivery

#: Seconds before each new try of a failed delivery.
WEBHOOK_BACKOFF = (30, 120, 600, 1800)

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


@shared_task(name="integrations.deliver_webhook", bind=True, max_retries=len(WEBHOOK_BACKOFF), ignore_result=True)
def deliver_webhook(self, delivery_id: int) -> str:
    delivery = WebhookDelivery.objects.select_related("integration").filter(pk=delivery_id).first()
    if delivery is None or delivery.ok:
        return "gone"
    integration = delivery.integration
    body = webhooks.body_of(delivery)
    headers = {
        "Content-Type": "application/json",
        "X-QQplay-Event": delivery.event,
        "X-QQplay-Delivery": str(delivery.pk),
        "X-QQplay-Signature": webhooks.signature(body, integration.secret),
    }
    delivery.attempts += 1
    try:
        answer = net.request("POST", delivery.url, body=body, headers=headers, max_bytes=64 * 1024, follow=False)
        delivery.status_code = answer.status
        delivery.ok = 200 <= answer.status < 300
        delivery.error = "" if delivery.ok else answer.body[:300].decode("utf-8", "replace")
    except net.NetError as exc:
        delivery.ok = False
        delivery.error = str(exc)[:300]
    if delivery.ok:
        delivery.delivered_at = timezone.now()
    delivery.save(update_fields=["attempts", "status_code", "ok", "error", "delivered_at"])
    if not delivery.ok and delivery.event != "test" and self.request.retries < len(WEBHOOK_BACKOFF):
        raise self.retry(countdown=WEBHOOK_BACKOFF[self.request.retries])
    return "delivered" if delivery.ok else "failed"
