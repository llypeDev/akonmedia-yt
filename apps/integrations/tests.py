"""Integrations: a site signs its people in, and they take part in lives."""
import json
import socket
import time
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.live.models import ChatMessage, ChatReaction, Live
from apps.media.models import Media
from apps.rbac.roles import Role
from apps.users.models import User

from . import identities, pictures, sessions, tokens
from .models import ExternalIdentity, Integration, normalize_origin

SITE = "https://escola.example.com"


def make_user(email, role=Role.MEMBER):
    return User.objects.create_user(email=email, password="x" * 20, role=role)


class IntegrationTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = make_user("prof@example.com")
        self.integration = Integration.objects.create(owner=self.owner, name="Escola Virtual", origins=SITE)
        self.live = Live.objects.create(
            owner=self.owner, title="Aula 1", status=Live.Status.LIVE, publishing=True,
            visibility=Media.Visibility.PUBLIC,
        )

    def token(self, integration=None, **claims):
        now = int(time.time())
        data = {"sub": "42", "name": "Ana Lima", "iat": now, "exp": now + 600}
        data.update(claims)
        return tokens.sign({k: v for k, v in data.items() if v is not None}, integration or self.integration)

    def sign_in(self, origin=SITE, **claims):
        return self.client.post(
            reverse("integrations:session"),
            json.dumps({"token": self.token(**claims), "origin": origin}),
            content_type="application/json",
        )

    def session(self, **claims):
        response = self.sign_in(**claims)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["session"]

    def api(self, name, *args, session=None, method="get", data=None):
        url = reverse(f"integrations:{name}", args=[self.live.uid, *args])
        headers = {"Authorization": f"Bearer {session}"} if session else {}
        if method == "post":
            return self.client.post(url, data or {}, headers=headers)
        return self.client.get(url, data or {}, headers=headers)


class TokenTests(IntegrationTestCase):
    def test_a_good_token(self):
        person = tokens.verify(self.token(email="ana@escola.example.com", picture="https://cdn.example.com/a.jpg",
                                          moderator=True))
        self.assertEqual(person.integration, self.integration)
        self.assertEqual((person.external_id, person.name), ("42", "Ana Lima"))
        self.assertEqual(person.email, "ana@escola.example.com")
        self.assertEqual(person.picture, "https://cdn.example.com/a.jpg")
        self.assertTrue(person.moderator)

    def test_numbers_as_ids_and_odd_extras(self):
        person = tokens.verify(self.token(sub=42, picture="http://insecure.example.com/a.jpg", email="nope",
                                          moderator="yes"))
        self.assertEqual(person.external_id, "42")
        self.assertEqual(person.picture, "")  # https only
        self.assertEqual(person.email, "")
        self.assertFalse(person.moderator)  # only a real true counts

    def assertRefused(self, token):
        with self.assertRaises(tokens.TokenError):
            tokens.verify(token)

    def test_refusals(self):
        good = self.token()
        head, body, signature = good.split(".")
        self.assertRefused("")
        self.assertRefused("not.a.token")
        self.assertRefused(f"{head}.{body}.{signature[:-2]}AA")  # wrong signature
        other = Integration.objects.create(owner=self.owner, name="Outra")
        # Signed with another integration's secret.
        self.assertRefused(f"{head}.{body}." + self.token(integration=other).split(".")[2])
        self.assertRefused(self.token(exp=int(time.time()) - 3600))  # expired
        self.assertRefused(self.token(exp=int(time.time()) + 3 * 86400))  # too long
        self.assertRefused(self.token(exp=None))
        self.assertRefused(self.token(sub=None))
        self.assertRefused(self.token(sub="   "))
        self.assertRefused(self.token(name=None))
        self.assertRefused(self.token(nbf=int(time.time()) + 3600))
        Integration.objects.filter(pk=self.integration.pk).update(is_active=False)
        self.assertRefused(good)

    def test_only_hs256(self):
        import base64

        def b64(data):
            return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

        claims = {"sub": "1", "name": "X", "exp": int(time.time()) + 60}
        for alg in ("none", "HS512", "RS256"):
            forged = f"{b64({'alg': alg, 'kid': self.integration.key})}.{b64(claims)}."
            self.assertRefused(forged)

    def test_the_examples_on_the_studio_page_make_valid_tokens(self):
        # The Python example, as shown to sites (Node and PHP follow the same steps).
        import base64
        import hashlib
        import hmac

        def b64(data):
            return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

        now = int(time.time())
        header = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": self.integration.key}).encode())
        claims = b64(json.dumps({"sub": "7", "name": "Leo", "picture": None, "moderator": False,
                                 "iat": now, "exp": now + 600}).encode())
        signature = b64(hmac.new(self.integration.secret.encode(), f"{header}.{claims}".encode(),
                                 hashlib.sha256).digest())
        self.assertEqual(tokens.verify(f"{header}.{claims}.{signature}").external_id, "7")


