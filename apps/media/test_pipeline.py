"""End-to-end media processing.

These run only when ffmpeg and ffprobe are on PATH; the rest of the suite must
pass on a machine that cannot encode video.
"""
import shutil
import subprocess
import unittest
import uuid
from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.users.models import User

from . import pipeline
from .models import Media
from .tasks import process_media

requires_ffmpeg = unittest.skipUnless(
    pipeline.available(), "ffmpeg and ffprobe are not installed"
)


def make_clip(target: Path, seconds: int = 2, size: str = "1280x720") -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"testsrc=duration={seconds}:size={size}:rate=10",
            "-pix_fmt", "yuv420p", str(target),
        ],
        capture_output=True,
        check=True,
        timeout=120,
    )
    return target


@requires_ffmpeg
class VideoPipelineTests(TestCase):
    def setUp(self):
        self.root = Path(settings.BASE_DIR) / ".test-media" / uuid.uuid4().hex
        self.override = override_settings(MEDIA_ROOT=str(self.root))
        self.override.enable()

        self.client = APIClient()
        self.user = User.objects.create_user(email="vid@example.com", password="x" * 20)
        self.client.force_authenticate(self.user)

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.root, ignore_errors=True)

    def _upload(self, name: str, payload: bytes, content_type: str):
        response = self.client.post(
            "/api/v1/media/",
            {"title": "Clip", "file": SimpleUploadedFile(name, payload, content_type)},
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def test_video_is_probed_posted_and_packaged(self):
        source = make_clip(self.root / "source.mp4", seconds=2)

        with override_settings(CELERY_TASK_ALWAYS_EAGER=False):
            payload = self._upload("clip.mp4", source.read_bytes(), "video/mp4")
        media = Media.objects.get(uid=payload["uid"])
        self.assertEqual(media.state, Media.State.PENDING)

        result = process_media(str(media.pk))
        self.assertEqual(result, "ready")

        media.refresh_from_db()
        self.assertEqual(media.state, Media.State.READY)
        self.assertGreater(media.duration_seconds, 1.0)
        self.assertEqual(media.width, 1280)
        self.assertEqual(media.height, 720)
        self.assertTrue(media.poster.name.endswith("poster.jpg"))
        self.assertTrue(Path(media.poster.path).exists())

        master = Path(self.root, media.hls_playlist)
        self.assertEqual(master.name, "master.m3u8")
        self.assertTrue(master.exists())

        text = master.read_text(encoding="utf-8")
        self.assertIn("#EXT-X-STREAM-INF", text)

        variant_playlists = [line.strip() for line in text.splitlines() if line.strip().endswith(".m3u8")]
        self.assertEqual(
            len(variant_playlists), 3, "a 720p source should yield 720p, 480p and 360p"
        )

        for relative in variant_playlists:
            playlist = master.parent / relative
            self.assertTrue(playlist.exists(), f"missing variant {relative}")
            segments = list(playlist.parent.glob("seg_*.ts"))
            self.assertTrue(segments, f"no segments for {relative}")

    def test_corrupt_video_ends_in_failed_state_not_processing(self):
        payload = self._upload("broken.mp4", b"not a video at all" * 100, "video/mp4")
        media = Media.objects.get(uid=payload["uid"])

        process_media(str(media.pk))

        media.refresh_from_db()
        self.assertEqual(media.state, Media.State.FAILED)
        self.assertTrue(media.failure_reason)
        self.assertNotEqual(media.state, Media.State.PROCESSING)


class PipelineUnitTests(TestCase):
    def test_unavailable_encoder_raises_a_typed_error(self):
        with unittest.mock.patch("shutil.which", return_value=None):
            self.assertFalse(pipeline.available())
            with self.assertRaises(pipeline.PipelineUnavailable):
                pipeline._binary("ffmpeg")
