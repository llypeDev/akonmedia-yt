from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .api import MediaViewSet
from .community import CategoryViewSet, CommentViewSet, PlaylistViewSet

app_name = "media"

router = DefaultRouter()
router.register("media", MediaViewSet, basename="media")
router.register("comments", CommentViewSet, basename="comment")
router.register("playlists", PlaylistViewSet, basename="playlist")
router.register("categories", CategoryViewSet, basename="category")

urlpatterns = [
    path("", include(router.urls)),
]
