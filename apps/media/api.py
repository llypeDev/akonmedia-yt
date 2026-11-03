"""Media API: read for everyone, write for signed-in members."""
import mimetypes

from django.db.models import F, Q
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.rbac import permissions as policy

from .models import Media
from .serializers import MediaCreateSerializer, MediaSerializer, MediaUpdateSerializer


class IsOwnerOrCurator(permissions.BasePermission):
    """Safe methods are open; writes require ownership or curation rights."""

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        if not request.user or not request.user.is_authenticated:
            return False
        return obj.owner_id == request.user.id or policy.can_curate_others(request.user)


def kind_for(filename: str, content_type: str = "") -> str:
    mime = content_type or mimetypes.guess_type(filename)[0] or ""
    if mime.startswith("image/"):
        return Media.Kind.IMAGE
    if mime.startswith("audio/"):
        return Media.Kind.AUDIO
    if mime == "application/pdf":
        return Media.Kind.DOCUMENT
    return Media.Kind.VIDEO


class MediaViewSet(viewsets.ModelViewSet):
    serializer_class = MediaSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsOwnerOrCurator]
    lookup_field = "uid"

    def get_queryset(self):
        queryset = Media.objects.select_related("owner", "category")
        user = self.request.user

        published = Q(visibility=Media.Visibility.PUBLIC, state=Media.State.READY)
        if self.action != "list":
            # Unlisted means "anyone with the link": fetchable, never listed.
            published |= Q(visibility=Media.Visibility.UNLISTED, state=Media.State.READY)

        if user.is_authenticated:
            if not policy.can_curate_others(user):
                queryset = queryset.filter(Q(owner=user) | published)
        else:
            queryset = queryset.filter(published)

        kind = self.request.query_params.get("kind")
        if kind:
            queryset = queryset.filter(kind=kind)

        category = self.request.query_params.get("category")
        if category:
            queryset = queryset.filter(category_id=category)

        return queryset.distinct()

    def get_serializer_class(self):
        if self.action == "create":
            return MediaCreateSerializer
        if self.action in {"update", "partial_update"}:
            return MediaUpdateSerializer
        return MediaSerializer

    def perform_create(self, serializer):
        upload = serializer.validated_data["file"]
        media = serializer.save(
            owner=self.request.user,
            original_name=upload.name,
            size_bytes=upload.size,
            kind=kind_for(upload.name, getattr(upload, "content_type", "")),
        )
        from .tasks import process_media

        process_media.delay(str(media.pk))
        return media

    def create(self, request, *args, **kwargs):
        """Accept the upload, then answer with the full representation.

        The create serializer only carries what a client may send; callers still
        need the uid and the initial state to follow processing.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        media = self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(
            MediaSerializer(media, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
            headers=headers,
        )

    @action(detail=True, methods=["post"], permission_classes=[permissions.AllowAny])
    def view(self, request, uid=None):
        """Count a playback. Cheap enough to run inline rather than on the queue."""
        media = self.get_object()
        Media.objects.filter(pk=media.pk).update(views_count=F("views_count") + 1)
        return Response({"status": "counted", "views": media.views_count + 1})
