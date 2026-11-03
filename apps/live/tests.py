"""Lives: ingest callbacks, the state machine, recordings, pages and chat."""
import shutil
import subprocess
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.media import pipeline
from apps.media.models import Media
from apps.rbac.roles import Role
from apps.users.models import User

from . import presence, services, tasks
from .models import ChatMessage, Live, LiveRecording

SECRET = "test-hook-secret"

requires_ffmpeg = unittest.skipUnless(pipeline.available(), "ffmpeg and ffprobe are not installed")


def make_user(email, role=Role.MEMBER):
    return User.objects.create_user(email=email, password="x" * 20, role=role)


class LiveTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = make_user("host@example.com")
        self.live = Live.objects.create(owner=self.owner, title="Aula de física")

    def reload(self):
        self.live.refresh_from_db()
        return self.live


class IdentifierTests(LiveTestCase):
    def test_the_three_identifiers_are_distinct(self):
        live = self.live
        self.assertEqual(len(live.uid), 12)
        self.assertEqual(len(live.playback_id), 20)
        self.assertGreaterEqual(len(live.stream_key), 30)
        self.assertEqual(len({live.uid, live.playback_id, live.stream_key}), 3)
        self.assertEqual(live.encoder_key, f"{live.playback_id}?key={live.stream_key}")

    @override_settings(LIVE_HLS_URL="https://media.example.com/hls/", LIVE_RTMP_URL="rtmp://media.example.com/live/")
    def test_addresses_follow_the_settings(self):
        self.assertEqual(self.live.hls_url, f"https://media.example.com/hls/{self.live.playback_id}/index.m3u8")
        self.assertEqual(self.live.ingest_url, "rtmp://media.example.com/live")


@override_settings(LIVE_HOOK_SECRET=SECRET)
class HookTests(LiveTestCase):
    def hook(self, event, secret=SECRET, **fields):
        return self.client.post(f"/live/hooks/{secret}/{event}/", fields)

    def publish(self, key=None):
        return self.hook("publish", name=self.live.playback_id, key=key or self.live.stream_key,
                         addr="203.0.113.7", app="live", call="publish")

    def test_callbacks_need_the_secret(self):
        self.assertEqual(self.hook("publish", secret="wrong", name=self.live.playback_id).status_code, 404)
        with override_settings(LIVE_HOOK_SECRET=""):
            self.assertEqual(self.hook("publish", secret="", name=self.live.playback_id).status_code, 404)
        self.assertEqual(self.client.get(f"/live/hooks/{SECRET}/publish/").status_code, 405)
        self.assertEqual(self.hook("something-else").status_code, 404)

    def test_the_right_key_goes_on_air(self):
        self.assertEqual(self.publish().status_code, 200)
        live = self.reload()
        self.assertEqual(live.status, Live.Status.LIVE)
        self.assertTrue(live.publishing)
        self.assertEqual(live.sessions, 1)
        self.assertIsNotNone(live.started_at)

    def test_a_wrong_key_or_an_unknown_stream_is_refused(self):
        self.assertEqual(self.publish(key="not-the-key").status_code, 403)
        self.assertEqual(self.hook("publish", name="nope", key=self.live.stream_key).status_code, 403)
        self.assertEqual(self.hook("publish", name=self.live.playback_id).status_code, 403)
        self.assertEqual(self.reload().status, Live.Status.SCHEDULED)

    def test_an_ended_live_cannot_be_restarted(self):
        Live.objects.filter(pk=self.live.pk).update(status=Live.Status.ENDED, ended_at=timezone.now())
        self.assertEqual(self.publish().status_code, 403)

    def test_a_dropped_encoder_interrupts_and_a_reconnect_resumes(self):
        self.publish()
        started = self.reload().started_at
        self.hook("publish-done", name=self.live.playback_id)
        live = self.reload()
        self.assertEqual(live.status, Live.Status.INTERRUPTED)
        self.assertFalse(live.publishing)
        self.assertIsNotNone(live.interrupted_at)

        self.publish()
        live = self.reload()
        self.assertEqual(live.status, Live.Status.LIVE)
        self.assertEqual(live.sessions, 2)
        self.assertIsNone(live.interrupted_at)
        self.assertEqual(live.started_at, started)

    def test_only_the_expected_recording_names_are_kept(self):
        pb = self.live.playback_id
        for path in (f"/data/recordings/{pb}-1793718393.flv", f"{pb}-1.flv"):
            self.assertEqual(self.hook("record-done", name=pb, path=path).status_code, 200)
        for path in ("../../etc/passwd", f"/data/{pb}-1.mp4", "/data/other-123.flv", f"/data/{pb}-x.flv"):
            self.hook("record-done", name=pb, path=path)
        self.assertEqual(
            sorted(self.live.recordings.values_list("filename", flat=True)),
            [f"{pb}-1.flv", f"{pb}-1793718393.flv"],
        )

    def test_callbacks_for_unknown_streams_are_ignored(self):
        self.assertEqual(self.hook("publish-done", name="unknown").status_code, 200)
        self.assertEqual(self.hook("record-done", name="unknown", path="/x/unknown-1.flv").status_code, 200)
        self.assertFalse(LiveRecording.objects.exists())


