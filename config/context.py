"""Values every template needs. Registered in ``TEMPLATES``."""
from django.conf import settings
from django.utils.functional import SimpleLazyObject

from apps.rbac import permissions as policy


def _categories():
    from apps.media.models import Category

    return list(Category.objects.only("name", "slug").order_by("name")[:12])


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
        # Only queried when a template actually iterates it.
        "NAV_CATEGORIES": SimpleLazyObject(_categories),
    }
