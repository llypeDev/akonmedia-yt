"""Serializers for the media API."""
from rest_framework import serializers

from .models import Category, Chapter, Comment, Media, Playlist, Subtitle


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
    owner_email = serializers.EmailField(source="owner.email", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True, default=None)
    duration_label = serializers.CharField(read_only=True)
    chapters = ChapterSerializer(many=True, read_only=True)
    subtitles = SubtitleSerializer(many=True, read_only=True)

    class Meta:
        model = Media
        fields = [
            "id", "uid", "title", "description", "kind", "state", "visibility",
            "owner_email", "category", "category_name", "duration_seconds",
            "duration_label", "width", "height", "size_bytes", "views_count",
            "chapters", "subtitles", "created_at",
        ]
        read_only_fields = fields


class MediaCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Media
        fields = ["title", "description", "kind", "visibility", "category", "file"]

    def validate_file(self, value):
        if value.size > 4 * 1024 * 1024 * 1024:
            raise serializers.ValidationError("Files above 4 GB must use the chunked uploader.")
        return value


class MediaUpdateSerializer(serializers.ModelSerializer):
    """Fields an owner may change after upload. Renditions are not editable."""

    class Meta:
        model = Media
        fields = ["title", "description", "visibility", "category"]
        extra_kwargs = {"title": {"required": False}}


class CommentSerializer(serializers.ModelSerializer):
    author_email = serializers.EmailField(source="author.email", read_only=True)

    class Meta:
        model = Comment
        fields = ["id", "media", "author_email", "parent", "body", "timestamp", "created_at"]
        read_only_fields = ["id", "author_email", "created_at"]


class PlaylistSerializer(serializers.ModelSerializer):
    owner_email = serializers.EmailField(source="owner.email", read_only=True)

    class Meta:
        model = Playlist
        fields = ["id", "uid", "title", "description", "visibility", "owner_email", "created_at"]
        read_only_fields = ["id", "uid", "owner_email", "created_at"]
