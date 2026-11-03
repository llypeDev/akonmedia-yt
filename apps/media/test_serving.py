"""Byte-range serving and rendition planning."""
import os
import shutil
import uuid
from pathlib import Path

from django.conf import settings
from django.test import TestCase, override_settings

from . import pipeline
from .serving import content_type_for

PAYLOAD = bytes(range(256)) * 8  # 2048 deterministic bytes


class RenditionPlanTests(TestCase):
    def test_full_hd_gets_three_rungs(self):
        rungs = pipeline.plan_renditions(1920, 1080)
        self.assertEqual([height for height, _ in rungs], [1080, 720, 480])

    def test_hd_starts_below_its_own_height(self):
        self.assertEqual(
            [h for h, _ in pipeline.plan_renditions(1280, 720)], [720, 480, 360]
        )

    def test_small_clip_is_never_upscaled(self):
        rungs = pipeline.plan_renditions(320, 240)
        self.assertEqual([h for h, _ in rungs], [360])
        self.assertTrue(all(h >= 0 for h, _ in rungs))

    def test_unknown_dimensions_fall_back_to_a_single_rung(self):
        self.assertEqual(pipeline.plan_renditions(0, 0), [(720, 2800)])

    def test_bitrate_falls_as_the_rung_falls(self):
        rungs = pipeline.plan_renditions(1920, 1080)
        bitrates = [b for _, b in rungs]
        self.assertEqual(bitrates, sorted(bitrates, reverse=True))


class ContentTypeTests(TestCase):
    def test_streaming_types_are_explicit(self):
        self.assertEqual(content_type_for("x/index.m3u8"), "application/vnd.apple.mpegurl")
        self.assertEqual(content_type_for("x/seg_00001.ts"), "video/mp2t")

    def test_ordinary_types_come_from_mimetypes(self):
        self.assertTrue(content_type_for("photo.png").startswith("image/"))


@override_settings(SERVE_MEDIA=True, DEBUG=False)
class RangeRequestTests(TestCase):
    def setUp(self):
        self.root = Path(settings.BASE_DIR) / ".test-media" / uuid.uuid4().hex
        self.root.mkdir(parents=True, exist_ok=True)
        self.file = self.root / "clip.mp4"
        self.file.write_bytes(PAYLOAD)

        self.override = override_settings(MEDIA_ROOT=str(self.root))
        self.override.enable()
        self.url = "/media/clip.mp4"

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_plain_request_returns_the_whole_file(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Accept-Ranges"], "bytes")
        self.assertEqual(int(response["Content-Length"]), len(PAYLOAD))
        self.assertEqual(b"".join(response.streaming_content), PAYLOAD)

    def test_range_returns_206_and_only_the_slice(self):
        response = self.client.get(self.url, headers={"Range": "bytes=0-99"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response["Content-Range"], f"bytes 0-99/{len(PAYLOAD)}")
        self.assertEqual(int(response["Content-Length"]), 100)
        self.assertEqual(b"".join(response.streaming_content), PAYLOAD[:100])

    def test_open_ended_range_runs_to_the_end(self):
        response = self.client.get(self.url, headers={"Range": "bytes=1000-"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(b"".join(response.streaming_content), PAYLOAD[1000:])

    def test_suffix_range_returns_the_tail(self):
        response = self.client.get(self.url, headers={"Range": "bytes=-64"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(b"".join(response.streaming_content), PAYLOAD[-64:])

    def test_range_beyond_the_end_is_unsatisfiable(self):
        response = self.client.get(self.url, headers={"Range": "bytes=999999-"})
        self.assertEqual(response.status_code, 416)
        self.assertEqual(response["Content-Range"], f"bytes */{len(PAYLOAD)}")

    def test_end_past_the_file_is_clamped(self):
        response = self.client.get(self.url, headers={"Range": "bytes=2000-99999"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response["Content-Range"], f"bytes 2000-{len(PAYLOAD) - 1}/{len(PAYLOAD)}")

    def test_missing_file_is_404(self):
        self.assertEqual(self.client.get("/media/nope.mp4").status_code, 404)

    def test_path_traversal_is_refused(self):
        response = self.client.get("/media/../../manage.py")
        self.assertIn(response.status_code, (400, 404))


@override_settings(SERVE_MEDIA=True, DEBUG=False)
class UploadedContentSafetyTests(TestCase):
    """An upload must never run script in the portal's origin."""

    def setUp(self):
        self.root = Path(settings.BASE_DIR) / ".test-media" / uuid.uuid4().hex
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "page.html").write_text("<script>alert(document.cookie)</script>", encoding="utf-8")
        (self.root / "logo.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', encoding="utf-8")
        (self.root / "clip.mp4").write_bytes(PAYLOAD)
        (self.root / "doc.pdf").write_bytes(b"%PDF-1.4")
        self.override = override_settings(MEDIA_ROOT=str(self.root))
        self.override.enable()

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_html_is_downloaded_and_sandboxed(self):
        response = self.client.get("/media/page.html")
        self.assertTrue(response["Content-Disposition"].startswith("attachment"))
        self.assertIn("sandbox", response["Content-Security-Policy"])
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")

    def test_svg_renders_but_cannot_run_script(self):
        response = self.client.get("/media/logo.svg")
        self.assertFalse(response.get("Content-Disposition", "").startswith("attachment"))
        self.assertIn("sandbox", response["Content-Security-Policy"])

    def test_video_stays_inline_even_for_ranges(self):
        response = self.client.get("/media/clip.mp4", headers={"Range": "bytes=0-9"})
        self.assertEqual(response.status_code, 206)
        self.assertNotIn("Content-Disposition", response)
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")

    def test_pdf_is_not_sandboxed_so_the_viewer_works(self):
        response = self.client.get("/media/doc.pdf")
        self.assertNotIn("Content-Security-Policy", response)
        self.assertEqual(response["Content-Type"], "application/pdf")


@override_settings(SERVE_MEDIA=True, DEBUG=False)
class MalformedRangeTests(TestCase):
    def setUp(self):
        self.root = Path(settings.BASE_DIR) / ".test-media" / uuid.uuid4().hex
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "clip.mp4").write_bytes(PAYLOAD)
        self.override = override_settings(MEDIA_ROOT=str(self.root))
        self.override.enable()

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_inverted_range_is_unsatisfiable(self):
        response = self.client.get("/media/clip.mp4", headers={"Range": "bytes=500-100"})
        self.assertEqual(response.status_code, 416)

    def test_empty_suffix_is_unsatisfiable(self):
        response = self.client.get("/media/clip.mp4", headers={"Range": "bytes=-0"})
        self.assertEqual(response.status_code, 416)

    def test_common_media_types_are_known(self):
        self.assertEqual(content_type_for("a.mkv"), "video/x-matroska")
        self.assertEqual(content_type_for("a.m4a"), "audio/mp4")
        self.assertEqual(content_type_for("a.flac"), "audio/flac")
