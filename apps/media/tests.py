"""Media model and API behaviour."""
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.rbac.roles import Role
from apps.users.models import User

from .models import Category, Media, new_uid


@contextmanager
def media_root():
    """A throwaway MEDIA_ROOT inside the project tree.

    The system temp directory is not reliably writable in every environment,
    and Windows refuses to chmod some temp paths, so this stays local and
    cleanup ignores errors.
    """
    path = Path(settings.BASE_DIR) / ".test-media" / uuid.uuid4().hex
    try:
        with override_settings(MEDIA_ROOT=str(path)):
            yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def make_media(owner, **kwargs):
    defaults = {
        "title": kwargs.pop("title", "Clip"),
        "kind": Media.Kind.VIDEO,
        "state": Media.State.READY,
        "visibility": Media.Visibility.PUBLIC,
    }
    defaults.update(kwargs)
    return Media.objects.create(owner=owner, **defaults)


class UidTests(TestCase):
    def test_uid_is_short_and_url_safe(self):
        value = new_uid()
        self.assertEqual(len(value), 12)
        self.assertTrue(value.isalnum())
        self.assertEqual(value, value.lower())

    def test_uid_collisions_are_not_reused(self):
        self.assertEqual(len({new_uid() for _ in range(500)}), 500)


class MediaModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="o@example.com", password="x" * 20)

    def test_duration_label_formats_minutes_and_hours(self):
        self.assertEqual(make_media(self.user, duration_seconds=75).duration_label, "1:15")
        self.assertEqual(make_media(self.user, duration_seconds=3725).duration_label, "1:02:05")

    def test_public_requires_both_visibility_and_ready_state(self):
        self.assertTrue(make_media(self.user).is_public)
        self.assertFalse(make_media(self.user, state=Media.State.PENDING).is_public)
        self.assertFalse(make_media(self.user, visibility=Media.Visibility.PRIVATE).is_public)

    def test_category_slug_is_generated(self):
        category = Category.objects.create(name="Field Recordings")
        self.assertEqual(category.slug, "field-recordings")

    def test_same_media_cannot_appear_twice_in_a_playlist(self):
        from django.db import IntegrityError

        from .models import Playlist, PlaylistItem

        media = make_media(self.user)
        playlist = Playlist.objects.create(title="P", owner=self.user)
        PlaylistItem.objects.create(playlist=playlist, media=media, position=0)
        with self.assertRaises(IntegrityError):
            PlaylistItem.objects.create(playlist=playlist, media=media, position=1)