class StateMachineTests(LiveTestCase):
    def go_live(self):
        services.start_publishing(self.live)

    @override_settings(LIVE_RECONNECT_GRACE_SECONDS=120)
    def test_an_interrupted_live_ends_after_the_grace_period(self):
        self.go_live()
        services.stop_publishing(self.live)
        dropped = timezone.now() - timedelta(seconds=200)
        Live.objects.filter(pk=self.live.pk).update(interrupted_at=dropped)
        LiveRecording.objects.create(live=self.live, filename=f"{self.live.playback_id}-1.flv")
        with mock.patch.object(tasks.finalize_live, "delay") as finalize, \
                self.captureOnCommitCallbacks(execute=True):
            services.tick(self.reload())
        live = self.reload()
        self.assertEqual(live.status, Live.Status.ENDED)
        self.assertEqual(live.ended_at, dropped)
        finalize.assert_called_once_with(str(live.pk))

    @override_settings(LIVE_RECONNECT_GRACE_SECONDS=120)
    def test_an_interrupted_live_waits_for_its_encoder(self):
        self.go_live()
        services.stop_publishing(self.live)
        services.tick(self.reload())
        self.assertEqual(self.reload().status, Live.Status.INTERRUPTED)

    def test_ending_from_the_studio_cuts_the_encoder_off_and_waits_for_the_file(self):
        self.go_live()
        with mock.patch("apps.live.ingest.drop_publisher", return_value=True) as drop, \
                mock.patch.object(tasks.finalize_live, "delay") as finalize, \
                self.captureOnCommitCallbacks(execute=True):
            self.assertTrue(services.end(self.reload()))
            drop.assert_called_once_with(self.live.playback_id)
            self.assertEqual(self.reload().status, Live.Status.ENDED)
            # The encoder goes away, but its recording is not written yet.
            services.stop_publishing(self.reload())
            finalize.assert_not_called()
            services.add_recording(self.reload(), f"{self.live.playback_id}-1.flv")
        finalize.assert_called_once()
        self.assertFalse(services.end(self.reload()))

    def test_ending_a_live_that_never_started_finishes_at_once(self):
        with mock.patch.object(tasks.finalize_live, "delay") as finalize, \
                self.captureOnCommitCallbacks(execute=True):
            services.end(self.live)
        finalize.assert_called_once()

    def test_a_missing_recording_callback_stops_blocking_after_a_while(self):
        Live.objects.filter(pk=self.live.pk).update(
            status=Live.Status.ENDED, sessions=2, ended_at=timezone.now()
        )
        LiveRecording.objects.create(live=self.live, filename=f"{self.live.playback_id}-1.flv")
        live = self.reload()
        self.assertFalse(services.recording_complete(live))
        later = timezone.now() + timedelta(seconds=services.RECORDING_SETTLE_SECONDS + 1)
        self.assertTrue(services.recording_complete(live, now=later))


