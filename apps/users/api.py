"""Small JSON surface for the account, used by the frontend and by scripts."""
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.rbac import permissions as policy

from .models import User


class UserSerializer(serializers.ModelSerializer):
    rank = serializers.SerializerMethodField()
    avatar_url = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "email", "display_name", "role", "rank", "avatar", "avatar_url", "date_joined"]
        read_only_fields = fields

    def get_rank(self, obj) -> int:
        return policy.rank_of(obj)

    def get_avatar_url(self, obj):
        """The picture's address, or null when the account shows its initials."""
        url = obj.avatar_url
        if not url:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url


class WhoAmIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = dict(UserSerializer(request.user, context={"request": request}).data)
        data["capabilities"] = {
            "upload": policy.can_upload(request.user),
            "curate_others": policy.can_curate_others(request.user),
            "moderate_comments": policy.can_moderate_comments(request.user),
            "manage_users": policy.can_manage_users(request.user),
            "configure_portal": policy.can_configure_portal(request.user),
        }
        return Response(data)
