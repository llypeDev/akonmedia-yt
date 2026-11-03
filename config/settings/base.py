"""Settings shared by every environment.

Everything environment-specific is read from the process environment so the
same image runs in development, staging and production without edits.
"""
import os
from pathlib import Path

from kombu import Queue

BASE_DIR = Path(__file__).resolve().parent.parent.parent


# --------------------------------------------------------------------------
# environment helpers
# --------------------------------------------------------------------------

def env(name: str, default=None):
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def env_list(name: str, default=()):
    raw = os.environ.get(name)
    if not raw:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# --------------------------------------------------------------------------
# core
# --------------------------------------------------------------------------

SECRET_KEY = env("SECRET_KEY", "insecure-development-key-change-me")
DEBUG = env_bool("DEBUG", False)
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", ["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", [])

PORTAL_NAME = env("PORTAL_NAME", "QQplay")
PORTAL_DESCRIPTION = env("PORTAL_DESCRIPTION", "")
SITE_URL = env("SITE_URL", "http://localhost:8000").rstrip("/")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "django.contrib.humanize",
    # third party
    "rest_framework",
    "allauth",
    "allauth.account",
    # local
    "apps.core",
    "apps.rbac",
    "apps.users",
    "apps.media",
    "apps.uploader",
    "apps.live",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "apps.core.middleware.PortalLanguageMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
]

ROOT_URLCONF = "config.urls"

# Pages may frame themselves (the PDF viewer does); /embed/ opts out per view.
X_FRAME_OPTIONS = "SAMEORIGIN"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.i18n",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "config.context.portal",
            ],
        },
    },
]


# --------------------------------------------------------------------------
# database and cache
# --------------------------------------------------------------------------

if env("POSTGRES_HOST"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("POSTGRES_DB", "qqplay"),
            "USER": env("POSTGRES_USER", "qqplay"),
            "PASSWORD": env("POSTGRES_PASSWORD", ""),
            "HOST": env("POSTGRES_HOST", "localhost"),
            "PORT": env_int("POSTGRES_PORT", 5432),
            "CONN_MAX_AGE": env_int("POSTGRES_CONN_MAX_AGE", 60),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

REDIS_URL = env("REDIS_URL", "")
if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": REDIS_URL,
            "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
        }
    }
else:
    CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }

CELERY_BROKER_URL = env("CELERY_BROKER_URL", REDIS_URL or "memory://")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", REDIS_URL or "cache+memory://")
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", False)
CELERY_TASK_TIME_LIMIT = env_int("CELERY_TASK_TIME_LIMIT", 60 * 60)

#: Encoding goes to its own queue, so a long class being encoded never holds up
#: the quick housekeeping. A worker started without ``-Q`` consumes both; on a
#: small server, docker-compose.prod.yml gives each queue its own worker.
CELERY_TASK_QUEUES = (Queue("celery"), Queue("media"))
CELERY_TASK_ROUTES = {
    "media.process": {"queue": "media"},
    "live.finalize": {"queue": "media"},
}

#: Periodic housekeeping, run by the ``beat`` service. A run that could not
#: start in time is dropped instead of piling up behind a stopped worker.
CELERY_BEAT_SCHEDULE = {
    "purge-stale-uploads": {
        "task": "uploads.purge_stale",
        "schedule": 60 * 60,
        "options": {"expires": 30 * 60},
    },
    # Ends lives whose encoder never came back and finishes any that slipped
    # through; the ingest server's callbacks handle the usual case at once.
    "live-housekeeping": {
        "task": "live.housekeeping",
        "schedule": 30,
        "options": {"expires": 25},
    },
}

#: Unfinished chunked uploads older than this are removed by the purge task.
UPLOAD_SESSION_TTL_HOURS = env_int("UPLOAD_SESSION_TTL_HOURS", 24)


# --------------------------------------------------------------------------
# Live streaming: encoders publish RTMP to the ingest server (nginx with the
# RTMP module, see docker/live), viewers watch the HLS it writes.
# --------------------------------------------------------------------------

