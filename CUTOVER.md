# Cut-over checklist

Moving from the previous instance to this one. Each step is reversible until the
last one, which is not.

## 1. Take a copy of everything

```bash
# database
pg_dump --format=custom olddb > olddb.dump

# uploaded files
tar czf media-backup.tar.gz /path/to/old/media_files
```

Keep both until the new instance has run in production for a week.

## 2. Build the manifest

The importer reads a JSON manifest, not the old database. Produce one export
listing every item you want to keep:

```json
{
  "media_root": "/path/to/old/media_files",
  "default_owner": "ops@example.com",
  "entries": [
    {
      "legacy_id": "nc9rotyWP",
      "title": "Harbour at dawn",
      "description": "…",
      "kind": "video",
      "visibility": "public",
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
```

`legacy_id` is what makes the import repeatable: run it twice and records are
updated, never duplicated.

## 3. Rehearse

```bash
python manage.py import_legacy manifest.json --dry-run
python manage.py import_legacy manifest.json --skip-files   # schema only
```

Both are safe. The dry run writes nothing; `--skip-files` creates records without
copying bytes, which is the fastest way to find manifest mistakes.

## 4. Real import

```bash
python manage.py import_legacy manifest.json
```

Every entry lands as `ready`, so nothing needs re-encoding. Entries that fail are
reported and skipped; the run does not abort.

Imported accounts have no usable password. Send them a reset link rather than
inventing one.

## 5. Verify

```bash
python manage.py shell -c "from apps.media.models import Media; print(Media.objects.count())"
```

Then open the library page and spot-check ten items: poster, duration, playback,
subtitle tracks.

## 6. Freeze the old instance

Stop accepting writes on the old system before the final import, otherwise
anything uploaded after the export is lost. The safe order is:

1. Put the old instance in read-only mode.
2. Re-run the import (it updates in place).
3. Point DNS at the new instance.
4. Keep the old one reachable, read-only, for two weeks.

## 7. The part that is not reversible

The licence on this repository says MIT. That is true only while the tree
contains nothing derived from the previous codebase.

Before publishing under MIT:

- confirm the legacy tree is not in this repository and not in its history;
- confirm nothing in `static/`, `templates/` or the docs was copied across;
- then, and only then, ship it.

If both codebases still live in the same tree, the licence must describe that
reality instead.
