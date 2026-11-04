"""A push channel that tells open pages a live has changed.

Pages poll ``state`` (views.py). With hundreds of people in a class, most of
those polls find nothing new, so this stream lets the server say when there
is something: a page connected here polls right after a change and otherwise
only every few tens of seconds, for the viewer count.

What it sends is only that something changed (the chat's version and the
live's status), never the content, so it needs no session: a page gets a
ticket for its live with the state, and the private lives stay private.

It is served by an ASGI server, the "events" service of the production
compose files, which holds thousands of idle connections in one process. One
poller per live per process reads the live's row once a second and wakes all
the connections watching it. Under WSGI (gunicorn's workers, runserver) a
connection would hold a whole worker, so there the view answers 204 at once,
which tells EventSource not to try again, and the page keeps polling.
"""
from __future__ import annotations

import asyncio
import json
import time

from asgiref.sync import sync_to_async
from django.core import signing
from django.core.handlers.asgi import ASGIRequest
from django.db import close_old_connections
from django.http import Http404, HttpResponse, StreamingHttpResponse

from .models import Live

SALT = "qqplay.live.events"
#: How often a live's row is read, by one poller for all its connections.
TICK = 1.0
#: A comment line keeps proxies from closing a quiet connection.
HEARTBEAT = 20
#: Connections end after this long; EventSource opens a new one by itself.
LIFETIME = 300
TICKET_AGE = 24 * 3600


def ticket(live: Live) -> str:
    return signing.dumps(str(live.pk), salt=SALT, compress=True)


def _live_for(ticket_value: str, uid: str):
    try:
        live_id = signing.loads(ticket_value, salt=SALT, max_age=TICKET_AGE)
    except signing.BadSignature:
        return None
    return Live.objects.filter(pk=live_id, uid=uid).values_list("pk", flat=True).first()


def _pulse(live_id):
    close_old_connections()
    try:
        return Live.objects.filter(pk=live_id).values_list(
            "chat_version", "status", "publishing", "recording_id"
        ).first()
    finally:
        close_old_connections()


class Hub:
    """One poller per live, shared by every connection watching it."""

    def __init__(self):
        self.entries = {}

    async def join(self, live_id):
        entry = self.entries.get(live_id)
        if entry is None:
            entry = {"value": None, "change": asyncio.Condition(), "listeners": 0, "task": None}
            self.entries[live_id] = entry
        entry["listeners"] += 1
        if entry["task"] is None or entry["task"].done():
            entry["task"] = asyncio.create_task(self._poll(live_id, entry))
        return entry

    def leave(self, entry) -> None:
        entry["listeners"] -= 1

    async def _poll(self, live_id, entry):
        read = sync_to_async(_pulse, thread_sensitive=False)
        try:
            while entry["listeners"] > 0:
                value = await read(live_id)
                if value != entry["value"]:
                    entry["value"] = value
                    async with entry["change"]:
                        entry["change"].notify_all()
                await asyncio.sleep(TICK)
        finally:
            if self.entries.get(live_id) is entry and entry["listeners"] <= 0:
                del self.entries[live_id]


hub = Hub()


def _message(value) -> str:
    version, status, publishing, recording = value or (0, "", False, None)
    data = json.dumps({"v": version, "s": status, "p": publishing, "r": str(recording or "")})
    return f"event: change\ndata: {data}\n\n"


async def _stream(live_id):
    entry = await hub.join(live_id)
    try:
        yield "retry: 3000\n\n"
        seen = entry["value"]
        started = time.monotonic()
        while time.monotonic() - started < LIFETIME:
            try:
                async with entry["change"]:
                    await asyncio.wait_for(entry["change"].wait(), timeout=HEARTBEAT)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            if entry["value"] != seen:
                seen = entry["value"]
                yield _message(seen)
    finally:
        hub.leave(entry)


async def events(request, uid: str):
    if not isinstance(request, ASGIRequest):
        return HttpResponse(status=204)
    live_id = await sync_to_async(_live_for)(request.GET.get("t", ""), uid)
    if live_id is None:
        raise Http404("live not found")
    response = StreamingHttpResponse(_stream(live_id), content_type="text/event-stream")
    response["Cache-Control"] = "no-store"
    # Proxies must pass every event on at once.
    response["X-Accel-Buffering"] = "no"
    return response
