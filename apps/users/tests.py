"""Foundation tests: account model, role ladder and the session API."""
from django.test import TestCase
from django.urls import reverse

from apps.rbac import permissions as policy
from apps.rbac.roles import Role

from .models import User


class UserModelTests(TestCase):
    def test_email_is_lowercased_and_unique(self):
        user = User.objects.create_user(email="Someone@Example.COM", password="x" * 20)
        self.assertEqual(user.email, "someone@example.com")

    def test_default_role_is_member(self):
        user = User.objects.create_user(email="a@example.com", password="x" * 20)
        self.assertEqual(user.role, Role.MEMBER)

    def test_superuser_gets_admin_role(self):
        admin = User.objects.create_superuser(email="root@example.com", password="x" * 20)
        self.assertEqual(admin.role, Role.ADMIN)
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_superuser)

    def test_display_name_falls_back_to_email(self):
        user = User.objects.create_user(email="b@example.com", password="x" * 20)
        self.assertEqual(str(user), "b@example.com")
        user.display_name = "Beatriz"
        self.assertEqual(str(user), "Beatriz")


class RoleLadderTests(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(email="m@example.com", password="x" * 20)
        self.editor = User.objects.create_user(
            email="e@example.com", password="x" * 20, role=Role.EDITOR
        )
        self.manager = User.objects.create_user(
            email="g@example.com", password="x" * 20, role=Role.MANAGER
        )

    def test_anonymous_ranks_below_everyone(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertEqual(policy.rank_of(AnonymousUser()), 0)
        self.assertFalse(policy.can_upload(AnonymousUser()))

    def test_editor_can_curate_but_not_manage_users(self):
        self.assertTrue(policy.can_curate_others(self.editor))
        self.assertFalse(policy.can_manage_users(self.editor))

    def test_manager_can_manage_users_but_not_configure_portal(self):
        self.assertTrue(policy.can_manage_users(self.manager))
        self.assertFalse(policy.can_configure_portal(self.manager))

    def test_member_cannot_curate_others(self):
        self.assertFalse(policy.can_curate_others(self.member))
        self.assertTrue(policy.can_upload(self.member))


class HealthTests(TestCase):
    def test_health_endpoint_is_public(self):
        response = self.client.get(reverse("core:health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_index_renders(self):
        response = self.client.get(reverse("core:index"))
        self.assertEqual(response.status_code, 200)


class WhoAmITests(TestCase):
    def test_anonymous_is_rejected(self):
        response = self.client.get("/api/v1/whoami/")
        self.assertIn(response.status_code, (401, 403))

    def test_capabilities_reflect_role(self):
        editor = User.objects.create_user(
            email="ed@example.com", password="x" * 20, role=Role.EDITOR
        )
        self.client.force_login(editor)
        payload = self.client.get("/api/v1/whoami/").json()
        self.assertEqual(payload["role"], Role.EDITOR)
        self.assertTrue(payload["capabilities"]["curate_others"])
        self.assertFalse(payload["capabilities"]["manage_users"])