@override_settings(CELERY_TASK_ALWAYS_EAGER=False)
class MediaApiVisibilityTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(email="owner@example.com", password="x" * 20)
        self.stranger = User.objects.create_user(email="s@example.com", password="x" * 20)
        self.curator = User.objects.create_user(
            email="c@example.com", password="x" * 20, role=Role.EDITOR
        )

        self.public = make_media(self.owner, title="Public")
        self.private = make_media(
            self.owner, title="Private", visibility=Media.Visibility.PRIVATE
        )
        self.pending = make_media(self.owner, title="Pending", state=Media.State.PENDING)

    def titles(self, response):
        return {item["title"] for item in response.json()["results"]}

    def test_anonymous_sees_only_public_and_ready(self):
        self.assertEqual(self.titles(self.client.get("/api/v1/media/")), {"Public"})

    def test_owner_sees_own_private_items(self):
        self.client.force_authenticate(self.owner)
        self.assertEqual(
            self.titles(self.client.get("/api/v1/media/")), {"Public", "Private", "Pending"}
        )

    def test_stranger_does_not_see_private_items(self):
        self.client.force_authenticate(self.stranger)
        self.assertEqual(self.titles(self.client.get("/api/v1/media/")), {"Public"})

    def test_curator_sees_everything(self):
        self.client.force_authenticate(self.curator)
        self.assertEqual(
            self.titles(self.client.get("/api/v1/media/")), {"Public", "Private", "Pending"}
        )

    def test_lookup_is_by_uid_not_primary_key(self):
        response = self.client.get(f"/api/v1/media/{self.public.uid}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["title"], "Public")

    def test_creating_requires_authentication(self):
        response = self.client.post("/api/v1/media/", {"title": "x"}, format="multipart")
        self.assertIn(response.status_code, (401, 403))


@override_settings(CELERY_TASK_ALWAYS_EAGER=False)
class MediaApiWriteTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(email="w@example.com", password="x" * 20)
        self.stranger = User.objects.create_user(email="x@example.com", password="x" * 20)

    def test_owner_can_rename_own_media(self):
        media = make_media(self.owner)
        self.client.force_authenticate(self.owner)
        response = self.client.patch(
            f"/api/v1/media/{media.uid}/", {"title": "Renamed"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        media.refresh_from_db()
        self.assertEqual(media.title, "Renamed")

    def test_stranger_cannot_edit_someone_elses_media(self):
        media = make_media(self.owner)
        self.client.force_authenticate(self.stranger)
        response = self.client.patch(
            f"/api/v1/media/{media.uid}/", {"title": "Hijack"}, format="json"
        )
        self.assertEqual(response.status_code, 403)
        media.refresh_from_db()
        self.assertEqual(media.title, "Clip")

    def test_view_counter_increments(self):
        media = make_media(self.owner)
        response = self.client.post(f"/api/v1/media/{media.uid}/view/")
        self.assertEqual(response.status_code, 200)
        media.refresh_from_db()
        self.assertEqual(media.views_count, 1)


class MediaUploadKindTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(email="u@example.com", password="x" * 20)
        self.client.force_authenticate(self.owner)

    def test_png_upload_is_classified_as_image(self):
        with media_root():
            upload = SimpleUploadedFile("shot.png", b"\x89PNG\r\n\x1a\n" + b"0" * 64, "image/png")
            response = self.client.post(
                "/api/v1/media/", {"title": "Screenshot", "file": upload}, format="multipart"
            )
            self.assertEqual(response.status_code, 201, response.content)
            self.assertEqual(response.json()["kind"], "image")

    def test_uploaded_file_is_stored_under_its_uid(self):
        with media_root():
            upload = SimpleUploadedFile("note.pdf", b"%PDF-1.4 test", "application/pdf")
            response = self.client.post(
                "/api/v1/media/", {"title": "Doc", "file": upload}, format="multipart"
            )
            self.assertEqual(response.status_code, 201)
            media = Media.objects.get(uid=response.json()["uid"])
            self.assertIn(media.uid, media.file.name)
            self.assertTrue(Path(media.file.path).exists())


class TaskRoutingTests(TestCase):
    """Encoding has its own queue so housekeeping never waits behind a class."""

    def route(self, name):
        from config.celery import app

        return app.amqp.router.route({}, name, args=(), kwargs={})["queue"].name

    def test_encoding_tasks_go_to_the_media_queue(self):
        self.assertEqual(self.route("media.process"), "media")
        self.assertEqual(self.route("live.finalize"), "media")

    def test_housekeeping_stays_on_the_default_queue(self):
        self.assertEqual(self.route("live.housekeeping"), "celery")
        self.assertEqual(self.route("uploads.purge_stale"), "celery")

    def test_a_worker_without_queues_consumes_both(self):
        from config.celery import app

        self.assertEqual({queue.name for queue in app.amqp.queues.values()}, {"celery", "media"})


class InterruptedEncodingTests(TestCase):
    """A restart in the middle of an encoding must not leave a spinner forever."""

    def setUp(self):
        self.user = User.objects.create_user(email="enc@example.com", password="x" * 20)
        self.cut = make_media(self.user, title="cut", state=Media.State.PROCESSING)
        self.elsewhere = make_media(self.user, title="elsewhere", state=Media.State.PROCESSING)
        self.waiting = make_media(self.user, title="waiting", state=Media.State.PENDING)

    def worker(self, queues, active=None):
        from types import SimpleNamespace
        from unittest import mock

        inspect = mock.Mock()
        inspect.active.return_value = active or {}
        app = SimpleNamespace(
            amqp=SimpleNamespace(queues=SimpleNamespace(consume_from={name: None for name in queues})),
            control=SimpleNamespace(inspect=mock.Mock(return_value=inspect)),
        )
        return SimpleNamespace(app=app)

    def test_encoding_worker_releases_what_a_restart_cut_short(self):
        from .tasks import INTERRUPTED, fail_interrupted

        busy = {"media@other": [{"name": "media.process", "args": [str(self.elsewhere.pk)]}]}
        self.assertEqual(fail_interrupted(sender=self.worker(["celery", "media"], busy)), 1)
        self.cut.refresh_from_db()
        self.elsewhere.refresh_from_db()
        self.waiting.refresh_from_db()
        self.assertEqual(self.cut.state, Media.State.FAILED)
        self.assertEqual(self.cut.failure_reason, INTERRUPTED)
        self.assertEqual(self.elsewhere.state, Media.State.PROCESSING)
        self.assertEqual(self.waiting.state, Media.State.PENDING)

    def test_a_worker_that_does_not_encode_leaves_everything_alone(self):
        from .tasks import fail_interrupted

        self.assertEqual(fail_interrupted(sender=self.worker(["celery"])), 0)
        self.cut.refresh_from_db()
        self.assertEqual(self.cut.state, Media.State.PROCESSING)

    def test_no_answer_from_other_workers_still_releases(self):
        from .tasks import fail_interrupted

        sender = self.worker(["media"])
        sender.app.control.inspect.side_effect = OSError("broker down")
        self.assertEqual(fail_interrupted(sender=sender), 2)
