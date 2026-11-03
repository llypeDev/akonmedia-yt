"""Serializers for the media API."""
from django.utils.translation import gettext as _
from rest_framework import serializers

from apps.rbac import permissions as policy

from .models import Category, Chapter, Comment, Media, Playlist, Subtitle


def _private_email(serializer, account):
    """An address is personal data: show it to its owner and to curators only."""
    request = serializer.context.get("request")
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        if user.pk == account.pk or policy.can_curate_others(user):
            return account.email
    return None


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "slug", "description"]


class SubtitleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subtitle
        fields = ["id", "language", "label", "file"]


class ChapterSerializer(serializers.ModelSerializer):
    class Meta:
        model = Chapter
        fields = ["id", "title", "start_seconds", "position"]


class MediaSerializer(serializers.ModelSerializer):
    owner_email = serializers.SerializerMethodField()
    owner_name = serializers.CharField(source="owner.get_short_name", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True, default=None)
    duration_label = serializers.CharField(read_only=True)
    thumbnail_url = serializers.CharField(read_only=True)
    hls_url = serializers.CharField(read_only=True)
    url = serializers.SerializerMethodField()
    chapters = ChapterSerializer(many=True, read_only=True)
    subtitles = SubtitleSerializer(many=True, read_only=True)

    class Meta:
        model = Media
        fields = [
            "id", "uid", "title", "description", "kind", "state", "visibility",
            "owner_email", "owner_name", "category", "category_name", "duration_seconds",
            "duration_label", "width", "height", "size_bytes", "views_count",
            "thumbnail_url", "hls_url", "url",
            "chapters", "subtitles", "created_at",
        ]
        read_only_fields = fields

    def get_url(self, obj) -> str:
        return f"/view/{obj.uid}/"

    def get_owner_email(self, obj):
        return _private_email(self, obj.owner)


class MediaCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Media
        fields = ["title", "description", "kind", "visibility", "category", "file"]

    def validate_file(self, value):
        if value.size > 4 * 1024 * 1024 * 1024:
            raise serializers.ValidationError(_("Files above 4 GB must use the chunked uploader."))
        return value


class MediaUpdateSerializer(serializers.ModelSerializer):
    """Fields an owner may change after upload. Renditions are not editable."""

    class Meta:
        model = Media
        fields = ["title", "description", "visibility", "category"]
        extra_kwargs = {"title": {"required": False}}


class CommentSerializer(serializers.ModelSerializer):
    author_email = serializers.SerializerMethodField()
    author_name = serializers.CharField(source="author.get_short_name", read_only=True)

    class Meta:
        model = Comment
        fields = [
            "id", "media", "author_email", "author_name", "parent", "body", "timestamp",
            "created_at",
        ]
        read_only_fields = ["id", "author_email", "author_name", "created_at"]

    def get_author_email(self, obj):
        return _private_email(self, obj.author)


class PlaylistSerializer(serializers.ModelSerializer):
    owner_email = serializers.SerializerMethodField()
    owner_name = serializers.CharField(source="owner.get_short_name", read_only=True)

    class Meta:
        model = Playlist
        fields = [
            "id", "uid", "title", "description", "visibility", "owner_email", "owner_name",
            "created_at",
        ]
        read_only_fields = ["id", "uid", "owner_email", "owner_name", "created_at"]

    def get_owner_email(self, obj):
        return _private_email(self, obj.owner)
