from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class LiveConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.live"
    verbose_name = _("Live streams")

    def ready(self):
        from django.db.models.signals import post_save

        from apps.media.models import Media

        post_save.connect(_recording_saved, sender=Media, dispatch_uid="live.recording_ready")


def _recording_saved(sender, instance, update_fields=None, **kwargs):
    """A live's recording has finished processing: say so."""
    if instance.state != instance.State.READY or (update_fields is not None and "state" not in update_fields):
        return
    from .models import Live
    from .signals import recording_ready

    live = Live.objects.filter(recording=instance).select_related("owner").first()
    if live is not None:
        recording_ready.send(sender=Live, live=live)