class SignInTests(IntegrationTestCase):
    def test_signing_in_makes_an_account_of_their_own(self):
        response = self.sign_in(email="prof@example.com")  # the owner's address!
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["user"]["name"], "Ana Lima")
        identity = ExternalIdentity.objects.get()
        self.assertNotEqual(identity.user, self.owner)
        self.assertTrue(identity.user.email.endswith("@" + identities.ACCOUNT_DOMAIN))
        self.assertFalse(identity.user.has_usable_password())
        self.assertEqual(identity.email, "prof@example.com")

    def test_the_same_person_keeps_the_account_and_gets_the_new_name(self):
        self.session()
        self.session(name="Ana L. Souza", moderator=True)
        identity = ExternalIdentity.objects.get()
        self.assertEqual(identity.user.display_name, "Ana L. Souza")
        self.assertTrue(identity.is_moderator)
        self.assertEqual(User.objects.filter(external_identity__isnull=False).count(), 1)

    def test_the_same_id_on_two_sites_is_two_people(self):
        other = Integration.objects.create(owner=self.owner, name="Outra escola")
        self.session()
        self.client.post(reverse("integrations:session"),
                         json.dumps({"token": self.token(integration=other), "origin": SITE}),
                         content_type="application/json")
        self.assertEqual(ExternalIdentity.objects.count(), 2)

    def test_only_the_listed_sites(self):
        refused = self.sign_in(origin="https://elsewhere.example.org")
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(refused.json()["code"], "origin")
        self.assertEqual(self.sign_in(origin="https://escola.example.com/aulas/1").status_code, 200)
        # The studio's preview runs on the portal itself.
        self.assertEqual(self.sign_in(origin="http://testserver").status_code, 200)
        Integration.objects.filter(pk=self.integration.pk).update(origins="")
        self.assertEqual(self.sign_in(origin="https://anywhere.example.org").status_code, 200)

    def test_bad_tokens_say_why(self):
        response = self.client.post(reverse("integrations:session"), json.dumps({"token": "x"}),
                                    content_type="application/json")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "token")
        self.assertEqual(self.client.get(reverse("integrations:session")).status_code, 405)

    def test_a_blocked_account_cannot_sign_in(self):
        self.session()
        ExternalIdentity.objects.get().user.__class__.objects.filter(
            external_identity__isnull=False).update(is_active=False)
        self.assertEqual(self.sign_in().status_code, 403)

    def test_sign_ins_are_rate_limited(self):
        codes = {self.sign_in().status_code for _ in range(35)}
        self.assertIn(429, codes)

    def test_origins_are_normalized(self):
        self.assertEqual(normalize_origin("Escola.com.br/aulas"), "https://escola.com.br")
        self.assertEqual(normalize_origin("http://localhost:8080/x"), "http://localhost:8080")
        self.assertEqual(normalize_origin("https://a.com:443"), "https://a.com")
        self.assertEqual(normalize_origin("ftp://a.com"), "")


