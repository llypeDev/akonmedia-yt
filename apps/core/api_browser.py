"""The API, browsable: the same responses as JSON, laid out as portal pages.

DRF picks a renderer from the Accept header. Scripts, the portal's own
JavaScript and tools such as curl ask for JSON or for anything (``*/*``) and
keep getting JSON, which stays the first renderer. A browser asks for HTML
first and gets :class:`PortalAPIRenderer`: the response, read-only, inside the
portal's layout, with the links between endpoints clickable. ``?format=json``
forces the plain JSON in a browser too.

Unlike DRF's own browsable API there are no forms: writes stay with the
portal's pages and with API clients.
"""
import json
import re
from http import HTTPStatus
from urllib.parse import urlsplit

from django.conf import settings
from django.template.loader import render_to_string
from django.urls import NoReverseMatch, reverse
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy
from rest_framework.renderers import BaseRenderer, JSONRenderer

#: What each router resource is, keyed by its basename: plural title, singular
#: title, a description and an icon from the sprite. The titles are nouns,
#: with a context of their own: elsewhere "Comment" is the button.
RESOURCES = {
    "media": (
        pgettext_lazy("API resource", "Media"),
        pgettext_lazy("API resource", "Media item"),
        _("Videos, audio, images and documents, with their HLS playlist, poster, chapters and subtitles."),
        "film",
    ),
    "comment": (
        pgettext_lazy("API resource", "Comments"),
        pgettext_lazy("API resource", "Comment"),
        _("What viewers wrote under each media item, replies included. Filter with ?media=<uid>."),
        "message",
    ),
    "playlist": (
        pgettext_lazy("API resource", "Playlists"),
        pgettext_lazy("API resource", "Playlist"),
        _("Collections of media in a set order, public or private."),
        "list-video",
    ),
    "category": (
        pgettext_lazy("API resource", "Categories"),
        pgettext_lazy("API resource", "Category"),
        _("The categories that organise the library."),
        "folder",
    ),
}

#: Endpoints outside the router, listed on the API's front page.
EXTRA_ENDPOINTS = [
    ("users:whoami", "GET", _("Who is signed in, and what the account may do."), "user"),
    ("uploader:start", "POST", _("Starts a resumable upload, sent in chunks."), "upload"),
]

PAGINATED_KEYS = {"count", "next", "previous", "results"}

#: The tokens of json.dumps() output: strings (keys when a colon follows),
#: numbers and literals. Everything between them is punctuation and spacing.
TOKEN = re.compile(
    r'"(?:\\.|[^"\\])*"(?P<colon>\s*:)?'
    r"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"
    r"|\btrue\b|\bfalse\b|\bnull\b"
)


#: A path on this site, such as "/view/abc/": no scheme, no host, no spaces,
#: and no backslash, which browsers read as a slash (/\evil is //evil).
SITE_PATH = re.compile(r"/[^\s/\\][^\s\\]*|/")


def _is_site_link(value: str, origin: str) -> bool:
    return value == origin or value.startswith(origin + "/") or bool(SITE_PATH.fullmatch(value))


def highlight(text: str, origin: str):
    """Pretty JSON as HTML, with links for the addresses of this same site.

    Only addresses on this site become links: the API's own hyperlinks, the
    portal's pages and the media files, absolute or as a path. Anything else,
    user-written text included, stays text.
    """
    parts = []
    position = 0
    for match in TOKEN.finditer(text):
        parts.append(escape(text[position:match.start()]))
        token = match.group(0)
        colon = match.group("colon")
        if colon:
            parts.append(format_html('<span class="j-key">{}</span>{}', token[: -len(colon)], colon))
        elif token.startswith('"'):
            value = json.loads(token)
            if _is_site_link(value, origin):
                parts.append(format_html('<a class="j-str" href="{}">{}</a>', value, token))
            else:
                parts.append(format_html('<span class="j-str">{}</span>', token))
        elif token in {"true", "false"}:
            parts.append(format_html('<span class="j-bool">{}</span>', token))
        elif token == "null":
            parts.append('<span class="j-null">null</span>')
        else:
            parts.append(format_html('<span class="j-num">{}</span>', token))
        position = match.end()
    parts.append(escape(text[position:]))
    return mark_safe("".join(parts))  # noqa: S308 - every piece above is escaped


