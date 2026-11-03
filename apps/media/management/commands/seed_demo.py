"""Fill an empty portal with demonstration content.

Everything is generated locally — ffmpeg's synthetic sources for video and
sound, Pillow for pictures and a PDF — and then goes through the real
processing pipeline, so a fresh checkout shows posters, HLS ladders, seek
previews, waveforms, chapters, subtitles, comments and playlists.

    python manage.py seed_demo            # about a minute on a laptop
    python manage.py seed_demo --quick    # shorter clips, for CI or a smoke test

Accounts are created with a known password, so the command refuses to run
when DEBUG is off unless ``--force`` is given.
"""
import math
from datetime import timedelta
import random
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.media import pipeline
from apps.media.models import (
    Category,
    Chapter,
    Comment,
    Media,
    Playlist,
    PlaylistItem,
    Subtitle,
    SubtitleCue,
)
from apps.media.tasks import process_media
from apps.rbac.roles import Role
from apps.users.models import User

PEOPLE = [
    ("studio@example.com", "Akon Studio", Role.EDITOR),
    ("ana@example.com", "Ana Lima", Role.MEMBER),
    ("leo@example.com", "Leo Park", Role.MEMBER),
    ("kai@example.com", "Kai Moreno", Role.MANAGER),
]

CATEGORIES = [
    ("Generative art", "Patterns grown by rules rather than drawn by hand."),
    ("Broadcast", "Test cards, signals and the craft of getting pictures on air."),
    ("Sound lab", "Tones, textures and field recordings."),
    ("Stills", "Pictures and posters."),
    ("Docs", "Specifications and handbooks."),
]

CHORD = "0.08*sin(2*PI*{a}*t)+0.06*sin(2*PI*{b}*t)+0.05*sin(2*PI*{c}*t)"

VIDEOS = [
    {
        "title": "Aurora flow — generative gradients in 1080p",
        "owner": "studio@example.com",
        "category": "Generative art",
        "source": "gradients=s=1920x1080:r=30:c0=0x05070c:c1=0x2ee6a6:c2=0x22d3ee:c3=0x7c6cff:nb_colors=4:speed=0.02",
        "audio": CHORD.format(a=220, b=277.18, c=329.63),
        "description": "Four colour stops drifting across a 1080p canvas — the brand palette in motion.\n\nEncoded into a three-rung HLS ladder (1080p / 720p / 480p). Open the gear menu to switch rungs, or press I for stats.",
        "chapters": [(0, "Fade in"), (0.35, "Mint meets cyan"), (0.7, "Violet drift")],
        "views": 1840,
        "subtitles": True,
    },
    {
        "title": "Mandelbrot deep zoom",
        "owner": "ana@example.com",
        "category": "Generative art",
        "source": "mandelbrot=s=1280x720:r=30",
        "audio": CHORD.format(a=196, b=246.94, c=293.66),
        "description": "The classic set, rendered frame by frame by ffmpeg's mandelbrot source.",
        "chapters": [(0, "Seahorse valley"), (0.5, "Into the spiral")],
        "views": 932,
    },
    {
        "title": "Game of Life — mould and growth",
        "owner": "leo@example.com",
        "category": "Generative art",
        "source": "life=s=320x180:mold=10:r=24:ratio=0.12:death_color=#05070c:life_color=#2ee6a6:mold_color=#7c6cff,scale=1280:720:flags=neighbor",
        "audio": CHORD.format(a=174.61, b=220, c=261.63),
        "description": "Conway's rules with a decay trail: dead cells fade from mint to violet before they disappear.",
        "views": 412,
    },
    {
        "title": "Rule 30 — a cellular automaton scrolling by",
        "owner": "studio@example.com",
        "category": "Generative art",
        "source": "cellauto=rule=30:s=320x180:r=24:scroll=1:start_full=0,scale=1280:720:flags=neighbor,lutrgb=r='val*0.18':g='val*0.83':b='val*0.93'",
        "audio": CHORD.format(a=146.83, b=185, c=220),
        "description": "One rule, one row of cells, endless complexity.",
        "views": 267,
    },
    {
        "title": "SMPTE HD colour bars with 1 kHz line-up tone",
        "owner": "kai@example.com",
        "category": "Broadcast",
        "source": "smptehdbars=s=1920x1080:r=25",
        "audio": "0.25*sin(2*PI*1000*t)",
        "description": "The reference every broadcast engineer knows. Useful to check colour handling across renditions.",
        "views": 3120,
    },
    {
        "title": "Signal generator — moving test pattern",
        "owner": "kai@example.com",
        "category": "Broadcast",
        "source": "testsrc2=s=1280x720:r=30",
        "audio": "0.2*sin(2*PI*440*t)*(0.5+0.5*sin(2*PI*0.5*t))",
        "description": "ffmpeg's testsrc2: a timecode, a moving gradient and a sweep. Handy for checking seeking — try the storyboard previews on the seek bar.",
        "views": 655,
    },
]

