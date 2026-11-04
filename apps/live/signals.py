"""What happens to a live, for whoever wants to know (apps.integrations
posts it to the schools' servers). Each is sent with ``live=``."""
from django.dispatch import Signal

live_started = Signal()
live_ended = Signal()
recording_ready = Signal()
