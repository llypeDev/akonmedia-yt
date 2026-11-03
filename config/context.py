"""Values every template needs. Registered in ``TEMPLATES``."""
from django.conf import settings
from django.utils.functional import SimpleLazyObject

from apps.rbac import permissions as policy


def _categories():
    from apps.media.models import Category

    return list(Category.objects.only("name", "slug").order_by("name")[:12])


def _live_on_air() -> bool:
    """Whether any public live is on air, for the dot next to "Live" in the
    sidebar. Cached briefly: every page shows it."""
    from django.core.cache import cache

    from apps.live.models import Live
    from apps.media import access

    return cache.get_or_set(
        "live:any-on-air",
        lambda: Live.objects.filter(access.live_listed_q(), status__in=Live.ON_AIR).exists(),
        10,
    )


def portal(request):
    user = getattr(request, "user", None)
    match = getattr(request, "resolver_match", None)
    return {
        "PORTAL_NAME": settings.PORTAL_NAME,
        "PORTAL_DESCRIPTION": settings.PORTAL_DESCRIPTION,
        "CAN_CURATE": policy.can_curate_others(user),
        "CAN_MANAGE_USERS": policy.can_manage_users(user),
        "CAN_UPLOAD": policy.can_upload(user),
        "NAV": match.url_name if match else "",
        "NAV_NS": match.namespace if match else "",
        "LIVE_ON_AIR": SimpleLazyObject(_live_on_air),
        # Only queried when a template actually iterates it.
        "NAV_CATEGORIES": SimpleLazyObject(_categories),
    }
