"""Profile pictures: the gallery, uploaded photos and how they are shown."""
import io
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template import Context, Template
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from . import avatars
from .models import User


def picture(width=400, height=200, fmt="JPEG", exif=None, color=(200, 40, 40)) -> bytes:
    image = Image.new("RGB", (width, height), color)
    # A marker in the top-left corner, to tell which way up it ends.
    for x in range(20):
        for y in range(20):
            image.putpixel((x, y), (0, 0, 255))
    out = io.BytesIO()
    kwargs = {"exif": exif} if exif is not None else {}
    image.save(out, format=fmt, **kwargs)
    return out.getvalue()


def upload(data: bytes, name="photo.jpg", content_type="image/jpeg"):
    return SimpleUploadedFile(name, data, content_type=content_type)


class AvatarTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.media = Path(tempfile.mkdtemp(prefix="qq-avatars-"))
        self.settings_override = override_settings(MEDIA_ROOT=str(self.media))
        self.settings_override.enable()
        self.user = User.objects.create_user(email="ana@example.com", password="x" * 20, display_name="Ana Lima")

    def tearDown(self):
        self.settings_override.disable()
        shutil.rmtree(self.media, ignore_errors=True)

    def render(self, source, **context):
        return Template("{% load akon %}" + source).render(Context(context))


class ProcessingTests(AvatarTestCase):
    def test_a_photo_becomes_a_small_square_webp_without_metadata(self):
        exif = Image.Exif()
        exif[0x010F] = "PhoneMaker"  # camera make
        exif[0x8825] = {2: (23.0, 33.0, 0.0)}  # GPS
        content = avatars.process_photo(upload(picture(800, 400, exif=exif.tobytes())))
        image = Image.open(io.BytesIO(content.read()))
        self.assertEqual(image.format, "WEBP")
        self.assertEqual(image.size, (avatars.SIZE, avatars.SIZE))
        self.assertFalse(image.getexif())
        self.assertTrue(content.name.endswith(".webp"))

    def test_a_sideways_phone_photo_is_turned_upright(self):
        # Stored on its side: left half blue, right half red. The camera's
        # note says to turn it a quarter clockwise, which puts blue on top.
        stored = Image.new("RGB", (400, 200), (220, 30, 30))
        stored.paste((30, 30, 220), (0, 0, 200, 200))
        exif = Image.Exif()
        exif[0x0112] = 6
        data = io.BytesIO()
        stored.save(data, format="JPEG", exif=exif.tobytes())
        content = avatars.process_photo(upload(data.getvalue()))
        image = Image.open(io.BytesIO(content.read())).convert("RGB")
        middle = image.width // 2
        top = image.getpixel((middle, 4))
        bottom = image.getpixel((middle, image.height - 4))
        self.assertGreater(top[2], 150)
        self.assertLess(top[0], 100)
        self.assertGreater(bottom[0], 150)
        self.assertLess(bottom[2], 100)

    def test_what_is_not_a_picture_is_refused(self):
        with self.assertRaises(ValidationError):
            avatars.process_photo(upload(b"<svg onload=alert(1)>", "x.svg", "image/svg+xml"))
        with self.assertRaises(ValidationError):
            avatars.process_photo(upload(b"plain text", "x.jpg"))

    def test_big_files_and_huge_pictures_are_refused(self):
        with mock.patch.object(avatars, "MAX_UPLOAD_BYTES", 100):
            with self.assertRaises(ValidationError):
                avatars.process_photo(upload(picture()))
        with mock.patch.object(avatars, "MAX_PIXELS", 1000):
            with self.assertRaises(ValidationError):
                avatars.process_photo(upload(picture()))

    def test_every_character_has_its_picture(self):
        folder = Path(__file__).resolve().parents[2] / "static" / "avatars"
        self.assertEqual({path.stem for path in folder.glob("*.svg")}, set(avatars.CHARACTERS))


