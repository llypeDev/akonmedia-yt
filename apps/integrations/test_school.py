"""The school features on top of the integration: classes, blocking and
forgetting people, secret rotation with a grace period, the site's look, the
library's videos with comments, webhooks, the server API and the change
stream."""
import asyncio
import hashlib
import hmac
import json
import time
from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone, translation

from apps.live import services, stream
from apps.live.models import ChatMessage, Live, LiveAttendance
from apps.media.models import Comment, Media
from apps.users.models import User

from . import identities, net, sessions, tokens, webhooks
from .models import ExternalIdentity, Integration, WebhookDelivery, normalize_origin
from .tests import SITE, IntegrationTestCase, make_user


class ClassTests(IntegrationTestCase):
    """A live for some classes opens, in the site's frame, to them only."""

    def setUp(self):
        super().setUp()
        Live.objects.filter(pk=self.live.pk).update(visibility=Media.Visibility.PRIVATE, groups="turma-a, turma-b")

    def test_the_token_brings_the_classes_and_they_follow_each_sign_in(self):
        self.session(groups=["turma-a", " turma-c ", "turma-a"])
        self.assertEqual(ExternalIdentity.objects.get().groups, ["turma-a", "turma-c"])
        self.session(groups="turma-d")
        self.assertEqual(ExternalIdentity.objects.get().groups, ["turma-d"])
        self.session()
        self.assertEqual(ExternalIdentity.objects.get().groups, [])

    def test_only_the_lives_classes_get_in(self):
        inside = self.session(groups=["turma-b"])
        self.assertEqual(self.api("live_state", session=inside).status_code, 200)
        outside = self.session(sub="99", groups=["turma-z"])
        self.assertEqual(self.api("live_state", session=outside).status_code, 404)
        nobody = self.session(sub="100")
        self.assertEqual(self.api("live_state", session=nobody).status_code, 404)

    def test_a_live_for_everyone_takes_everyone(self):
        Live.objects.filter(pk=self.live.pk).update(groups="")
        self.assertEqual(self.api("live_state", session=self.session(groups=["turma-z"])).status_code, 200)

    def test_teachers_moderate_their_classes_only(self):
        student = self.session(groups=["turma-a"])
        message = self.api("live_chat", session=student, method="post", data={"body": "?"}).json()["message"]
        other_teacher = self.session(sub="t2", name="Prof. Lia", moderator=True, groups=["turma-z"])
        self.assertEqual(self.api("live_hide", message["id"], session=other_teacher, method="post").status_code, 404)
        teacher = self.session(sub="t1", name="Prof. Rui", moderator=True, groups=["turma-a"])
        self.assertEqual(self.api("live_hide", message["id"], session=teacher, method="post").status_code, 200)

    def test_the_recording_keeps_the_classes(self):
        media = Media.objects.create(owner=self.owner, title="Aula 1", state=Media.State.READY,
                                     visibility=Media.Visibility.PRIVATE, file="media/x/rec.mp4")
        Live.objects.filter(pk=self.live.pk).update(recording=media, status=Live.Status.ENDED)
        url = reverse("integrations:media_info", args=[media.uid])
        inside = self.session(groups=["turma-a"])
        self.assertEqual(self.client.get(url, headers={"Authorization": f"Bearer {inside}"}).status_code, 200)
        outside = self.session(sub="99", groups=["turma-z"])
        self.assertEqual(self.client.get(url, headers={"Authorization": f"Bearer {outside}"}).status_code, 404)


