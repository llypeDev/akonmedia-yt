"""Values every template needs. Registered in ``TEMPLATES``."""
from django.conf import settings

from apps.rbac import permissions as policy


def portal(request):
    user = getattr(request, "user", None)
    return {
        "PORTAL_NAME": settings.PORTAL_NAME,
        "PORTAL_DESCRIPTION": settings.PORTAL_DESCRIPTION,
        "CAN_CURATE": policy.can_curate_others(user),
        "CAN_MANAGE_USERS": policy.can_manage_users(user),
    }
