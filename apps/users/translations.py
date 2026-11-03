"""Corrections to django-allauth's Brazilian Portuguese catalog.

allauth ships its own pt_BR translations, and a few of its strings are left
partly in English. Naming those message ids here keeps them in this project's
catalog (``makemessages`` only collects ids it finds in our sources), and the
project catalog wins over an app's because ``LOCALE_PATHS`` is searched first.
Nothing imports this module; it exists for the extractor.
"""

from django.utils.translation import gettext_noop

ALLAUTH_CORRECTIONS = (
    # account/signup.html: allauth's pt_BR translation keeps "sign in".
    gettext_noop("Already have an account? Then please %(link)ssign in%(end_link)s."),
)
