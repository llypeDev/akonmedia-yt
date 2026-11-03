"""Viewsets for the surrounding objects: comments, playlists, categories."""
from django.db.models import Q
from django.utils.translation import gettext as _
from rest_framework import permissions, serializers, viewsets
from rest_framework.exceptions import PermissionDenied

from apps.rbac import permissions as policy

from . import access
from .models import Category, Comment, Media, Playlist
from .serializers import CategorySerializer, CommentSerializer, PlaylistSerializer


class IsAuthorOrModerator(permissions.BasePermission):
    """Anyone may read; only the author or a moderator may change a comment."""

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        user = request.user
        if not user or not user.is_authenticated:
            return False
        return obj.author_id == user.id or policy.can_moderate_comments(user)


class IsPlaylistOwnerOrCurator(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        return access.can_edit_playlist(request.user, obj)


class CommentViewSet(viewsets.ModelViewSet):
    serializer_class = CommentSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsAuthorOrModerator]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        user = self.request.user
        # Comments are only as visible as the media they belong to.
        queryset = Comment.objects.select_related("author", "media").filter(
            access.viewable_q(user, prefix="media__")
        )
        if not (user.is_authenticated and policy.can_moderate_comments(user)):
            queryset = queryset.filter(is_hidden=False)

        media_uid = self.request.query_params.get("media")
        if media_uid:
            queryset = queryset.filter(media__uid=media_uid)

        return queryset

    def perform_create(self, serializer):
        media = serializer.validated_data["media"]
        if media.state != Media.State.READY:
            raise PermissionDenied(_("That media is not published yet."))
        if not access.can_view_media(self.request.user, media):
            raise PermissionDenied(_("That media is not available."))
        parent = serializer.validated_data.get("parent")
        if parent is not None and parent.media_id != media.pk:
            raise serializers.ValidationError({"parent": _("Reply to a comment on the same media.")})
        serializer.save(author=self.request.user)

    def perform_update(self, serializer):
        # Moving a comment to another media item or thread is not an edit.
        serializer.validated_data.pop("media", None)
        serializer.validated_data.pop("parent", None)
        serializer.save()

    def perform_destroy(self, instance):
        user = self.request.user
        if instance.author_id != user.id and not policy.can_moderate_comments(user):
            raise PermissionDenied(_("Not your comment."))
        instance.delete()


class PlaylistViewSet(viewsets.ModelViewSet):
    serializer_class = PlaylistSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsPlaylistOwnerOrCurator]

    def get_queryset(self):
        queryset = Playlist.objects.select_related("owner")
        user = self.request.user
        if user.is_authenticated and policy.can_curate_others(user):
            return queryset

        # Listings show public playlists; a direct link also opens unlisted ones.
        shown = Q(visibility=Media.Visibility.PUBLIC)
        if self.action != "list":
            shown |= Q(visibility=Media.Visibility.UNLISTED)
        if user.is_authenticated:
            return queryset.filter(Q(owner=user) | shown)
        return queryset.filter(shown)

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)


class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = CategorySerializer
    queryset = Category.objects.all()
    pagination_class = None
