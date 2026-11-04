from django.urls import path

from . import hooks, stream, views

app_name = "live"

urlpatterns = [
    # callbacks from the ingest server; the secret is checked in the view
    path("live/hooks/<str:secret>/<str:event>/", hooks.callback, name="hook"),
    # viewers
    path("lives/", views.lives, name="lives"),
    path("live/<str:uid>/", views.watch, name="watch"),
    path("live/<str:uid>/state/", views.state, name="state"),
    # pushed news for open pages; served by the ASGI "events" service (stream.py)
    path("live/<str:uid>/events/", stream.events, name="events"),
    path("live/<str:uid>/chat/", views.chat_post, name="chat"),
    path("live/<str:uid>/chat/<int:message_id>/hide/", views.chat_hide, name="chat_hide"),
    path("live/<str:uid>/chat/<int:message_id>/react/", views.chat_react, name="chat_react"),
    path("live/<str:uid>/chat/<int:message_id>/pin/", views.chat_pin, name="chat_pin"),
    path("live/<str:uid>/chat/<int:message_id>/answer/", views.chat_answer, name="chat_answer"),
    path("live/<str:uid>/chat/<int:message_id>/mute/", views.chat_mute, name="chat_mute"),
    path("live/<str:uid>/chat/unmute/<int:mute_id>/", views.chat_unmute, name="chat_unmute"),
    path("live/<str:uid>/chat/settings/", views.chat_settings, name="chat_settings"),
    path("embed/live/<str:uid>/", views.embed, name="embed"),
    # creators
    path("studio/lives/", views.studio_lives, name="studio"),
    path("studio/lives/new/", views.live_new, name="new"),
    path("studio/lives/<str:uid>/", views.control, name="control"),
    path("studio/lives/<str:uid>/edit/", views.live_edit, name="edit"),
    path("studio/lives/<str:uid>/end/", views.live_end, name="end"),
    path("studio/lives/<str:uid>/key/", views.rotate_key, name="rotate_key"),
    path("studio/lives/<str:uid>/delete/", views.live_delete, name="delete"),
    path("studio/lives/<str:uid>/attendance/", views.live_attendance, name="attendance"),
    # streaming from the browser
    path("studio/lives/<str:uid>/cast/", views.cast_start, name="cast_start"),
    path("studio/lives/<str:uid>/cast/<str:session>/<int:seq>/", views.cast_chunk, name="cast_chunk"),
    path("studio/lives/<str:uid>/cast/<str:session>/stop/", views.cast_stop, name="cast_stop"),
]
