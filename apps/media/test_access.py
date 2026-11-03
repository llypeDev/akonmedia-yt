"""Visibility rules and API authorisation.

Each test here pins a hole that existed before ``apps.media.access``: unlisted
items behaving like private ones, private items leaking through public
playlists, and API writes that only checked authentication.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from apps.rbac.roles import Role
from apps.users.models import User

from .models import Comment, Media, Playlist, PlaylistItem, Subtitle, SubtitleCue


def make_media(owner, **kwargs):
    defaults = {
        "title": "Clip",
        "kind": Media.Kind.VIDEO,
        "state": Media.State.READY,
        "visibility": Media.Visibility.PUBLIC,
    }
    defaults.update(kwargs)
    return Media.objects.create(owner=owner, **defaults)


class UnlistedTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="o@example.com", password="x" * 20)
        self.unlisted = make_media(self.owner, title="UNLISTED_MARK", visibility=Media.Visibility.UNLISTED)

    def test_anyone_with_the_link_can_open_it(self):
        self.assertEqual(self.client.get(f"/view/{self.unlisted.uid}/").status_code, 200)

    def test_it_can_be_embedded(self):
        self.assertEqual(self.client.get(f"/embed/{self.unlisted.uid}/").status_code, 200)

    def test_it_is_not_listed_anywhere(self):
        self.assertNotContains(self.client.get("/"), "UNLISTED_MARK")
        self.assertNotContains(self.client.get("/search/?q=UNLISTED"), "UNLISTED_MARK")
        titles = [item["title"] for item in APIClient().get("/api/v1/media/").json()["results"]]
        self.assertNotIn("UNLISTED_MARK", titles)

    def test_api_returns_it_by_identifier(self):
        response = APIClient().get(f"/api/v1/media/{self.unlisted.uid}/")
        self.assertEqual(response.status_code, 200)

    def test_unlisted_but_unprocessed_stays_hidden(self):
        pending = make_media(self.owner, visibility=Media.Visibility.UNLISTED, state=Media.State.PENDING)
        self.assertEqual(self.client.get(f"/view/{pending.uid}/").status_code, 404)

    def test_subtitles_of_unlisted_media_are_served(self):
        track = Subtitle.objects.create(media=self.unlisted, language="en")
        SubtitleCue.objects.create(subtitle=track, start_ms=0, end_ms=1000, text="hi")
        response = self.client.get(f"/view/{self.unlisted.uid}/subtitles/en/track.vtt")
        self.assertEqual(response.status_code, 200)

    def test_private_stays_private(self):
        private = make_media(self.owner, visibility=Media.Visibility.PRIVATE)
        self.assertEqual(self.client.get(f"/view/{private.uid}/").status_code, 404)
        self.assertEqual(self.client.get(f"/embed/{private.uid}/").status_code, 404)


class PlaylistPrivacyTests(TestCase):
    def setUp(self):
        self.ana = User.objects.create_user(email="ana@example.com", password="x" * 20)
        self.bob = User.objects.create_user(email="bob@example.com", password="x" * 20)
        self.secret = make_media(self.ana, title="SECRET_MARK", visibility=Media.Visibility.PRIVATE)
        self.playlist = Playlist.objects.create(title="Bob's list", owner=self.bob)

    def test_someone_elses_private_media_cannot_be_added(self):
        self.client.force_login(self.bob)
        self.client.post(
            f"/playlists/{self.playlist.uid}/edit/", {"action": "add", "media_uid": self.secret.uid}
        )
        self.assertFalse(self.playlist.entries.exists())

    def test_private_entries_never_render_for_people_who_cannot_see_them(self):
        # Even if one got in (e.g. it was public when added), it must not show.
        PlaylistItem.objects.create(playlist=self.playlist, media=self.secret, position=0)
        self.assertNotContains(self.client.get(f"/playlists/{self.playlist.uid}/"), "SECRET_MARK")
        self.client.force_login(self.ana)
        self.assertContains(self.client.get(f"/playlists/{self.playlist.uid}/"), "SECRET_MARK")

    def test_a_pasted_link_is_accepted(self):
        public = make_media(self.ana, title="Shared")
        self.client.force_login(self.bob)
        self.client.post(
            f"/playlists/{self.playlist.uid}/edit/",
            {"action": "add", "media_uid": f"https://example.com/view/{public.uid}/?list=x"},
        )
        self.assertTrue(self.playlist.entries.filter(media=public).exists())

    def test_unlisted_playlist_opens_by_link_but_is_not_listed(self):
        hidden = Playlist.objects.create(
            title="HIDDEN_LIST", owner=self.ana, visibility=Media.Visibility.UNLISTED
        )
        self.assertEqual(self.client.get(f"/playlists/{hidden.uid}/").status_code, 200)
        self.assertNotContains(self.client.get("/playlists/"), "HIDDEN_LIST")


class ApiAuthorisationTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.ana = User.objects.create_user(email="ana@example.com", password="x" * 20)
        self.bob = User.objects.create_user(email="bob@example.com", password="x" * 20)
        self.editor = User.objects.create_user(email="ed@example.com", password="x" * 20, role=Role.EDITOR)
        self.media = make_media(self.ana)
        self.playlist = Playlist.objects.create(title="Ana's", owner=self.ana)
        self.comment = Comment.objects.create(media=self.media, author=self.ana, body="original")

    def test_stranger_cannot_rename_or_delete_a_playlist(self):
        self.api.force_authenticate(self.bob)
        url = f"/api/v1/playlists/{self.playlist.id}/"
        self.assertEqual(self.api.patch(url, {"title": "pwned"}, format="json").status_code, 403)
        self.assertEqual(self.api.delete(url).status_code, 403)
        self.playlist.refresh_from_db()
        self.assertEqual(self.playlist.title, "Ana's")

    def test_owner_and_curator_can_edit_a_playlist(self):
        url = f"/api/v1/playlists/{self.playlist.id}/"
        self.api.force_authenticate(self.ana)
        self.assertEqual(self.api.patch(url, {"title": "Mine"}, format="json").status_code, 200)
        self.api.force_authenticate(self.editor)
        self.assertEqual(self.api.patch(url, {"title": "Curated"}, format="json").status_code, 200)

    def test_stranger_cannot_edit_a_comment(self):
        self.api.force_authenticate(self.bob)
        response = self.api.patch(f"/api/v1/comments/{self.comment.id}/", {"body": "hacked"}, format="json")
        self.assertEqual(response.status_code, 403)
        self.comment.refresh_from_db()
        self.assertEqual(self.comment.body, "original")

    def test_author_can_edit_their_comment(self):
        self.api.force_authenticate(self.ana)
        response = self.api.patch(f"/api/v1/comments/{self.comment.id}/", {"body": "fixed typo"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.comment.refresh_from_db()
        self.assertEqual(self.comment.body, "fixed typo")

    def test_comments_on_private_media_are_neither_readable_nor_writable(self):
        private = make_media(self.ana, visibility=Media.Visibility.PRIVATE)
        Comment.objects.create(media=private, author=self.ana, body="PRIVATE_NOTE")
        self.api.force_authenticate(self.bob)
        listing = self.api.get(f"/api/v1/comments/?media={private.uid}").json()["results"]
        self.assertEqual(listing, [])
        response = self.api.post("/api/v1/comments/", {"media": str(private.id), "body": "peek"}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_reply_must_belong_to_the_same_media(self):
        other = make_media(self.ana, title="Other")
        self.api.force_authenticate(self.bob)
        response = self.api.post(
            "/api/v1/comments/",
            {"media": str(other.id), "parent": str(self.comment.id), "body": "wrong thread"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_email_addresses_are_not_published(self):
        anonymous = APIClient().get(f"/api/v1/media/{self.media.uid}/").json()
        self.assertIsNone(anonymous["owner_email"])
        self.assertTrue(anonymous["owner_name"])
        self.api.force_authenticate(self.ana)
        mine = self.api.get(f"/api/v1/media/{self.media.uid}/").json()
        self.assertEqual(mine["owner_email"], "ana@example.com")


class EmbedOptionTests(TestCase):
    def setUp(self):
        from django.core.files.base import ContentFile

        self.owner = User.objects.create_user(email="e@example.com", password="x" * 20)
        self.media = make_media(self.owner)
        import shutil
        import uuid
        from pathlib import Path

        from django.conf import settings
        from django.test import override_settings

        self.root = Path(settings.BASE_DIR) / ".test-media" / uuid.uuid4().hex
        self.override = override_settings(MEDIA_ROOT=str(self.root))
        self.override.enable()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(self.override.disable)
        self.media.file.save("clip.mp4", ContentFile(b"0" * 32), save=True)

    def test_controls_can_be_switched_off(self):
        html = self.client.get(f"/embed/{self.media.uid}/?controls=0").content.decode()
        video = html[html.index("<video"):html.index(">", html.index("<video"))]
        self.assertNotIn("controls", video)
        self.assertIn('data-controls="0"', html)

    def test_controls_are_on_by_default(self):
        html = self.client.get(f"/embed/{self.media.uid}/").content.decode()
        video = html[html.index("<video"):html.index(">", html.index("<video"))]
        self.assertIn("controls", video)
