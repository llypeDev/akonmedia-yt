"""Root URL configuration."""
import re

from django.conf import settings
from django.contrib import admin
from django.http import HttpResponse
from django.shortcuts import redirect
from django.templatetags.static import static
from django.urls import include, path, re_path
from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import cache_control
from django.views.i18n import JavaScriptCatalog

from apps.media.serving import serve_media

admin.site.site_header = format_lazy(_("{portal} administration"), portal=settings.PORTAL_NAME)
admin.site.site_title = settings.PORTAL_NAME
admin.site.index_title = _("Operations")


@cache_control(max_age=86400, public=True)
def favicon(request):
    """Browsers ask for /favicon.ico regardless of the <link> tags."""
    return redirect(static("brand/favicon.ico"), permanent=True)


@cache_control(max_age=3600, public=True)
def robots(request):
    lines = [
        "User-agent: *",
        "Disallow: /admin/",
        "Disallow: /api/",
        "Disallow: /manage/",
        "Disallow: /studio/",
        "Disallow: /account/",
        "Disallow: /accounts/",
        "Disallow: /embed/",
        "Allow: /",
    ]
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain; charset=utf-8")


urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("api/v1/", include("apps.users.urls")),
    path("api/v1/", include("apps.media.urls")),
    path("api/v1/", include("apps.uploader.urls")),
    path("favicon.ico", favicon),
    path("robots.txt", robots),
    # Language switcher (set_language) and the catalog the scripts translate with.
    path("i18n/", include("django.conf.urls.i18n")),
    path("jsi18n/", JavaScriptCatalog.as_view(packages=["apps.core"]), name="javascript-catalog"),
    path("", include("apps.integrations.urls")),
    path("", include("apps.live.urls")),
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