def make_flv(path: Path, seconds: int = 2) -> None:
    subprocess.run(
        [
            shutil.which("ffmpeg"), "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=25:duration={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={seconds}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-g", "50",
            "-c:a", "aac", "-shortest", "-f", "flv", str(path),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )


@requires_ffmpeg
class RecordingTests(LiveTestCase):
    def setUp(self):
        super().setUp()
        self.root = Path(tempfile.mkdtemp(prefix="akon-live-test-"))
        self.recordings = self.root / "recordings"
        self.recordings.mkdir()
        self.settings_override = override_settings(
            MEDIA_ROOT=str(self.root / "media"), LIVE_RECORDINGS_DIR=str(self.recordings)
        )
        self.settings_override.enable()
        Live.objects.filter(pk=self.live.pk).update(
            status=Live.Status.ENDED, sessions=2, visibility=Media.Visibility.UNLISTED,
            started_at=timezone.now() - timedelta(minutes=5), ended_at=timezone.now(),
        )
        self.files = []
        for n in (1, 2):
            path = self.recordings / f"{self.live.playback_id}-{n}.flv"
            make_flv(path)
            LiveRecording.objects.create(live=self.live, filename=path.name)
            self.files.append(path)

    def tearDown(self):
        self.settings_override.disable()
        shutil.rmtree(self.root, ignore_errors=True)
        super().tearDown()

    def test_the_sessions_are_joined_and_published_as_a_video(self):
        self.assertEqual(tasks.finalize_live(str(self.live.pk)), "recorded")
        live = self.reload()
        media = live.recording
        self.assertIsNotNone(media)
        self.assertEqual(media.title, live.title)
        self.assertEqual(media.owner, self.owner)
        self.assertEqual(media.visibility, Media.Visibility.UNLISTED)
        self.assertEqual(media.kind, Media.Kind.VIDEO)
        self.assertEqual(media.state, Media.State.READY)
        self.assertAlmostEqual(media.duration_seconds, 4, delta=0.6)
        self.assertTrue(media.hls_playlist)
        self.assertEqual(list(self.recordings.iterdir()), [])
        self.assertEqual(tasks.finalize_live(str(self.live.pk)), "skipped")

    def test_a_live_that_is_not_recorded_leaves_nothing(self):
        Live.objects.filter(pk=self.live.pk).update(record=False)
        self.assertEqual(tasks.finalize_live(str(self.live.pk)), "no recording")
        self.assertIsNone(self.reload().recording)
        self.assertEqual(list(self.recordings.iterdir()), [])
        state = self.client.get(reverse("live:state", args=[self.live.uid])).json()
        self.assertEqual(state["recording"]["state"], "none")

    def test_missing_files_are_reported(self):
        for path in self.files:
            path.unlink()
        self.assertEqual(tasks.finalize_live(str(self.live.pk)), "missing")
        state = self.client.get(reverse("live:state", args=[self.live.uid])).json()
        self.assertEqual(state["recording"]["state"], "failed")


class PageTests(LiveTestCase):
    def setUp(self):
        super().setUp()
        self.viewer = make_user("viewer@example.com")
        self.curator = make_user("curator@example.com", role=Role.EDITOR)

    def set(self, **fields):
        Live.objects.filter(pk=self.live.pk).update(**fields)
        return self.reload()

    def test_a_public_live_is_listed_and_its_page_has_no_secret(self):
        page = self.client.get(reverse("live:watch", args=[self.live.uid]))
        self.assertContains(page, "Aula de física")
        self.assertContains(page, "data-live-state")
        self.assertNotContains(page, self.live.stream_key)
        self.assertContains(self.client.get(reverse("live:lives")), "Aula de física")
        state = self.client.get(reverse("live:state", args=[self.live.uid]))
        self.assertNotIn(self.live.stream_key, state.content.decode())
        self.assertEqual(state.json()["hls"], "")
        self.assertNotIn("control", state.json())

    def test_the_stream_address_is_only_given_while_on_air(self):
        self.set(status=Live.Status.LIVE, publishing=True, started_at=timezone.now())
        state = self.client.get(reverse("live:state", args=[self.live.uid])).json()
        self.assertTrue(state["on_air"])
        self.assertIn(self.live.playback_id, state["hls"])

    def test_on_air_lives_show_on_the_home_page(self):
        self.set(status=Live.Status.LIVE, started_at=timezone.now())
        home = self.client.get("/")
        self.assertContains(home, reverse("live:watch", args=[self.live.uid]))
        self.assertContains(home, "nav-live")

    def test_unlisted_lives_open_by_link_only(self):
        self.set(visibility=Media.Visibility.UNLISTED)
        self.assertEqual(self.client.get(reverse("live:watch", args=[self.live.uid])).status_code, 200)
        self.assertNotContains(self.client.get(reverse("live:lives")), "Aula de física")

    def test_private_lives_are_for_the_owner_and_curators(self):
        self.set(visibility=Media.Visibility.PRIVATE)
        for url in (reverse("live:watch", args=[self.live.uid]), reverse("live:state", args=[self.live.uid])):
            self.assertEqual(self.client.get(url).status_code, 404)
            self.client.force_login(self.viewer)
            self.assertEqual(self.client.get(url).status_code, 404)
            self.client.force_login(self.curator)
            self.assertEqual(self.client.get(url).status_code, 200)
            self.client.force_login(self.owner)
            self.assertEqual(self.client.get(url).status_code, 200)
            self.client.logout()
        self.assertEqual(self.client.get(reverse("live:embed", args=[self.live.uid])).status_code, 404)

    def test_the_embed_can_live_in_other_sites(self):
        response = self.client.get(reverse("live:embed", args=[self.live.uid]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Security-Policy"], "frame-ancestors *")
        self.assertNotContains(response, self.live.stream_key)

    def test_the_embed_plays_the_recording_once_it_is_ready(self):
        media = Media.objects.create(owner=self.owner, title="Aula de física", state=Media.State.READY,
                                     file="media/x/rec.mp4")
        self.set(status=Live.Status.ENDED, ended_at=timezone.now(), recording=media,
                 finalized_at=timezone.now())
        response = self.client.get(reverse("live:embed", args=[self.live.uid]) + "?autoplay=1")
        self.assertRedirects(response, reverse("core:embed", args=[media.uid]) + "?autoplay=1",
                             fetch_redirect_response=False)
        state = self.client.get(reverse("live:state", args=[self.live.uid])).json()
        self.assertEqual(state["recording"], {
            "state": "ready", "ready": True,
            "url": reverse("core:media", args=[media.uid]), "embed": reverse("core:embed", args=[media.uid]),
        })

    def test_the_control_room_is_for_the_owner_and_curators(self):
        url = reverse("live:control", args=[self.live.uid])
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.curator)
        self.assertContains(self.client.get(url), self.live.stream_key)
        self.client.force_login(self.owner)
        page = self.client.get(url)
        self.assertContains(page, self.live.encoder_key)
        self.assertContains(page, self.live.ingest_url)
        self.assertIn("control", self.client.get(reverse("live:state", args=[self.live.uid])).json())

    def test_creating_a_live_reads_the_start_time_in_the_viewers_time_zone(self):
        self.client.force_login(self.viewer)
        response = self.client.post(reverse("live:new"), {
            "title": "Revisão para a prova",
            "visibility": "public",
            "scheduled_for": "2026-12-01T19:00",
            # What the page's script sends for 19:00 in São Paulo (UTC−3).
            "scheduled_for_iso": "2026-12-01T22:00:00.000Z",
            "record": "on",
        })
        live = Live.objects.get(title="Revisão para a prova")
        self.assertRedirects(response, reverse("live:control", args=[live.uid]))
        self.assertEqual(live.owner, self.viewer)
        self.assertEqual(live.scheduled_for.isoformat(), "2026-12-01T22:00:00+00:00")
        self.assertTrue(live.record)
        self.assertFalse(live.chat_enabled)

    def test_editing_ending_rotating_and_deleting(self):
        self.client.force_login(self.owner)
        control = reverse("live:control", args=[self.live.uid])
        self.client.post(reverse("live:edit", args=[self.live.uid]), {
            "title": "Aula de física — parte 2", "visibility": "public", "chat_enabled": "on", "record": "on",
        })
        self.assertEqual(self.reload().title, "Aula de física — parte 2")

        old_key = self.live.stream_key
        self.client.post(reverse("live:rotate_key", args=[self.live.uid]))
        self.assertNotEqual(self.reload().stream_key, old_key)

        self.set(status=Live.Status.LIVE, publishing=True)
        key = self.live.stream_key
        self.client.post(reverse("live:rotate_key", args=[self.live.uid]))
        self.assertEqual(self.reload().stream_key, key)
        self.assertRedirects(self.client.post(reverse("live:delete", args=[self.live.uid])), control)
        self.assertTrue(Live.objects.filter(pk=self.live.pk).exists())

        self.assertEqual(self.client.get(reverse("live:end", args=[self.live.uid])).status_code, 405)
        with mock.patch("apps.live.ingest.drop_publisher", return_value=True):
            self.client.post(reverse("live:end", args=[self.live.uid]))
        self.assertEqual(self.reload().status, Live.Status.ENDED)
        self.set(publishing=False)
        self.assertRedirects(self.client.post(reverse("live:delete", args=[self.live.uid])), reverse("live:studio"))
        self.assertFalse(Live.objects.filter(pk=self.live.pk).exists())

    def test_the_studio_needs_an_account(self):
        self.assertEqual(self.client.get(reverse("live:studio")).status_code, 302)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("live:studio")), "Aula de física")


