"""The live chat: what pages are sent, and what people may do in it.

Every change bumps ``Live.chat_version`` (one UPDATE): a message, a reaction
or vote, a hidden, pinned or answered message, a muted person, the slow mode.
The chat as of a version (the recent messages and their reactions, the pinned
message, the open questions) is built once and kept in the cache, shared by
every page polling the live. What differs per person (their own messages,
reactions and votes, whether they may write or moderate) goes on top. A page
that already has the current version gets an answer without a single query
for the chat, which is what lets a class of hundreds poll a small server.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Q
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.templatetags.akon import avatar
from apps.rbac import permissions as policy

from .models import ChatMessage, ChatMute, ChatReaction, Live

MAX_LENGTH = 500
#: Messages a page that has none gets.
HISTORY = 50
#: Most new messages one answer carries.
BATCH = 100
#: Recent messages a page is kept up to date on (it keeps 300 at most).
WINDOW = 300
#: Open questions listed, most voted first.
QUESTIONS = 40
#: The pause between two messages of one person, slow mode or not.
RATE_SECONDS = 2
#: How long a hidden message keeps being announced so open pages drop it.
HIDDEN_ANNOUNCE = timedelta(minutes=10)
SNAPSHOT_TTL = 600
SLOW_MODES = (0, 5, 10, 30, 60, 120)
MUTE_MINUTES = (5, 15, 60)
#: Reactions and votes one person may add or take back in REACTION_PERIOD seconds.
REACTION_BURST = 20
REACTION_PERIOD = 10
PICTURES = [kind for kind in ChatReaction.EMOJI]


class ChatError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------ helpers


def is_open(live: Live) -> bool:
    return live.chat_enabled and not live.is_ended


def may_write(user, live: Live) -> bool:
    return bool(user and user.is_authenticated and is_open(live))


def bump(live: Live) -> None:
    Live.objects.filter(pk=live.pk).update(chat_version=F("chat_version") + 1)


def _identity(user):
    try:
        return user.external_identity
    except (ObjectDoesNotExist, AttributeError):
        return None


def is_staff(user, live: Live) -> bool:
    """The host, the portal's moderators, and a school's teachers on the
    lives of the school's integration: they get the badge."""
    if user is None or not user.is_authenticated:
        return False
    if user.pk == live.owner_id or policy.can_moderate_comments(user):
        return True
    identity = _identity(user)
    return bool(identity and identity.is_moderator and identity.integration.owner_id == live.owner_id)


def active_mute(live: Live, user):
    if user is None or not user.is_authenticated:
        return None
    return ChatMute.objects.filter(live=live, user=user, until__gt=timezone.now()).first()


def _base(message: ChatMessage, live: Live) -> dict:
    author = message.author
    return {
        "id": message.id,
        "author": author.get_short_name(),
        "avatar": str(avatar(author, "sm")),
        "body": message.body,
        "at": message.created_at.isoformat(),
        "host": message.author_id == live.owner_id,
        "staff": is_staff(author, live),
        "question": message.is_question,
        "answered": message.answered_at is not None,
        "_author": str(message.author_id),
    }


def _visible(live: Live):
    return live.messages.filter(is_hidden=False).select_related(
        "author", "author__external_identity__integration"
    )


# ----------------------------------------------------------------- snapshot


def _build(live: Live) -> dict:
    recent = list(_visible(live).order_by("-id")[:WINDOW])[::-1]
    ids = [m.id for m in recent]
    reactions = defaultdict(list)
    votes = {}
    order = {kind: index for index, kind in enumerate(PICTURES)}
    for row in (
        ChatReaction.objects.filter(message_id__in=ids).values("message_id", "kind").annotate(count=Count("id"))
    ):
        if row["kind"] == ChatReaction.Kind.UP:
            votes[row["message_id"]] = row["count"]
        else:
            reactions[row["message_id"]].append({"kind": row["kind"], "count": row["count"]})
    for items in reactions.values():
        items.sort(key=lambda item: order.get(item["kind"], 99))

    pinned = None
    if live.pinned_message_id:
        message = _visible(live).filter(pk=live.pinned_message_id).first()
        if message is not None:
            pinned = _base(message, live)

    questions = []
    for message in (
        _visible(live)
        .filter(is_question=True, answered_at__isnull=True)
        .annotate(up=Count("reactions", filter=Q(reactions__kind=ChatReaction.Kind.UP)))
        .order_by("-up", "id")[:QUESTIONS]
    ):
        item = _base(message, live)
        item["votes"] = message.up
        questions.append(item)

    hidden = list(
        live.messages.filter(is_hidden=True, hidden_at__gte=timezone.now() - HIDDEN_ANNOUNCE)
        .values_list("id", flat=True)[:200]
    )
    return {
        "version": live.chat_version,
        "messages": [_base(m, live) for m in recent],
        "reactions": dict(reactions),
        "votes": votes,
        "pinned": pinned,
        "questions": questions,
        "hidden": hidden,
    }