class PeopleTests(IntegrationTestCase):
    """Blocking someone and forgetting them, from the studio."""

    def setUp(self):
        super().setUp()
        self.student = self.session()
        self.identity = ExternalIdentity.objects.get()
        self.message = self.api("live_chat", session=self.student, method="post", data={"body": "Oi"}).json()["message"]
        self.client.force_login(self.owner)

    def act(self, action, **extra):
        url = reverse("integrations:person", args=[self.integration.pk, self.identity.pk])
        return self.client.post(url, {"action": action, **extra})

    def test_blocking_ends_the_sessions_and_the_sign_ins(self):
        self.act("block")
        self.assertIsNone(sessions.load(self.student))
        self.assertFalse(self.api("live_state", session=self.student).json()["chat"]["can_post"])
        response = self.sign_in()
        self.assertEqual((response.status_code, response.json()["code"]), (403, "blocked"))
        self.act("unblock")
        self.assertEqual(self.sign_in().status_code, 200)

    def test_forgetting_someone_and_keeping_their_messages(self):
        user = self.identity.user
        old_email = user.email
        self.act("erase", keep_messages="1")
        self.assertFalse(ExternalIdentity.objects.exists())
        user.refresh_from_db()
        with translation.override(settings.LANGUAGE_CODE):
            self.assertEqual(user.display_name, translation.gettext("Removed person"))
        self.assertNotEqual(user.email, old_email)
        self.assertTrue(user.email.startswith("removed-"))
        self.assertFalse(user.is_active)
        self.assertEqual(ChatMessage.objects.get(pk=self.message["id"]).author, user)
        self.assertIsNone(sessions.load(self.student))
        # Signing in again from the site makes a new, empty account.
        self.client.logout()
        self.session()
        self.assertNotEqual(ExternalIdentity.objects.get().user, user)

    def test_forgetting_someone_with_everything_they_wrote(self):
        user_id = self.identity.user_id
        self.act("erase")
        self.assertFalse(ChatMessage.objects.filter(pk=self.message["id"]).exists())
        self.assertFalse(User.objects.filter(pk=user_id).exists())

    def test_only_the_owner(self):
        self.client.force_login(make_user("nosy@example.com"))
        self.assertEqual(self.act("block").status_code, 404)
        self.assertFalse(ExternalIdentity.objects.get().is_blocked)


class SecretGraceTests(IntegrationTestCase):
    def test_the_old_secret_works_for_a_while(self):
        old_token = self.token()
        old_session = self.session()
        self.integration.rotate_secret(grace_hours=24)
        self.assertEqual(tokens.verify(old_token).external_id, "42")
        self.assertIsNotNone(sessions.load(old_session))
        self.assertEqual(tokens.verify(self.token()).external_id, "42")  # and the new one, of course
        # Once the grace is over, only the new one.
        Integration.objects.filter(pk=self.integration.pk).update(
            previous_secret_until=timezone.now() - timedelta(seconds=1)
        )
        with self.assertRaises(tokens.TokenError):
            tokens.verify(old_token)
        self.assertIsNone(sessions.load(old_session))

    def test_without_grace_the_old_one_stops_at_once(self):
        old_token = self.token()
        self.integration.rotate_secret()
        with self.assertRaises(tokens.TokenError):
            tokens.verify(old_token)

    def test_the_studio_picks_the_grace(self):
        self.client.force_login(self.owner)
        old = self.integration.secret
        self.client.post(reverse("integrations:rotate", args=[self.integration.pk]), {"grace": "1"})
        self.integration.refresh_from_db()
        self.assertEqual(self.integration.previous_secret, old)
        self.assertAlmostEqual(
            (self.integration.previous_secret_until - timezone.now()).total_seconds(), 3600, delta=30
        )
        self.client.post(reverse("integrations:rotate", args=[self.integration.pk]), {"grace": "0"})
        self.integration.refresh_from_db()
        self.assertEqual(self.integration.secrets_in_force(), [self.integration.secret])


