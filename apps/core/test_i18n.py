"""Interface language: Brazilian Portuguese by default, English on request."""
import datetime

from django.template import Context, Template
from django.test import TestCase
from django.utils import translation

from apps.media.models import Media
from apps.users.models import User


class LanguageSelectionTests(TestCase):
    def test_portuguese_is_the_default(self):
        response = self.client.get("/")
        self.assertEqual(response["Content-Language"], "pt-br")
        self.assertContains(response, '<html lang="pt-br"')
        self.assertContains(response, "Início")
        self.assertContains(response, "Plataforma de mídia auto-hospedada")

    def test_the_browser_language_does_not_switch_the_interface(self):
        response = self.client.get("/", HTTP_ACCEPT_LANGUAGE="en-US,en;q=0.9")
        self.assertContains(response, "Início")
        self.assertNotContains(response, "Self-hosted media platform")

    def test_switcher_sets_the_cookie_and_returns_to_the_page(self):
        response = self.client.post("/i18n/setlang/", {"language": "en", "next": "/playlists/"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/playlists/")
        self.assertEqual(self.client.cookies["django_language"].value, "en")
        english = self.client.get("/playlists/")
        self.assertEqual(english["Content-Language"], "en")
        self.assertContains(english, "Collections")

    def test_unknown_language_in_the_cookie_falls_back_to_the_default(self):
        self.client.cookies["django_language"] = "xx"
        self.assertContains(self.client.get("/"), "Início")

    def test_every_page_offers_the_switcher(self):
        response = self.client.get("/")
        self.assertContains(response, 'action="/i18n/setlang/"')
        self.assertContains(response, 'value="en"')


class TranslatedContentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="pt@example.com", password="x" * 20)

    def test_choice_labels_are_translated(self):
        media = Media.objects.create(owner=self.user, title="X", visibility=Media.Visibility.UNLISTED)
        with translation.override("pt-br"):
            self.assertEqual(media.get_visibility_display(), "Não listada")
            self.assertEqual(media.get_state_display(), "Na fila")
        with translation.override("en"):
            self.assertEqual(media.get_visibility_display(), "Unlisted")

    def test_flash_messages_follow_the_language(self):
        self.client.force_login(self.user)
        response = self.client.post("/playlists/", {"title": "  "}, follow=True)
        self.assertContains(response, "A playlist precisa de um título.")
        self.client.cookies["django_language"] = "en"
        response = self.client.post("/playlists/", {"title": "  "}, follow=True)
        self.assertContains(response, "A playlist needs a title.")

    def test_plural_counts(self):
        Media.objects.create(owner=self.user, title="One", state=Media.State.READY, views_count=1)
        Media.objects.create(owner=self.user, title="Two", state=Media.State.READY, views_count=2)
        response = self.client.get("/")
        self.assertContains(response, "1 visualização")
        self.assertContains(response, "2 visualizações")

    def test_scripts_get_their_catalog(self):
        portuguese = self.client.get("/jsi18n/")
        self.assertEqual(portuguese.status_code, 200)
        self.assertContains(portuguese, "Qualidade")
        self.assertContains(portuguese, "Copiado para a ")  # the catalog escapes accents as \uXXXX
        self.client.cookies["django_language"] = "en"
        self.assertNotContains(self.client.get("/jsi18n/"), "Qualidade")

    def test_pages_load_the_catalog_before_the_scripts(self):
        html = self.client.get("/").content.decode()
        self.assertLess(html.index("/jsi18n/"), html.index("js/app.js"))

    def test_sign_in_page_is_translated(self):
        response = self.client.get("/accounts/login/")
        self.assertContains(response, "Entrar")
        self.assertContains(response, "Senha")

    def test_sign_up_page_has_no_english_left(self):
        # allauth's own pt_BR catalog keeps "sign in" here; ours corrects it.
        response = self.client.get("/accounts/signup/")
        self.assertContains(response, "Cadastrar-se")
        self.assertNotContains(response, "sign in")
        self.assertContains(response, 'por favor, <a href="/accounts/login/">entre</a>.')


class DateFormatTests(TestCase):
    def render(self, language, fmt, value):
        with translation.override(language):
            return Template("{{ value|date:fmt }}").render(Context({"value": value, "fmt": fmt}))

    def test_short_dates_follow_each_language(self):
        day = datetime.date(2026, 10, 20)
        self.assertEqual(self.render("pt-br", "SHORT_DATE_FORMAT", day), "20 de out. de 2026")
        self.assertEqual(self.render("en", "SHORT_DATE_FORMAT", day), "20 Oct 2026")
        self.assertEqual(self.render("pt-br", "SHORT_DATE_FORMAT", datetime.date(2026, 5, 3)), "3 de mai. de 2026")

    def test_short_date_times_follow_each_language(self):
        moment = datetime.datetime(2026, 11, 3, 13, 5)
        self.assertEqual(self.render("pt-br", "SHORT_DATETIME_FORMAT", moment), "3 de nov. de 2026, 13:05")
        self.assertEqual(self.render("en", "SHORT_DATETIME_FORMAT", moment), "3 Nov 2026, 13:05")

    def test_channel_page_says_when_the_owner_joined(self):
        owner = User.objects.create_user(email="joined@example.com", password="x" * 20)
        User.objects.filter(pk=owner.pk).update(date_joined=datetime.datetime(2026, 3, 9, tzinfo=datetime.UTC))
        self.assertContains(self.client.get(f"/channel/{owner.pk}/"), "entrou em mar. de 2026")
