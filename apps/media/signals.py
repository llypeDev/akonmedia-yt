"""Keep the disk in step with the database.

Deleting a media record removes its original, poster, HLS ladder and
storyboard. The work waits for the transaction to commit, so a rolled-back
delete never loses files.
"""
import logging
import shutil
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import Media

logger = logging.getLogger(__name__)


def _inside_media_root(path: Path) -> bool:
    root = Path(settings.MEDIA_ROOT).resolve()
    try:
        return root in path.resolve().parents
    except (OSError, ValueError):
        return False


@receiver(post_delete, sender=Media)
def remove_media_files(sender, instance: Media, **kwargs):
    folder = instance.media_directory()
    loose = []
    for field in (instance.file, instance.poster):
        if field and field.name:
            try:
                loose.append(Path(field.path))
            except (NotImplementedError, ValueError):
                continue

    def cleanup():
        if folder is not None and folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
        for path in loose:
            # A poster replaced in a later month lives in a sibling folder.
            if path.exists() and _inside_media_root(path):
                try:
                    path.unlink()
                except OSError:
                    logger.warning("could not remove %s", path)
                parent = path.parent
                if parent.name == instance.uid and not any(parent.iterdir()):
                    parent.rmdir()

    transaction.on_commit(cleanup)
