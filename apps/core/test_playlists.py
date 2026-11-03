"""Playlist pages: creation, ordering and access rules."""
from django.test import TestCase

from apps.media.models import Media, Playlist
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


class PlaylistPageTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="p@example.com", password="x" * 20)
        self.other = User.objects.create_user(email="o@example.com", password="x" * 20)
        self.editor = User.objects.create_user(
            email="e@example.com", password="x" * 20, role=Role.EDITOR
        )
        self.playlist = Playlist.objects.create(title="Mine", owner=self.owner)
        self.secret = Playlist.objects.create(
            title="Secret", owner=self.owner, visibility=Media.Visibility.PRIVATE
        )
        self.media = [make_media(self.owner, title=f"Clip {i}") for i in range(3)]

    def test_anonymous_sees_only_public_playlists(self):
        response = self.client.get("/playlists/")
        self.assertContains(response, "Mine")
        self.assertNotContains(response, "Secret")

    def test_owner_sees_their_private_playlist(self):
        self.client.force_login(self.owner)
        response = self.client.get("/playlists/")
        self.assertContains(response, "Secret")

    def test_creation_requires_login(self):
        response = self.client.post("/playlists/", {"title": "Nope"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Playlist.objects.filter(title="Nope").exists())

    def test_creator_is_redirected_to_edit(self):
        self.client.force_login(self.owner)
        response = self.client.post("/playlists/", {"title": "Fresh"})
        created = Playlist.objects.get(title="Fresh")
        self.assertEqual(response["Location"], f"/playlists/{created.uid}/edit/")
        self.assertEqual(created.owner, self.owner)

    def test_blank_title_is_refused(self):
        self.client.force_login(self.owner)
        self.client.post("/playlists/", {"title": "   "})
        self.assertEqual(Playlist.objects.filter(owner=self.owner).count(), 2)


class PlaylistMutationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="m@example.com", password="x" * 20)
        self.stranger = User.objects.create_user(email="s@example.com", password="x" * 20)
        self.playlist = Playlist.objects.create(title="List", owner=self.owner)
        self.items = [make_media(self.owner, title=f"Item {i}") for i in range(3)]

    def edit_url(self):
        return f"/playlists/{self.playlist.uid}/edit/"

    def test_add_by_uid(self):
        self.client.force_login(self.owner)
        self.client.post(self.edit_url(), {"action": "add", "media_uid": self.items[0].uid})
        self.assertEqual(self.playlist.entries.count(), 1)
        self.assertEqual(self.playlist.entries.first().media, self.items[0])

    def test_adding_the_same_media_twice_is_a_no_op(self):
        self.client.force_login(self.owner)
        for _ in range(2):
            self.client.post(self.edit_url(), {"action": "add", "media_uid": self.items[0].uid})
        self.assertEqual(self.playlist.entries.count(), 1)

    def test_remove_leaves_the_others_in_order(self):
        self.client.force_login(self.owner)
        for media in self.items:
            self.client.post(self.edit_url(), {"action": "add", "media_uid": media.uid})

        first = self.playlist.entries.order_by("position").first()
        self.client.post(self.edit_url(), {"action": "remove", "item_id": first.pk})

        remaining = list(self.playlist.entries.order_by("position"))
        self.assertEqual(len(remaining), 2)
        self.assertEqual([item.position for item in remaining], [0, 1])

    def test_move_down_swaps_neighbours(self):
        self.client.force_login(self.owner)
        for media in self.items:
            self.client.post(self.edit_url(), {"action": "add", "media_uid": media.uid})

        first = self.playlist.entries.order_by("position").first()
        self.client.post(
            self.edit_url(),
            {"action": "move", "item_id": first.pk, "direction": "down"},
        )

        ordered = list(self.playlist.entries.order_by("position").values_list("media__title", flat=True))
        self.assertEqual(ordered, ["Item 1", "Item 0", "Item 2"])

    def test_stranger_cannot_open_the_editor(self):
        self.client.force_login(self.stranger)
        response = self.client.get(self.edit_url())
        self.assertEqual(response.status_code, 404)

    def test_editor_can_manage_someone_elses_playlist(self):
        editor = User.objects.create_user(
            email="ed@example.com", password="x" * 20, role=Role.EDITOR
        )
        self.client.force_login(editor)
        response = self.client.get(self.edit_url())
        self.assertEqual(response.status_code, 200)

    def test_detail_excludes_media_that_is_not_ready(self):
        self.client.force_login(self.owner)
        self.client.post(self.edit_url(), {"action": "add", "media_uid": self.items[0].uid})
        pending = make_media(self.owner, title="Still encoding", state=Media.State.PENDING)
        self.client.post(self.edit_url(), {"action": "add", "media_uid": pending.uid})

        response = self.client.get(f"/playlists/{self.playlist.uid}/")
        self.assertContains(response, "Item 0")
        self.assertNotContains(response, "Still encoding")
