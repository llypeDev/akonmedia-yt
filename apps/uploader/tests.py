"""Chunked upload behaviour."""
import shutil
import uuid
from pathlib import Path

from django.conf import settings
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.media.models import Media
from apps.users.models import User

from .models import UploadSession

CHUNK = 1024 * 1024  # 1 MB: the smallest size the API accepts


class UploadFlowTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(email="up@example.com", password="x" * 20)
        self.client.force_authenticate(self.user)
        self.tmp = Path(settings.BASE_DIR) / ".test-media" / uuid.uuid4().hex
        self.override = override_settings(
            MEDIA_ROOT=str(self.tmp), CELERY_TASK_ALWAYS_EAGER=False
        )
        self.override.enable()

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start(self, name="clip.mp4", total=CHUNK * 2):
        response = self.client.post(
            "/api/v1/uploads/",
            {"filename": name, "total_size": total, "chunk_size": CHUNK},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def test_session_reports_expected_chunk_count(self):
        session = self.start(total=CHUNK * 3)
        self.assertEqual(session["expected_chunks"], 3)
        self.assertEqual(session["next_index"], 0)
        self.assertEqual(session["progress"], 0.0)

    def test_out_of_order_chunk_is_rejected(self):
        session = self.start()
        response = self.client.put(
            f"/api/v1/uploads/{session['id']}/chunk/?index=1",
            data=b"x" * CHUNK,
            content_type="application/octet-stream",
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["expected_index"], 0)

    def test_complete_before_all_chunks_arrive_is_rejected(self):
        session = self.start()
        response = self.client.post(f"/api/v1/uploads/{session['id']}/complete/")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["received_bytes"], 0)

    def test_chunks_assemble_into_a_media_record(self):
        session = self.start(name="take.mp4", total=CHUNK * 2)

        for index in (0, 1):
            response = self.client.put(
                f"/api/v1/uploads/{session['id']}/chunk/?index={index}",
                data=bytes([index]) * CHUNK,
                content_type="application/octet-stream",
            )
            self.assertEqual(response.status_code, 200, response.content)

        response = self.client.post(
            f"/api/v1/uploads/{session['id']}/complete/", {"title": "Take one"}, format="json"
        )
        self.assertEqual(response.status_code, 201, response.content)

        media = Media.objects.get(uid=response.json()["media_uid"])
        self.assertEqual(media.title, "Take one")
        self.assertEqual(media.size_bytes, CHUNK * 2)
        self.assertEqual(media.owner, self.user)
        self.assertTrue(media.file.path.endswith("take.mp4"))

        stored = UploadSession.objects.get(pk=session["id"])
        self.assertTrue(stored.completed)
        self.assertEqual(stored.media_uid, media.uid)

    def test_second_user_cannot_touch_someone_elses_session(self):
        session = self.start()
        intruder = User.objects.create_user(email="in@example.com", password="x" * 20)
        self.client.force_authenticate(intruder)
        response = self.client.put(
            f"/api/v1/uploads/{session['id']}/chunk/?index=0",
            data=b"x" * CHUNK,
            content_type="application/octet-stream",
        )
        self.assertEqual(response.status_code, 404)

    def test_chunk_size_below_one_megabyte_is_refused(self):
        response = self.client.post(
            "/api/v1/uploads/",
            {"filename": "a.mp4", "total_size": 2048, "chunk_size": 512},
            format="json",
        )
        self.assertEqual(response.status_code, 400)


class ResumeAndHousekeepingTests(TestCase):
    """What the browser relies on to resume, cancel, and what cleans up after it."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(email="res@example.com", password="x" * 20)
        self.client.force_authenticate(self.user)
        self.tmp = Path(settings.BASE_DIR) / ".test-media" / uuid.uuid4().hex
        self.override = override_settings(MEDIA_ROOT=str(self.tmp), CELERY_TASK_ALWAYS_EAGER=False)
        self.override.enable()

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start(self, total=CHUNK * 2, chunk=CHUNK, name="clip.mp4"):
        response = self.client.post(
            "/api/v1/uploads/", {"filename": name, "total_size": total, "chunk_size": chunk}, format="json"
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def put(self, session, index, payload):
        return self.client.put(
            f"/api/v1/uploads/{session['id']}/chunk/?index={index}",
            data=payload, content_type="application/octet-stream",
        )

    def test_status_reports_progress_for_resuming(self):
        session = self.start()
        self.put(session, 0, b"a" * CHUNK)
        status = self.client.get(f"/api/v1/uploads/{session['id']}/").json()
        self.assertEqual(status["next_index"], 1)
        self.assertEqual(status["received_bytes"], CHUNK)
        self.assertEqual(status["progress"], 0.5)

    def test_status_realigns_after_a_torn_write(self):
        session = self.start()
        self.put(session, 0, b"a" * CHUNK)
        stored = UploadSession.objects.get(pk=session["id"])
        with stored.part_path.open("ab") as handle:  # half a chunk landed, the row never updated
            handle.write(b"b" * (CHUNK // 2))
        status = self.client.get(f"/api/v1/uploads/{session['id']}/").json()
        self.assertEqual(status["next_index"], 1)
        self.assertEqual(stored.bytes_on_disk(), CHUNK)

    def test_cancel_removes_session_and_bytes(self):
        session = self.start()
        self.put(session, 0, b"a" * CHUNK)
        part_dir = UploadSession.objects.get(pk=session["id"]).part_dir
        self.assertTrue(part_dir.exists())
        response = self.client.delete(f"/api/v1/uploads/{session['id']}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(UploadSession.objects.filter(pk=session["id"]).exists())
        self.assertFalse(part_dir.exists())

    def test_completion_drops_the_duplicate_part_file(self):
        from apps.media.models import Category

        category = Category.objects.create(name="Clips")
        session = self.start(total=CHUNK)
        self.put(session, 0, b"z" * CHUNK)
        part_dir = UploadSession.objects.get(pk=session["id"]).part_dir
        response = self.client.post(
            f"/api/v1/uploads/{session['id']}/complete/",
            {"title": "With details", "description": "About it", "category": category.pk},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertFalse(part_dir.exists())
        media = Media.objects.get(uid=response.json()["media_uid"])
        self.assertEqual(media.description, "About it")
        self.assertEqual(media.category, category)
        self.assertEqual(response.json()["url"], f"/view/{media.uid}/")

    def test_chunks_larger_than_the_form_limit_are_accepted(self):
        big = 12 * 1024 * 1024  # above DATA_UPLOAD_MAX_MEMORY_SIZE (10 MB)
        session = self.start(total=big, chunk=big)
        response = self.put(session, 0, b"q" * big)
        self.assertEqual(response.status_code, 200, response.content)

    def test_stale_sessions_are_purged(self):
        from datetime import timedelta

        from django.utils import timezone

        from .tasks import purge

        old = self.start()
        self.put(old, 0, b"a" * CHUNK)
        UploadSession.objects.filter(pk=old["id"]).update(updated_at=timezone.now() - timedelta(hours=48))
        fresh = self.start()
        self.assertEqual(purge(24), 1)
        self.assertFalse(UploadSession.objects.filter(pk=old["id"]).exists())
        self.assertTrue(UploadSession.objects.filter(pk=fresh["id"]).exists())
