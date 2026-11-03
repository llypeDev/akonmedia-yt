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


# --------------------------------------------------------------------------
# a lighter ladder: the source copied when it can be, one decode for the rest
# --------------------------------------------------------------------------

def _probe(**changes) -> pipeline.Probe:
    """A browser-ready 1080p H.264 source, changed as a test needs."""
    base = dict(duration=60.0, width=1920, height=1080, codec="h264", has_video=True,
                has_audio=True, pix_fmt="yuv420p", video_bitrate=4_000_000, rotation=0, video_index=0)
    base.update(changes)
    return pipeline.Probe(**base)


class PassthroughDecisionTests(TestCase):
    def test_browser_ready_source_is_copied(self):
        self.assertTrue(pipeline.can_pass_through(_probe(), 1080, 5000, gap=2.0))

    def test_what_a_browser_cannot_play_as_it_is_gets_encoded(self):
        for reason, changes in {
            "HEVC": {"codec": "hevc"},
            "10-bit": {"pix_fmt": "yuv420p10le"},
            "4:2:2": {"pix_fmt": "yuv422p"},
            "filmed sideways": {"rotation": 90},
            "too heavy to stream": {"video_bitrate": 17_000_000},
            "bit rate unknown": {"video_bitrate": 0},
            "not a rung height": {"height": 1088},
        }.items():
            with self.subTest(reason):
                self.assertFalse(pipeline.can_pass_through(_probe(**changes), 1080, 5000, gap=2.0))

    def test_sparse_keyframes_are_encoded_so_segments_stay_short(self):
        self.assertFalse(pipeline.can_pass_through(_probe(), 1080, 5000, gap=12.0))
        self.assertFalse(pipeline.can_pass_through(_probe(), 1080, 5000, gap=0.0))

    def test_only_the_top_rung_is_copied_at_the_source_rate(self):
        rungs = pipeline.plan_ladder(_probe(video_bitrate=6_200_000), gap=2.0)
        self.assertEqual([(r.height, r.copy) for r in rungs], [(1080, True), (720, False), (480, False)])
        self.assertEqual(rungs[0].bitrate, 6200)
        self.assertEqual(rungs[1].bitrate, 2800)

    def test_without_keyframe_information_everything_is_encoded(self):
        self.assertFalse(any(r.copy for r in pipeline.plan_ladder(_probe())))


class KeyframeGapTests(TestCase):
    def test_longest_stretch_including_the_tail(self):
        packets = "0.000000,K__\n0.033,___\n2.000000,K__\n4.000,K_\n4.5,__\n"
        self.assertEqual(pipeline.longest_keyframe_gap(packets, duration=11.0), 7.0)

    def test_order_and_noise_do_not_matter(self):
        packets = "6.0,K__\nN/A,K__\n0.0,K__\n2.0,___\n3.0,K__\n"
        self.assertEqual(pipeline.longest_keyframe_gap(packets, duration=6.0), 3.0)

    def test_no_keyframe_means_unknown(self):
        self.assertEqual(pipeline.longest_keyframe_gap("0.0,___\n", duration=5.0), 0.0)


class LadderCommandTests(TestCase):
    def command(self, rungs):
        return pipeline.ladder_command("ffmpeg", "in.mp4", "/out", rungs, segment_seconds=6, video_index=0)

    def test_one_read_of_the_source_for_every_rung(self):
        command = self.command([pipeline.Rung(1080, 5200, copy=True), pipeline.Rung(720, 2800), pipeline.Rung(480, 1400)])
        self.assertEqual(command.count("-i"), 1)
        graph = command[command.index("-filter_complex") + 1]
        self.assertIn("[0:0]split=2[in0][in1]", graph)
        self.assertIn("scale=-2:720,format=yuv420p[v0]", graph)
        self.assertIn("scale=-2:480,format=yuv420p[v1]", graph)
        self.assertEqual(command.count("copy"), 1)
        self.assertEqual(command.count("libx264"), 2)
        self.assertEqual(command.count("-crf"), 2)
        self.assertIn("expr:gte(t,n_forced*6)", command)
        playlists = [arg for arg in command if arg.endswith("index.m3u8")]
        self.assertEqual(playlists, ["/out/1080p/index.m3u8", "/out/720p/index.m3u8", "/out/480p/index.m3u8"])

    def test_a_single_encoded_rung_needs_no_split(self):
        graph = self.command([pipeline.Rung(360, 800)])
        self.assertEqual(graph[graph.index("-filter_complex") + 1], "[0:0]scale=-2:360,format=yuv420p[v0]")

    def test_a_copied_ladder_has_no_filter_at_all(self):
        self.assertNotIn("-filter_complex", self.command([pipeline.Rung(360, 800, copy=True)]))


