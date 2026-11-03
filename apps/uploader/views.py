"""Chunked upload endpoints.

    POST   /api/v1/uploads/                 start a session
    GET    /api/v1/uploads/<id>/            where did we get to? (used to resume)
    DELETE /api/v1/uploads/<id>/            abandon the session and its bytes
    PUT    /api/v1/uploads/<id>/chunk/?index=N
    POST   /api/v1/uploads/<id>/complete/   turn the bytes into a media item
"""
from django.core.files import File
from django.db import transaction
from django.http import Http404
from django.utils.translation import gettext as _
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.media.api import kind_for
from apps.media.models import Category, Media

from .models import UploadSession

MAX_CHUNK = 32 * 1024 * 1024
DEFAULT_CHUNK = 5 * 1024 * 1024


class UploadSessionSerializer(serializers.ModelSerializer):
    expected_chunks = serializers.IntegerField(read_only=True)
    progress = serializers.FloatField(read_only=True)

    class Meta:
        model = UploadSession
        fields = [
            "id", "filename", "content_type", "total_size", "chunk_size",
            "next_index", "received_bytes", "expected_chunks", "progress",
            "completed", "media_uid", "created_at",
        ]
        read_only_fields = fields


class StartUploadSerializer(serializers.Serializer):
    filename = serializers.CharField(max_length=255)
    total_size = serializers.IntegerField(min_value=1)
    chunk_size = serializers.IntegerField(required=False, default=DEFAULT_CHUNK)
    content_type = serializers.CharField(required=False, allow_blank=True, default="")


class StartUploadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        payload = StartUploadSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        if not 1024 * 1024 <= data["chunk_size"] <= MAX_CHUNK:
            return Response({"detail": _("chunk_size out of range")}, status=400)

        session = UploadSession.objects.create(
            owner=request.user,
            filename=data["filename"][:255],
            content_type=data["content_type"][:120],
            total_size=data["total_size"],
            chunk_size=data["chunk_size"],
        )
        return Response(UploadSessionSerializer(session).data, status=status.HTTP_201_CREATED)


class UploadSessionView(APIView):
    """Report a session so a browser can resume it, or abandon it."""

    permission_classes = [IsAuthenticated]

    def get(self, request, session_id):
        session = _session_for(request, session_id)
        # The part file is the truth: if bytes were lost, start that chunk again.
        on_disk = session.bytes_on_disk()
        if not session.completed and on_disk != session.received_bytes:
            session.realign(on_disk)
        return Response(UploadSessionSerializer(session).data)

    def delete(self, request, session_id):
        session = _session_for(request, session_id)
        if session.completed:
            return Response({"detail": _("This upload was already completed.")}, status=400)
        session.discard_part()
        session.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ChunkUploadView(APIView):
    permission_classes = [IsAuthenticated]

    def put(self, request, session_id):
        session = _session_for(request, session_id)
        if session.completed:
            return Response({"detail": _("This upload was already completed.")}, status=400)

        try:
            index = int(request.query_params.get("index", ""))
        except (TypeError, ValueError):
            return Response({"detail": _("The index query parameter is required.")}, status=400)

        if index != session.next_index:
            return Response(
                {"detail": _("Chunk out of order."), "expected_index": session.next_index},
                status=status.HTTP_409_CONFLICT,
            )

        try:
            declared = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            declared = 0
        if declared > MAX_CHUNK:
            return Response({"detail": _("Chunk too large.")}, status=413)

        # Read the raw stream rather than ``request.body``: the body accessor
        # enforces DATA_UPLOAD_MAX_MEMORY_SIZE, which is smaller than the
        # largest chunk this endpoint accepts.
        payload = request._request.read(MAX_CHUNK + 1)
        if not payload:
            return Response({"detail": _("Empty chunk.")}, status=400)
        if len(payload) > MAX_CHUNK:
            return Response({"detail": _("Chunk too large.")}, status=413)
        if session.received_bytes + len(payload) > session.total_size:
            return Response({"detail": _("The chunk goes past the declared file size.")}, status=400)

        with session.part_path.open("ab") as handle:
            handle.write(payload)

        session.next_index = index + 1
        session.received_bytes += len(payload)
        session.save(update_fields=["next_index", "received_bytes", "updated_at"])

        return Response(UploadSessionSerializer(session).data)


class CompleteUploadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, session_id):
        session = _session_for(request, session_id)
        if session.completed:
            return Response({"detail": _("This upload was already completed.")}, status=400)

        part = session.part_path
        on_disk = part.stat().st_size if part.exists() else 0
        if on_disk != session.total_size:
            return Response(
                {"detail": _("The upload is incomplete."), "received_bytes": on_disk,
                 "total_size": session.total_size},
                status=400,
            )

        title = (request.data.get("title") or "").strip() or session.filename.rsplit(".", 1)[0]
        visibility = request.data.get("visibility", Media.Visibility.PUBLIC)
        if visibility not in Media.Visibility.values:
            return Response({"detail": _("Invalid visibility.")}, status=400)

        category = None
        category_id = request.data.get("category")
        if category_id not in (None, ""):
            category = Category.objects.filter(pk=category_id).first()
            if category is None:
                return Response({"detail": _("Unknown category.")}, status=400)

        with transaction.atomic():
            media = Media(
                title=title[:255],
                description=(request.data.get("description") or "")[:10000],
                owner=request.user,
                original_name=session.filename,
                size_bytes=session.total_size,
                kind=kind_for(session.filename, session.content_type),
                visibility=visibility,
                category=category,
            )
            with part.open("rb") as handle:
                media.file.save(session.filename, File(handle), save=True)

            session.completed = True
            session.media_uid = media.uid
            session.save(update_fields=["completed", "media_uid", "updated_at"])

        # The bytes now live with the media item; the part file is a duplicate.
        session.discard_part()

        from apps.media.tasks import process_media

        process_media.delay(str(media.pk))
        media.refresh_from_db(fields=["state"])

        return Response(
            {"media_uid": media.uid, "state": media.state, "url": f"/view/{media.uid}/"},
            status=status.HTTP_201_CREATED,
        )


def _session_for(request, session_id) -> UploadSession:
    session = UploadSession.objects.filter(pk=session_id, owner=request.user).first()
    if session is None:
        raise Http404("upload session not found")
    return session
