from django.urls import path

from . import manage_views, playlist_views, subtitle_views, views

app_name = "core"

urlpatterns = [
    path("", views.index, name="index"),
    path("view/<str:uid>/", views.media_detail, name="media"),
    path("embed/<str:uid>/", views.embed, name="embed"),
    path("search/", views.search, name="search"),
    path("upload/", views.upload, name="upload"),
    path("health/", views.health, name="health"),
    # subtitles
    path("view/<str:uid>/subtitles/", subtitle_views.subtitle_upload, name="subtitle_upload"),
    path(
        "view/<str:uid>/subtitles/<str:language>/",
        subtitle_views.subtitle_editor,
        name="subtitle_editor",
    ),
    path(
        "view/<str:uid>/subtitles/<str:language>/delete/",
        subtitle_views.subtitle_delete,
        name="subtitle_delete",
    ),
    path(
        "view/<str:uid>/subtitles/<str:language>/track.vtt",
        subtitle_views.subtitle_vtt,
        name="subtitle_vtt",
    ),
    # playlists
    path("playlists/", playlist_views.playlists, name="playlists"),
    path("playlists/<str:uid>/", playlist_views.playlist_detail, name="playlist"),
    path("playlists/<str:uid>/edit/", playlist_views.playlist_edit, name="playlist_edit"),
    # curation
    path("manage/", manage_views.manage, name="manage"),
    path("manage/action/", manage_views.manage_action, name="manage_action"),
]