class MeasuredBandwidthTests(TestCase):
    def setUp(self):
        self.dir = Path(settings.BASE_DIR) / ".test-media" / uuid.uuid4().hex
        self.dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_peak_and_average_come_from_the_segments(self):
        (self.dir / "seg_00000.ts").write_bytes(b"x" * 750_000)   # 6 Mbit in 6 s
        (self.dir / "seg_00001.ts").write_bytes(b"x" * 250_000)   # 2 Mbit in 4 s
        (self.dir / "index.m3u8").write_text(
            "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6.000000,\nseg_00000.ts\n"
            "#EXTINF:4.000000,\nseg_00001.ts\n#EXT-X-ENDLIST\n", encoding="utf-8")
        self.assertEqual(pipeline.measured_bandwidth(self.dir), (1_000_000, 800_000))

    def test_unreadable_playlist_gives_nothing(self):
        self.assertEqual(pipeline.measured_bandwidth(self.dir), (0, 0))


def _frame_hashes(path: Path, frames: int = 8) -> list[str]:
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0", "-frames:v", str(frames), "-f", "framemd5", "-"],
        capture_output=True, text=True, check=True, timeout=120,
    )
    return [line.rsplit(",", 1)[-1].strip() for line in result.stdout.splitlines() if line and not line.startswith("#")]


@requires_ffmpeg
class LighterLadderTests(TestCase):
    def setUp(self):
        self.root = Path(settings.BASE_DIR) / ".test-media" / uuid.uuid4().hex
        self.root.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def clip(self, name, *encoder, seconds=4, size="640x360"):
        target = self.root / name
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size={size}:rate=25",
             "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", *encoder, "-c:a", "aac", "-shortest", str(target)],
            capture_output=True, check=True, timeout=120,
        )
        return target

    def test_browser_ready_source_is_copied_untouched_and_the_rest_encoded(self):
        source = self.clip("ready.mp4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "50", "-b:v", "600k")
        master = pipeline.to_hls(source, self.root / "hls", info=pipeline.probe(source))
        self.assertEqual(_frame_hashes(self.root / "hls" / "360p" / "index.m3u8"), _frame_hashes(source),
                         "the copied rung is the original, frame for frame")
        text = master.read_text(encoding="utf-8")
        self.assertIn("AVERAGE-BANDWIDTH=", text)
        self.assertIn('CODECS="avc1.', text)

    def test_reprocessing_leaves_no_stale_segments(self):
        source = self.clip("ready.mp4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "50")
        stale = self.root / "hls" / "1080p" / "seg_00099.ts"
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b"old")
        pipeline.to_hls(source, self.root / "hls")
        self.assertFalse(stale.parent.exists(), "the previous ladder is gone")
        self.assertTrue((self.root / "hls" / "360p" / "index.m3u8").exists())

    def test_ten_bit_source_is_encoded_for_browsers(self):
        try:
            source = self.clip("ten.mp4", "-c:v", "libx264", "-pix_fmt", "yuv420p10le")
        except subprocess.CalledProcessError:
            self.skipTest("this libx264 cannot write 10-bit video")
        info = pipeline.probe(source)
        self.assertEqual(info.pix_fmt, "yuv420p10le")
        pipeline.to_hls(source, self.root / "hls", info=info)
        first = sorted((self.root / "hls" / "360p").glob("seg_*.ts"))[0]
        pix_fmt = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=pix_fmt", "-of", "csv=p=0", str(first)],
            capture_output=True, text=True, check=True,
        ).stdout.split()
        self.assertEqual(set(pix_fmt), {"yuv420p"}, "a ten-bit picture would not play in browsers")

    def test_long_videos_get_their_storyboard_from_keyframes(self):
        source = self.clip("long.mp4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "50", seconds=420, size="160x90")
        meta = pipeline.storyboard(source, self.root / "board.jpg", 420.0)
        self.assertGreaterEqual(meta["interval"], pipeline.STORYBOARD_KEYFRAMES_FROM)
        self.assertEqual(meta["count"], pipeline.STORYBOARD_MAX_TILES)
        self.assertTrue((self.root / "board.jpg").stat().st_size > 0)