class ChatTests(LiveTestCase):
    def setUp(self):
        super().setUp()
        self.viewer = make_user("viewer@example.com")
        self.curator = make_user("curator@example.com", role=Role.EDITOR)
        self.url = reverse("live:chat", args=[self.live.uid])
        self.state_url = reverse("live:state", args=[self.live.uid])

    def say(self, body):
        cache.clear()
        return self.client.post(self.url, {"body": body})

    def test_signing_in_is_required_to_talk(self):
        response = self.client.post(self.url, {"body": "Oi"})
        self.assertEqual(response.status_code, 403)
        self.assertIn("detail", response.json())

    def test_messages_arrive_with_the_next_poll(self):
        self.client.force_login(self.viewer)
        response = self.say("Professor, pode repetir?")
        self.assertEqual(response.status_code, 201)
        message = response.json()["message"]
        self.assertTrue(message["mine"])
        self.assertFalse(message["host"])

        self.client.force_login(self.owner)
        self.say("Claro!")
        state = self.client.get(self.state_url, {"after": message["id"]}).json()
        bodies = [m["body"] for m in state["chat"]["messages"]]
        self.assertEqual(bodies, ["Claro!"])
        self.assertTrue(state["chat"]["messages"][0]["host"])

    def test_empty_long_and_hasty_messages_are_refused(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.say("   ").status_code, 400)
        self.assertEqual(self.say("x" * 501).status_code, 400)
        self.assertEqual(self.client.post(self.url, {"body": "um"}).status_code, 201)
        self.assertEqual(self.client.post(self.url, {"body": "dois"}).status_code, 429)

    def test_the_chat_closes_with_the_live_or_when_turned_off(self):
        self.client.force_login(self.viewer)
        Live.objects.filter(pk=self.live.pk).update(chat_enabled=False)
        self.assertEqual(self.say("Oi").status_code, 403)
        Live.objects.filter(pk=self.live.pk).update(chat_enabled=True, status=Live.Status.ENDED)
        self.assertEqual(self.say("Oi").status_code, 403)

    def test_moderators_hide_messages_for_everyone(self):
        message = ChatMessage.objects.create(live=self.live, author=self.viewer, body="spam")
        hide = reverse("live:chat_hide", args=[self.live.uid, message.id])
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.post(hide).status_code, 403)
        self.client.force_login(self.curator)
        self.assertEqual(self.client.post(hide).status_code, 200)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(hide).status_code, 200)

        state = self.client.get(self.state_url, {"after": message.id}).json()
        self.assertEqual(state["chat"]["hidden"], [message.id])
        self.assertEqual(self.client.get(self.state_url).json()["chat"]["messages"], [])

    def test_a_new_page_gets_the_latest_messages(self):
        for n in range(60):
            ChatMessage.objects.create(live=self.live, author=self.viewer, body=f"m{n}")
        messages = self.client.get(self.state_url).json()["chat"]["messages"]
        self.assertEqual(len(messages), 50)
        self.assertEqual(messages[-1]["body"], "m59")


class PresenceTests(LiveTestCase):
    def test_each_viewer_counts_once(self):
        cache.clear()
        for client in ("aaaaaaaa1", "bbbbbbbb2", "aaaaaaaa1", "not ok!"):
            presence.touch(self.live, client)
        self.assertEqual(presence.count(self.live), 2)
        self.assertEqual(self.reload().peak_viewers, 2)


class PageStateEscapingTests(LiveTestCase):
    def test_a_chat_message_cannot_break_out_of_the_page_state(self):
        viewer = make_user("mallory@example.com")
        ChatMessage.objects.create(live=self.live, author=viewer, body='<!--<script></script><img src=x onerror=alert(1)>&')
        page = self.client.get(reverse("live:watch", args=[self.live.uid])).content.decode()
        start = page.index("data-live-state>") + len("data-live-state>")
        data = page[start:page.index("</script>", start)]
        self.assertNotIn("<", data)
        self.assertNotIn(">", data)
        self.assertIn("\\u003C!--\\u003Cscript", data)
