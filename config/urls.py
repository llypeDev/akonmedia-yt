"""Root URL configuration."""
import re

from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path

from apps.media.serving import serve_media

admin.site.site_header = f"{settings.PORTAL_NAME} administration"
admin.site.site_title = settings.PORTAL_NAME
admin.site.index_title = "Operations"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("api/v1/", include("apps.users.urls")),
    path("api/v1/", include("apps.media.urls")),
    path("api/v1/", include("apps.uploader.urls")),
    path("", include("apps.core.urls")),
]

# Uploaded media. Always routed here because this view answers Range requests,
# which Django's DEBUG-only static helper does not. Behind nginx or a CDN the
# request never reaches the application, so the cost is zero.
urlpatterns += [
    re_path(
        rf"^{re.escape(settings.MEDIA_URL.lstrip('/'))}(?P<path>.*)$",
        serve_media,
        name="media-file",
    )
]