#: What encoders such as OBS connect to; shown in the live's control room.
LIVE_RTMP_URL = env("LIVE_RTMP_URL", "rtmp://localhost:1935/live")
#: Public base address of the HLS: <base>/<playback id>/index.m3u8.
LIVE_HLS_URL = env("LIVE_HLS_URL", "http://localhost:8080/hls")
#: The ingest server's control endpoint, used to cut an encoder off when a
#: live is ended from the studio. Internal: never expose it publicly.
LIVE_CONTROL_URL = env("LIVE_CONTROL_URL", "http://localhost:8081/control")
#: Shared with the ingest server, which puts it in every callback URL. While it
#: is empty the callbacks are refused and nobody can go live.
LIVE_HOOK_SECRET = env("LIVE_HOOK_SECRET", "")
#: Where the ingest server's recordings appear to this process.
LIVE_RECORDINGS_DIR = env("LIVE_RECORDINGS_DIR", str(BASE_DIR / ".live" / "recordings"))
#: How long a dropped encoder may take to reconnect before the live ends.
LIVE_RECONNECT_GRACE_SECONDS = env_int("LIVE_RECONNECT_GRACE_SECONDS", 120)
#: Where streams from a browser are published: the same ingest server, seen
#: from this process (inside Docker, rtmp://live:1935/live).
LIVE_RTMP_INTERNAL_URL = env("LIVE_RTMP_INTERNAL_URL", LIVE_RTMP_URL)
#: Working folder for streams from a browser (chunks waiting for the relay).
LIVE_BROWSER_DIR = env("LIVE_BROWSER_DIR", str(BASE_DIR / ".live" / "browser"))


# --------------------------------------------------------------------------
# authentication
# --------------------------------------------------------------------------

AUTH_USER_MODEL = "users.User"

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_REDIRECT_URL = "/"
LOGIN_URL = "/accounts/login/"

SITE_ID = 1

# Email is the only identifier: no separate username field anywhere.
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_EMAIL_VERIFICATION = env("ACCOUNT_EMAIL_VERIFICATION", "optional")
ACCOUNT_RATE_LIMITS = {"login_failed": "5/5m"}


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticatedOrReadOnly",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 24,
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
}


# --------------------------------------------------------------------------
# i18n, static, media
# --------------------------------------------------------------------------

#: Interface language for everyone who has not picked one in the switcher.
LANGUAGE_CODE = env("LANGUAGE_CODE", "pt-br")
LANGUAGES = [
    ("pt-br", "Português (Brasil)"),
    ("en", "English"),
]
LOCALE_PATHS = [BASE_DIR / "locale"]
#: Per-language overrides of Django's date formats (config/formats/<lang>/formats.py).
FORMAT_MODULE_PATH = ["config.formats"]
#: The switcher's choice survives the browser being closed.
LANGUAGE_COOKIE_AGE = 60 * 60 * 24 * 365
TIME_ZONE = env("TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").exists() else []

# WhiteNoise serves the collected files straight from the app, so a container
# needs no separate web server for them.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"
        if not DEBUG
        else "django.contrib.staticfiles.storage.StaticFilesStorage"
    },
}

MEDIA_URL = "media/"
MEDIA_ROOT = Path(env("MEDIA_ROOT", str(BASE_DIR / "media")))

#: When true, Django serves uploaded media itself. Fine for a single container or
#: a demo; put nginx or object storage in front of it for real traffic.
SERVE_MEDIA = env_bool("SERVE_MEDIA", False)

#: Behind a web server that understands X-Accel-Redirect (Caddy in
#: docker-compose.prod.yml, or nginx), Django only checks the file and sets its
#: headers, and the server sends the bytes from this internal prefix. Empty:
#: Django streams the file itself.
MEDIA_ACCEL_REDIRECT = env("MEDIA_ACCEL_REDIRECT", "")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

DATA_UPLOAD_MAX_MEMORY_SIZE = env_int("DATA_UPLOAD_MAX_MEMORY_SIZE", 10 * 1024 * 1024)
FILE_UPLOAD_MAX_MEMORY_SIZE = env_int("FILE_UPLOAD_MAX_MEMORY_SIZE", 10 * 1024 * 1024)


# --------------------------------------------------------------------------
# email
# --------------------------------------------------------------------------

DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", f"{PORTAL_NAME} <no-reply@localhost>")
EMAIL_HOST = env("EMAIL_HOST", "")
if EMAIL_HOST:
    EMAIL_PORT = env_int("EMAIL_PORT", 587)
    EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
    EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"


# --------------------------------------------------------------------------
# logging
# --------------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "{asctime} {levelname:8} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
}
