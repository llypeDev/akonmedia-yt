"""Configuration package for AkonMedia."""

from .celery import app as celery_app

__all__ = ("celery_app",)
