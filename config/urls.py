"""Root URL configuration."""
from django.conf import settings
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = f"{settings.PORTAL_NAME} administration"
admin.site.site_title = settings.PORTAL_NAME
admin.site.index_title = "Operations"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("api/v1/", include("apps.users.urls")),
    path("", include("apps.core.urls")),
]
