# AkonMedia — core

The clean-room core of AkonMedia: a self-hosted media platform built from
scratch, with no code carried over from any previous implementation. The
application code is original work licensed MIT (see `LICENSE`); third-party
assets are listed under [Licences](#licences).

![Library, dark theme](docs/screenshots/home-dark.jpg)

| | |
|---|---|
| ![Watch page: adaptive player with chapter markers and a storyboard seek preview](docs/screenshots/watch.jpg) | ![Creator studio](docs/screenshots/studio.jpg) |
| ![Manage: queues, stats and account roles](docs/screenshots/manage.jpg) | ![Library, light theme](docs/screenshots/home-light.jpg) |

<p align="center"><img src="docs/screenshots/phone.jpg" alt="Watch page on a phone" width="300"></p>

## Status

Phase 5 of 6 — curation and captions are done, and the interface has been
rebuilt end to end. What remains is the cut-over itself.

| Area | State |
|---|---|
| Django project, settings per environment, Celery wiring | done |
| Email-based accounts (signup, login, reset, email management) via `django-allauth`, styled | done |
| Five-level role ladder with a single policy module | done |
| Visibility rules in one module: public, unlisted (link-only), private | done |
| Session API (`/api/v1/whoami/`), liveness and readiness probes | done |
| Media domain models: media, category, playlist, comment, subtitle, chapter | done |
| Resumable chunked upload: retries, resume after reload, cancel, purge of stale sessions | done |
| ffprobe/ffmpeg pipeline: poster, HLS ladder with `CODECS`, seek-preview storyboard | done |
| Audio: embedded cover art or a rendered waveform poster | done |
| Images: EXIF-aware thumbnails; PDFs: first-page preview (poppler) | done |
| Media REST API with visibility rules and object-level permissions | done |
| Library with hero, trending, filters by type and category, sorting | done |
| Watch page: custom player, chapters, threaded comments pinned to moments, up next | done |
| Player: quality menu, speed, captions, PiP, theater, stats overlay, keyboard, seek previews | done |
| Playlists: create, save from any video, reorder, share, play-all queue with autoplay | done |
| Embed page with start time, autoplay, title and controls options | done |
| Creator studio: metrics, filters, edit details, thumbnail, chapters, re-process, delete | done |
| Channel pages and account settings | done |
| Curation: encoding queue, failures with retry, comment moderation, account roles | done |
| Subtitle upload (SRT/VTT), cue editor synced to a preview, WebVTT and SRT download | done |
| Dark and light themes, phone layout, branded error pages | done |
| Import from a previous deployment (`import_legacy` command) | done |
| Cut-over: delete the legacy tree, switch the licence | see `CUTOVER.md` |

## Requirements

- Python 3.13
- `uv` (recommended) or `pip`
- ffmpeg and ffprobe for video and audio (poppler's `pdftoppm` for PDF previews, optional)

## Run it with Docker

```bash
cp .env.docker .env     # change SECRET_KEY before exposing this anywhere
docker compose up -d
docker compose exec web python manage.py createsuperuser
```

Open <http://localhost:8000>. The first boot applies migrations and collects
static files; workers wait for the database instead of racing it.

| Service | Role |
|---|---|
| `web` | gunicorn, serves the site and the API; health-checked on `/health/` |
| `worker` | Celery worker: transcoding, thumbnails, storyboards |
| `beat` | Celery scheduler: hourly purge of abandoned uploads |
| `db` | PostgreSQL 17 |
| `redis` | cache and broker |

The image carries ffmpeg and poppler, so encoding works out of the box. Uploaded
media is stored in the `media` volume; for real traffic put nginx or object
storage in front of it and set `SERVE_MEDIA=False`.

## Run it without Docker

```bash
uv venv .venv --python 3.13
uv pip install -r requirements.txt

python manage.py migrate
python manage.py createsuperuser

python manage.py runserver
```

Without `POSTGRES_HOST` the project runs on SQLite, so nothing else is needed to
start. Set `POSTGRES_HOST` (and the related variables) to switch to PostgreSQL.
In development Celery runs tasks inline, so an upload is encoded before the
"complete" request returns.

### Demo content

```bash
python manage.py seed_demo           # about a minute
python manage.py seed_demo --quick   # shorter clips
```

Generates six videos (1080p and 720p, so you see the whole ladder), three audio
pieces, three images and a PDF with ffmpeg and Pillow, runs them through the
real pipeline, and adds chapters, English and Portuguese subtitles, threaded
comments and three playlists. It creates four accounts with a known password
(`demo-pass-123`), so it refuses to run with `DEBUG` off unless you pass
`--force`.

## Tests

```bash
python manage.py test apps
```

The pipeline tests run only where ffmpeg is installed.

### End-to-end smoke test

`tools/e2e/smoke.mjs` drives a real browser through the product: sign up,
upload a video in chunks while one chunk is deliberately dropped, wait for
encoding, play the HLS stream and switch quality, comment and reply, build a
playlist and play it, edit details, chapters and thumbnail, add and edit
subtitles, studio, search, themes, upload and delete an image, embed, phone
layouts, and a manager changing a role — 39 checks, and it fails on any
JavaScript error.

```bash
npm i -D playwright && npx playwright install chromium   # once
python manage.py seed_demo && python manage.py runserver &
BASE_URL=http://127.0.0.1:8000 \
E2E_MANAGER_EMAIL=kai@example.com E2E_MANAGER_PASSWORD=demo-pass-123 \
node tools/e2e/smoke.mjs
```

## Layout

```
config/
  settings/base.py      everything read from the environment
  settings/dev.py       permissive local defaults
  settings/prod.py      fails loudly if a secret is missing
  urls.py               admin, accounts, API, public pages, favicon, robots
  context.py            values every template needs
  celery.py             task discovery
apps/
  core/                 pages: library, watch, search, channel, studio, edit,
                        playlists, subtitles, manage; template tags; health
  rbac/                 role ladder and the only place that interprets it
  users/                account model, admin, session API
  media/                models, access rules, REST API, ffmpeg pipeline, tasks,
                        byte-range serving, importer, demo seeder
  uploader/             resumable chunked upload sessions and their purge
templates/              HTML, written for this project only (allauth included)
static/css/app.css      the design system: tokens, themes, layout, components
static/css/player.css   the player's look
static/js/              app.js (interface), player.js, upload.js
static/brand/           logo mark, wordmarks, favicons, app icons, OG image
static/icons/sprite.svg icon sprite (Lucide, see Licences)
static/vendor/          third-party builds and fonts, licence kept alongside
tools/build_logo.py     regenerates the brand kit, web fonts and icon sprite
tools/e2e/smoke.mjs     browser end-to-end test
```

## Design notes

**Streaming is a ladder, not a file.** Each video is encoded into up to three
renditions (1080/720/480 or 720/480/360, depending on the source) and a
`master.m3u8` that lists them with resolution, bandwidth and RFC 6381 `CODECS`
read back from the encoded segments. A source is never upscaled. The player
uses hls.js everywhere except Apple devices, so the quality menu and the stats
overlay work in every browser, and it falls back to the original file if the
stream fails.

**Seek previews are one request.** The pipeline also writes a storyboard — a
single sprite sheet of up to 100 thumbnails — and its grid metadata. Hovering
the seek bar shows the right tile without fetching anything else.

**Nothing in the library looks broken.** Audio gets its embedded cover art or,
failing that, a waveform drawn in the brand colours; images get an upright
thumbnail; PDFs get a first-page preview. An `.mp4` that only carries sound is
reclassified as audio. Posters are a convenience: if one cannot be produced the
item is still published.

**Media is served with byte ranges, safely.** `apps/media/serving.py` answers
`206` for `Range` requests (and `416` for impossible ones). Every response
carries `X-Content-Type-Options: nosniff` and a sandboxing CSP; anything that is
not audio, video, an image or a PDF is sent as a download, so an uploaded HTML
or SVG file can never run script with the portal's cookies. For real traffic,
put nginx or object storage in front.

**Visibility is data, read in one place.** `apps/media/access.py` says who may
see what: public items are listed, unlisted items open for anyone with the link
(page, embed, subtitles, API detail) but never appear in listings, private
items are for the owner and curators. Playlist pages filter their entries with
the same rules, so a public playlist cannot leak someone's private video.

**Roles are data, not conditionals.** `apps/rbac/roles.py` defines the ladder
and `apps/rbac/permissions.py` is the only module that reads it. Views and
templates ask `can_curate_others(user)`, never `user.role == "editor"`. A
manager can grant roles up to their own level, never above it.

**Uploads survive bad networks.** The browser sends 5 MB chunks with
XMLHttpRequest (for real progress, speed and time remaining), retries each one
with exponential backoff, re-synchronises on `409`, and remembers the session
per file so choosing the same file after a reload continues where it stopped.
Bytes are read from the raw stream, so chunks up to 32 MB work regardless of
`DATA_UPLOAD_MAX_MEMORY_SIZE`. The part file is deleted once the media item
exists, and beat purges sessions abandoned for more than
`UPLOAD_SESSION_TTL_HOURS` (or run `manage.py purge_uploads`).

**Deleting media frees the disk.** Removing an item removes its folder —
original, poster, HLS ladder and storyboard — after the transaction commits.

**Email addresses are personal data.** Public pages show display names; the API
returns an owner's or author's address only to that person and to curators.

**The wordmark is outlines, not text.** `static/brand/` is generated by
`tools/build_logo.py`, which converts Space Grotesk to paths. The mark is a play
button cut into three rungs — the bitrate ladder the platform encodes. The same
script subsets and vendors the UI fonts and builds the icon sprite, so the site
needs no CDN at runtime.

**Email is the identifier.** There is no username column anywhere. Addresses
are normalised to lower case on save so one person cannot hold two accounts by
changing capitalisation.

**Settings fail closed.** `config/settings/prod.py` raises at import time when
`SECRET_KEY` or `ALLOWED_HOSTS` is missing, rather than booting with a
development default in production.

## Regenerating the brand kit

```bash
uv pip install -r requirements-dev.txt     # fonttools, brotli, cairosvg…
python tools/build_logo.py                 # or: brand | fonts | icons
```

## Environment

See `.env.example`. Nothing is required for local development; production needs
at minimum `SECRET_KEY`, `ALLOWED_HOSTS` and a database.

## Licences

The application code is MIT. Third-party files keep their own licences, next to
them in `static/vendor/` (see `static/vendor/README.md`): hls.js (Apache-2.0),
Inter, Space Grotesk and JetBrains Mono (SIL Open Font License 1.1), and the
Lucide icons used to build `static/icons/sprite.svg` (ISC).
