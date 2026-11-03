"""Celery application.

Task modules are discovered automatically, so new apps only need to add
``tasks.py`` to their package.
"""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("qqplay")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
