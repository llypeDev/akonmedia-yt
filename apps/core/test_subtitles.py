"""Subtitle parsing, upload, editing and WebVTT output."""
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.media import subtitles as subs
from apps.media.models import Media, Subtitle, SubtitleCue
from apps.rbac.roles import Role
from apps.users.models import User

SRT = """1
00:00:01,000 --> 00:00:03,500
Primeira linha

2
00:00:04,000 --> 00:00:06,250
Segunda linha
com duas linhas
"""

VTT = """WEBVTT

00:00:01.000 --> 00:00:03.500
Hello there

00:00:04.000 --> 00:00:06.250
Second cue
"""


def make_media(owner, **kwargs):
    defaults = {
        "title": "Clip",
        "kind": Media.Kind.VIDEO,
        "state": Media.State.READY,
        "visibility": Media.Visibility.PUBLIC,
    }
    defaults.update(kwargs)
    return Media.objects.create(owner=owner, **defaults)


class TimestampTests(TestCase):
    def test_comma_and_dot_separators_agree(self):
        self.assertEqual(subs.parse_timestamp("00:00:01,500"), subs.parse_timestamp("00:00:01.500"))

    def test_round_trip(self):
        for value in (0, 999, 61_500, 3_599_999, 7_200_000):
            self.assertEqual(subs.parse_timestamp(subs.format_timestamp(value)), value)

    def test_short_fraction_is_padded(self):
        self.assertEqual(subs.parse_timestamp("00:00:01.5"), 1500)

    def test_garbage_is_rejected(self):
        with self.assertRaises(subs.SubtitleFormatError):
            subs.parse_timestamp("soon")


class ParseTests(TestCase):
    def test_srt_cues(self):
        cues = subs.parse(SRT)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0]["start_ms"], 1000)
        self.assertEqual(cues[0]["end_ms"], 3500)
        self.assertEqual(cues[0]["text"], "Primeira linha")

    def test_multiline_cue_keeps_its_line_break(self):
        self.assertEqual(subs.parse(SRT)[1]["text"], "Segunda linha\ncom duas linhas")

    def test_vtt_cues(self):
        cues = subs.parse(VTT)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[1]["start_ms"], 4000)

    def test_bom_and_crlf_are_tolerated(self):
        messy = "\ufeff" + SRT.replace("\n", "\r\n")
        self.assertEqual(len(subs.parse(messy)), 2)

    def test_empty_input(self):
        self.assertEqual(subs.parse(""), [])


class SerialiseTests(TestCase):
    def test_vtt_output_is_valid(self):
        output = subs.to_vtt(subs.parse(SRT))
        self.assertTrue(output.startswith("WEBVTT"))
        self.assertIn("00:00:01.000 --> 00:00:03.500", output)

    def test_srt_output_renumbers(self):
        output = subs.to_srt(subs.parse(SRT))
        self.assertTrue(output.startswith("1\n"))
        self.assertIn("00:00:01,000 --> 00:00:03,500", output)


class SubtitlePageTests(TestCase):
    def setUp(self):
        import shutil
        import uuid
        from pathlib import Path

        from django.conf import settings
        from django.test import override_settings

        self.media_root = Path(settings.BASE_DIR) / ".test-media" / uuid.uuid4().hex
        self.override = override_settings(MEDIA_ROOT=str(self.media_root))
        self.override.enable()

        self.owner = User.objects.create_user(email="sub@example.com", password="x" * 20)
        self.stranger = User.objects.create_user(email="st@example.com", password="x" * 20)
        self.media = make_media(self.owner)
        self.media.file.save("clip.mp4", SimpleUploadedFile("clip.mp4", b"0" * 32), save=True)

    def tearDown(self):
        import shutil

        self.override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)

    def upload(self, body=SRT, language="pt-BR", client=None):
        client = client or self.client
        return client.post(
            f"/view/{self.media.uid}/subtitles/",
            {
                "language": language,
                "label": "Português",
                "file": SimpleUploadedFile("track.srt", body.encode("utf-8")),
            },
            follow=True,
        )

    def test_owner_can_upload_a_track(self):
        self.client.force_login(self.owner)
        response = self.upload()
        self.assertEqual(response.status_code, 200)

        subtitle = Subtitle.objects.get(media=self.media)
        self.assertEqual(subtitle.language, "pt-br")
        self.assertEqual(subtitle.cues.count(), 2)

    def test_language_codes_are_normalised(self):
        self.client.force_login(self.owner)
        self.upload(language="PT_br")
        self.assertTrue(Subtitle.objects.filter(media=self.media, language="pt-br").exists())

    def test_stranger_cannot_upload(self):
        self.client.force_login(self.stranger)
        response = self.client.post(
            f"/view/{self.media.uid}/subtitles/",
            {"language": "en", "file": SimpleUploadedFile("t.srt", SRT.encode())},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Subtitle.objects.count(), 0)

    def test_reupload_replaces_the_cues(self):
        self.client.force_login(self.owner)
        self.upload()
        self.upload(body=VTT.replace("Hello there", "Replaced"))
        subtitle = Subtitle.objects.get(media=self.media)
        self.assertEqual(subtitle.cues.count(), 2)
        self.assertEqual(subtitle.cues.first().text, "Replaced")

    def test_unreadable_file_is_refused(self):
        self.client.force_login(self.owner)
        self.upload(body="not subtitles at all")
        self.assertEqual(Subtitle.objects.count(), 0)

    def test_editor_saves_edited_timings(self):
        self.client.force_login(self.owner)
        self.upload()
        subtitle = Subtitle.objects.get(media=self.media)

        response = self.client.post(
            f"/view/{self.media.uid}/subtitles/pt-br/",
            {
                "cue_start": ["00:00:02.000", "00:00:05.000"],
                "cue_end": ["00:00:04.000", "00:00:08.000"],
                "cue_text": ["Edited one", "Edited two"],
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)

        cues = list(subtitle.cues.order_by("position"))
        self.assertEqual(cues[0].start_ms, 2000)
        self.assertEqual(cues[0].text, "Edited one")
        self.assertEqual(cues[1].end_ms, 8000)

    def test_cue_ending_before_it_starts_is_rejected(self):
        self.client.force_login(self.owner)
        self.upload()
        subtitle = Subtitle.objects.get(media=self.media)

        self.client.post(
            f"/view/{self.media.uid}/subtitles/pt-br/",
            {"cue_start": ["00:00:10.000"], "cue_end": ["00:00:05.000"], "cue_text": ["bad"]},
        )
        self.assertEqual(subtitle.cues.count(), 2)

    def test_vtt_endpoint_serves_the_track(self):
        self.client.force_login(self.owner)
        self.upload()
        response = self.client.get(f"/view/{self.media.uid}/subtitles/pt-br/track.vtt")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/vtt; charset=utf-8")
        self.assertIn("WEBVTT", response.content.decode())

    def test_media_page_lists_the_track(self):
        self.client.force_login(self.owner)
        self.upload()
        response = self.client.get(f"/view/{self.media.uid}/")
        self.assertContains(response, "Português")
        self.assertContains(response, "<track")

    def test_editor_can_manage_someone_elses_subtitles(self):
        editor = User.objects.create_user(
            email="ed@example.com", password="x" * 20, role=Role.EDITOR
        )
        self.client.force_login(editor)
        self.upload()
        self.assertEqual(SubtitleCue.objects.count(), 2)
