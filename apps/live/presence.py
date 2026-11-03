"""Who is watching right now, counted in the cache.

Every viewer's page polls the live's state every few seconds with a random
client id. Each client is counted once per ten-second bucket, and the count is
the larger of the current and the previous bucket, so the number does not dip
at every bucket boundary. Approximate on purpose: nothing is stored per viewer.
"""
import re

from django.core.cache import cache
from django.utils import timezone

from .models import Live

BUCKET_SECONDS = 10
CLIENT_ID = re.compile(r"[A-Za-z0-9_-]{8,40}")


def _bucket() -> int:
    return int(timezone.now().timestamp()) // BUCKET_SECONDS


def _counter(live: Live, bucket: int) -> str:
    return f"live:{live.pk}:viewers:{bucket}"


def touch(live: Live, client: str) -> None:
    if not client or not CLIENT_ID.fullmatch(client):
        return
    bucket = _bucket()
    ttl = BUCKET_SECONDS * 3
    if not cache.add(f"live:{live.pk}:seen:{bucket}:{client}", 1, timeout=ttl):
        return
    counter = _counter(live, bucket)
    cache.add(counter, 0, timeout=ttl)
    try:
        cache.incr(counter)
    except ValueError:
        cache.set(counter, 1, timeout=ttl)


def count(live: Live) -> int:
    bucket = _bucket()
    current = cache.get(_counter(live, bucket)) or 0
    previous = cache.get(_counter(live, bucket - 1)) or 0
    viewers = max(current, previous)
    if viewers > live.peak_viewers:
        Live.objects.filter(pk=live.pk, peak_viewers__lt=viewers).update(peak_viewers=viewers)
        live.peak_viewers = viewers
    return viewers
