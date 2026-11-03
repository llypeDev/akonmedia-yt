from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.index, name="index"),
    path("view/<str:uid>/", views.media_detail, name="media"),
    path("embed/<str:uid>/", views.embed, name="embed"),
    path("search/", views.search, name="search"),
    path("upload/", views.upload, name="upload"),
    path("health/", views.health, name="health"),
]