def snapshot(live: Live) -> dict:
    key = f"live:chat:{live.pk}:{live.chat_version}"
    snap = cache.get(key)
    if snap is None:
        snap = _build(live)
        cache.set(key, snap, timeout=SNAPSHOT_TTL)
    return snap


# --------------------------------------------------------------- per person


def _mine(user, message_ids) -> set:
    if user is None or not user.is_authenticated or not message_ids:
        return set()
    return set(
        ChatReaction.objects.filter(message_id__in=message_ids, user=user).values_list("message_id", "kind")
    )


def _personal(item: dict, user, moderator: bool, snap: dict, mine: set) -> dict:
    out = {key: value for key, value in item.items() if not key.startswith("_")}
    mid = item["id"]
    out["mine"] = bool(user and user.is_authenticated and item["_author"] == str(user.pk))
    out["can_hide"] = moderator
    out["reactions"] = [
        {"kind": r["kind"], "count": r["count"], "mine": (mid, r["kind"]) in mine}
        for r in snap["reactions"].get(mid, [])
    ]
    if item.get("question"):
        out["votes"] = item.get("votes", snap["votes"].get(mid, 0))
        out["voted"] = (mid, ChatReaction.Kind.UP) in mine
    return out


def payload(request, live: Live, moderator: bool, after: int = 0, version: int = -1) -> dict:
    """The chat for a page that has every message up to ``after`` and the
    chat as of ``version`` (-1: nothing yet)."""
    user = request.user
    out = {
        "enabled": live.chat_enabled,
        "open": is_open(live),
        "can_post": may_write(user, live),
        "can_react": may_write(user, live),
        "can_moderate": moderator,
        "slow_mode": live.slow_mode_seconds,
        "version": live.chat_version,
    }
    if after > 0 and version == live.chat_version:
        out["unchanged"] = True
        return out

    mute = active_mute(live, user)
    if mute is not None:
        out["can_post"] = False
        out["muted_until"] = mute.until.isoformat()

    snap = snapshot(live)
    messages = snap["messages"]
    batch = [m for m in messages if m["id"] > after][-BATCH:] if after > 0 else messages[-HISTORY:]
    shown_ids = [m["id"] for m in messages] + [q["id"] for q in snap["questions"]]
    if snap["pinned"]:
        shown_ids.append(snap["pinned"]["id"])
    mine = _mine(user, shown_ids)

    out["messages"] = [_personal(m, user, moderator, snap, mine) for m in batch]
    out["hidden"] = snap["hidden"] if after > 0 else []
    if after > 0 and messages:
        # Every shown message from reactions_from on takes these reactions,
        # and one missing from the map has none left.
        out["reactions"] = {
            str(m["id"]): _personal(m, user, moderator, snap, mine)["reactions"]
            for m in messages
            if m["id"] <= after and m["id"] in snap["reactions"]
        }
        # ...and the questions their votes, and whether they were answered since.
        out["votes"] = {
            str(m["id"]): {
                "votes": snap["votes"].get(m["id"], 0),
                "voted": (m["id"], ChatReaction.Kind.UP) in mine,
                "answered": m["answered"],
            }
            for m in messages
            if m["id"] <= after and m["question"]
        }
        out["reactions_from"] = messages[0]["id"]
    out["pinned"] = _personal(snap["pinned"], user, moderator, snap, mine) if snap["pinned"] else None
    out["questions"] = [_personal(q, user, moderator, snap, mine) for q in snap["questions"]]
    if moderator:
        out["muted"] = [
            {"id": m.id, "name": m.user.get_short_name(), "until": m.until.isoformat()}
            for m in ChatMute.objects.filter(live=live, until__gt=timezone.now()).select_related("user")[:50]
        ]
    return out


def message_payload(message: ChatMessage, user, live: Live, moderator: bool) -> dict:
    """One message as its author's page shows it right after sending."""
    item = _base(message, live)
    if message.is_question:
        item["votes"] = 0
    snap = {"reactions": {}, "votes": {}}
    return _personal(item, user, moderator, snap, set())


# ------------------------------------------------------------------ actions


