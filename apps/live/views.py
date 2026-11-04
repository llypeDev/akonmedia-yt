"""Pages and JSON endpoints for lives.

Viewers' pages poll ``state`` every few seconds: one request answers what the
page needs to know (status, stream address, viewers, recording) and brings
what changed in the chat since the version the page has (see chat.py).
"""
import json
import logging
import re
import secrets
import subprocess
from pathlib import Path

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.db.models import F
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.html import escape
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_GET, require_POST

from apps.core.templatetags.akon import avatar
from apps.media import access
from apps.media.models import Category, Media
from apps.rbac import permissions as policy

from . import attendance, chat, presence, relay, services, stream
from .models import ChatMessage, ChatMute, Live, new_stream_key

logger = logging.getLogger(__name__)

CHAT_MAX_LENGTH = chat.MAX_LENGTH


# ------------------------------------------------------------------ forms


class LiveForm(forms.ModelForm):
    #: The page's script fills this with the date and time field read in the
    #: viewer's own time zone. Without JavaScript the field is read in the
    #: server's time zone instead.
    scheduled_for_iso = forms.CharField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = Live
        fields = ["title", "description", "visibility", "category", "scheduled_for",
                  "record", "chat_enabled", "groups"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 5}),
            "scheduled_for": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.order_by("name")
        self.fields["category"].empty_label = gettext_lazy("No category")
        self.fields["title"].widget.attrs.update({"maxlength": 255, "autocomplete": "off"})
        self.fields["scheduled_for"].input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"]
        # The script shows the stored instant in the browser's time zone and
        # sends what is typed back as an ISO timestamp (see live.js).
        widget = self.fields["scheduled_for"].widget
        widget.attrs["data-iso-field"] = "scheduled_for_iso"
        # The calendar (datepicker.js) fades the days that already went by.
        widget.attrs["data-picker-min"] = "today"
        stored = self.instance.scheduled_for if not self.is_bound else None
        widget.attrs["data-utc"] = stored.isoformat() if stored else ""

    def clean_groups(self):
        from .models import parse_groups

        return ", ".join(parse_groups(self.cleaned_data.get("groups") or ""))

    def clean(self):
        data = super().clean()
        raw = (data.get("scheduled_for_iso") or "").strip()
        if raw:
            parsed = parse_datetime(raw)
            if parsed is not None and timezone.is_aware(parsed):
                data["scheduled_for"] = parsed
        return data


# ---------------------------------------------------------------- helpers


def _iso(value):
    return value.isoformat() if value else None


def _embed_grants(request, live: Live) -> bool:
    """A site's frame whose integration opens this live (see apps.integrations)."""
    embed = getattr(request, "embed", None)
    return bool(embed is not None and embed.grants_live(live))


def _viewable(request, uid: str) -> Live:
    live = get_object_or_404(
        Live.objects.select_related("owner", "category", "recording"), uid=uid
    )
    if not (access.can_view_live(request.user, live) or _embed_grants(request, live)):
        raise Http404("live not found")
    return live


def can_moderate(request, live: Live) -> bool:
    """Hide chat messages: the host and curators here, and on a site's frame
    the teachers its tokens mark as moderators."""
    embed = getattr(request, "embed", None)
    if embed is not None:
        return embed.moderates(live)
    return access.can_moderate_live_chat(request.user, live)


def _editable(request, uid: str) -> Live:
    live = get_object_or_404(
        Live.objects.select_related("owner", "category", "recording"), uid=uid
    )
    if not access.can_edit_live(request.user, live):
        raise Http404("live not found")
    return live


def chat_open(live: Live) -> bool:
    return chat.is_open(live)


