"""News for a school's server: a live started, ended, its recording is ready.

Each item is a JSON POST to the integration's webhook address, signed with
its secret like GitHub's webhooks, so the server can tell it comes from here:

    X-QQplay-Event: live.started
    X-QQplay-Delivery: <id>
    X-QQplay-Signature: sha256=<HMAC-SHA256 of the body with the secret>

    {"event": "live.started", "sent_at": "...", "live": {...}}

The same news about the same live goes out once. A failed delivery is tried
again a few times, further and further apart.
"""
from __future__ import annotations

import hashlib
import hmac
import json

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .models import Integration, WebhookDelivery

EVENTS = ("live.started", "live.ended", "recording.ready", "test")


def signature(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _iso(value):
    return value.isoformat() if value else None


def live_data(live) -> dict:
    site = settings.SITE_URL
    recording = None
    if live.recording_id and live.recording is not None:
        recording = {
            "id": live.recording.uid,
            "state": live.recording.state,
            "url": site + reverse("core:media", args=[live.recording.uid]),
        }
    return {
        "id": live.uid,
        "title": live.title,
        "status": live.status,
        "visibility": live.visibility,
        "classes": live.group_list,
        "scheduled_for": _iso(live.scheduled_for),
        "started_at": _iso(live.started_at),
        "ended_at": _iso(live.ended_at),
        "url": site + live.get_absolute_url(),
        "recording": recording,
    }


def notify(live, event: str) -> list[WebhookDelivery]:
    """Queue the news for every active integration of the live's owner that
    has a webhook, once per live and event."""
    from .tasks import deliver_webhook

    deliveries = []
    for integration in Integration.objects.filter(owner_id=live.owner_id, is_active=True).exclude(webhook_url=""):
        if WebhookDelivery.objects.filter(integration=integration, event=event, subject=live.uid).exists():
            continue
        payload = {"event": event, "sent_at": timezone.now().isoformat(), "live": live_data(live)}
        delivery = WebhookDelivery.objects.create(
            integration=integration, event=event, subject=live.uid, url=integration.webhook_url, payload=payload,
        )
        deliveries.append(delivery)
        transaction.on_commit(lambda pk=delivery.pk: deliver_webhook.delay(pk))
    return deliveries


def send_test(integration) -> WebhookDelivery:
    from .tasks import deliver_webhook

    payload = {
        "event": "test",
        "sent_at": timezone.now().isoformat(),
        "integration": {"key": integration.key, "name": integration.name},
    }
    delivery = WebhookDelivery.objects.create(
        integration=integration, event="test", url=integration.webhook_url, payload=payload,
    )
    transaction.on_commit(lambda: deliver_webhook.delay(delivery.pk))
    return delivery


def connect() -> None:
    """Listen to the lives (apps.live.signals); called when the app is ready."""
    from apps.live import signals

    signals.live_started.connect(_started, dispatch_uid="integrations.live_started")
    signals.live_ended.connect(_ended, dispatch_uid="integrations.live_ended")
    signals.recording_ready.connect(_recording, dispatch_uid="integrations.recording_ready")


def _started(sender, live, **kwargs):
    notify(live, "live.started")


def _ended(sender, live, **kwargs):
    notify(live, "live.ended")


def _recording(sender, live, **kwargs):
    notify(live, "recording.ready")


def body_of(delivery: WebhookDelivery) -> bytes:
    payload = dict(delivery.payload)
    payload["delivery"] = str(delivery.pk)
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