class LookTests(IntegrationTestCase):
    def test_frames_show_only_on_the_listed_sites(self):
        live = self.client.get(reverse("live:embed", args=[self.live.uid]), {"school": self.integration.key})
        self.assertEqual(live["Content-Security-Policy"], f"frame-ancestors 'self' {SITE}")
        media = Media.objects.create(owner=self.owner, title="V", state=Media.State.READY,
                                     visibility=Media.Visibility.PUBLIC, file="media/x/v.mp4")
        video = self.client.get(reverse("core:embed", args=[media.uid]), {"school": self.integration.key})
        self.assertEqual(video["Content-Security-Policy"], f"frame-ancestors 'self' {SITE}")
        # Without a site, the public embed goes anywhere, as before.
        self.assertEqual(self.client.get(reverse("core:embed", args=[media.uid]))["Content-Security-Policy"],
                         "frame-ancestors *")

    def test_site_addresses_cannot_break_out_of_the_policy(self):
        self.assertEqual(normalize_origin("Escola.com.br/aulas"), "https://escola.com.br")
        self.assertEqual(normalize_origin("http://127.0.0.1:8765"), "http://127.0.0.1:8765")
        self.assertEqual(normalize_origin("https://[::1]:8443"), "https://[::1]:8443")
        self.assertEqual(normalize_origin("https://*.escola.com.br"), "https://*.escola.com.br")
        for bad in ("https://a;b.com", "https://escola.com.br;script-src", "https://a b.com", "https://x:99999",
                    "https://'none'", "javascript:alert(1)"):
            self.assertEqual(normalize_origin(bad), "", bad)

    def test_the_sites_colour_title_badge_and_no_brand(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("integrations:look", args=[self.integration.pk]), {
            "accent_color": "#7c3aed", "chat_title": "Sala de aula", "staff_label": "Professor",
            "hide_branding": "on", "webhook_url": "",
        })
        self.assertEqual(response.status_code, 302)
        self.client.logout()
        html = self.client.get(reverse("live:embed", args=[self.live.uid]),
                               {"school": self.integration.key, "layout": "both"}).content.decode()
        self.assertIn("--accent: #7c3aed", html)
        self.assertIn("Sala de aula", html)
        self.assertIn('data-staff-label="Professor"', html)

    def test_the_badge_is_streaming_by_default(self):
        html = self.client.get(reverse("live:embed", args=[self.live.uid]),
                               {"school": self.integration.key, "layout": "both"}).content.decode()
        self.assertIn('data-staff-label="Streaming"', html)

    def test_bad_colours_and_plain_http_webhooks_are_refused(self):
        self.client.force_login(self.owner)
        url = reverse("integrations:look", args=[self.integration.pk])
        self.client.post(url, {"accent_color": "red;}</style>", "webhook_url": ""})
        self.client.post(url, {"accent_color": "", "webhook_url": "http://escola.example.com/hook"})
        self.integration.refresh_from_db()
        self.assertEqual((self.integration.accent_color, self.integration.webhook_url), ("", ""))


