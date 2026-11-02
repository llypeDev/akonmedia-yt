"""Values every template needs. Registered in ``TEMPLATES``."""
from django.conf import settings


def portal(request):
    return {
        "PORTAL_NAME": settings.PORTAL_NAME,
        "PORTAL_DESCRIPTION": settings.PORTAL_DESCRIPTION,
    }
