"""Public pages: library, detail, search, comments."""
import uuid
from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apps.media.models import Comment, Media
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


class LibraryPageTests(TestCase):
    def setUp(self):
        self.media_root = Path(settings.BASE_DIR) / ".test-media" / uuid.uuid4().hex
        self.override = override_settings(MEDIA_ROOT=str(self.media_root))
        self.override.enable()

        self.owner = User.objects.create_user(
            email="owner@example.com", password="x" * 20, display_name="Owner"
        )
        self.public = make_media(self.owner, title="Public clip")
        self.private = make_media(
            self.owner, title="Private clip", visibility=Media.Visibility.PRIVATE
        )

    def tearDown(self):
        self.override.disable()
        import shutil

        shutil.rmtree(self.media_root, ignore_errors=True)

    def test_index_lists_only_public_media(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Public clip")
        self.assertNotContains(response, "Private clip")

    def test_kind_filter_narrows_the_grid(self):
        make_media(self.owner, title="A track", kind=Media.Kind.AUDIO)
        response = self.client.get("/?kind=audio")
        self.assertContains(response, "A track")
        self.assertNotContains(response, "Public clip")

    def test_unknown_kind_falls_back_to_everything(self):
        response = self.client.get("/?kind=bogus")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Public clip")

    def test_detail_renders_player_for_public_media(self):
        with self.captureOnCommitCallbacks(execute=False):
            self.public.file.save("clip.mp4", SimpleUploadedFile("clip.mp4", b"0" * 32), save=True)
        response = self.client.get(f"/view/{self.public.uid}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<video")

    def test_detail_without_a_file_explains_itself(self):
        response = self.client.get(f"/view/{self.public.uid}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No source file attached")

    def test_private_media_is_a_404_for_visitors(self):
        response = self.client.get(f"/view/{self.private.uid}/")
        self.assertEqual(response.status_code, 404)

    def test_owner_can_open_own_private_media(self):
        self.client.force_login(self.owner)
        response = self.client.get(f"/view/{self.private.uid}/")
        self.assertEqual(response.status_code, 200)

    def test_viewing_increments_the_counter(self):
        self.client.get(f"/view/{self.public.uid}/")
        self.public.refresh_from_db()
        self.assertEqual(self.public.views_count, 1)

    def test_owner_visits_do_not_inflate_their_own_counter(self):
        self.client.force_login(self.owner)
        self.client.get(f"/view/{self.public.uid}/")
        self.public.refresh_from_db()
        self.assertEqual(self.public.views_count, 0)


class SearchPageTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="s@example.com", password="x" * 20)
        make_media(self.owner, title="Harbour at dawn")
        make_media(self.owner, title="Studio tour", description="harbour lights")

    def test_search_matches_title(self):
        response = self.client.get("/search/?q=harbour")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Harbour at dawn")

    def test_search_matches_description(self):
        response = self.client.get("/search/?q=harbour")
        self.assertContains(response, "Studio tour")

    def test_empty_query_returns_nothing(self):
        response = self.client.get("/search/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Type something")


class CommentPageTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="c@example.com", password="x" * 20)
        self.media = make_media(self.owner, title="Discussable")

    def test_anonymous_cannot_post(self):
        response = self.client.post(f"/view/{self.media.uid}/", {"body": "hi"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])
        self.assertEqual(Comment.objects.count(), 0)

    def test_signed_in_user_can_comment(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            f"/view/{self.media.uid}/", {"body": "First thought"}, follow=True
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "First thought")

        comment = Comment.objects.get()
        self.assertEqual(comment.author, self.owner)
        self.assertEqual(comment.media, self.media)

    def test_blank_comment_is_ignored(self):
        self.client.force_login(self.owner)
        self.client.post(f"/view/{self.media.uid}/", {"body": "   "})
        self.assertEqual(Comment.objects.count(), 0)

    def test_hidden_comments_are_not_rendered(self):
        self.client.force_login(self.owner)
        Comment.objects.create(media=self.media, author=self.owner, body="VISIBLE_MARKER")
        Comment.objects.create(
            media=self.media, author=self.owner, body="HIDDEN_MARKER", is_hidden=True
        )
        response = self.client.get(f"/view/{self.media.uid}/")
        self.assertContains(response, "VISIBLE_MARKER")
        self.assertNotContains(response, "HIDDEN_MARKER")