class ShowingTests(AvatarTestCase):
    def test_initials_by_default(self):
        html = self.render('{% avatar user "md" %}', user=self.user)
        self.assertIn(">AL<", html)
        self.assertIn(f'data-avatar-of="{self.user.pk}"', html)
        self.assertNotIn("<img", html)

    def test_a_character(self):
        avatars.choose(self.user, "fox")
        html = self.render('{% avatar user "lg" %}', user=self.user)
        self.assertIn("avatar-pic", html)
        self.assertIn("avatars/fox.svg", html)
        self.assertEqual(self.user.avatar_url, avatars.character_url("fox"))

    def test_initials_can_be_asked_for_whatever_was_picked(self):
        avatars.choose(self.user, "fox")
        html = self.render('{% avatar user "lg" "initials" %}', user=self.user)
        self.assertNotIn("<img", html)
        self.assertNotIn("data-avatar-of", html)

    def test_an_unknown_value_falls_back_to_the_initials(self):
        User.objects.filter(pk=self.user.pk).update(avatar="dragon")
        self.user.refresh_from_db()
        self.assertEqual(self.user.avatar_url, "")
        self.assertIn(">AL<", self.render("{% avatar user %}", user=self.user))

    def test_the_account_api_gives_the_picture(self):
        avatars.choose(self.user, "owl")
        self.client.force_login(self.user)
        data = self.client.get("/api/v1/whoami/").json()
        self.assertEqual(data["avatar"], "owl")
        self.assertTrue(data["avatar_url"].startswith("http://testserver/"))
        self.assertTrue(data["avatar_url"].endswith("avatars/owl.svg"))


class AccountPageTests(AvatarTestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("core:account_avatar")
        self.client.force_login(self.user)

    def post(self, data, json=True):
        headers = {"Accept": "application/json"} if json else {}
        return self.client.post(self.url, data, headers=headers)

    def photos(self):
        return sorted(path.name for path in (self.media / "avatars").glob("*")) if (self.media / "avatars").exists() else []

    def test_the_page_offers_every_character(self):
        html = self.client.get(reverse("core:account")).content.decode()
        for key in avatars.CHARACTERS:
            self.assertIn(f'value="{key}"', html)
        self.assertIn('value="initials"', html)

    def test_picking_a_character(self):
        response = self.post({"choice": "robot"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["choice"], "robot")
        self.assertIn("avatars/robot.svg", payload["html"]["xl"])
        self.assertEqual(set(payload["html"]), {"xs", "sm", "md", "lg", "xl"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.avatar, "robot")

        self.assertEqual(self.post({"choice": "initials"}).json()["choice"], "initials")
        self.user.refresh_from_db()
        self.assertEqual(self.user.avatar, "")

    def test_wrong_picks_are_refused(self):
        self.assertEqual(self.post({"choice": "dragon"}).status_code, 400)
        self.assertEqual(self.post({"choice": "photo"}).status_code, 400)  # none uploaded yet
        self.user.refresh_from_db()
        self.assertEqual(self.user.avatar, "")

    def test_uploading_replacing_and_removing_a_photo(self):
        first = self.post({"photo": upload(picture())})
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(first.json()["choice"], "photo")
        self.assertTrue(first.json()["photo"].endswith(".webp"))
        self.assertEqual(len(self.photos()), 1)
        old = self.photos()[0]

        cache.clear()  # the pause between two uploads
        self.post({"photo": upload(picture(color=(10, 200, 10)))})
        self.assertEqual(len(self.photos()), 1)
        self.assertNotEqual(self.photos()[0], old)

        # A character can be picked and the photo picked again later.
        self.post({"choice": "cat"})
        self.assertEqual(self.post({"choice": "photo"}).json()["choice"], "photo")

        removed = self.post({"remove_photo": "1"}).json()
        self.assertEqual(removed["choice"], "initials")
        self.assertEqual(removed["photo"], "")
        self.assertEqual(self.photos(), [])

    def test_uploads_are_spaced_out(self):
        self.assertEqual(self.post({"photo": upload(picture())}).status_code, 200)
        self.assertEqual(self.post({"photo": upload(picture())}).status_code, 400)

    def test_a_bad_file_is_refused_with_a_reason(self):
        response = self.post({"photo": upload(b"not a picture", "x.png", "image/png")})
        self.assertEqual(response.status_code, 400)
        self.assertIn("detail", response.json())
        self.assertEqual(self.photos(), [])

    def test_without_javascript_it_is_a_plain_form(self):
        response = self.post({"choice": "panda"}, json=False)
        self.assertRedirects(response, reverse("core:account"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.avatar, "panda")

    def test_signing_in_is_required(self):
        self.client.logout()
        response = self.post({"choice": "panda"}, json=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_a_deleted_account_takes_its_photo_with_it(self):
        self.post({"photo": upload(picture())})
        self.assertEqual(len(self.photos()), 1)
        User.objects.filter(pk=self.user.pk).delete()
        self.assertEqual(self.photos(), [])