def post(live: Live, user, body: str, question: bool = False, moderator: bool = False) -> ChatMessage:
    if not user.is_authenticated:
        raise ChatError(_("Sign in to take part in the chat."), 403)
    if not is_open(live):
        raise ChatError(_("The chat is closed."), 403)
    mute = active_mute(live, user)
    if mute is not None:
        raise ChatError(
            _("You were muted until %(time)s.") % {"time": timezone.localtime(mute.until).strftime("%H:%M")}, 403
        )
    body = (body or "").strip()
    if not body:
        raise ChatError(_("Write a message first."))
    if len(body) > MAX_LENGTH:
        raise ChatError(_("Messages are limited to %(limit)d characters.") % {"limit": MAX_LENGTH})
    wait = RATE_SECONDS if moderator else max(RATE_SECONDS, live.slow_mode_seconds)
    if not cache.add(f"live:chat:{user.pk}", 1, timeout=wait):
        if wait > RATE_SECONDS:
            raise ChatError(
                _("Slow mode is on: one message every %(seconds)d seconds.") % {"seconds": wait}, 429
            )
        raise ChatError(_("Slow down a little: one message every two seconds."), 429)
    with transaction.atomic():
        message = ChatMessage.objects.create(live=live, author=user, body=body, is_question=bool(question))
        bump(live)
    return message


def _throttle_reactions(user) -> None:
    key = f"live:react:{user.pk}"
    cache.add(key, 0, timeout=REACTION_PERIOD)
    try:
        used = cache.incr(key)
    except ValueError:  # expired between the two calls
        cache.set(key, 1, timeout=REACTION_PERIOD)
        used = 1
    if used > REACTION_BURST:
        raise ChatError(_("Slow down a little with the reactions."), 429)


def react(live: Live, user, message: ChatMessage, kind: str, wanted: bool) -> dict:
    """Add or take back a reaction (or a vote, on a question). Stating the
    wanted end, rather than toggling, makes a double click harmless."""
    if not user.is_authenticated:
        raise ChatError(_("Sign in to react."), 403)
    if not is_open(live):
        raise ChatError(_("The chat is closed."), 403)
    if kind not in ChatReaction.Kind.values:
        raise ChatError(_("Unknown reaction."))
    if kind == ChatReaction.Kind.UP and not message.is_question:
        raise ChatError(_("Only questions take votes."))
    _throttle_reactions(user)
    with transaction.atomic():
        if wanted:
            try:
                with transaction.atomic():
                    changed = ChatReaction.objects.get_or_create(message=message, user=user, kind=kind)[1]
            except IntegrityError:  # the same request, twice at once
                changed = False
        else:
            changed = ChatReaction.objects.filter(message=message, user=user, kind=kind).delete()[0] > 0
        if changed:
            bump(live)
    return summary(message, user)


def summary(message: ChatMessage, user) -> dict:
    order = {kind: index for index, kind in enumerate(PICTURES)}
    rows = ChatReaction.objects.filter(message=message).values("kind").annotate(count=Count("id"))
    mine = set(ChatReaction.objects.filter(message=message, user=user).values_list("kind", flat=True)) \
        if user.is_authenticated else set()
    reactions = sorted(
        [{"kind": r["kind"], "count": r["count"], "mine": r["kind"] in mine}
         for r in rows if r["kind"] != ChatReaction.Kind.UP],
        key=lambda item: order.get(item["kind"], 99),
    )
    votes = next((r["count"] for r in rows if r["kind"] == ChatReaction.Kind.UP), 0)
    return {"id": message.id, "reactions": reactions, "votes": votes, "voted": ChatReaction.Kind.UP in mine}


def hide(live: Live, message: ChatMessage) -> None:
    with transaction.atomic():
        if not message.is_hidden:
            message.is_hidden = True
            message.hidden_at = timezone.now()
            message.save(update_fields=["is_hidden", "hidden_at"])
        if live.pinned_message_id == message.id:
            Live.objects.filter(pk=live.pk).update(pinned_message=None)
        bump(live)


def pin(live: Live, message: ChatMessage | None) -> None:
    with transaction.atomic():
        Live.objects.filter(pk=live.pk).update(pinned_message=message)
        bump(live)


def answer(live: Live, message: ChatMessage, done: bool) -> None:
    if not message.is_question:
        raise ChatError(_("Only questions can be answered."))
    with transaction.atomic():
        message.answered_at = timezone.now() if done else None
        message.save(update_fields=["answered_at"])
        bump(live)


def mute(live: Live, target, minutes: int, by) -> ChatMute:
    if minutes not in MUTE_MINUTES:
        raise ChatError(_("Pick how long: 5, 15 or 60 minutes."))
    if is_staff(target, live):
        raise ChatError(_("The host and moderators cannot be muted."), 403)
    with transaction.atomic():
        muted, _created = ChatMute.objects.update_or_create(
            live=live, user=target,
            defaults={"until": timezone.now() + timedelta(minutes=minutes), "created_by": by},
        )
        bump(live)
    return muted


def unmute(live: Live, mute_id: int) -> None:
    with transaction.atomic():
        ChatMute.objects.filter(live=live, pk=mute_id).delete()
        bump(live)


def set_slow_mode(live: Live, seconds: int) -> None:
    if seconds not in SLOW_MODES:
        raise ChatError(_("Pick one of the slow mode options."))
    with transaction.atomic():
        Live.objects.filter(pk=live.pk).update(slow_mode_seconds=seconds)
        bump(live)