def _status_tone(code: int) -> str:
    if code < 300:
        return "success"
    if code < 400:
        return "info"
    if code < 500:
        return "warning"
    return "danger"


def _is_root(view) -> bool:
    # Imported here: DRF loads this module while rest_framework.views is
    # still being imported, and the routers module imports the views.
    from rest_framework.routers import APIRootView

    return isinstance(view, APIRootView)


def _reverse(name: str) -> str:
    try:
        return reverse(name)
    except NoReverseMatch:
        return ""


class PortalAPIRenderer(BaseRenderer):
    media_type = "text/html"
    format = "api"
    charset = "utf-8"
    template_name = "core/api_browser.html"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        context = renderer_context or {}
        request = context["request"]
        response = context["response"]
        view = context["view"]
        http_request = request._request

        # The same bytes the JSON renderer would send, so the page shows
        # exactly what a client gets.
        payload = None
        if data is not None:
            payload = json.loads(JSONRenderer().render(data, renderer_context=context) or "null")
        text = json.dumps(payload, indent=2, ensure_ascii=False) if data is not None else ""

        origin = http_request.build_absolute_uri("/").rstrip("/")
        root_url = _reverse("media:api-root")
        query = http_request.GET.copy()
        query["format"] = "json"

        page = {
            "body": highlight(text, origin),
            "has_body": data is not None,
            "method": http_request.method,
            "path": http_request.get_full_path(),
            "absolute_url": http_request.build_absolute_uri(),
            "json_url": f"{http_request.path}?{query.urlencode()}",
            "root_url": root_url,
            "status_code": response.status_code,
            "status_text": self._status_text(response.status_code),
            "status_tone": _status_tone(response.status_code),
            "methods": [m for m in view.allowed_methods if m not in {"HEAD", "OPTIONS"}],
            "crumbs": self._crumbs(http_request.path, root_url),
            "is_root": _is_root(view),
            "error": None,
        }
        page.update(self._describe(view))

        if response.status_code >= 400 and isinstance(payload, dict):
            page["error"] = payload.get("detail")
        if isinstance(payload, dict) and PAGINATED_KEYS <= payload.keys():
            page["pagination"] = {
                "count": payload["count"],
                "shown": len(payload["results"] or []),
                "next": payload["next"],
                "previous": payload["previous"],
            }
        if page["is_root"]:
            page["resources"] = self._resources(view, payload or {})
            page["extra_endpoints"] = [
                {"url": url, "method": method, "description": description, "icon": icon}
                for name, method, description, icon in EXTRA_ENDPOINTS
                if (url := _reverse(name))
            ]
            page["example"] = f'curl -H "Accept: application/json" {origin}{_reverse("media:media-list")}'

        return render_to_string(self.template_name, {"api": page}, request=http_request)

    @staticmethod
    def _status_text(code: int) -> str:
        try:
            return HTTPStatus(code).phrase
        except ValueError:
            return ""

    @staticmethod
    def _crumbs(path: str, root: str):
        """``/api/v1/media/abc/`` -> media (linked), then abc."""
        if not root or not path.startswith(root):
            return []
        crumbs = []
        current = root
        for segment in [s for s in path[len(root):].split("/") if s]:
            current += segment + "/"
            crumbs.append({"label": segment, "url": current})
        return crumbs

    @staticmethod
    def _describe(view) -> dict:
        if _is_root(view):
            return {
                "title": _("%(portal)s API") % {"portal": settings.PORTAL_NAME},
                "description": _(
                    "The data behind the portal, as JSON: public reading for everyone, writing with "
                    "the session of a signed-in account."
                ),
                "icon": "code",
            }
        resource = RESOURCES.get(getattr(view, "basename", None))
        if resource:
            plural, singular, description, icon = resource
            return {
                "title": singular if getattr(view, "detail", False) else plural,
                "description": description,
                "icon": icon,
            }
        return {"title": view.get_view_name(), "description": "", "icon": "code"}

    @staticmethod
    def _resources(view, payload: dict):
        names = getattr(view, "api_root_dict", {}) or {}
        cards = []
        for prefix, url in payload.items():
            basename = names.get(prefix, "").removesuffix("-list")
            plural, _singular, description, icon = RESOURCES.get(basename, (prefix, "", "", "link"))
            cards.append({
                "url": url,
                "path": urlsplit(str(url)).path,
                "title": plural,
                "description": description,
                "icon": icon,
            })
        return cards
