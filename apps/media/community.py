"""Viewsets for the surrounding objects: comments, playlists, categories."""
from django.db.models import Q
from rest_framework import permissions, viewsets

from apps.rbac import permissions as policy

from .models import Category, Comment, Media, Playlist
from .serializers import CategorySerializer, CommentSerializer, PlaylistSerializer


class CommentViewSet(viewsets.ModelViewSet):
    serializer_class = CommentSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        queryset = Comment.objects.select_related("author", "media")
        user = self.request.user
        if not (user.is_authenticated and policy.can_moderate_comments(user)):
            queryset = queryset.filter(is_hidden=False)

        media_uid = self.request.query_params.get("media")
        if media_uid:
            queryset = queryset.filter(media__uid=media_uid)

        return queryset

    def perform_create(self, serializer):
        media = serializer.validated_data["media"]
        if media.state != Media.State.READY:
            raise permissions.PermissionDenied("That media is not published yet.")
        serializer.save(author=self.request.user)

    def perform_destroy(self, instance):
        user = self.request.user
        if instance.author_id != user.id and not policy.can_moderate_comments(user):
            raise permissions.PermissionDenied("Not your comment.")
        instance.delete()


class PlaylistViewSet(viewsets.ModelViewSet):
    serializer_class = PlaylistSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        queryset = Playlist.objects.select_related("owner")
        user = self.request.user
        if user.is_authenticated and policy.can_curate_others(user):
            return queryset

        public = Q(visibility=Media.Visibility.PUBLIC)
        if user.is_authenticated:
            return queryset.filter(Q(owner=user) | public)
        return queryset.filter(public)

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)


class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = CategorySerializer
    queryset = Category.objects.all()
    pagination_class = None