AUDIO = [
    {
        "title": "Ambient pad in A major",
        "owner": "ana@example.com",
        "category": "Sound lab",
        "expr": "(0.12*sin(2*PI*220*t)+0.09*sin(2*PI*277.18*t)+0.08*sin(2*PI*329.63*t)+0.04*sin(2*PI*440*t))*(0.6+0.4*sin(2*PI*0.2*t))",
        "description": "A slow, breathing chord. The poster is the waveform, drawn at upload.",
        "views": 389,
    },
    {
        "title": "Rain on the studio roof (pink noise study)",
        "owner": "leo@example.com",
        "category": "Sound lab",
        "noise": True,
        "description": "Pink noise shaped by a slow tremolo — a stand-in for a field recording.",
        "views": 211,
    },
    {
        "title": "Sine sweep 40 Hz → 4 kHz",
        "owner": "kai@example.com",
        "category": "Sound lab",
        "expr": "0.3*sin(2*PI*(40*t+(4000-40)*t*t/(2*DUR)))",
        "description": "A linear chirp for checking speakers and headphones.",
        "views": 98,
    },
]

IMAGES = [
    ("Aurora field", "ana@example.com", "aurora", 510),
    ("Neon horizon grid", "studio@example.com", "horizon", 742),
    ("Orbit study", "leo@example.com", "orbits", 133),
]

SUBTITLES_EN = [
    (0.5, 2.8, "Four colour stops start to move."),
    (3.0, 5.6, "Mint drifts towards cyan…"),
    (5.8, 8.6, "…while violet rises from the corner."),
    (8.8, 11.5, "Every frame is generated, nothing is filmed."),
]
SUBTITLES_PT = [
    (0.5, 2.8, "Quatro cores começam a se mover."),
    (3.0, 5.6, "O menta desliza em direção ao ciano…"),
    (5.8, 8.6, "…enquanto o violeta sobe pelo canto."),
    (8.8, 11.5, "Cada quadro é gerado; nada foi filmado."),
]

COMMENTS = [
    ("leo@example.com", "The 1080p rung looks spotless even on a slow connection — switch to Auto and watch it climb.", None),
    ("ana@example.com", "Love the moment at 0:06 when violet takes over.", 6.0),
    ("kai@example.com", "Stats overlay (press I) shows the bandwidth estimate live. Very handy for testing.", None),
]


