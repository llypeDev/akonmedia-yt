"""Curation queues and their access rules."""
from django.test import TestCase

from apps.media.models import Comment, Media
from apps.rbac.roles import Role
from apps.users.models import User


def make_media(owner, **kwargs):
    defaults = {
        "title": "Clip",
        "kind": Media.Kind.VIDEO,
        "state": Media.State.READY,
        "visibility": Media.Visibility.PUBLIC,
    }
    defaults.update(kwargs)
    return Media.objects.create(owner=owner, **defaults)


class ManageAccessTests(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(email="m@example.com", password="x" * 20)
        self.editor = User.objects.create_user(
            email="e@example.com", password="x" * 20, role=Role.EDITOR
        )

    def test_anonymous_is_redirected_to_sign_in(self):
        response = self.client.get("/manage/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_plain_member_is_forbidden(self):
        self.client.force_login(self.member)
        response = self.client.get("/manage/")
        self.assertEqual(response.status_code, 403)

    def test_editor_can_open_the_queue(self):
        self.client.force_login(self.editor)
        response = self.client.get("/manage/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fila de codificação")

    def test_member_cannot_fire_actions(self):
        self.client.force_login(self.member)
        media = make_media(self.member, state=Media.State.FAILED)
        response = self.client.post(
            "/manage/action/", {"kind": "media", "action": "delete", "id": media.pk}
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Media.objects.filter(pk=media.pk).exists())

    def test_nav_link_only_appears_for_curators(self):
        self.client.force_login(self.member)
        self.assertNotContains(self.client.get("/"), 'href="/manage/"')
        self.client.force_login(self.editor)
        self.assertContains(self.client.get("/"), 'href="/manage/"')


class ManageActionTests(TestCase):
    def setUp(self):
        self.editor = User.objects.create_user(
            email="ed@example.com", password="x" * 20, role=Role.EDITOR
        )
        self.client.force_login(self.editor)

    def post(self, **payload):
        return self.client.post("/manage/action/", payload, follow=True)

    def test_failed_media_appears_with_its_reason(self):
        media = make_media(
            self.editor, title="Broken clip", state=Media.State.FAILED,
            failure_reason="ffprobe: moov atom not found",
        )
        response = self.client.get("/manage/")
        self.assertContains(response, "Broken clip")
        self.assertContains(response, "moov atom not found")

    def test_retry_resets_state_and_clears_the_reason(self):
        media = make_media(
            self.editor, state=Media.State.FAILED, failure_reason="boom"
        )
        # Keep the queue from draining immediately so the reset is visible.
        with self.settings(CELERY_TASK_ALWAYS_EAGER=False):
            self.post(kind="media", action="retry", id=media.pk)

        media.refresh_from_db()
        self.assertEqual(media.state, Media.State.PENDING)
        self.assertEqual(media.failure_reason, "")

    def test_delete_removes_the_media(self):
        media = make_media(self.editor, state=Media.State.FAILED)
        self.post(kind="media", action="delete", id=media.pk)
        self.assertFalse(Media.objects.filter(pk=media.pk).exists())

    def test_hidden_comment_can_be_restored(self):
        media = make_media(self.editor)
        comment = Comment.objects.create(
            media=media, author=self.editor, body="flagged", is_hidden=True
        )
        self.post(kind="comment", action="unhide", id=comment.pk)

        comment.refresh_from_db()
        self.assertFalse(comment.is_hidden)

    def test_comment_can_be_hidden_from_the_queue(self):
        media = make_media(self.editor, title="Discussable")
        comment = Comment.objects.create(media=media, author=self.editor, body="visible")
        self.post(kind="comment", action="hide", id=comment.pk)

        comment.refresh_from_db()
        self.assertTrue(comment.is_hidden)

    def test_pending_media_shows_in_the_encoding_queue(self):
        make_media(self.editor, title="Waiting", state=Media.State.PENDING)
        response = self.client.get("/manage/")
        self.assertContains(response, "Waiting")
