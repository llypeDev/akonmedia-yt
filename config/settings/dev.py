"""Development settings: everything permissive, nothing cached."""
from .base import *  # noqa: F401,F403

DEBUG = True

ALLOWED_HOSTS = ["*"]

# console email so signup and reset links appear in the terminal
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

CELERY_TASK_ALWAYS_EAGER = True

INTERNAL_IPS = ["127.0.0.1", "localhost"]
