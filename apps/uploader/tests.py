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