class EmbedApiTests(IntegrationTestCase):
    def test_signed_out_frames_read_but_do_not_write(self):
        ChatMessage.objects.create(live=self.live, author=self.owner, body="Oi")
        chat = self.api("live_state").json()["chat"]
        self.assertFalse(chat["can_post"])
        self.assertEqual(chat["messages"][0]["body"], "Oi")
        self.assertEqual(self.api("live_chat", method="post", data={"body": "x"}).status_code, 403)

    def test_students_talk_and_react_as_themselves(self):
        session = self.session()
        state = self.api("live_state", session=session).json()
        self.assertTrue(state["chat"]["can_post"])
        sent = self.api("live_chat", session=session, method="post", data={"body": "Bom dia!"})
        self.assertEqual(sent.status_code, 201)
        message = sent.json()["message"]
        self.assertEqual(message["author"], "Ana Lima")
        self.assertTrue(message["mine"])
        reacted = self.api("live_react", message["id"], session=session, method="post", data={"kind": "clap"})
        self.assertEqual(reacted.json()["reactions"], [{"kind": "clap", "count": 1, "mine": True}])
        # The same message on the live's own page.
        page = self.client.get(reverse("live:state", args=[self.live.uid])).json()["chat"]["messages"]
        self.assertEqual(page[0]["author"], "Ana Lima")
        self.assertEqual(page[0]["reactions"], [{"kind": "clap", "count": 1, "mine": False}])

    def test_cookies_mean_nothing_here(self):
        # A QQplay session in the browser is ignored: without the header the
        # request is anonymous, so no other site can post in someone's name.
        self.client.force_login(self.owner)
        self.assertEqual(self.api("live_chat", method="post", data={"body": "x"}).status_code, 403)
        self.assertFalse(self.api("live_state").json()["chat"]["can_post"])

    def test_a_bad_session_is_anonymous_and_says_so(self):
        response = self.api("live_state", session="garbage")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-QQplay-Session"], "expired")
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_a_new_secret_or_turning_off_ends_sessions(self):
        session = self.session()
        self.assertTrue(self.api("live_state", session=session).json()["chat"]["can_post"])
        self.integration.rotate_secret()
        self.assertFalse(self.api("live_state", session=session).json()["chat"]["can_post"])
        session = self.session()
        Integration.objects.filter(pk=self.integration.pk).update(is_active=False)
        self.assertIsNone(sessions.load(session))

    def test_private_lives_open_to_the_owners_site_only(self):
        Live.objects.filter(pk=self.live.pk).update(visibility=Media.Visibility.PRIVATE)
        self.assertEqual(self.api("live_state").status_code, 404)
        self.assertEqual(self.client.get(reverse("live:state", args=[self.live.uid])).status_code, 404)
        session = self.session()
        state = self.api("live_state", session=session)
        self.assertEqual(state.status_code, 200)
        self.assertTrue(state.json()["hls"])

        # Another creator's integration opens nothing of this live.
        stranger = make_user("other@example.com")
        theirs = Integration.objects.create(owner=stranger, name="Outra")
        response = self.client.post(reverse("integrations:session"),
                                    json.dumps({"token": self.token(integration=theirs), "origin": SITE}),
                                    content_type="application/json")
        self.assertEqual(self.api("live_state", session=response.json()["session"]).status_code, 404)

    def test_teachers_moderate_the_owners_lives(self):
        student = self.session()
        message = self.api("live_chat", session=student, method="post", data={"body": "spam"}).json()["message"]
        self.assertFalse(message["can_hide"])
        self.assertEqual(self.api("live_hide", message["id"], session=student, method="post").status_code, 403)

        teacher = self.session(sub="t1", name="Prof. Rui", moderator=True)
        state = self.api("live_state", session=teacher).json()
        self.assertTrue(state["chat"]["can_moderate"])
        self.assertEqual(self.api("live_hide", message["id"], session=teacher, method="post").status_code, 200)
        self.assertTrue(ChatMessage.objects.get(pk=message["id"]).is_hidden)

        # Not on someone else's live, even a public one.
        other_live = Live.objects.create(owner=make_user("x@example.com"), title="Outra", status=Live.Status.LIVE)
        spam = ChatMessage.objects.create(live=other_live, author=self.owner, body="?")
        url = reverse("integrations:live_hide", args=[other_live.uid, spam.id])
        self.assertEqual(self.client.post(url, headers={"Authorization": f"Bearer {teacher}"}).status_code, 403)


