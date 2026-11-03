"""Remove upload sessions that were started but never finished."""
from django.core.management.base import BaseCommand

from apps.uploader.tasks import purge


class Command(BaseCommand):
    help = "Delete unfinished upload sessions (and their part files) older than a cut-off."

    def add_arguments(self, parser):
        parser.add_argument(
            "--hours",
            type=int,
            default=None,
            help="Age in hours after which an unfinished session is removed "
                 "(default: UPLOAD_SESSION_TTL_HOURS)",
        )

    def handle(self, *args, **options):
        removed = purge(options["hours"])
        self.stdout.write(self.style.SUCCESS(f"removed {removed} stale upload session(s)"))