def recording_payload(request, live: Live):
    if not live.is_ended:
        return None
    media = live.recording
    if media is not None:
        if media.state in (Media.State.PENDING, Media.State.PROCESSING):
            return {"state": "processing", "ready": False, "url": ""}
        if media.state == Media.State.FAILED:
            return {"state": "failed", "ready": False, "url": ""}
        allowed = access.can_view_media(request.user, media) or (
            _embed_grants(request, live) and media.state == Media.State.READY
        )
        if not allowed:
            # The owner made the recording private after the live.
            return {"state": "none", "ready": False, "url": ""}
        payload = {
            "state": "ready",
            "ready": True,
            "url": reverse("core:media", args=[media.uid]),
            "embed": reverse("core:embed", args=[media.uid]),
        }
        # Frames play the recording where the live was, chat and all.
        payload["play"] = {
            "hls": media.hls_url or "",
            "src": media.file.url if media.file else "",
            "poster": media.poster.url if media.poster else "",
            "kind": media.kind,
        }
        return payload
    if live.recording_error:
        return {"state": "failed", "ready": False, "url": ""}
    if not live.record or (live.finalized_at and not live.recordings.exists()):
        return {"state": "none", "ready": False, "url": ""}
    # Not finished yet, or being joined right now.
    return {"state": "pending", "ready": False, "url": ""}


def state_payload(request, live: Live, after: int = 0, client: str = "", chat_version: int = -1) -> dict:
    services.tick(live)
    on_air = live.is_on_air
    if on_air and client:
        presence.touch(live, client)
        attendance.touch(live, request.user)
    payload = {
        "status": live.status,
        "label": live.get_status_display(),
        "on_air": on_air,
        "hls": live.hls_url if on_air else "",
        "scheduled_for": _iso(live.scheduled_for),
        "started_at": _iso(live.started_at),
        "ended_at": _iso(live.ended_at),
        "viewers": presence.count(live) if on_air else 0,
        "recording": recording_payload(request, live),
        # Where the page may listen for changes instead of polling often.
        "events": f"{reverse('live:events', args=[live.uid])}?t={stream.ticket(live)}",
        "chat": chat.payload(request, live, can_moderate(request, live), after, chat_version),
    }
    if access.can_edit_live(request.user, live):
        payload["control"] = {
            "publishing": live.publishing,
            "sessions": live.sessions,
            "peak": live.peak_viewers,
            "recording_error": live.recording_error,
        }
    return payload


#: What Django's json_script escapes: with "<", ">" and "&" written as \u
#: escapes, nothing in the data (a chat message, say) can end the <script>
#: element or put the HTML parser in a state where its end tag is ignored.
_JSON_SCRIPT_ESCAPES = {ord(">"): "\\u003E", ord("<"): "\\u003C", ord("&"): "\\u0026"}


def _page_state(request, live: Live) -> tuple[dict, str]:
    """The state for the template to render, and the same as JSON for the
    page's script, safe to place inside <script type="application/json">."""
    payload = state_payload(request, live)
    return payload, json.dumps(payload).translate(_JSON_SCRIPT_ESCAPES)


def _embed_snippet(live: Live, embed_url: str) -> str:
    return (
        f'<iframe src="{escape(embed_url)}" width="640" height="360" frameborder="0" '
        f'allow="autoplay; fullscreen; picture-in-picture" allowfullscreen '
        f'title="{escape(live.title)}"></iframe>'
    )