class VideoCommentTests(IntegrationTestCase):
    """A video of the library in the site's frame, with its comments."""

    def setUp(self):
        super().setUp()
        self.media = Media.objects.create(owner=self.owner, title="Revisão", state=Media.State.READY,
                                          visibility=Media.Visibility.PRIVATE, file="media/x/revisao.mp4")

    def call(self, name, *args, session=None, method="get", data=None):
        url = reverse(f"integrations:{name}", args=[self.media.uid, *args])
        headers = {"Authorization": f"Bearer {session}"} if session else {}
        if method == "post":
            return self.client.post(url, json.dumps(data or {}), content_type="application/json", headers=headers)
        return self.client.get(url, headers=headers)

    def test_a_private_video_waits_for_the_site(self):
        self.assertEqual(self.client.get(reverse("core:embed", args=[self.media.uid])).status_code, 404)
        page = self.client.get(reverse("core:embed", args=[self.media.uid]),
                               {"school": self.integration.key, "layout": "both"})
        html = page.content.decode()
        self.assertIn("data-awaiting", html)
        self.assertNotIn("revisao.mp4", html)  # no address before the person signs in
        self.assertEqual(self.call("media_info").status_code, 404)
        info = self.call("media_info", session=self.session()).json()
        self.assertTrue(info["src"].endswith("revisao.mp4"))
        # Another creator's site gets nothing.
        theirs = Integration.objects.create(owner=make_user("x@example.com"), name="Outra", origins=SITE)
        self.assertEqual(self.client.get(reverse("core:embed", args=[self.media.uid]),
                                         {"school": theirs.key}).status_code, 404)

    def test_students_comment_reply_and_teachers_hide(self):
        student = self.session()
        self.assertEqual(self.call("media_comments", method="post", data={"body": "x"}).status_code, 404)
        sent = self.call("media_comments", session=student, method="post",
                         data={"body": "Minuto 3 ficou confuso", "timestamp": 180})
        self.assertEqual(sent.status_code, 201)
        comment = sent.json()["comment"]
        self.assertEqual((comment["author"], comment["timestamp"], comment["mine"]), ("Ana Lima", 180, True))

        teacher = self.session(sub="t1", name="Prof. Rui", moderator=True)
        reply = self.call("media_comments", session=teacher, method="post",
                          data={"body": "Vou explicar de novo", "parent": comment["id"]})
        self.assertEqual(reply.status_code, 201)
        self.assertTrue(reply.json()["comment"]["staff"])

        threads = self.call("media_comments", session=student).json()
        self.assertEqual(threads["count"], 2)
        self.assertEqual(threads["comments"][0]["replies"][0]["body"], "Vou explicar de novo")
        self.assertFalse(threads["can_moderate"])
        self.assertEqual(self.call("media_comment_hide", comment["id"], session=student, method="post").status_code,
                         403)
        self.assertTrue(self.call("media_comments", session=teacher).json()["can_moderate"])
        self.assertEqual(self.call("media_comment_hide", comment["id"], session=teacher, method="post").status_code,
                         200)
        self.assertTrue(Comment.objects.get(pk=comment["id"]).is_hidden)
        # The same comments on the video's own page.
        self.assertEqual(self.media.comments.filter(is_hidden=False).count(), 1)

    def test_the_rules_of_a_comment(self):
        student = self.session()
        self.assertEqual(self.call("media_comments", session=student, method="post", data={"body": " "}).status_code,
                         400)
        self.assertEqual(self.call("media_comments", session=student, method="post",
                                   data={"body": "x" * 2001}).status_code, 400)
        self.assertEqual(self.call("media_comments", session=student, method="post", data={"body": "1"}).status_code,
                         201)
        self.assertEqual(self.call("media_comments", session=student, method="post", data={"body": "2"}).status_code,
                         429)


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class WebhookTests(IntegrationTestCase):
    def setUp(self):
        super().setUp()
        Integration.objects.filter(pk=self.integration.pk).update(webhook_url="https://escola.example.com/qqplay")
        self.integration.refresh_from_db()
        self.sent = []

    def answer(self, status=200):
        def fake(method, url, body=None, headers=None, **kwargs):
            self.sent.append({"url": url, "body": body, "headers": headers})
            return net.Answer(status, b"ok")
        return fake

    def test_a_live_tells_the_site_when_it_starts_ends_and_its_recording_is_ready(self):
        live = Live.objects.create(owner=self.owner, title="Aula 2", groups="turma-a")
        with mock.patch.object(net, "request", side_effect=self.answer()), \
                self.captureOnCommitCallbacks(execute=True):
            services.start_publishing(live)
            services.stop_publishing(live)
            services.start_publishing(live)  # the encoder came back: not news
            services.end(live)
            media = Media.objects.create(owner=self.owner, title="Aula 2", state=Media.State.PROCESSING)
            Live.objects.filter(pk=live.pk).update(recording=media)
            media.state = Media.State.READY
            media.save(update_fields=["state"])
        self.assertEqual([json.loads(item["body"])["event"] for item in self.sent],
                         ["live.started", "live.ended", "recording.ready"])
        first = self.sent[0]
        self.assertEqual(first["url"], "https://escola.example.com/qqplay")
        self.assertEqual(first["headers"]["X-QQplay-Event"], "live.started")
        expected = "sha256=" + hmac.new(self.integration.secret.encode(), first["body"], hashlib.sha256).hexdigest()
        self.assertEqual(first["headers"]["X-QQplay-Signature"], expected)
        body = json.loads(self.sent[2]["body"])
        self.assertEqual((body["live"]["id"], body["live"]["classes"]), (live.uid, ["turma-a"]))
        self.assertEqual(body["live"]["recording"]["id"], media.uid)
        self.assertTrue(WebhookDelivery.objects.filter(ok=True).count() == 3)

    def test_failures_are_retried_then_given_up(self):
        live = Live.objects.create(owner=self.owner, title="Aula 3")
        with mock.patch.object(net, "request", side_effect=net.NetError("connection refused")), \
                self.captureOnCommitCallbacks(execute=True):
            webhooks.notify(live, "live.started")
        delivery = WebhookDelivery.objects.get()
        self.assertEqual(delivery.attempts, 5)  # once, then four more
        self.assertFalse(delivery.ok)
        self.assertEqual(delivery.error, "connection refused")

    def test_an_error_answer_counts_as_a_failure(self):
        live = Live.objects.create(owner=self.owner, title="Aula 4")
        with mock.patch.object(net, "request", side_effect=self.answer(500)), \
                self.captureOnCommitCallbacks(execute=True):
            webhooks.notify(live, "live.started")
        delivery = WebhookDelivery.objects.get()
        self.assertEqual((delivery.status_code, delivery.ok, delivery.attempts), (500, False, 5))

    def test_sites_without_a_webhook_or_turned_off_hear_nothing(self):
        Integration.objects.create(owner=self.owner, name="Sem webhook")
        Integration.objects.create(owner=self.owner, name="Desligada", webhook_url="https://x.example.com/h",
                                   is_active=False)
        Integration.objects.create(owner=make_user("x@example.com"), name="De outro",
                                   webhook_url="https://y.example.com/h")
        live = Live.objects.create(owner=self.owner, title="Aula 5")
        with mock.patch.object(net, "request", side_effect=self.answer()), \
                self.captureOnCommitCallbacks(execute=True):
            webhooks.notify(live, "live.started")
            webhooks.notify(live, "live.started")  # once per live and event
        self.assertEqual(len(self.sent), 1)

    def test_the_studio_sends_a_test(self):
        self.client.force_login(self.owner)
        with mock.patch.object(net, "request", side_effect=self.answer()), \
                self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("integrations:webhook_test", args=[self.integration.pk]),
                                        {"webhook_url": "https://escola.example.com/qqplay"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(json.loads(self.sent[0]["body"])["event"], "test")
        page = self.client.get(reverse("integrations:detail", args=[self.integration.pk])).content.decode()
        self.assertIn("escola.example.com/qqplay", page)


class ServerApiTests(IntegrationTestCase):
    def api_token(self, integration=None, **claims):
        now = int(time.time())
        data = {"scope": "api", "iat": now, "exp": now + 300, **claims}
        return tokens.sign(data, integration or self.integration)

    def call(self, method, name, *args, data=None, token=None, query=""):
        url = reverse(f"integrations:{name}", args=args) + query
        headers = {"Authorization": f"Bearer {token or self.api_token()}"}
        body = json.dumps(data) if data is not None else ""
        return getattr(self.client, method)(url, body, content_type="application/json", headers=headers)

    def test_only_api_tokens(self):
        url = reverse("integrations:api_lives")
        self.assertEqual(self.client.get(url).status_code, 401)
        person = self.token()
        self.assertEqual(self.client.get(url, headers={"Authorization": f"Bearer {person}"}).status_code, 401)
        long = self.api_token(exp=int(time.time()) + 7200)
        self.assertEqual(self.client.get(url, headers={"Authorization": f"Bearer {long}"}).status_code, 401)
        # ...and an API token signs no one in.
        with self.assertRaises(tokens.TokenError):
            tokens.verify(self.api_token())
        self.assertEqual(self.call("get", "api_lives").status_code, 200)

    def test_scheduling_a_live_for_some_classes(self):
        response = self.call("post", "api_lives", data={
            "title": "Revisão para a prova", "scheduled_for": "2026-11-20T19:00:00-03:00",
            "classes": ["turma-a", "turma-b"], "chat": True,
        })
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()
        live = Live.objects.get(uid=data["id"])
        self.assertEqual(live.owner, self.owner)
        self.assertEqual(live.visibility, Media.Visibility.PRIVATE)  # private unless said otherwise
        self.assertEqual(live.group_list, ["turma-a", "turma-b"])
        self.assertEqual(live.scheduled_for.isoformat(), "2026-11-20T22:00:00+00:00")
        self.assertEqual(data["stream"], {"server": live.ingest_url, "key": live.encoder_key})
        self.assertEqual(data["classes"], ["turma-a", "turma-b"])

    def test_bad_lives_are_refused(self):
        self.assertEqual(self.call("post", "api_lives", data={}).status_code, 400)
        self.assertEqual(self.call("post", "api_lives", data={"title": "  "}).status_code, 400)
        self.assertEqual(self.call("post", "api_lives", data={"title": "X", "visibility": "secret"}).status_code, 400)
        naive = self.call("post", "api_lives", data={"title": "X", "scheduled_for": "2026-11-20T19:00:00"})
        self.assertEqual(naive.status_code, 400)
        self.assertFalse(Live.objects.filter(title="X").exists())

    def test_reading_changing_ending_and_deleting(self):
        found = self.call("get", "api_lives").json()["lives"]
        self.assertEqual([item["id"] for item in found], [self.live.uid])
        changed = self.call("patch", "api_live", self.live.uid, data={"title": "Aula 1 (revisada)", "classes": "t1"})
        self.assertEqual(changed.json()["title"], "Aula 1 (revisada)")
        self.assertEqual(self.call("get", "api_live", self.live.uid).json()["classes"], ["t1"])
        self.assertEqual(self.call("delete", "api_live", self.live.uid).status_code, 409)  # on air
        ended = self.call("post", "api_live_end", self.live.uid).json()
        self.assertEqual(ended["status"], Live.Status.ENDED)
        self.assertEqual(self.call("delete", "api_live", self.live.uid).status_code, 200)
        self.assertFalse(Live.objects.filter(pk=self.live.pk).exists())

    def test_another_creators_lives_are_not_there(self):
        theirs = Live.objects.create(owner=make_user("x@example.com"), title="Deles")
        self.assertEqual(self.call("get", "api_live", theirs.uid).status_code, 404)
        self.assertEqual(self.call("delete", "api_live", theirs.uid).status_code, 404)
        self.assertNotIn(theirs.uid, str(self.call("get", "api_lives").content))

    def test_attendance_of_the_sites_people(self):
        self.session()
        mine = ExternalIdentity.objects.get().user
        other_site = Integration.objects.create(owner=self.owner, name="Outra escola")
        self.client.post(reverse("integrations:session"),
                         json.dumps({"token": self.token(integration=other_site, sub="7"), "origin": SITE}),
                         content_type="application/json")
        theirs = ExternalIdentity.objects.get(integration=other_site).user
        now = timezone.now()
        LiveAttendance.objects.create(live=self.live, user=mine, first_seen=now, last_seen=now, seconds=1800)
        LiveAttendance.objects.create(live=self.live, user=theirs, first_seen=now, last_seen=now, seconds=60)
        LiveAttendance.objects.create(live=self.live, user=self.owner, first_seen=now, last_seen=now, seconds=60)
        rows = self.call("get", "api_live_attendance", self.live.uid).json()["attendance"]
        self.assertEqual([(row["student"], row["minutes"]) for row in rows], [("42", 30.0)])

    def test_videos(self):
        ready = Media.objects.create(owner=self.owner, title="Pronto", state=Media.State.READY,
                                     visibility=Media.Visibility.PRIVATE, file="media/x/a.mp4")
        Media.objects.create(owner=self.owner, title="Processando", state=Media.State.PROCESSING)
        Media.objects.create(owner=make_user("x@example.com"), title="Deles", state=Media.State.READY)
        videos = self.call("get", "api_videos").json()["videos"]
        self.assertEqual([video["id"] for video in videos], [ready.uid])

    def test_students(self):
        self.session()
        self.assertEqual(self.call("post", "api_student_action", "42", "block").json()["blocked"], True)
        self.assertTrue(ExternalIdentity.objects.get().is_blocked)
        self.call("post", "api_student_action", "42", "unblock")
        self.assertFalse(ExternalIdentity.objects.get().is_blocked)
        self.assertEqual(self.call("post", "api_student_action", "42", "promote").status_code, 405)
        self.assertEqual(self.call("delete", "api_student", "nobody").status_code, 404)
        self.assertEqual(self.call("delete", "api_student", "42", query="?keep_messages=1").status_code, 200)
        self.assertFalse(ExternalIdentity.objects.exists())
        self.assertIsNone(identities.find(self.integration, "42"))


class StreamTests(IntegrationTestCase):
    """The change stream under ASGI; under WSGI it steps aside."""

    def test_wsgi_says_no_content(self):
        url = reverse("live:events", args=[self.live.uid]) + f"?t={stream.ticket(self.live)}"
        self.assertEqual(self.client.get(url).status_code, 204)

    def test_the_ticket_is_for_one_live(self):
        other = Live.objects.create(owner=self.owner, title="Outra")
        self.assertEqual(stream._live_for(stream.ticket(self.live), self.live.uid), self.live.pk)
        self.assertIsNone(stream._live_for(stream.ticket(other), self.live.uid))
        self.assertIsNone(stream._live_for("forged", self.live.uid))

    def test_the_state_hands_out_a_ticket(self):
        state = self.client.get(reverse("live:state", args=[self.live.uid])).json()
        self.assertTrue(state["events"].startswith(reverse("live:events", args=[self.live.uid]) + "?t="))

    def test_changes_reach_every_listener_from_one_poller(self):
        calls = []

        def pulse(live_id):
            calls.append(live_id)
            return (1 if len(calls) < 4 else 2, "live", True, None)

        async def listen():
            out = []
            events = stream._stream(self.live.pk)
            async for item in events:
                out.append(item)
                if '"v": 2' in item:
                    break
            await events.aclose()
            return out

        async def everyone():
            return await asyncio.gather(*(listen() for _ in range(3)))

        with mock.patch.object(stream, "_pulse", side_effect=pulse), mock.patch.object(stream, "TICK", 0.01):
            heard = asyncio.run(asyncio.wait_for(everyone(), timeout=5))
        for messages in heard:
            self.assertEqual(messages[0], "retry: 3000\n\n")
            self.assertIn("event: change", messages[-1])
        # One reader for the three of them: the row was read a few times, not
        # three times as often.
        self.assertLess(len(calls), 10)
        self.assertEqual(stream.hub.entries, {})