class Command(BaseCommand):
    help = "Create demonstration accounts, media, playlists, subtitles and comments."

    def add_arguments(self, parser):
        parser.add_argument("--quick", action="store_true", help="Shorter clips (about 6 s each)")
        parser.add_argument("--password", default="demo-pass-123", help="Password for the demo accounts")
        parser.add_argument("--force", action="store_true", help="Run even when DEBUG is off")

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError(
                "seed_demo creates accounts with a known password; refusing to run with "
                "DEBUG off. Pass --force if you really mean it."
            )
        if not pipeline.available():
            raise CommandError("ffmpeg and ffprobe are required to generate the demo media.")

        self.seconds = 6 if options["quick"] else 12
        self.random = random.Random(7)
        users = self._people(options["password"])
        categories = self._categories()

        work = Path(tempfile.mkdtemp(prefix="akon-demo-"))
        try:
            made = []
            for spec in VIDEOS:
                made.append(self._video(work, spec, users, categories))
            for spec in AUDIO:
                made.append(self._audio(work, spec, users, categories))
            for title, owner, style, views in IMAGES:
                made.append(self._image(work, title, users[owner], categories["Stills"], style, views))
            made.append(self._document(work, users["studio@example.com"], categories["Docs"]))
        finally:
            shutil.rmtree(work, ignore_errors=True)

        made = [media for media in made if media is not None]
        self._playlists(users, made)
        self._comments(users, made)

        ready = sum(1 for media in made if media.state == Media.State.READY)
        self.stdout.write(self.style.SUCCESS(
            f"Demo ready: {ready}/{len(made)} items processed, {len(users)} accounts."
        ))
        self.stdout.write("Sign in with any of these (password: %s):" % options["password"])
        for email, name, role in PEOPLE:
            self.stdout.write(f"  {email:<22} {name:<12} {role.label}")

    # ------------------------------------------------------------------ setup

    def _people(self, password):
        users = {}
        for email, name, role in PEOPLE:
            user = User.objects.filter(email=email).first()
            if user is None:
                user = User.objects.create_user(email=email, password=password, display_name=name, role=role)
            else:
                user.display_name, user.role = name, role
                user.set_password(password)
                user.save()
            users[email] = user
        return users

    def _categories(self):
        found = {}
        for name, description in CATEGORIES:
            category, _ = Category.objects.get_or_create(name=name, defaults={"description": description})
            found[name] = category
        return found

    def _publish(self, path: Path, *, title, owner, kind, category, description="", views=0):
        existing = Media.objects.filter(title=title, owner=owner).first()
        if existing is not None:
            self.stdout.write(f"  = {title} (already there)")
            return existing
        media = Media(
            title=title,
            description=description,
            owner=owner,
            kind=kind,
            category=category,
            original_name=path.name,
            size_bytes=path.stat().st_size,
            created_at=timezone.now() - timedelta(hours=self.random.randint(2, 24 * 40)),
        )
        with path.open("rb") as handle:
            media.file.save(path.name, File(handle), save=True)
        outcome = process_media(str(media.pk))
        media.refresh_from_db()
        Media.objects.filter(pk=media.pk).update(views_count=views)
        media.views_count = views
        self.stdout.write(f"  + {title} [{kind}] → {outcome}")
        return media

    # ------------------------------------------------------------------ media

    def _ffmpeg(self, *args):
        result = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
                                capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            raise CommandError(result.stderr.strip()[-400:])

    def _video(self, work, spec, users, categories):
        target = work / (spec["title"].split(" —")[0].lower().replace(" ", "-").replace("'", "")[:40] + ".mp4")
        duration = self.seconds
        fade_out = max(0.5, duration - 1.2)
        self._ffmpeg(
            "-f", "lavfi", "-i", spec["source"],
            "-f", "lavfi", "-i", f"aevalsrc='{spec['audio']}':s=48000:d={duration}",
            "-t", str(duration),
            "-vf", f"fade=t=in:st=0:d=0.8,fade=t=out:st={fade_out}:d=1.2,format=yuv420p",
            "-af", f"afade=t=in:st=0:d=0.8,afade=t=out:st={fade_out}:d=1.2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "160k", "-shortest", str(target),
        )
        media = self._publish(
            target, title=spec["title"], owner=users[spec["owner"]], kind=Media.Kind.VIDEO,
            category=categories[spec["category"]], description=spec["description"], views=spec["views"],
        )
        if media.state == Media.State.READY and spec.get("chapters") and not media.chapters.exists():
            for index, (fraction, name) in enumerate(spec["chapters"]):
                Chapter.objects.create(media=media, title=name, position=index,
                                       start_seconds=round(fraction * media.duration_seconds, 2))
        if spec.get("subtitles") and not media.subtitles.exists():
            scale = media.duration_seconds / 12 if media.duration_seconds else 1
            for language, label, lines in (("en", "English", SUBTITLES_EN), ("pt-br", "Português", SUBTITLES_PT)):
                track = Subtitle.objects.create(media=media, language=language, label=label)
                SubtitleCue.objects.bulk_create(
                    SubtitleCue(subtitle=track, position=i, text=text,
                                start_ms=int(start * scale * 1000), end_ms=int(end * scale * 1000))
                    for i, (start, end, text) in enumerate(lines)
                )
        return media

    def _audio(self, work, spec, users, categories):
        target = work / (spec["title"].split(" (")[0].lower().replace(" ", "-")[:36] + ".mp3")
        duration = self.seconds * 2
        if spec.get("noise"):
            source = [
                "-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude=0.35:d={duration}",
                "-af", f"lowpass=f=2400,tremolo=f=0.35:d=0.7,afade=t=in:d=1,afade=t=out:st={duration - 1.5}:d=1.5",
            ]
        else:
            expr = spec["expr"].replace("DUR", str(duration))
            source = [
                "-f", "lavfi", "-i", f"aevalsrc='{expr}':s=44100:d={duration}",
                "-af", f"afade=t=in:d=1,afade=t=out:st={duration - 1.5}:d=1.5",
            ]
        self._ffmpeg(*source, "-c:a", "libmp3lame", "-b:a", "192k", str(target))
        return self._publish(
            target, title=spec["title"], owner=users[spec["owner"]], kind=Media.Kind.AUDIO,
            category=categories[spec["category"]], description=spec["description"], views=spec["views"],
        )

    def _image(self, work, title, owner, category, style, views):
        from PIL import Image, ImageDraw, ImageFilter

        width, height = 1920, 1080
        image = Image.new("RGB", (width, height), (5, 7, 12))
        draw = ImageDraw.Draw(image, "RGBA")
        rng = random.Random(style)
        if style == "aurora":
            for _ in range(7):
                x, y = rng.randint(0, width), rng.randint(0, height)
                r = rng.randint(220, 520)
                colour = rng.choice([(46, 230, 166), (34, 211, 238), (124, 108, 255)])
                draw.ellipse([x - r, y - r, x + r, y + r], fill=colour + (90,))
            image = image.filter(ImageFilter.GaussianBlur(120))
            draw = ImageDraw.Draw(image, "RGBA")
            for x in range(0, width, 48):
                draw.line([(x, 0), (x, height)], fill=(255, 255, 255, 10))
            for y in range(0, height, 48):
                draw.line([(0, y), (width, y)], fill=(255, 255, 255, 10))
        elif style == "horizon":
            horizon = int(height * 0.52)
            for y in range(horizon):
                t = y / horizon
                draw.line([(0, y), (width, y)], fill=(int(5 + 40 * t), int(7 + 10 * t), int(12 + 90 * t)))
            draw.ellipse([width // 2 - 260, horizon - 330, width // 2 + 260, horizon + 190], fill=(34, 211, 238, 230))
            for i, y in enumerate(range(horizon - 120, horizon, 22)):
                draw.rectangle([width // 2 - 280, y, width // 2 + 280, y + 4 + i], fill=(5, 7, 12, 255))
            draw.rectangle([0, horizon, width, height], fill=(5, 7, 12, 255))
            for i in range(1, 22):
                y = horizon + int((height - horizon) * (i / 21) ** 2.2)
                draw.line([(0, y), (width, y)], fill=(124, 108, 255, 160), width=2)
            for i in range(-24, 25):
                draw.line([(width // 2 + i * 26, horizon), (width // 2 + i * 190, height)], fill=(46, 230, 166, 140), width=2)
        else:
            centre = (width // 2, height // 2)
            for i in range(14):
                r = 60 + i * 34
                colour = [(46, 230, 166), (34, 211, 238), (124, 108, 255)][i % 3]
                draw.ellipse([centre[0] - r * 1.6, centre[1] - r, centre[0] + r * 1.6, centre[1] + r],
                             outline=colour + (200 - i * 10,), width=3)
                angle = rng.random() * math.tau
                px = centre[0] + math.cos(angle) * r * 1.6
                py = centre[1] + math.sin(angle) * r
                draw.ellipse([px - 9, py - 9, px + 9, py + 9], fill=colour + (255,))
        target = work / f"{style}.jpg"
        image.save(target, "JPEG", quality=90)
        return self._publish(target, title=title, owner=owner, kind=Media.Kind.IMAGE,
                             category=category, views=views,
                             description="Generated with Pillow for the demo library.")

    def _document(self, work, owner, category):
        from PIL import Image, ImageDraw, ImageFont

        page = Image.new("RGB", (1240, 1754), (255, 255, 255))
        draw = ImageDraw.Draw(page)
        try:
            heading = ImageFont.load_default(size=64)
            body = ImageFont.load_default(size=30)
        except TypeError:  # Pillow without FreeType support
            heading = body = ImageFont.load_default()
        draw.rectangle([0, 0, 1240, 18], fill=(34, 211, 238))
        draw.text((100, 140), "AkonMedia", font=heading, fill=(5, 7, 12))
        draw.text((100, 230), "Platform overview", font=body, fill=(90, 106, 129))
        lines = [
            "1. Upload in resumable chunks.",
            "2. ffmpeg builds an adaptive HLS ladder.",
            "3. Viewers get the best rendition for their link.",
            "4. Curators keep the library tidy.",
        ]
        for index, line in enumerate(lines):
            draw.text((100, 360 + index * 70), line, font=body, fill=(30, 41, 59))
        for i in range(6):
            draw.rectangle([100 + i * 170, 760, 240 + i * 170, 900], fill=[(46, 230, 166), (34, 211, 238), (124, 108, 255)][i % 3])
        target = work / "platform-overview.pdf"
        page.save(target, "PDF", resolution=150)
        return self._publish(target, title="Platform overview (PDF)", owner=owner,
                             kind=Media.Kind.DOCUMENT, category=category, views=57,
                             description="A one-page handbook, previewed in the browser.")

    # ------------------------------------------------------------- community

    def _playlists(self, users, made):
        by_title = {media.title: media for media in made if media.state == Media.State.READY}
        plans = [
            ("Generative art showcase", "studio@example.com", Media.Visibility.PUBLIC,
             [title for title in by_title if by_title[title].category and by_title[title].category.name == "Generative art"]),
            ("Sound lab sessions", "ana@example.com", Media.Visibility.PUBLIC,
             [title for title in by_title if by_title[title].kind == Media.Kind.AUDIO]),
            ("Broadcast reference kit", "kai@example.com", Media.Visibility.UNLISTED,
             [title for title in by_title if by_title[title].category and by_title[title].category.name == "Broadcast"]),
        ]
        for title, owner, visibility, items in plans:
            playlist, created = Playlist.objects.get_or_create(
                title=title, owner=users[owner],
                defaults={"visibility": visibility, "description": f"Curated by {users[owner].display_name}."},
            )
            if created:
                for position, media_title in enumerate(items):
                    PlaylistItem.objects.get_or_create(playlist=playlist, media=by_title[media_title],
                                                       defaults={"position": position})

    def _comments(self, users, made):
        target = next((media for media in made if media.title.startswith("Aurora flow")), None)
        if target is None or target.comments.exists() or target.state != Media.State.READY:
            return
        first = None
        for email, body, moment in COMMENTS:
            comment = Comment.objects.create(media=target, author=users[email], body=body, timestamp=moment)
            first = first or comment
        Comment.objects.create(media=target, author=users["studio@example.com"], parent=first,
                               body="Thanks! The ladder is planned from the source height, so nothing is upscaled.")