def _int(value, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------ pages


def lives(request):
    listed = Live.objects.filter(access.live_listed_q()).select_related("owner", "recording")
    context = {
        "on_air": listed.filter(status__in=Live.ON_AIR).order_by("-started_at"),
        "upcoming": listed.filter(status=Live.Status.SCHEDULED).order_by(
            F("scheduled_for").asc(nulls_last=True), "-created_at"
        )[:24],
        "recent": listed.filter(status=Live.Status.ENDED).order_by("-ended_at")[:12],
    }
    return render(request, "live/lives.html", context)


def watch(request, uid: str):
    live = _viewable(request, uid)
    page_state, page_json = _page_state(request, live)
    embed_url = request.build_absolute_uri(reverse("live:embed", args=[live.uid]))
    return render(
        request,
        "live/watch.html",
        {
            "live": live,
            "state": page_state,
            "state_json": page_json,
            "page_url": request.build_absolute_uri(live.get_absolute_url()),
            "embed_url": embed_url,
            "embed_snippet": _embed_snippet(live, embed_url),
            "can_edit": access.can_edit_live(request.user, live),
        },
    )


EMBED_LAYOUTS = ("player", "chat", "both")
EMBED_THEMES = ("dark", "light")


def embed(request, uid: str):
    """The live for other sites: the player, its chat, or both side by side.

        ?layout=player|chat|both    what the frame shows (the player by default)
        ?school=<integration key>   people sign in through that site to talk
                                    (apps.integrations); a private live of the
                                    integration's owner shows once they have
        ?theme=dark|light           the chat's colours (the system's otherwise)

    Once the recording is ready the player's embed code plays it, so a page
    that embedded a class keeps working after the class is over.

    The frame never speaks for whoever is signed in to QQplay in this browser:
    on another site that could be anybody. Its chat is read-only until the
    site signs the person in.
    """
    from django.contrib.auth.models import AnonymousUser

    from apps.integrations.models import Integration

    live = get_object_or_404(Live.objects.select_related("recording", "owner"), uid=uid)
    layout = request.GET.get("layout", "player")
    layout = layout if layout in EMBED_LAYOUTS else "player"
    theme = request.GET.get("theme", "")
    theme = theme if theme in EMBED_THEMES else ""
    school = (request.GET.get("school") or "")[:40]
    integration = Integration.objects.filter(key=school, is_active=True).first() if school else None

    available = access.can_view_live(request.user, live)
    # A private live opens to the site's people once one of them signs in.
    awaiting = not available and integration is not None and integration.grants_live(live)
    if available and layout == "player" and integration is None and live.is_ended and live.recording \
            and live.recording.is_ready and access.can_view_media(request.user, live.recording):
        target = reverse("core:embed", args=[live.recording.uid])
        query = request.META.get("QUERY_STRING", "")
        return redirect(f"{target}?{query}" if query else target)

    viewer = request.user
    request.user = AnonymousUser()
    try:
        page_state, page_json = _page_state(request, live) if available else ({}, "{}")
    finally:
        request.user = viewer
    context = {
        "live": live,
        "available": available or awaiting,
        "awaiting": awaiting,
        "layout": layout,
        "theme": theme,
        "integration": integration,
        "state": page_state,
        "state_json": page_json,
        "page_url": request.build_absolute_uri(live.get_absolute_url()),
    }
    response = render(request, "live/embed.html", context, status=200 if available or awaiting else 404)
    response["X-Frame-Options"] = "ALLOWALL"
    # A school's frame shows only on the school's sites.
    response["Content-Security-Policy"] = integration.frame_ancestors() if integration else "frame-ancestors *"
    return response


@require_GET
def state(request, uid: str):
    live = _viewable(request, uid)
    try:
        chat_version = int(request.GET.get("cv", -1))
    except (TypeError, ValueError):
        chat_version = -1
    payload = state_payload(
        request,
        live,
        after=_int(request.GET.get("after")),
        client=(request.GET.get("client") or "")[:40],
        chat_version=chat_version,
    )
    response = JsonResponse(payload)
    response["Cache-Control"] = "no-store"
    return response


def _chat_error(error: chat.ChatError) -> JsonResponse:
    return JsonResponse({"detail": str(error)}, status=error.status)


def _message(live: Live, message_id: int) -> ChatMessage:
    return get_object_or_404(
        ChatMessage.objects.select_related("author"), pk=message_id, live=live, is_hidden=False
    )


def _moderated(request, uid: str) -> Live:
    live = _viewable(request, uid)
    if not can_moderate(request, live):
        raise PermissionDenied
    return live


def _flag(request, name: str, default: str = "1") -> bool:
    return request.POST.get(name, default) not in ("0", "false", "")


@require_POST
def chat_post(request, uid: str):
    live = _viewable(request, uid)
    moderator = can_moderate(request, live)
    try:
        message = chat.post(
            live, request.user, request.POST.get("body"), _flag(request, "question", "0"), moderator
        )
    except chat.ChatError as error:
        return _chat_error(error)
    return JsonResponse({"message": chat.message_payload(message, request.user, live, moderator)}, status=201)


@require_POST
def chat_react(request, uid: str, message_id: int):
    """Add (``on=1``) or take back (``on=0``) one's reaction of a ``kind``,
    or one's vote (``kind=up``) on a question."""
    live = _viewable(request, uid)
    if not request.user.is_authenticated:
        return JsonResponse({"detail": _("Sign in to react.")}, status=403)
    message = _message(live, message_id)
    try:
        result = chat.react(live, request.user, message, request.POST.get("kind", ""), _flag(request, "on"))
    except chat.ChatError as error:
        return _chat_error(error)
    return JsonResponse(result)


@require_POST
def chat_hide(request, uid: str, message_id: int):
    live = _viewable(request, uid)
    if not can_moderate(request, live):
        return JsonResponse({"detail": _("You cannot moderate this chat.")}, status=403)
    message = get_object_or_404(ChatMessage, pk=message_id, live=live)
    chat.hide(live, message)
    return JsonResponse({"hidden": message.id})


def _moderation(view):
    """A moderator's action on the chat, answering JSON either way."""

    def wrapper(request, uid, *args, **kwargs):
        live = _viewable(request, uid)
        if not can_moderate(request, live):
            return JsonResponse({"detail": _("You cannot moderate this chat.")}, status=403)
        try:
            return view(request, live, *args, **kwargs)
        except chat.ChatError as error:
            return _chat_error(error)

    wrapper.__name__ = view.__name__
    wrapper.__doc__ = view.__doc__
    return require_POST(wrapper)


@_moderation
def chat_pin(request, live: Live, message_id: int):
    """Pin a message over the chat (``on=1``) or take it down (``on=0``)."""
    message = _message(live, message_id)
    chat.pin(live, message if _flag(request, "on") else None)
    return JsonResponse({"pinned": message.id if _flag(request, "on") else None})


@_moderation
def chat_answer(request, live: Live, message_id: int):
    """Mark a question answered (``on=1``) or open again (``on=0``)."""
    message = _message(live, message_id)
    chat.answer(live, message, _flag(request, "on"))
    return JsonResponse({"answered": _flag(request, "on")})


@_moderation
def chat_mute(request, live: Live, message_id: int):
    """Mute the author of a message for ``minutes`` (5, 15 or 60)."""
    message = _message(live, message_id)
    try:
        minutes = int(request.POST.get("minutes", 5))
    except (TypeError, ValueError):
        minutes = 0
    muted = chat.mute(live, message.author, minutes, request.user if request.user.is_authenticated else None)
    return JsonResponse({"muted": message.author.get_short_name(), "until": muted.until.isoformat()})


@_moderation
def chat_unmute(request, live: Live, mute_id: int):
    chat.unmute(live, mute_id)
    return JsonResponse({"unmuted": mute_id})


@_moderation
def chat_settings(request, live: Live):
    """The chat's slow mode: ``slow_mode`` seconds between two messages."""
    try:
        seconds = int(request.POST.get("slow_mode", 0))
    except (TypeError, ValueError):
        seconds = -1
    chat.set_slow_mode(live, seconds)
    return JsonResponse({"slow_mode": seconds})


# ----------------------------------------------------------------- studio


@login_required
def studio_lives(request):
    mine = Live.objects.filter(owner=request.user).select_related("recording")
    return render(
        request,
        "live/studio_lives.html",
        {
            "lives": mine.order_by("-created_at"),
            "on_air": mine.filter(status__in=Live.ON_AIR).count(),
            "scheduled": mine.filter(status=Live.Status.SCHEDULED).count(),
            "ended": mine.filter(status=Live.Status.ENDED).count(),
        },
    )


@login_required
def live_new(request):
    if not policy.can_upload(request.user):
        raise PermissionDenied
    form = LiveForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        live = form.save(commit=False)
        live.owner = request.user
        live.save()
        messages.success(request, _("Live created. Point your encoder at it to go on air."))
        return redirect("live:control", uid=live.uid)
    return render(request, "live/form.html", {"form": form, "live": None})


@login_required
def control(request, uid: str):
    live = _editable(request, uid)
    page_state, page_json = _page_state(request, live)
    embed_url = request.build_absolute_uri(reverse("live:embed", args=[live.uid]))
    return render(
        request,
        "live/control.html",
        {
            "live": live,
            "state": page_state,
            "state_json": page_json,
            "page_url": request.build_absolute_uri(live.get_absolute_url()),
            "embed_url": embed_url,
            "embed_snippet": _embed_snippet(live, embed_url),
            "is_owner": live.owner_id == request.user.id,
            "ingest_ready": bool(settings.LIVE_HOOK_SECRET),
        },
    )


@login_required
def live_edit(request, uid: str):
    live = _editable(request, uid)
    form = LiveForm(request.POST or None, instance=live)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Live details saved."))
        return redirect("live:control", uid=live.uid)
    return render(request, "live/form.html", {"form": form, "live": live})


@login_required
@require_POST
def live_end(request, uid: str):
    live = _editable(request, uid)
    if services.end(live):
        messages.success(request, _("The live has ended."))
    else:
        messages.info(request, _("This live had already ended."))
    return redirect("live:control", uid=live.uid)


@login_required
@require_POST
def rotate_key(request, uid: str):
    live = _editable(request, uid)
    if live.publishing:
        messages.error(request, _("Stop the encoder before changing the stream key."))
    elif live.is_ended:
        messages.info(request, _("This live has ended; its key no longer works."))
    else:
        live.stream_key = new_stream_key()
        live.save(update_fields=["stream_key", "updated_at"])
        messages.success(request, _("New stream key created. The old one no longer works."))
    return redirect("live:control", uid=live.uid)


@login_required
@require_POST
def live_delete(request, uid: str):
    live = _editable(request, uid)
    if live.is_on_air:
        messages.error(request, _("End the live before deleting it."))
        return redirect("live:control", uid=live.uid)
    title = live.title
    live.delete()
    messages.success(
        request,
        _("“%(title)s” was deleted. Its recording, if there is one, stays in your media.") % {"title": title},
    )
    return redirect("live:studio")


# ----------------------------------------------------- streaming from a browser
#
# The control room records the camera or the screen with MediaRecorder and
# posts one chunk a second; a relay process (relay.py) turns the chunks into an
# RTMP stream to the ingest server, as if an encoder were connected.

CAST_SESSION = re.compile(r"[0-9a-f]{16}")
MAX_CAST_CHUNK_BYTES = 8 * 1024 * 1024


def _cast_root(live: Live) -> Path:
    return Path(settings.LIVE_BROWSER_DIR) / live.playback_id


def _cast_session(live: Live, session: str) -> Path:
    if not CAST_SESSION.fullmatch(session or ""):
        raise Http404("unknown session")
    folder = _cast_root(live) / session
    if not folder.is_dir():
        raise Http404("unknown session")
    return folder


def _active_casts(live: Live) -> list:
    root = _cast_root(live)
    if not root.is_dir():
        return []
    return [
        folder for folder in root.iterdir()
        if folder.is_dir() and not (folder / "end").exists()
        and relay.read_status(folder).get("state") in ("starting", "running")
    ]


@login_required
@require_POST
def cast_start(request, uid: str):
    live = _editable(request, uid)
    if live.is_ended:
        return JsonResponse({"detail": _("This live has ended.")}, status=409)
    if not settings.LIVE_HOOK_SECRET:
        return JsonResponse({"detail": _("Live ingest is not configured on this server.")}, status=503)
    active = _active_casts(live)
    if active:
        # Only one source at a time: the earlier browser stream stops first.
        for folder in active:
            (folder / "end").touch()
        return JsonResponse(
            {"detail": _("The previous stream from a browser is stopping. Try again in a few seconds.")},
            status=409,
        )
    if live.publishing:
        return JsonResponse(
            {"detail": _("An encoder is already streaming to this live. Stop it first.")}, status=409
        )

    session = secrets.token_hex(8)
    folder = _cast_root(live) / session
    folder.mkdir(parents=True)
    target = f"{settings.LIVE_RTMP_INTERNAL_URL.rstrip('/')}/{live.encoder_key}"
    try:
        relay.start(folder, target, cwd=settings.BASE_DIR)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.exception("live %s: the browser relay did not start", live.uid)
        relay.write_status(folder, "failed", reason="failed", error=str(exc)[:300])
        return JsonResponse({"detail": _("The stream could not be started on the server.")}, status=503)
    return JsonResponse(
        {
            "session": session,
            "chunk_url": reverse("live:cast_chunk", args=[live.uid, session, 0]),
            "stop_url": reverse("live:cast_stop", args=[live.uid, session]),
        },
        status=201,
    )


@login_required
@require_POST
def cast_chunk(request, uid: str, session: str, seq: int):
    live = _editable(request, uid)
    folder = _cast_session(live, session)
    state = relay.read_status(folder).get("state", "")
    if state in ("stopped", "failed") or (folder / "end").exists():
        return JsonResponse(
            {"detail": _("The stream from this browser has stopped."), "state": state or "stopped"},
            status=409,
        )
    if seq > 10_000_000:
        return JsonResponse({"detail": "sequence out of range"}, status=400)
    body = request.body
    if not body:
        return JsonResponse({"detail": "empty chunk"}, status=400)
    if len(body) > MAX_CAST_CHUNK_BYTES:
        return JsonResponse({"detail": "chunk too large"}, status=413)
    part = folder / f"{seq:08d}.part"
    part.write_bytes(body)
    part.replace(folder / f"{seq:08d}.chunk")
    return JsonResponse({"state": state or "starting"}, status=202)


@login_required
@require_POST
def cast_stop(request, uid: str, session: str):
    live = _editable(request, uid)
    folder = _cast_session(live, session)
    (folder / "end").touch()
    return JsonResponse({"stopping": True})


@login_required
def live_attendance(request, uid: str):
    """Who watched the live and for how long; ``?format=csv`` for a spreadsheet."""
    live = _editable(request, uid)
    rows = attendance.report(live)
    if request.GET.get("format") == "csv":
        import csv

        from django.http import HttpResponse

        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="attendance-{live.uid}.csv"'
        response.write("\ufeff")  # so spreadsheet programs read the accents right
        writer = csv.writer(response)
        writer.writerow([
            _("Name"), _("Site"), _("Id on the site"), _("Joined at"), _("Last seen at"),
            _("Minutes watched"), _("Messages"),
        ])
        for row in rows:
            writer.writerow([
                row["name"], row["site"], row["site_id"],
                timezone.localtime(row["first_seen"]).strftime("%Y-%m-%d %H:%M"),
                timezone.localtime(row["last_seen"]).strftime("%Y-%m-%d %H:%M"),
                str(row["minutes"]).replace(".", ","), row["messages"],
            ])
        return response
    total = sum(row["seconds"] for row in rows)
    return render(request, "live/attendance.html", {
        "live": live,
        "rows": rows,
        "people": len(rows),
        "average": round(total / len(rows) / 60, 1) if rows else 0,
    })
