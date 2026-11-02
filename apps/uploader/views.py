"""Chunked upload endpoints."""
from django.core.files import File
from django.db import transaction
from django.http import Http404
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.media.api import kind_for
from apps.media.models import Media

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
            return Response({"detail": "chunk_size out of range"}, status=400)

        session = UploadSession.objects.create(
            owner=request.user,
            filename=data["filename"][:255],
            content_type=data["content_type"][:120],
            total_size=data["total_size"],
            chunk_size=data["chunk_size"],
        )
        return Response(UploadSessionSerializer(session).data, status=status.HTTP_201_CREATED)


class ChunkUploadView(APIView):
    permission_classes = [IsAuthenticated]

    def put(self, request, session_id):
        session = _session_for(request, session_id)
        if session.completed:
            return Response({"detail": "session already completed"}, status=400)

        try:
            index = int(request.query_params.get("index", ""))
        except (TypeError, ValueError):
            return Response({"detail": "index query parameter is required"}, status=400)

        if index != session.next_index:
            return Response(
                {"detail": "out of order", "expected_index": session.next_index},
                status=status.HTTP_409_CONFLICT,
            )

        payload = request.body
        if not payload:
            return Response({"detail": "empty chunk"}, status=400)
        if len(payload) > MAX_CHUNK:
            return Response({"detail": "chunk too large"}, status=413)
        if session.received_bytes + len(payload) > session.total_size:
            return Response({"detail": "chunk exceeds declared size"}, status=400)

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
            return Response({"detail": "already completed"}, status=400)

        part = session.part_path
        on_disk = part.stat().st_size if part.exists() else 0
        if on_disk != session.total_size:
            return Response(
                {"detail": "upload incomplete", "received_bytes": on_disk,
                 "total_size": session.total_size},
                status=400,
            )

        title = request.data.get("title") or session.filename.rsplit(".", 1)[0]
        visibility = request.data.get("visibility", Media.Visibility.PUBLIC)
        if visibility not in Media.Visibility.values:
            return Response({"detail": "invalid visibility"}, status=400)

        with transaction.atomic():
            media = Media(
                title=title[:255],
                owner=request.user,
                original_name=session.filename,
                size_bytes=session.total_size,
                kind=kind_for(session.filename, session.content_type),
                visibility=visibility,
            )
            with part.open("rb") as handle:
                media.file.save(session.filename, File(handle), save=True)

            session.completed = True
            session.media_uid = media.uid
            session.save(update_fields=["completed", "media_uid", "updated_at"])

        from apps.media.tasks import process_media

        process_media.delay(str(media.pk))

        return Response(
            {"media_uid": media.uid, "state": media.state},
            status=status.HTTP_201_CREATED,
        )


def _session_for(request, session_id) -> UploadSession:
    session = UploadSession.objects.filter(pk=session_id, owner=request.user).first()
    if session is None:
        raise Http404("upload session not found")
    return session
