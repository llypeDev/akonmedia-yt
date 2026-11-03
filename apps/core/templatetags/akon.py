"""Template helpers for the QQplay interface.

    {% load akon %}
    {% icon "play" %}                  inline SVG from the sprite
    {{ media.views_count|compact }}   1234 -> 1.2K
    {{ chapter.start_seconds|clock }} 75 -> 1:15
    {% avatar user "lg" %}            initials on a colour derived from the account
    {{ comment.body|timestamps }}     "see 1:23" -> a link that seeks the player
"""
import hashlib
import re

from django import template
from django.templatetags.static import static
from django.utils.formats import get_format, number_format
from django.utils.html import conditional_escape, format_html
from django.utils.safestring import mark_safe
from django.utils.translation import pgettext

register = template.Library()


@register.simple_tag
def icon(name: str, css: str = "", label: str = ""):
    """An icon from ``static/icons/sprite.svg``. Decorative unless labelled."""
    href = f"{static('icons/sprite.svg')}#i-{name}"
    if label:
        return format_html(
            '<svg class="icon {}" role="img" aria-label="{}" focusable="false">'
            '<use href="{}"></use></svg>',
            css, label, href,
        )
    return format_html(
        '<svg class="icon {}" aria-hidden="true" focusable="false"><use href="{}"></use></svg>',
        css, href,
    )


def _decimal(value: float, places: int) -> str:
    """A number in the active locale's notation, without a pointless ",0"."""
    text = number_format(round(value, places), places)
    if places:
        tail = f"{get_format('DECIMAL_SEPARATOR')}{'0' * places}"
        if text.endswith(tail):
            text = text[: -len(tail)]
    return text


@register.filter
def compact(value) -> str:
    """Short human numbers: 950, 1.2K, 34K, 1.5M (1,2 mil and 1,5 mi in Portuguese)."""
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return str(value)
    scales = (
        (1e9, pgettext("compact number, billions", "%(number)sB")),
        (1e6, pgettext("compact number, millions", "%(number)sM")),
        (1e3, pgettext("compact number, thousands", "%(number)sK")),
    )
    for limit, pattern in scales:
        if abs(number) >= limit:
            scaled = number / limit
            return pattern % {"number": _decimal(scaled, 1 if scaled < 10 else 0)}
    return f"{int(number)}"


@register.filter
def clock(value) -> str:
    """Seconds as m:ss or h:mm:ss."""
    try:
        total = max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        return ""
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def _runtime(value) -> tuple[str, str]:
    try:
        seconds = float(value or 0)
    except (TypeError, ValueError):
        seconds = 0.0
    if seconds < 60:
        return f"{int(seconds)}", "s"
    if seconds < 3600:
        minutes = seconds / 60
        return number_format(minutes, 1 if minutes < 10 else 0), "min"
    hours = seconds / 3600
    return number_format(hours, 1 if hours < 100 else 0), "h"


@register.filter
def runtime_value(seconds) -> str:
    """Total running time scaled to a readable unit: 45 s, 12 min, 3.4 h."""
    return _runtime(seconds)[0]


@register.filter
def runtime_unit(seconds) -> str:
    return _runtime(seconds)[1]


@register.filter
def initials(user) -> str:
    if user is None:
        return "?"
    name = (getattr(user, "display_name", "") or "").strip()
    if name:
        parts = [part for part in re.split(r"\s+", name) if part]
        letters = parts[0][0] + (parts[-1][0] if len(parts) > 1 else parts[0][1:2])
        return letters.upper()
    email = getattr(user, "email", "") or "?"
    return email[:2].upper()


#: Pairs of gradient stops; an account always lands on the same pair.
AVATAR_TONES = (
    ("#2ee6a6", "#22d3ee"),
    ("#22d3ee", "#7c6cff"),
    ("#7c6cff", "#ff5c9a"),
    ("#ffb547", "#ff5c7a"),
    ("#34d399", "#3b82f6"),
    ("#f472b6", "#a78bfa"),
    ("#38bdf8", "#2ee6a6"),
    ("#facc15", "#22d3ee"),
)


@register.simple_tag
def avatar(user, size: str = "md"):
    key = str(getattr(user, "pk", "") or getattr(user, "email", "") or "anonymous")
    digest = int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:8], 16)
    first, second = AVATAR_TONES[digest % len(AVATAR_TONES)]
    return format_html(
        '<span class="avatar avatar-{}" style="--av-a:{};--av-b:{}" aria-hidden="true">{}</span>',
        size, first, second, initials(user),
    )


TIMESTAMP = re.compile(r"(?<![\d:])((?:\d{1,2}:)?\d{1,2}:\d{2})(?![\d:])")


def _seconds(label: str) -> int:
    total = 0
    for part in label.split(":"):
        total = total * 60 + int(part)
    return total


@register.filter(needs_autoescape=True)
def timestamps(text, autoescape=True):
    """Escape, keep line breaks, and turn 1:23 or 1:02:03 into seek links."""
    escape = conditional_escape if autoescape else (lambda value: value)
    safe = str(escape(text or ""))

    def link(match):
        label = match.group(1)
        return f'<a href="#t={_seconds(label)}" class="ts-link" data-seek="{_seconds(label)}">{label}</a>'

    linked = TIMESTAMP.sub(link, safe)
    return mark_safe(linked.replace("\r\n", "\n").replace("\n", "<br>"))


@register.filter
def kind_icon(kind: str) -> str:
    return {"video": "film", "audio": "music", "image": "image", "document": "file-text"}.get(
        kind, "film"
    )


@register.filter
def visibility_icon(visibility: str) -> str:
    return {"public": "globe", "unlisted": "link", "private": "lock"}.get(visibility, "globe")


@register.filter
def state_icon(state: str) -> str:
    return {
        "pending": "clock",
        "processing": "loader",
        "ready": "check-circle",
        "failed": "x-circle",
    }.get(state, "info")