class EmbedPageTests(IntegrationTestCase):
    def page(self, **params):
        return self.client.get(reverse("live:embed", args=[self.live.uid]), params)

    def test_layouts(self):
        player = self.page().content.decode()
        self.assertNotIn("data-chat-list", player)
        both = self.page(layout="both", school=self.integration.key, theme="light").content.decode()
        self.assertIn("data-chat-list", both)
        self.assertIn("embed-both", both)
        self.assertIn(f'data-school="{self.integration.key}"', both)
        self.assertIn('data-theme="light"', both)
        self.assertIn(reverse("integrations:live_state", args=[self.live.uid]), both)
        self.assertIn("Escola Virtual", both)  # "sign in to Escola Virtual"
        chat = self.page(layout="chat").content.decode()
        self.assertIn("embed-chat", chat)

    def test_the_frame_never_speaks_for_the_browsers_account(self):
        self.client.force_login(self.owner)
        state = self.page(layout="both").context["state"]
        self.assertFalse(state["chat"]["can_post"])

    def test_a_private_live_waits_for_the_site(self):
        Live.objects.filter(pk=self.live.pk).update(visibility=Media.Visibility.PRIVATE)
        self.assertEqual(self.page(layout="both").status_code, 404)
        response = self.page(layout="both", school=self.integration.key)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("data-awaiting", html)
        self.assertNotIn(self.live.playback_id, html)  # no stream address before sign-in
        stranger = Integration.objects.create(owner=make_user("y@example.com"), name="Outra")
        self.assertEqual(self.page(layout="both", school=stranger.key).status_code, 404)

    def test_the_script(self):
        response = self.client.get(reverse("integrations:sdk"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("javascript", response["Content-Type"])
        body = response.content.decode()
        self.assertIn('var ORIGIN = "http://testserver"', body)
        self.assertIn("qqplay:token", body)


class StudioTests(IntegrationTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def test_creating_one(self):
        response = self.client.post(reverse("integrations:list"),
                                     {"name": "Minha escola", "origins": "escola.com.br\nhttps://www.escola.com.br/x"})
        integration = Integration.objects.get(name="Minha escola")
        self.assertRedirects(response, reverse("integrations:detail", args=[integration.pk]))
        self.assertEqual(integration.origin_list, ["https://escola.com.br", "https://www.escola.com.br"])
        self.assertTrue(integration.key.startswith("sch_"))

    def test_bad_sites_are_refused(self):
        response = self.client.post(reverse("integrations:list"), {"name": "X", "origins": "ftp://nope"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Integration.objects.filter(name="X").exists())

    def test_the_page_has_the_keys_and_the_code(self):
        html = self.client.get(reverse("integrations:detail", args=[self.integration.pk])).content.decode()
        self.assertIn(self.integration.key, html)
        self.assertIn(self.integration.secret, html)
        self.assertIn(f'data-qqplay-live="{self.live.uid}"', html)
        self.assertIn("/embed/sdk.js", html)

    def test_only_the_owner_sees_it(self):
        self.client.force_login(make_user("nosy@example.com"))
        self.assertEqual(self.client.get(reverse("integrations:detail", args=[self.integration.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("integrations:rotate", args=[self.integration.pk])).status_code, 404)

    def test_new_secret_turning_off_and_deleting(self):
        old = self.integration.secret
        self.client.post(reverse("integrations:rotate", args=[self.integration.pk]))
        self.integration.refresh_from_db()
        self.assertNotEqual(self.integration.secret, old)
        self.client.post(reverse("integrations:detail", args=[self.integration.pk]), {"is_active": "0"})
        self.integration.refresh_from_db()
        self.assertFalse(self.integration.is_active)
        self.client.post(reverse("integrations:delete", args=[self.integration.pk]))
        self.assertFalse(Integration.objects.exists())

    def test_the_preview_signs_in_a_test_student(self):
        response = self.client.get(reverse("integrations:preview", args=[self.integration.pk]),
                                   {"live": self.live.uid, "name": "Bia", "moderator": "1"})
        self.assertEqual(response.status_code, 200)
        person = tokens.verify(response.context["token"])
        self.assertEqual((person.name, person.moderator), ("Bia", True))

    def test_people_from_a_site_cannot_make_integrations(self):
        self.client.logout()
        self.session()
        identity = ExternalIdentity.objects.get()
        self.client.force_login(identity.user)
        self.assertEqual(self.client.get(reverse("integrations:list")).status_code, 403)


class PictureTests(IntegrationTestCase):
    def test_only_public_addresses_are_fetched(self):
        def resolve(*addresses):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)) for address in addresses]

        for blocked in ("127.0.0.1", "10.0.0.5", "169.254.169.254", "192.168.1.2", "::1"):
            with mock.patch("socket.getaddrinfo", return_value=resolve(blocked)):
                with self.assertRaises(pictures.PictureError):
                    pictures.public_address("cdn.example.com", 443)
        with mock.patch("socket.getaddrinfo", return_value=resolve("93.184.216.34", "10.0.0.1")):
            with self.assertRaises(pictures.PictureError):  # one bad address is enough
                pictures.public_address("cdn.example.com", 443)
        with mock.patch("socket.getaddrinfo", return_value=resolve("93.184.216.34")):
            self.assertEqual(pictures.public_address("cdn.example.com", 443), "93.184.216.34")
        with self.assertRaises(pictures.PictureError):
            pictures.download("http://cdn.example.com/a.jpg")

    def test_the_sites_picture_becomes_the_avatar(self):
        import io

        from PIL import Image

        out = io.BytesIO()
        Image.new("RGB", (300, 300), (20, 120, 220)).save(out, format="PNG")
        with mock.patch.object(pictures, "download", return_value=out.getvalue()) as download, \
                override_settings(MEDIA_ROOT=self._media()):
            with self.captureOnCommitCallbacks(execute=True):
                self.session(picture="https://cdn.example.com/ana.png")
            download.assert_called_once_with("https://cdn.example.com/ana.png")
            user = ExternalIdentity.objects.get().user
            self.assertEqual(user.avatar, "photo")
            self.assertTrue(user.avatar_photo.name.endswith(".webp"))

            # Same address next time: not fetched again.
            with self.captureOnCommitCallbacks(execute=True):
                self.session(picture="https://cdn.example.com/ana.png")
            self.assertEqual(download.call_count, 1)
            # No picture any more: back to the initials.
            with self.captureOnCommitCallbacks(execute=True):
                self.session()
            user.refresh_from_db()
            self.assertEqual(user.avatar, "")
            self.assertFalse(user.avatar_photo)

    def test_a_picture_that_cannot_be_fetched_leaves_the_initials(self):
        with mock.patch.object(pictures, "download", side_effect=pictures.PictureError("nope")) as download:
            with self.captureOnCommitCallbacks(execute=True):
                self.session(picture="https://cdn.example.com/ana.png")
        download.assert_called_once()
        self.assertEqual(ExternalIdentity.objects.get().user.avatar, "")

    def _media(self):
        import shutil
        import tempfile

        folder = tempfile.mkdtemp(prefix="qq-int-")
        self.addCleanup(shutil.rmtree, folder, True)
        return folder
