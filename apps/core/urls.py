from django.urls import path

from . import manage_views, playlist_views, studio_views, subtitle_views, views

app_name = "core"

urlpatterns = [
    path("", views.index, name="index"),
    path("view/<str:uid>/", views.media_detail, name="media"),
    path("view/<str:uid>/edit/", studio_views.media_edit, name="media_edit"),
    path("view/<str:uid>/delete/", studio_views.media_delete, name="media_delete"),
    path("view/<str:uid>/reprocess/", studio_views.media_reprocess, name="media_reprocess"),
    path("view/<str:uid>/playlists/", views.add_to_playlist, name="add_to_playlist"),
    path("embed/<str:uid>/", views.embed, name="embed"),
    path("search/", views.search, name="search"),
    path("upload/", views.upload, name="upload"),
    path("channel/<uuid:user_id>/", views.channel, name="channel"),
    path("comments/<uuid:comment_id>/action/", views.comment_action, name="comment_action"),
    path("health/", views.health, name="health"),
    path("health/ready/", views.ready, name="ready"),
    # creator
    path("studio/", studio_views.studio, name="studio"),
    path("account/", studio_views.account, name="account"),
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
    path(
        "view/<str:uid>/subtitles/<str:language>/track.srt",
        subtitle_views.subtitle_srt,
        name="subtitle_srt",
    ),
    # playlists
    path("playlists/", playlist_views.playlists, name="playlists"),
    path("playlists/<str:uid>/", playlist_views.playlist_detail, name="playlist"),
    path("playlists/<str:uid>/edit/", playlist_views.playlist_edit, name="playlist_edit"),
    # curation
    path("manage/", manage_views.manage, name="manage"),
    path("manage/action/", manage_views.manage_action, name="manage_action"),
]
