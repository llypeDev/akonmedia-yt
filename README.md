# AkonMedia — core

The clean-room core of AkonMedia: a media platform built from scratch, with no
code carried over from any previous implementation. Everything under this
directory is original work and is licensed MIT (see `LICENSE`).

## Status

Phase 5 of 6 — curation and captions. Playlists, moderation queues and an
in-browser subtitle editor are working. What remains is the cut-over itself.

| Area | State |
|---|---|
| Django project, settings per environment, Celery wiring | done |
| Email-based accounts (signup, login, reset) via `django-allauth` | done |
| Five-level role ladder with a single policy module | done |
| Session API (`/api/v1/whoami/`) and health probe | done |
| Media domain models: media, category, playlist, comment, subtitle, chapter | done |
| Resumable chunked upload API and browser upload page | done |
| ffprobe/ffmpeg pipeline: duration, dimensions, poster, HLS | done |
| Media REST API with visibility rules | done |
| API for comments, playlists and categories | done |
| Public library page, media page, comments, search | done |
| Adaptive HLS player with progressive fallback (vendored hls.js) | done |
| Embed page with iframe snippet, start time and title options | done |
| Playlists: create, add, remove, reorder, share | done |
| Curation queue: encoding, failures with retry, hidden comments | done |
| Subtitle upload (SRT/VTT), cue editor, WebVTT served per track | done |
| Cut-over: delete the legacy tree, switch the licence | next |

## Requirements

- Python 3.13
- `uv` (recommended) or `pip`

## Run it

```bash
uv venv .venv --python 3.13
uv pip install -r requirements.txt

python manage.py migrate
python manage.py createsuperuser

python manage.py runserver
```

Then open <http://localhost:8000>. Sign up at `/accounts/signup/`, or use the
superuser you just created at `/admin/`.

Without `POSTGRES_HOST` the project runs on SQLite, so nothing else is needed to
start. Set `POSTGRES_HOST` (and the related variables) to switch to PostgreSQL.

## Tests

```bash
python manage.py test apps
```

## Layout

```
config/
  settings/base.py      everything read from the environment
  settings/dev.py       permissive local defaults
  settings/prod.py      fails loudly if a secret is missing
  urls.py               admin, accounts, API, public pages
  celery.py             task discovery
apps/
  core/                 public pages (library, media, search) and /health/
  rbac/                 role ladder and the only place that interprets it
  users/                account model, admin, session API
  media/                domain models, REST API, ffmpeg pipeline, tasks
  uploader/             resumable chunked upload sessions
templates/              HTML, written for this project only
static/css/app.css      one hand-written stylesheet, no framework
```

## Design notes

**Email is the identifier.** There is no username column anywhere. Addresses
are normalised to lower case on save so one person cannot hold two accounts by
changing capitalisation.

**Roles are data, not conditionals.** `apps/rbac/roles.py` defines the ladder and
`apps/rbac/permissions.py` is the only module that reads it. Views and templates
ask `can_curate_others(user)`, never `user.role == "editor"`. Adding a level or
moving a boundary is one edit.

**Settings fail closed.** `config/settings/prod.py` raises at import time when
`SECRET_KEY` or `ALLOWED_HOSTS` is missing, rather than booting with a
development default in production.

## Environment

See `.env.example`. Nothing is required for local development; production needs
at minimum `SECRET_KEY`, `ALLOWED_HOSTS` and a database.
