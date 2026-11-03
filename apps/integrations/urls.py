from django.urls import path

from apps.live import views as live_views

from . import views
from .sessions import embed_api

app_name = "integrations"

urlpatterns = [
    # the frame on a site: its script, its sign-in, and the live's state and
    # chat with the session in a header (see sessions.py)
    path("embed/sdk.js", views.sdk, name="sdk"),
    path("embed/api/session/", views.session, name="session"),
    path("embed/api/live/<str:uid>/state/", embed_api(live_views.state), name="live_state"),
    path("embed/api/live/<str:uid>/chat/", embed_api(live_views.chat_post), name="live_chat"),
    path(
        "embed/api/live/<str:uid>/chat/<int:message_id>/react/",
        embed_api(live_views.chat_react),
        name="live_react",
    ),
    path(
        "embed/api/live/<str:uid>/chat/<int:message_id>/hide/",
        embed_api(live_views.chat_hide),
        name="live_hide",
    ),
    # the studio
    path("studio/integrations/", views.studio_list, name="list"),
    path("studio/integrations/<uuid:pk>/", views.studio_detail, name="detail"),
    path("studio/integrations/<uuid:pk>/rotate/", views.rotate, name="rotate"),
    path("studio/integrations/<uuid:pk>/delete/", views.delete, name="delete"),
    path("studio/integrations/<uuid:pk>/preview/", views.preview, name="preview"),
]
