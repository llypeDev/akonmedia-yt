"""Callbacks from the ingest server (nginx with the RTMP module).

nginx-rtmp POSTs form fields. ``name`` is the stream name, which for us is the
live's playback id, and the query string the encoder sent arrives as extra
fields, so ``key`` holds the stream key. A 2xx answer lets a publish through;
anything else refuses it. The shared secret in the URL keeps everyone else
out: these views are reachable through the public site too.
"""
import logging
from pathlib import Path

from django.conf import settings
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.utils.crypto import constant_time_compare
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import ingest, services
from .models import Live

logger = logging.getLogger(__name__)

EVENTS = {"publish", "publish-done", "record-done"}


@csrf_exempt
@require_POST
def callback(request, secret: str, event: str):
    expected = settings.LIVE_HOOK_SECRET
    if not expected or not constant_time_compare(secret, expected) or event not in EVENTS:
        raise Http404("unknown callback")

    name = (request.POST.get("name") or "").strip()
    live = Live.objects.filter(playback_id=name).first() if name else None
    address = request.POST.get("addr", "")

    if event == "publish":
        key = request.POST.get("key") or ""
        if live is None or not key or not constant_time_compare(key, live.stream_key):
            logger.warning("live publish refused for %r from %s: unknown stream or wrong key", name, address)
            return HttpResponseForbidden("unknown stream or wrong key")
        if not services.start_publishing(live):
            logger.info("live %s: publish refused from %s, the live has ended", live.uid, address)
            return HttpResponseForbidden("this live has ended")
        logger.info("live %s: on air from %s", live.uid, address)
        return HttpResponse("ok")

    if live is None:
        return HttpResponse("ignored")

    if event == "publish-done":
        services.stop_publishing(live)
        logger.info("live %s: encoder disconnected", live.uid)
    else:
        filename = Path(request.POST.get("path") or "").name
        if ingest.recording_name_ok(live.playback_id, filename):
            services.add_recording(live, filename)
        else:
            logger.warning("live %s: ignored recording %r", live.uid, filename)
    return HttpResponse("ok")
