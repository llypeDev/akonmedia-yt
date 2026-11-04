"""Who watched a live, and for how long: the school's attendance list.

Every poll of a signed-in viewer while the live is on air counts the time
since their previous poll (gaps longer than GAP mean they left in between and
are not counted). The seconds gather in the cache and reach the database every
FLUSH seconds per person, so a class of hundreds costs a handful of writes a
second instead of one per poll.
"""
from __future__ import annotations

import time

from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Count, F
from django.utils import timezone

from .models import LiveAttendance

#: A pause between two polls longer than this is time away, not watching.
GAP = 45
#: Seconds gathered before they are written.
FLUSH = 30
TTL = 6 * 3600


def touch(live, user, now: float | None = None) -> None:
    if user is None or not user.is_authenticated:
        return
    now = time.time() if now is None else now
    key = f"live:att:{live.pk}:{user.pk}"
    state = cache.get(key)
    if state is None:
        LiveAttendance.objects.get_or_create(live=live, user=user)
        cache.set(key, {"last": now, "pending": 0.0}, timeout=TTL)
        return
    gap = now - state["last"]
    pending = state["pending"] + (gap if 0 < gap <= GAP else 0)
    if pending >= FLUSH:
        updated = LiveAttendance.objects.filter(live=live, user=user).update(
            seconds=F("seconds") + int(pending), last_seen=timezone.now()
        )
        if not updated:
            LiveAttendance.objects.create(live=live, user=user, seconds=int(pending))
        pending -= int(pending)
    cache.set(key, {"last": now, "pending": pending}, timeout=TTL)


def report(live):
    """Everyone who watched, most minutes first, with their messages counted
    and, for people of a school site, their id there."""
    rows = list(
        LiveAttendance.objects.filter(live=live)
        .select_related("user", "user__external_identity__integration")
        .order_by("-seconds", "first_seen")
    )
    counts = dict(live.messages.values_list("author_id").annotate(n=Count("id")))
    result = []
    for row in rows:
        try:
            identity = row.user.external_identity
        except ObjectDoesNotExist:  # a QQplay account
            identity = None
        result.append({
            "name": row.user.get_short_name(),
            "site": identity.integration.name if identity else "",
            "site_id": identity.external_id if identity else "",
            "integration_id": str(identity.integration_id) if identity else "",
            "first_seen": row.first_seen,
            "last_seen": row.last_seen,
            "minutes": round(row.seconds / 60, 1),
            "seconds": row.seconds,
            "messages": counts.get(row.user_id, 0),
        })
    return result
