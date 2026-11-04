"""The API for a school's server: its lives, their attendance, its people.

Every request carries ``Authorization: Bearer <token>``: the same kind of
token the site makes for its students (tokens.py), with ``"scope": "api"``
instead of a person, valid for an hour at most. It acts for the integration's
owner, on the owner's lives and videos and on the integration's people only.

    GET    /api/integrations/v1/lives/                    the lives, newest first
    POST   /api/integrations/v1/lives/                    schedule one
    GET    /api/integrations/v1/lives/<id>/               one, with its stream key
    PATCH  /api/integrations/v1/lives/<id>/               change it
    DELETE /api/integrations/v1/lives/<id>/               delete it (not on air)
    POST   /api/integrations/v1/lives/<id>/end/           end it
    GET    /api/integrations/v1/lives/<id>/attendance/    who watched, how long
    GET    /api/integrations/v1/videos/                   the videos ready to show
    POST   /api/integrations/v1/students/<id>/block/      and .../unblock/
    DELETE /api/integrations/v1/students/<id>/            forget (?keep_messages=1)
"""
from __future__ import annotations

import json
from functools import wraps

from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt

from apps.live import attendance, services
from apps.live.models import Live, parse_groups
from apps.media.models import Media

from . import identities, tokens
from .webhooks import live_data

VISIBILITIES = {choice for choice, _label in Media.Visibility.choices}


def _error(message: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"detail": message}, status=status)


def server_api(view):
    """Authenticate the site's server; the view gets the integration."""

    @csrf_exempt
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        header = request.META.get("HTTP_AUTHORIZATION", "")
        token = header[7:].strip() if header[:7].lower() == "bearer " else ""
        try:
            integration = tokens.verify_api(token)
        except tokens.TokenError as error:
            return _error(str(error), 401)
        response = view(request, integration, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response

    return wrapper


def _body(request) -> dict:
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _live_out(live: Live, with_stream: bool = False) -> dict:
    data = live_data(live)
    data["description"] = live.description
    data["chat"] = live.chat_enabled
    data["record"] = live.record
    data["peak_viewers"] = live.peak_viewers
    if with_stream:
        data["stream"] = {"server": live.ingest_url, "key": live.encoder_key}
    return data


def _apply(live: Live, data: dict) -> str:
    """Copy the given fields onto the live; an error message, or ''."""
    if "title" in data:
        title = str(data.get("title") or "").strip()[:255]
        if not title:
            return _("The live needs a title.")
        live.title = title
    if "description" in data:
        live.description = str(data.get("description") or "")[:5000]
    if "visibility" in data:
        if data["visibility"] not in VISIBILITIES:
            return _("visibility is public, unlisted or private.")
        live.visibility = data["visibility"]
    if "scheduled_for" in data:
        value = data.get("scheduled_for")
        if value in (None, ""):
            live.scheduled_for = None
        else:
            parsed = parse_datetime(str(value))
            if parsed is None or timezone.is_naive(parsed):
                return _("scheduled_for is a date and time with its time zone, such as 2026-11-20T19:00:00-03:00.")
            live.scheduled_for = parsed
    if "classes" in data:
        live.groups = ", ".join(parse_groups(data.get("classes") or []))
    if "chat" in data:
        live.chat_enabled = bool(data["chat"])
    if "record" in data:
        live.record = bool(data["record"])
    return ""


@server_api
def lives(request, integration):
    if request.method == "GET":
        found = Live.objects.filter(owner_id=integration.owner_id).select_related("recording").order_by("-created_at")
        status = request.GET.get("status")
        if status:
            found = found.filter(status=status)
        return JsonResponse({"lives": [_live_out(live) for live in found[:100]]})
    if request.method != "POST":
        return _error(_("Not allowed."), 405)
    live = Live(owner_id=integration.owner_id, visibility=Media.Visibility.PRIVATE)
    data = _body(request)
    if "title" not in data:
        return _error(_("The live needs a title."))
    problem = _apply(live, data)
    if problem:
        return _error(problem)
    live.save()
    return JsonResponse(_live_out(live, with_stream=True), status=201)


def _owned(integration, uid: str):
    return Live.objects.filter(owner_id=integration.owner_id, uid=uid).select_related("recording").first()


@server_api
def live_detail(request, integration, uid: str):
    live = _owned(integration, uid)
    if live is None:
        return _error(_("No such live."), 404)
    if request.method == "GET":
        return JsonResponse(_live_out(live, with_stream=True))
    if request.method == "PATCH":
        problem = _apply(live, _body(request))
        if problem:
            return _error(problem)
        live.save()
        return JsonResponse(_live_out(live, with_stream=True))
    if request.method == "DELETE":
        if live.is_on_air:
            return _error(_("End the live before deleting it."), 409)
        live.delete()
        return JsonResponse({"deleted": uid})
    return _error(_("Not allowed."), 405)


@server_api
def live_end(request, integration, uid: str):
    if request.method != "POST":
        return _error(_("Not allowed."), 405)
    live = _owned(integration, uid)
    if live is None:
        return _error(_("No such live."), 404)
    services.end(live)
    live.refresh_from_db()
    return JsonResponse(_live_out(live))


@server_api
def live_attendance(request, integration, uid: str):
    live = _owned(integration, uid)
    if live is None:
        return _error(_("No such live."), 404)
    rows = [
        {
            "student": row["site_id"],
            "name": row["name"],
            "joined_at": row["first_seen"].isoformat(),
            "last_seen_at": row["last_seen"].isoformat(),
            "minutes": row["minutes"],
            "messages": row["messages"],
        }
        for row in attendance.report(live)
        if row["integration_id"] == str(integration.pk)
    ]
    return JsonResponse({"live": live.uid, "attendance": rows})


@server_api
def videos(request, integration):
    found = (
        Media.objects.filter(owner_id=integration.owner_id, state=Media.State.READY)
        .filter(Q(kind=Media.Kind.VIDEO) | Q(kind=Media.Kind.AUDIO))
        .order_by("-created_at")[:200]
    )
    return JsonResponse({"videos": [
        {
            "id": media.uid,
            "title": media.title,
            "kind": media.kind,
            "visibility": media.visibility,
            "duration": media.duration_seconds,
            "created_at": media.created_at.isoformat(),
        }
        for media in found
    ]})


@server_api
def student(request, integration, external_id: str, action: str = ""):
    identity = identities.find(integration, external_id)
    if identity is None:
        return _error(_("No such student."), 404)
    if request.method == "POST" and action in ("block", "unblock"):
        identities.block(identity, action == "block")
        return JsonResponse({"student": external_id, "blocked": action == "block"})
    if request.method == "DELETE" and not action:
        identities.erase(identity, keep_messages=request.GET.get("keep_messages") == "1")
        return JsonResponse({"student": external_id, "deleted": True})
    return _error(_("Not allowed."), 405)
