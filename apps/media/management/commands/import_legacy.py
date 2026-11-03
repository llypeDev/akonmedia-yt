"""Import media from an earlier deployment described by a JSON manifest.

The manifest is deliberately dumb: it lists what to bring over and where the
files live, and nothing about the old database schema. That keeps the bridge
between two systems to one file format instead of a coupling between schemas.

Shape::

    {
      "media_root": "/srv/old/media",          // optional, prefixes relative paths
      "default_owner": "ops@example.com",      // optional, for entries without one
      "entries": [
        {
          "legacy_id": "nc9rotyWP",
          "title": "Harbour at dawn",
          "description": "…",
          "kind": "video",                      // video|audio|image|document
          "visibility": "public",               // public|unlisted|private
          "owner": "someone@example.com",
          "file": "encoded/nc9rotyWP/original.mp4",
          "poster": "encoded/nc9rotyWP/poster.jpg",
          "duration_seconds": 92.4,
          "width": 1280,
          "height": 720,
          "created_at": "2024-03-02T10:11:12Z",
          "views_count": 41
        }
      ]
    }

Running the command twice is safe: an entry whose ``legacy_id`` already exists is
updated, never duplicated.
"""
import json
import shutil
from datetime import datetime
from datetime import timezone as dt_timezone
from pathlib import Path

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.media.api import kind_for
from apps.media.models import Media
from apps.users.models import User

VALID_KINDS = {choice for choice, _ in Media.Kind.choices}
VALID_VISIBILITY = {choice for choice, _ in Media.Visibility.choices}


class Command(BaseCommand):
    help = "Import media from a JSON manifest produced by an earlier deployment."

    def add_arguments(self, parser):
        parser.add_argument("manifest", help="Path to the JSON manifest")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would happen without writing anything",
        )
        parser.add_argument(
            "--skip-files",
            action="store_true",
            help="Create records without copying files (useful for a schema rehearsal)",
        )

    def handle(self, *args, **options):
        manifest_path = Path(options["manifest"]).expanduser()
        if not manifest_path.exists():
            raise CommandError(f"manifest not found: {manifest_path}")

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise CommandError(f"manifest is not valid JSON: {exc}") from exc

        entries = manifest.get("entries") or []
        if not isinstance(entries, list):
            raise CommandError("manifest 'entries' must be a list")

        media_root = manifest.get("media_root")
        base = Path(media_root).expanduser() if media_root else manifest_path.parent
        default_owner = manifest.get("default_owner")

        created = updated = skipped = failed = 0
        for index, entry in enumerate(entries, start=1):
            try:
                outcome = self._import_one(
                    entry,
                    base=base,
                    default_owner=default_owner,
                    dry_run=options["dry_run"],
                    skip_files=options["skip_files"],
                )
            except Exception as exc:  # noqa: BLE001 - one bad record must not stop the run
                failed += 1
                self.stderr.write(f"[{index}] {entry.get('legacy_id', '?')}: {exc}")
                continue

            if outcome == "created":
                created += 1
            elif outcome == "updated":
                updated += 1
            else:
                skipped += 1

        prefix = "would import" if options["dry_run"] else "imported"
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}: {created} created, {updated} updated, "
                f"{skipped} skipped, {failed} failed"
            )
        )

    # ------------------------------------------------------------------

    def _import_one(self, entry, *, base: Path, default_owner, dry_run: bool, skip_files: bool):
        legacy_id = str(entry.get("legacy_id") or "").strip()
        if not legacy_id:
            raise ValueError("missing legacy_id")

        title = (entry.get("title") or "").strip()
        if not title:
            raise ValueError("missing title")

        kind = entry.get("kind") or "video"
        if kind not in VALID_KINDS:
            kind = kind_for(entry.get("file", ""), "") or Media.Kind.VIDEO

        visibility = entry.get("visibility") or Media.Visibility.PUBLIC
        if visibility not in VALID_VISIBILITY:
            visibility = Media.Visibility.PRIVATE

        owner = self._resolve_owner(entry.get("owner") or default_owner, dry_run=dry_run)

        source = None
        if entry.get("file"):
            source = (base / entry["file"]).expanduser()
            if not source.exists() and not skip_files:
                raise FileNotFoundError(f"file not found: {source}")

        payload = {
            "title": title[:255],
            "description": entry.get("description") or "",
            "kind": kind,
            "visibility": visibility,
            "duration_seconds": float(entry.get("duration_seconds") or 0),
            "width": int(entry.get("width") or 0),
            "height": int(entry.get("height") or 0),
            "views_count": int(entry.get("views_count") or 0),
            "original_name": Path(entry.get("file") or "").name[:255],
        }
        created_at = _parse_date(entry.get("created_at"))
        if created_at:
            payload["created_at"] = created_at

        existing = Media.objects.filter(legacy_id=legacy_id).first()
        if dry_run:
            return "created" if existing is None else "updated"

        if existing is None:
            media = Media(legacy_id=legacy_id, owner=owner, **payload)
            if not skip_files and source is not None:
                with source.open("rb") as handle:
                    media.file.save(payload["original_name"], File(handle), save=False)
                media.size_bytes = source.stat().st_size
            media.state = Media.State.READY
            media.published_at = media.created_at
            media.save()
            outcome = "created"
        else:
            for field, value in payload.items():
                setattr(existing, field, value)
            existing.owner = owner
            existing.state = Media.State.READY
            existing.save()
            media = existing
            outcome = "updated"

        poster = entry.get("poster")
        if poster and not skip_files:
            poster_path = (base / poster).expanduser()
            if poster_path.exists():
                # Overwrite in place. Left to itself, storage would append a
                # random suffix on every re-run and the directory would grow.
                from apps.media.models import poster_upload_path

                canonical = poster_upload_path(media, poster_path.name)
                storage = media.poster.storage
                if media.poster and media.poster.name and media.poster.name != canonical:
                    media.poster.delete(save=False)
                if storage.exists(canonical):
                    storage.delete(canonical)
                with poster_path.open("rb") as handle:
                    media.poster.save(canonical, File(handle), save=True)

        return outcome

    def _resolve_owner(self, email, *, dry_run: bool):
        email = (email or "").strip().lower()
        if not email:
            raise ValueError("no owner and no default_owner in the manifest")

        user = User.objects.filter(email=email).first()
        if user is not None:
            return user
        if dry_run:
            return None

        # An imported account has no usable password: the person must reset.
        return User.objects.create_user(email=email, password=None, display_name=email.split("@")[0])


def _parse_date(raw):
    if not raw:
        return None
    parsed = parse_datetime(str(raw))
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, dt_timezone.utc)
    return parsed
