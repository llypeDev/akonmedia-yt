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


def make_tone(target: Path, seconds: int = 2, cover: bool = False) -> Path:
    """An MP3, optionally carrying an embedded cover picture like real music."""
    target.parent.mkdir(parents=True, exist_ok=True)
    command = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}"]
    if cover:
        command += ["-f", "lavfi", "-i", "color=c=0x7c6cff:s=320x320:d=1",
                    "-map", "0:a", "-map", "1:v", "-c:v", "mjpeg", "-frames:v", "1",
                    "-disposition:v", "attached_pic", "-id3v2_version", "3"]
    command += ["-c:a", "libmp3lame", str(target)]
    subprocess.run(command, capture_output=True, check=True, timeout=120)
    return target


@requires_ffmpeg
class AudioAndStillTests(TestCase):
    """The kinds that used to fail (audio) or look broken (images) in the library."""

    def setUp(self):
        self.root = Path(settings.BASE_DIR) / ".test-media" / uuid.uuid4().hex
        self.override = override_settings(MEDIA_ROOT=str(self.root), CELERY_TASK_ALWAYS_EAGER=False)
        self.override.enable()
        self.user = User.objects.create_user(email="aud@example.com", password="x" * 20)

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.root, ignore_errors=True)

    def _media(self, source: Path, kind: str) -> Media:
        from django.core.files import File

        media = Media(title=source.stem, owner=self.user, kind=kind, original_name=source.name)
        with source.open("rb") as handle:
            media.file.save(source.name, File(handle), save=True)
        return media

    def test_audio_without_cover_gets_a_waveform_poster(self):
        media = self._media(make_tone(self.root / "src" / "tone.mp3"), Media.Kind.AUDIO)
        self.assertEqual(process_media(str(media.pk)), "ready")
        media.refresh_from_db()
        self.assertEqual(media.state, Media.State.READY)
        self.assertGreater(media.duration_seconds, 1.0)
        self.assertTrue(media.poster.name.endswith("poster.jpg"))
        from PIL import Image

        with Image.open(media.poster.path) as poster:
            self.assertEqual(poster.size, pipeline.POSTER_SIZE)

    def test_audio_cover_art_becomes_the_poster(self):
        media = self._media(make_tone(self.root / "src" / "album.mp3", cover=True), Media.Kind.AUDIO)
        process_media(str(media.pk))
        media.refresh_from_db()
        self.assertEqual(media.state, Media.State.READY)
        from PIL import Image

        with Image.open(media.poster.path) as poster:
            self.assertEqual(poster.size, (320, 320))

    def test_sound_only_mp4_is_reclassified_as_audio(self):
        source = self.root / "src" / "voice.m4a"
        source.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=200:duration=2", "-c:a", "aac", str(source)],
            capture_output=True, check=True, timeout=120,
        )
        media = self._media(source, Media.Kind.VIDEO)
        process_media(str(media.pk))
        media.refresh_from_db()
        self.assertEqual(media.state, Media.State.READY)
        self.assertEqual(media.kind, Media.Kind.AUDIO)
        self.assertEqual(media.hls_playlist, "")

    def test_video_gets_a_storyboard_for_seek_previews(self):
        media = self._media(make_clip(self.root / "src" / "board.mp4", seconds=4, size="640x360"), Media.Kind.VIDEO)
        process_media(str(media.pk))
        media.refresh_from_db()
        self.assertEqual(media.state, Media.State.READY)
        self.assertTrue(media.storyboard.endswith("storyboard.jpg"))
        meta = media.storyboard_meta
        self.assertEqual(meta["width"], 160)
        self.assertGreaterEqual(meta["count"], 3)
        config = media.storyboard_config
        self.assertTrue(config["src"].startswith("/media/"))
        from PIL import Image

        with Image.open(Path(self.root, media.storyboard)) as sheet:
            self.assertEqual(sheet.size, (meta["cols"] * 160, meta["rows"] * 90))

    def test_images_get_an_upright_thumbnail(self):
        from PIL import Image

        source = self.root / "src" / "portrait.jpg"
        source.parent.mkdir(parents=True, exist_ok=True)
        picture = Image.new("RGB", (400, 200), (34, 211, 238))
        exif = picture.getexif()
        exif[0x0112] = 6  # "rotate 90° clockwise to display"
        picture.save(source, exif=exif)

        media = self._media(source, Media.Kind.IMAGE)
        process_media(str(media.pk))
        media.refresh_from_db()
        self.assertEqual((media.width, media.height), (200, 400))
        with Image.open(media.poster.path) as poster:
            self.assertEqual(poster.size, (200, 400))


class StoryboardPlanTests(TestCase):
    def test_short_clip_gets_one_tile_per_second(self):
        self.assertEqual(pipeline.plan_storyboard(12), (1.0, 12))

    def test_long_clip_is_capped(self):
        interval, count = pipeline.plan_storyboard(3600)
        self.assertEqual(count, pipeline.STORYBOARD_MAX_TILES)
        self.assertEqual(interval, 36.0)

    def test_no_duration_no_storyboard(self):
        self.assertEqual(pipeline.plan_storyboard(0), (0.0, 0))
