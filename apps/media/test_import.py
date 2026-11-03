"""The importer that bridges an earlier deployment into this schema."""
import json
import shutil
import uuid
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from apps.media.models import Media
from apps.users.models import User

MANIFEST = {
    "default_owner": "ops@example.com",
    "entries": [
        {
            "legacy_id": "abc123",
            "title": "Harbour at dawn",
            "description": "Imported from the old instance.",
            "kind": "video",
            "visibility": "public",
            "file": "files/harbour.mp4",
            "poster": "files/harbour.jpg",
            "duration_seconds": 92.4,
            "width": 1280,
            "height": 720,
            "created_at": "2024-03-02T10:11:12Z",
            "views_count": 41,
        },
        {
            "legacy_id": "def456",
            "title": "Studio tour",
            "kind": "video",
            "visibility": "private",
            "owner": "someone@example.com",
            "file": "files/studio.mp4",
        },
    ],
}


class ImportLegacyTests(TestCase):
    def setUp(self):
        self.source = Path(settings.BASE_DIR) / ".test-import" / uuid.uuid4().hex
        self.files = self.source / "files"
        self.files.mkdir(parents=True)
        (self.files / "harbour.mp4").write_bytes(b"x" * 2048)
        (self.files / "harbour.jpg").write_bytes(b"\xff\xd8\xff" + b"0" * 64)
        (self.files / "studio.mp4").write_bytes(b"y" * 512)

        self.media_root = self.source / "media"
        self.override = override_settings(MEDIA_ROOT=str(self.media_root))
        self.override.enable()

        self.manifest = self.source / "manifest.json"
        self.write_manifest()

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.source, ignore_errors=True)

    def write_manifest(self, manifest=None):
        self.manifest.write_text(json.dumps(manifest or MANIFEST), encoding="utf-8")

    def run_import(self, *args):
        out = StringIO()
        call_command("import_legacy", str(self.manifest), *args, stdout=out, stderr=StringIO())
        return out.getvalue()

    def test_creates_records_and_copies_files(self):
        self.run_import()

        self.assertEqual(Media.objects.count(), 2)
        first = Media.objects.get(legacy_id="abc123")
        self.assertEqual(first.title, "Harbour at dawn")
        self.assertEqual(first.state, Media.State.READY)
        self.assertEqual(first.duration_seconds, 92.4)
        self.assertEqual(first.views_count, 41)
        self.assertEqual(first.size_bytes, 2048)
        self.assertTrue(Path(first.file.path).exists())
        # The upload path normalises the poster name to poster.<ext>
        self.assertTrue(first.poster.name.endswith("poster.jpg"))
        self.assertTrue(Path(first.poster.path).exists())
        self.assertIsNotNone(first.published_at)

    def test_images_keep_their_dimensions_from_the_manifest(self):
        self.run_import()
        media = Media.objects.get(legacy_id="abc123")
        self.assertEqual((media.width, media.height), (1280, 720))

    def test_owner_is_created_with_an_unusable_password(self):
        self.run_import()
        owner = User.objects.get(email="someone@example.com")
        self.assertFalse(owner.has_usable_password())

    def test_default_owner_is_applied(self):
        self.run_import()
        self.assertEqual(Media.objects.get(legacy_id="abc123").owner.email, "ops@example.com")

    def test_running_twice_updates_instead_of_duplicating(self):
        self.run_import()
        changed = json.loads(json.dumps(MANIFEST))
        changed["entries"][0]["title"] = "Harbour at first light"
        self.write_manifest(changed)

        self.run_import()

        self.assertEqual(Media.objects.count(), 2)
        self.assertEqual(Media.objects.get(legacy_id="abc123").title, "Harbour at first light")

    def test_dry_run_writes_nothing(self):
        output = self.run_import("--dry-run")

        self.assertEqual(Media.objects.count(), 0)
        self.assertIn("would import", output)
        self.assertIn("2 created", output)

    def test_missing_file_is_reported_without_stopping_the_run(self):
        broken = json.loads(json.dumps(MANIFEST))
        broken["entries"][1]["file"] = "files/does-not-exist.mp4"
        self.write_manifest(broken)

        self.run_import()

        self.assertTrue(Media.objects.filter(legacy_id="abc123").exists())
        self.assertFalse(Media.objects.filter(legacy_id="def456").exists())

    def test_skip_files_creates_records_without_media(self):
        self.run_import("--skip-files")

        media = Media.objects.get(legacy_id="abc123")
        self.assertEqual(media.state, Media.State.READY)
        self.assertEqual(media.size_bytes, 0)

    def test_entries_without_legacy_id_are_rejected(self):
        bad = {"entries": [{"title": "No identity"}]}
        self.write_manifest(bad)
        self.run_import()
        self.assertEqual(Media.objects.count(), 0)

    def test_unknown_kind_falls_back_to_file_detection(self):
        odd = json.loads(json.dumps(MANIFEST))
        odd["entries"] = [odd["entries"][0]]
        odd["entries"][0]["kind"] = "hologram"
        odd["entries"][0]["file"] = "files/harbour.mp4"
        self.write_manifest(odd)

        self.run_import()
        self.assertEqual(Media.objects.get(legacy_id="abc123").kind, Media.Kind.VIDEO)

    def test_bad_json_is_a_command_error(self):
        self.manifest.write_text("{not json", encoding="utf-8")
        with self.assertRaises(CommandError):
            self.run_import()
