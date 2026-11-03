# Operations

Day-two tasks for a running AkonMedia instance.

## What holds your data

Two Docker volumes. Lose them and the library is gone.

| Volume | Contents |
|---|---|
| `akonmedia_postgres` | accounts, media records, comments, playlists, subtitles |
| `akonmedia_media` | original uploads, posters, HLS segments |

## Backup

Both, together, while the stack is running:

```bash
docker compose exec -T db pg_dump -U akonmedia -Fc akonmedia > akonmedia-$(date +%F).dump
docker run --rm -v akonmedia_media:/data -v "$PWD":/backup alpine \
  tar czf /backup/media-$(date +%F).tar.gz -C /data .
```

Keep the pair. A database dump without the media volume restores a catalogue of
broken links; the media without the database is a folder nobody can browse.

## Restore

```bash
docker compose up -d db
docker compose exec -T db pg_restore -U akonmedia -d akonmedia --clean --if-exists < akonmedia-2026-11-03.dump
docker run --rm -v akonmedia_media:/data -v "$PWD":/backup alpine \
  sh -c "rm -rf /data/* && tar xzf /backup/media-2026-11-03.tar.gz -C /data"
docker compose up -d
```

The web container applies any pending migrations on boot, so it is safe to
restore a dump taken before an upgrade.

## Verify after a restore

```bash
docker compose ps                      # five services, web and worker Up
curl -s localhost:8000/health/         # {"status": "ok"}
```

Then open the library and spot-check three items: poster, playback, subtitle
track.

## Housekeeping

- **Storage grows by design.** Every video keeps its original plus one copy per
  rendition. Check `docker system df` monthly.
- **Failed encodes are visible.** Anything that could not be processed appears in
  `/manage/` under *Failed*, with the reason. Nothing sits silently pending.
- **The secret key** in `.env` signs sessions. Changing it logs everyone out.

## One thing that is not a task

This repository contains no code from any earlier system, and its licence says so
accurately. If that ever changes — a file copied in from somewhere else — the
licence has to change with it, on the same commit.
