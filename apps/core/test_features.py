"""Creator studio, editing, community features and operational pages."""
import io
import shutil
import uuid
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.template import Context, Template
from django.test import TestCase, override_settings
from django.utils import translation

from apps.media.models import Category, Chapter, Comment, Media, Playlist, PlaylistItem
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


class MediaRootMixin:
    def use_media_root(self):
        self.media_root = Path(settings.BASE_DIR) / ".test-media" / uuid.uuid4().hex
        override = override_settings(MEDIA_ROOT=str(self.media_root), CELERY_TASK_ALWAYS_EAGER=False)
        override.enable()
        self.addCleanup(shutil.rmtree, self.media_root, True)
        self.addCleanup(override.disable)


class StudioTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="me@example.com", password="x" * 20)
        self.other = User.objects.create_user(email="you@example.com", password="x" * 20)
        make_media(self.owner, title="MY_PUBLIC")
        make_media(self.owner, title="MY_PRIVATE", visibility=Media.Visibility.PRIVATE)
        make_media(self.owner, title="MY_FAILED", state=Media.State.FAILED, failure_reason="boom")
        make_media(self.other, title="NOT_MINE")

    def test_requires_sign_in(self):
        self.assertEqual(self.client.get("/studio/").status_code, 302)

    def test_lists_every_state_but_only_my_items(self):
        self.client.force_login(self.owner)
        response = self.client.get("/studio/")
        for title in ("MY_PUBLIC", "MY_PRIVATE", "MY_FAILED"):
            self.assertContains(response, title)
        self.assertNotContains(response, "NOT_MINE")

    def test_state_filter(self):
        self.client.force_login(self.owner)
        response = self.client.get("/studio/?state=failed")
        self.assertContains(response, "MY_FAILED")
        self.assertNotContains(response, "MY_PUBLIC")

    def test_library_no_longer_mixes_in_private_items(self):
        self.client.force_login(self.owner)
        response = self.client.get("/")
        self.assertContains(response, "MY_PUBLIC")
        self.assertNotContains(response, "MY_PRIVATE")


class MediaEditTests(MediaRootMixin, TestCase):
    def setUp(self):
        self.use_media_root()
        self.owner = User.objects.create_user(email="ed@example.com", password="x" * 20)
        self.stranger = User.objects.create_user(email="st@example.com", password="x" * 20)
        self.media = make_media(self.owner, title="Before", duration_seconds=120)
        self.media.file.save("clip.mp4", ContentFile(b"0" * 64), save=True)
        self.url = f"/view/{self.media.uid}/edit/"

    def test_stranger_gets_a_404(self):
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_details_are_saved(self):
        category = Category.objects.create(name="Talks")
        self.client.force_login(self.owner)
        self.client.post(self.url, {
            "action": "details", "title": "After", "description": "New words",
            "visibility": "unlisted", "category": category.pk,
        })
        self.media.refresh_from_db()
        self.assertEqual((self.media.title, self.media.visibility), ("After", "unlisted"))
        self.assertEqual(self.media.category, category)

    def test_chapters_accept_clock_times_and_are_sorted(self):
        self.client.force_login(self.owner)
        self.client.post(self.url, {
            "action": "chapters",
            "chapter_start": ["1:30", "0:00", ""],
            "chapter_title": ["Second", "First", ""],
        })
        chapters = list(self.media.chapters.order_by("start_seconds"))
        self.assertEqual([(c.start_seconds, c.title) for c in chapters], [(0.0, "First"), (90.0, "Second")])

    def test_chapter_past_the_end_is_refused(self):
        self.client.force_login(self.owner)
        self.client.post(self.url, {"action": "chapters", "chapter_start": ["9:00"], "chapter_title": ["Late"]})
        self.assertFalse(Chapter.objects.exists())

    def test_custom_thumbnail_replaces_the_poster(self):
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (1600, 900), (124, 108, 255)).save(buffer, "PNG")
        self.client.force_login(self.owner)
        self.client.post(self.url, {
            "action": "poster",
            "poster": SimpleUploadedFile("thumb.png", buffer.getvalue(), "image/png"),
        })
        self.media.refresh_from_db()
        self.assertIn("/poster-", f"/{self.media.poster.name}")
        with Image.open(self.media.poster.path) as poster:
            self.assertEqual(poster.size, (1280, 720))

    def test_not_an_image_is_rejected(self):
        self.client.force_login(self.owner)
        self.client.post(self.url, {"action": "poster", "poster": SimpleUploadedFile("x.png", b"nope")})
        self.media.refresh_from_db()
        self.assertFalse(self.media.poster)

    def test_delete_removes_the_record_and_its_folder(self):
        folder = Path(self.media.file.path).parent
        self.assertTrue(folder.exists())
        self.client.force_login(self.owner)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(f"/view/{self.media.uid}/delete/")
        self.assertRedirects(response, "/studio/", fetch_redirect_response=False)
        self.assertFalse(Media.objects.filter(pk=self.media.pk).exists())
        self.assertFalse(folder.exists())

    def test_reprocess_queues_the_item_again(self):
        Media.objects.filter(pk=self.media.pk).update(state=Media.State.FAILED, failure_reason="x")
        self.client.force_login(self.owner)
        self.client.post(f"/view/{self.media.uid}/reprocess/")
        self.media.refresh_from_db()
        self.assertEqual(self.media.state, Media.State.PENDING)
        self.assertEqual(self.media.failure_reason, "")


class AccountAndChannelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="ch@example.com", password="x" * 20)

    def test_display_name_is_editable(self):
        self.client.force_login(self.user)
        self.client.post("/account/", {"display_name": "  Channel Name  "})
        self.user.refresh_from_db()
        self.assertEqual(self.user.display_name, "Channel Name")

    def test_channel_lists_only_public_ready_items(self):
        make_media(self.user, title="PUBLIC_ONE")
        make_media(self.user, title="HIDDEN_ONE", visibility=Media.Visibility.UNLISTED)
        response = self.client.get(f"/channel/{self.user.pk}/")
        self.assertContains(response, "PUBLIC_ONE")
        self.assertNotContains(response, "HIDDEN_ONE")

    def test_public_pages_never_show_email_addresses(self):
        make_media(self.user, title="Anything")
        for url in ("/", f"/channel/{self.user.pk}/"):
            self.assertNotContains(self.client.get(url), "ch@example.com")


class CommentFeatureTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="own@example.com", password="x" * 20)
        self.viewer = User.objects.create_user(email="view@example.com", password="x" * 20)
        self.editor = User.objects.create_user(email="mod@example.com", password="x" * 20, role=Role.EDITOR)
        self.media = make_media(self.owner, duration_seconds=100)
        self.url = f"/view/{self.media.uid}/"

    def test_reply_lands_in_the_thread(self):
        root = Comment.objects.create(media=self.media, author=self.owner, body="Question?")
        self.client.force_login(self.viewer)
        self.client.post(self.url, {"body": "Answer.", "parent": str(root.pk)})
        reply = Comment.objects.get(body="Answer.")
        self.assertEqual(reply.parent, root)

    def test_reply_to_a_reply_joins_the_same_thread(self):
        root = Comment.objects.create(media=self.media, author=self.owner, body="Root")
        child = Comment.objects.create(media=self.media, author=self.owner, body="Child", parent=root)
        self.client.force_login(self.viewer)
        self.client.post(self.url, {"body": "Grandchild", "parent": str(child.pk)})
        self.assertEqual(Comment.objects.get(body="Grandchild").parent, root)

    def test_garbage_parent_is_ignored(self):
        self.client.force_login(self.viewer)
        self.client.post(self.url, {"body": "Fine", "parent": "not-a-uuid"})
        self.assertIsNone(Comment.objects.get(body="Fine").parent)

    def test_comment_can_be_pinned_to_a_moment(self):
        self.client.force_login(self.viewer)
        self.client.post(self.url, {"body": "Here", "timestamp": "42.5"})
        self.assertEqual(Comment.objects.get(body="Here").timestamp, 42.5)
        self.client.post(self.url, {"body": "Too late", "timestamp": "500"})
        self.assertEqual(Comment.objects.get(body="Too late").timestamp, 100)

    def test_timestamps_in_text_become_seek_links(self):
        Comment.objects.create(media=self.media, author=self.owner, body="See 1:23 <b>bold</b>")
        response = self.client.get(self.url)
        self.assertContains(response, 'data-seek="83"')
        self.assertContains(response, "&lt;b&gt;bold&lt;/b&gt;")

    def test_author_can_delete_but_stranger_cannot(self):
        comment = Comment.objects.create(media=self.media, author=self.viewer, body="Mine")
        self.client.force_login(self.owner)
        response = self.client.post(f"/comments/{comment.pk}/action/", {"action": "delete"})
        self.assertEqual(response.status_code, 403)
        self.client.force_login(self.viewer)
        self.client.post(f"/comments/{comment.pk}/action/", {"action": "delete"})
        self.assertFalse(Comment.objects.filter(pk=comment.pk).exists())

    def test_moderator_can_hide(self):
        comment = Comment.objects.create(media=self.media, author=self.viewer, body="Rude")
        self.client.force_login(self.editor)
        self.client.post(f"/comments/{comment.pk}/action/", {"action": "hide"})
        comment.refresh_from_db()
        self.assertTrue(comment.is_hidden)

    def test_next_parameter_cannot_redirect_off_site(self):
        comment = Comment.objects.create(media=self.media, author=self.viewer, body="x")
        self.client.force_login(self.viewer)
        response = self.client.post(
            f"/comments/{comment.pk}/action/", {"action": "delete", "next": "https://evil.example/"}
        )
        self.assertTrue(response["Location"].startswith(f"/view/{self.media.uid}/"))


class SaveToPlaylistTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="pl@example.com", password="x" * 20)
        self.media = make_media(self.user)
        self.client.force_login(self.user)
        self.url = f"/view/{self.media.uid}/playlists/"

    def test_create_a_playlist_while_saving(self):
        self.client.post(self.url, {"new_title": "Watch later", "visibility": "private"})
        playlist = Playlist.objects.get(title="Watch later")
        self.assertEqual(playlist.visibility, "private")
        self.assertTrue(playlist.entries.filter(media=self.media).exists())

    def test_toggle_membership(self):
        playlist = Playlist.objects.create(title="Mine", owner=self.user)
        self.client.post(self.url, {"playlist": playlist.uid})
        self.assertEqual(playlist.entries.count(), 1)
        self.client.post(self.url, {"playlist": playlist.uid})  # second add is a no-op
        self.assertEqual(playlist.entries.count(), 1)
        self.client.post(self.url, {"playlist": playlist.uid, "action": "remove"})
        self.assertEqual(playlist.entries.count(), 0)

    def test_cannot_save_into_someone_elses_playlist(self):
        other = User.objects.create_user(email="o@example.com", password="x" * 20)
        playlist = Playlist.objects.create(title="Theirs", owner=other)
        response = self.client.post(self.url, {"playlist": playlist.uid})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(playlist.entries.exists())


class PlaylistPlaybackTests(MediaRootMixin, TestCase):
    def setUp(self):
        self.use_media_root()
        self.user = User.objects.create_user(email="pp@example.com", password="x" * 20)
        self.items = [make_media(self.user, title=f"Track {i}") for i in range(3)]
        for media in self.items:
            media.file.save("clip.mp4", ContentFile(b"0" * 32), save=True)
        self.playlist = Playlist.objects.create(title="Run", owner=self.user)
        for position, media in enumerate(self.items):
            PlaylistItem.objects.create(playlist=self.playlist, media=media, position=position)

    def test_media_page_knows_what_comes_next(self):
        response = self.client.get(f"/view/{self.items[0].uid}/?list={self.playlist.uid}")
        context = response.context["playlist_ctx"]
        self.assertEqual(context["position"], 1)
        self.assertEqual(context["next"], self.items[1])
        self.assertContains(response, f'data-next-url="/view/{self.items[1].uid}/?list={self.playlist.uid}"')

    def test_last_item_has_no_next(self):
        response = self.client.get(f"/view/{self.items[2].uid}/?list={self.playlist.uid}")
        self.assertIsNone(response.context["playlist_ctx"]["next"])

    def test_unknown_playlist_is_ignored(self):
        response = self.client.get(f"/view/{self.items[0].uid}/?list=doesnotexist")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["playlist_ctx"])


class LibraryFeatureTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="lib@example.com", password="x" * 20, display_name="Marta Quinn")
        self.music = Category.objects.create(name="Music")
        self.quiet = make_media(self.user, title="Quiet one", views_count=1)
        self.loud = make_media(self.user, title="Loud one", views_count=500, category=self.music)

    def test_sort_by_popularity(self):
        titles = [m.title for m in self.client.get("/?sort=popular").context["page_obj"].object_list]
        self.assertEqual(titles[0], "Loud one")

    def test_category_filter(self):
        response = self.client.get("/?category=music")
        self.assertContains(response, "Loud one")
        self.assertNotContains(response, "Quiet one")

    def test_search_finds_creator_and_category(self):
        self.assertContains(self.client.get("/search/?q=marta"), "Quiet one")
        self.assertContains(self.client.get("/search/?q=music"), "Loud one")

    def test_hero_shows_library_statistics(self):
        response = self.client.get("/")
        self.assertEqual(response.context["stats"]["items"], 2)
        self.assertContains(response, "Em alta")


class ManageAccountsTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(email="mgr@example.com", password="x" * 20, role=Role.MANAGER)
        self.editor = User.objects.create_user(email="edt@example.com", password="x" * 20, role=Role.EDITOR)
        self.member = User.objects.create_user(email="mem@example.com", password="x" * 20)

    def change(self, actor, target, **extra):
        self.client.force_login(actor)
        payload = {"kind": "user", "id": target.pk, "tab": "users", **extra}
        return self.client.post("/manage/action/", payload)

    def test_manager_promotes_a_member(self):
        self.change(self.manager, self.member, action="role", role="editor")
        self.member.refresh_from_db()
        self.assertEqual(self.member.role, Role.EDITOR)

    def test_manager_cannot_grant_admin(self):
        response = self.change(self.manager, self.member, action="role", role="admin")
        self.assertEqual(response.status_code, 403)
        self.member.refresh_from_db()
        self.assertEqual(self.member.role, Role.MEMBER)

    def test_editor_cannot_manage_accounts(self):
        response = self.change(self.editor, self.member, action="role", role="editor")
        self.assertEqual(response.status_code, 403)

    def test_deactivate_and_activate(self):
        self.change(self.manager, self.member, action="deactivate")
        self.member.refresh_from_db()
        self.assertFalse(self.member.is_active)
        self.change(self.manager, self.member, action="activate")
        self.member.refresh_from_db()
        self.assertTrue(self.member.is_active)

    def test_accounts_tab_is_for_managers_only(self):
        self.client.force_login(self.editor)
        self.assertEqual(self.client.get("/manage/?tab=users").context["tab"], "queue")
        self.client.force_login(self.manager)
        self.assertContains(self.client.get("/manage/?tab=users"), "mem@example.com")


class OperationalPageTests(TestCase):
    def test_readiness_probe_checks_database_and_cache(self):
        payload = self.client.get("/health/ready/").json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["checks"]["database"], "ok")
        self.assertEqual(payload["checks"]["cache"], "ok")

    def test_robots_and_favicon(self):
        robots = self.client.get("/robots.txt")
        self.assertContains(robots, "Disallow: /admin/")
        favicon = self.client.get("/favicon.ico")
        self.assertEqual(favicon.status_code, 301)
        self.assertTrue(favicon["Location"].endswith("brand/favicon.ico"))

    @override_settings(DEBUG=False)
    def test_branded_404(self):
        response = self.client.get("/view/doesnotexist/")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Nada está passando por aqui", status_code=404)

    def test_sign_in_page_uses_the_portal_layout(self):
        response = self.client.get("/accounts/login/")
        self.assertContains(response, "auth-card")
        self.assertContains(response, "logo-mark.svg")

    def test_seed_demo_refuses_without_debug(self):
        with self.assertRaises(CommandError):
            call_command("seed_demo")


class TemplateHelperTests(TestCase):
    def render(self, source, **context):
        return Template("{% load akon %}" + source).render(Context(context))

    def test_compact_numbers(self):
        with translation.override("en"):
            self.assertEqual(self.render("{{ n|compact }}", n=950), "950")
            self.assertEqual(self.render("{{ n|compact }}", n=1234), "1.2K")
            self.assertEqual(self.render("{{ n|compact }}", n=34000), "34K")
            self.assertEqual(self.render("{{ n|compact }}", n=1500000), "1.5M")
        with translation.override("pt-br"):
            self.assertEqual(self.render("{{ n|compact }}", n=1234), "1,2 mil")
            self.assertEqual(self.render("{{ n|compact }}", n=2000), "2 mil")
            self.assertEqual(self.render("{{ n|compact }}", n=1500000), "1,5 mi")

    def test_clock(self):
        self.assertEqual(self.render("{{ s|clock }}", s=75), "1:15")
        self.assertEqual(self.render("{{ s|clock }}", s=3725), "1:02:05")

    def test_runtime_units(self):
        with translation.override("en"):
            self.assertEqual(self.render("{{ s|runtime_value }}{{ s|runtime_unit }}", s=45), "45s")
            self.assertEqual(self.render("{{ s|runtime_value }}{{ s|runtime_unit }}", s=150), "2.5min")
            self.assertEqual(self.render("{{ s|runtime_value }}{{ s|runtime_unit }}", s=7200), "2.0h")
        with translation.override("pt-br"):
            self.assertEqual(self.render("{{ s|runtime_value }}{{ s|runtime_unit }}", s=150), "2,5min")

    def test_timestamps_escape_before_linking(self):
        html = self.render("{{ text|timestamps }}", text="<script>x</script> at 10:05\nnext line")
        self.assertIn("&lt;script&gt;", html)
        self.assertIn('data-seek="605"', html)
        self.assertIn("<br>", html)

    def test_avatar_is_stable_per_account(self):
        user = User(email="stable@example.com", display_name="Rui Alves")
        first = self.render("{% avatar user %}", user=user)
        self.assertEqual(first, self.render("{% avatar user %}", user=user))
        self.assertIn(">RA<", first)
