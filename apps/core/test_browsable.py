"""The status page and the browsable API: pages for browsers, JSON for the rest.

Both addresses existed as JSON only. A browser now gets a page, and every other
client (probes, monitors, curl, the portal's own scripts) must keep getting
exactly the JSON it got before.
"""
from unittest import mock

from django.db import OperationalError
from django.test import TestCase

from apps.media.models import Comment, Media
from apps.users.models import User

from .api_browser import highlight

BROWSER = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"


def make_media(owner, **kwargs):
    defaults = {
        "title": "Clip",
        "kind": Media.Kind.VIDEO,
        "state": Media.State.READY,
        "visibility": Media.Visibility.PUBLIC,
    }
    defaults.update(kwargs)
    return Media.objects.create(owner=owner, **defaults)


class StatusPageTests(TestCase):
    def test_probes_still_get_json(self):
        for accept in (None, "*/*", "application/json"):
            headers = {"HTTP_ACCEPT": accept} if accept else {}
            response = self.client.get("/health/ready/", **headers)
            self.assertEqual(response["Content-Type"], "application/json", accept)
            self.assertEqual(response.json()["checks"]["database"], "ok")

    def test_a_browser_gets_the_status_page(self):
        response = self.client.get("/health/ready/", HTTP_ACCEPT=BROWSER)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("text/html"))
        self.assertIn("Accept", response["Vary"])
        self.assertContains(response, "Todos os sistemas operando")
        self.assertContains(response, "Banco de dados")
        self.assertContains(response, 'href="?format=json"')

    def test_format_json_wins_over_the_browser(self):
        response = self.client.get("/health/ready/?format=json", HTTP_ACCEPT=BROWSER)
        self.assertEqual(response.json()["status"], "ok")

    def test_the_page_needs_no_database(self):
        # Rendered without the request: no session, user or category lookup,
        # so it still answers when the database is the thing that is down.
        with self.assertNumQueries(0):
            self.client.get("/health/ready/", HTTP_ACCEPT=BROWSER)

    def test_a_failing_database_shows_and_answers_503(self):
        with mock.patch("apps.core.views.connection.ensure_connection", side_effect=OperationalError("x")):
            page = self.client.get("/health/ready/", HTTP_ACCEPT=BROWSER)
            probe = self.client.get("/health/ready/")
        self.assertEqual(page.status_code, 503)
        self.assertContains(page, "O portal está com problemas", status_code=503)
        self.assertContains(page, "OperationalError", status_code=503)
        self.assertEqual(probe.status_code, 503)
        self.assertEqual(probe.json()["status"], "degraded")

    def test_a_missing_encoder_is_a_warning_not_an_outage(self):
        with mock.patch("apps.core.views.pipeline.available", return_value=False):
            page = self.client.get("/health/ready/", HTTP_ACCEPT=BROWSER)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "No ar, sem o codificador")
        self.assertContains(page, "Não instalado")

    def test_english(self):
        self.client.cookies["django_language"] = "en"
        self.assertContains(self.client.get("/health/ready/", HTTP_ACCEPT=BROWSER), "All systems operational")


class BrowsableAPITests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="o@example.com", password="x" * 20)
        self.media = make_media(self.owner, title="<script>alert(1)</script>")

    def test_clients_still_get_json(self):
        for accept in (None, "*/*", "application/json"):
            headers = {"HTTP_ACCEPT": accept} if accept else {}
            response = self.client.get("/api/v1/", **headers)
            self.assertEqual(response["Content-Type"], "application/json", accept)
            self.assertIn("media", response.json())

    def test_format_json_wins_over_the_browser(self):
        response = self.client.get("/api/v1/media/?format=json", HTTP_ACCEPT=BROWSER)
        self.assertEqual(response.json()["count"], 1)

    def test_the_root_lists_the_resources(self):
        response = self.client.get("/api/v1/", HTTP_ACCEPT=BROWSER)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "API do QQplay")
        self.assertContains(response, 'class="api-card" href="http://testserver/api/v1/media/"')
        self.assertContains(response, "Comentários")
        self.assertContains(response, "/api/v1/whoami/")
        self.assertContains(response, 'href="/api/v1/?format=json"')

    def test_a_list_page_shows_the_count_and_the_json(self):
        response = self.client.get("/api/v1/media/", HTTP_ACCEPT=BROWSER)
        self.assertContains(response, "1 de 1 item")
        self.assertContains(response, '<span class="j-key">&quot;count&quot;</span>')
        self.assertContains(response, f'href="/view/{self.media.uid}/"')

    def test_content_is_escaped(self):
        response = self.client.get(f"/api/v1/media/{self.media.uid}/", HTTP_ACCEPT=BROWSER)
        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertContains(response, "&lt;script&gt;alert(1)&lt;/script&gt;")

    def test_errors_are_explained(self):
        response = self.client.get("/api/v1/media/doesnotexist/", HTTP_ACCEPT=BROWSER)
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "404 Not Found", status_code=404)
        self.assertContains(response, "api-notice", status_code=404)

    def test_the_session_is_named(self):
        self.client.force_login(self.owner)
        response = self.client.get("/api/v1/whoami/", HTTP_ACCEPT=BROWSER)
        self.assertContains(response, "Conectado como o@example.com")

    def test_comment_is_a_noun_here(self):
        comment = Comment.objects.create(media=self.media, author=self.owner, body="hi")
        response = self.client.get(f"/api/v1/comments/{comment.id}/", HTTP_ACCEPT=BROWSER)
        self.assertContains(response, "<h1>Comentário</h1>")


class HighlightTests(TestCase):
    ORIGIN = "https://portal.example"

    def links(self, value):
        return 'href="' in highlight(f'"{value}"', self.ORIGIN)

    def test_only_this_sites_addresses_become_links(self):
        self.assertTrue(self.links("https://portal.example/api/v1/media/"))
        self.assertTrue(self.links("/view/abc/"))
        self.assertFalse(self.links("https://elsewhere.example/"))
        self.assertFalse(self.links("https://portal.example.elsewhere.example/"))
        self.assertFalse(self.links("//elsewhere.example/"))
        self.assertFalse(self.links("/\\\\elsewhere.example/"))
        self.assertFalse(self.links("javascript:alert(1)"))
        self.assertFalse(self.links("/a path with spaces"))

    def test_text_survives_as_is(self):
        source = '{\n  "a": "x\\"<b>",\n  "n": -1.5e3,\n  "t": true,\n  "z": null\n}'
        html = str(highlight(source, self.ORIGIN))
        self.assertIn("&lt;b&gt;", html)
        self.assertIn('<span class="j-num">-1.5e3</span>', html)
        self.assertIn('<span class="j-bool">true</span>', html)
        self.assertIn('<span class="j-null">null</span>', html)
