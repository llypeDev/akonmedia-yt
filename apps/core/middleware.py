"""Pick the interface language.

The portal speaks its configured language (``LANGUAGE_CODE``, Brazilian
Portuguese by default) to everybody. A visitor who picks another language in
the switcher keeps it through Django's language cookie, set by the
``set_language`` view.

The browser's ``Accept-Language`` header is deliberately not consulted: the
interface should not change language depending on how someone's computer
happens to be configured.
"""
from django.conf import settings
from django.utils import translation
from django.utils.cache import patch_vary_headers


class PortalLanguageMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        supported = {code.lower(): code for code, _ in settings.LANGUAGES}
        chosen = (request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME) or "").lower()
        language = supported.get(chosen) or settings.LANGUAGE_CODE
        translation.activate(language)
        request.LANGUAGE_CODE = translation.get_language()

        response = self.get_response(request)

        patch_vary_headers(response, ("Cookie",))
        response.headers.setdefault("Content-Language", request.LANGUAGE_CODE)
        return response
