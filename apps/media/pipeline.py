"""Thin wrappers around ffmpeg and ffprobe.

Kept free of Django so it can be exercised directly in tests and from a shell.
Every function raises :class:`PipelineUnavailable` when the binaries are missing
rather than failing with a confusing CalledProcessError.
"""
import json
import logging
import math
import re
import shutil
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

logger = logging.getLogger(__name__)


class PipelineError(RuntimeError):
    pass


class PipelineUnavailable(PipelineError):
    pass


@dataclass(frozen=True)
class Probe:
    duration: float
    width: int
    height: int
    codec: str
    #: True when the file carries a real picture stream (not just cover art).
    has_video: bool = False
    has_audio: bool = False
    #: True when the only picture is an embedded cover (ID3 APIC, MP4 covr).
    has_cover: bool = False
    #: Of the picture stream: pixel format, bit rate in bit/s (0 when the
    #: container does not say), rotation in degrees and its index in the file.
    pix_fmt: str = ""
    video_bitrate: int = 0
    rotation: int = 0
    video_index: int = -1


def _binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise PipelineUnavailable(f"{name} is not installed or not on PATH")
    return path


def available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def probe(path: str | Path) -> Probe:
    """Read duration and dimensions without decoding the stream."""
    ffprobe = _binary("ffprobe")
    result = subprocess.run(
        [
            ffprobe, "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise PipelineError(f"ffprobe failed: {result.stderr.strip()[:300]}")

    data = json.loads(result.stdout or "{}")
    streams = data.get("streams", [])
    pictures = [s for s in streams if s.get("codec_type") == "video"]
    covers = [s for s in pictures if (s.get("disposition") or {}).get("attached_pic")]
    moving = [s for s in pictures if s not in covers]
    video = moving[0] if moving else None
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    chosen = video or audio or {}

    duration = 0.0
    raw_duration = data.get("format", {}).get("duration") or chosen.get("duration")
    try:
        duration = float(raw_duration)
    except (TypeError, ValueError):
        duration = 0.0

    return Probe(
        duration=duration,
        width=int(chosen.get("width") or 0),
        height=int(chosen.get("height") or 0),
        codec=chosen.get("codec_name", ""),
        has_video=video is not None,
        has_audio=audio is not None,
        has_cover=bool(covers),
        pix_fmt=(video or {}).get("pix_fmt", ""),
        video_bitrate=_video_bitrate(video, audio, data.get("format", {})),
        rotation=_rotation(video),
        video_index=int(video["index"]) if video and "index" in video else -1,
    )


def _number(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _video_bitrate(video, audio, container) -> int:
    """The picture stream's bit rate; MKV and WebM only give the file's, so
    the sound is taken out of that when it is known."""
    if not video:
        return 0
    own = _number(video.get("bit_rate"))
    if own:
        return own
    total = _number(container.get("bit_rate"))
    return max(0, total - _number((audio or {}).get("bit_rate"))) if total else 0


def _rotation(video) -> int:
    """Degrees a player must turn the picture (phones film sideways)."""
    if not video:
        return 0
    for side in video.get("side_data_list") or []:
        if "rotation" in side:
            return abs(_number(side["rotation"])) % 360
    return abs(_number((video.get("tags") or {}).get("rotate"))) % 360


def extract_poster(source: str | Path, target: str | Path, at: float = 1.0) -> Path:
    """Write a single JPEG frame. Falls back to the first frame for short clips."""
    ffmpeg = _binary("ffmpeg")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)

    for seek in (at, 0):
        result = subprocess.run(
            [
                ffmpeg, "-y", "-ss", f"{seek}", "-i", str(source),
                "-frames:v", "1", "-vf", "scale=1280:-2", "-q:v", "3", str(target),
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode == 0 and target.exists() and target.stat().st_size > 0:
            return target

    raise PipelineError("could not extract a frame from the source")


#: H.264 profile names to profile_idc and the constraint byte x264 writes.
H264_PROFILES = {
    "Constrained Baseline": (0x42, 0xE0),
    "Baseline": (0x42, 0xE0),
    "Main": (0x4D, 0x40),
    "High": (0x64, 0x00),
}
AAC_PROFILES = {"LC": "mp4a.40.2", "HE-AAC": "mp4a.40.5", "HE-AACv2": "mp4a.40.29"}


def variant_codecs(variant_dir: str | Path) -> str:
    """RFC 6381 codec string for one rendition, e.g. ``avc1.4d401f,mp4a.40.2``.

    Read from the first segment rather than assumed from the encoder flags, so
    the master playlist never advertises a level the stream does not have.
    Returns an empty string when anything is unknown: no CODECS attribute is
    better than a wrong one, which some players refuse to play.
    """
    ffprobe = shutil.which("ffprobe")
    segments = sorted(Path(variant_dir).glob("seg_*.ts"))
    if not ffprobe or not segments:
        return ""
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "stream=codec_type,codec_name,profile,level",
         "-of", "json", str(segments[0])],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        return ""
    try:
        streams = json.loads(result.stdout or "{}").get("streams", [])
    except ValueError:
        return ""

    parts = []
    for stream in streams:
        kind, codec = stream.get("codec_type"), stream.get("codec_name")
        if kind == "video":
            profile = H264_PROFILES.get(stream.get("profile", ""))
            level = stream.get("level")
            if codec != "h264" or profile is None or not isinstance(level, int) or level <= 0:
                return ""
            parts.append(f"avc1.{profile[0]:02x}{profile[1]:02x}{level:02x}")
        elif kind == "audio":
            audio = AAC_PROFILES.get(stream.get("profile", ""))
            if codec != "aac" or audio is None:
                return ""
            parts.append(audio)
    return ",".join(parts)


#: (height, video bit rate in kbit/s). A source never gets a rendition taller
#: than itself, so nothing is upscaled. For an encoded rung the bit rate is a
#: ceiling (see X264_CRF), not a target.
LADDER = ((1080, 5000), (720, 2800), (480, 1400), (360, 800))

MAX_RENDITIONS = 3
AUDIO_BITRATE_KBPS = 128

#: x264 settings for the rungs that are encoded. Constant quality with a
#: ceiling: a busy scene gets up to the rung's bit rate, as a fixed bit rate
#: would give it, while slides and a talking head need far less and get
#: smaller files for the same picture.
X264_PRESET = "veryfast"
X264_CRF = 21

#: The tallest rung is the source itself, copied instead of encoded again,
#: when a browser can play it as it is: H.264 in 8-bit 4:2:0, exactly that
#: tall, not turned sideways, with a keyframe at least every few seconds (the
#: segments are cut there) and a bit rate a viewer can stream. Recordings from
#: OBS, Zoom and the portal's own lives usually are. That rung, the costliest
#: to encode, then costs next to nothing and keeps the original's quality.
PASSTHROUGH_CODECS = {"h264"}
PASSTHROUGH_PIX_FMTS = {"yuv420p", "yuvj420p"}
PASSTHROUGH_MAX_KEYFRAME_GAP = 10.0
PASSTHROUGH_BITRATE_HEADROOM = 1.6


@dataclass(frozen=True)
class Rung:
    height: int
    #: kbit/s: the ceiling of an encoded rung, the nominal rate of a copied one.
    bitrate: int
    copy: bool = False


def plan_renditions(width: int, height: int) -> list[tuple[int, int]]:
    """Pick the rungs that make sense for a source of this size."""
    if not height:
        return [(720, 2800)]

    rungs = [(h, bitrate) for h, bitrate in LADDER if h <= height]
    if not rungs:
        rungs = [(360, 800)]
    return rungs[:MAX_RENDITIONS]


def longest_keyframe_gap(packets_csv: str, duration: float = 0.0) -> float:
    """Longest stretch without a keyframe, in seconds, from ``ffprobe -show_entries
    packet=pts_time,flags -of csv=p=0`` output. 0 when there is no keyframe."""
    times = []
    for line in packets_csv.splitlines():
        pts, _, flags = line.strip().partition(",")
        if "K" not in flags:
            continue
        try:
            times.append(float(pts))
        except ValueError:
            continue
    if not times:
        return 0.0
    times.sort()
    gaps = [later - earlier for earlier, later in zip(times, times[1:])]
    if duration > times[-1]:
        gaps.append(duration - times[-1])
    return max(gaps) if gaps else 0.0


def keyframe_gap(source: str | Path, duration: float = 0.0) -> float:
    """Longest stretch without a keyframe in the first picture stream. Read
    from the packet flags, so nothing is decoded; 0 when unknown."""
    ffprobe = _binary("ffprobe")
    result = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", str(source)],
        capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        return 0.0
    return longest_keyframe_gap(result.stdout, duration)


def can_pass_through(info: Probe, height: int, bitrate_kbps: int, gap: float) -> bool:
    """Whether the source can be the rung of this height without encoding."""
    return (
        info.codec in PASSTHROUGH_CODECS
        and info.pix_fmt in PASSTHROUGH_PIX_FMTS
        and info.height == height
        and info.rotation == 0
        and 0 < info.video_bitrate <= bitrate_kbps * 1000 * PASSTHROUGH_BITRATE_HEADROOM
        and 0 < gap <= PASSTHROUGH_MAX_KEYFRAME_GAP
    )


def plan_ladder(info: Probe, gap: float = 0.0) -> list[Rung]:
    """The rungs for this source, tallest first, the tallest copied if it can be."""
    rungs = [Rung(h, bitrate) for h, bitrate in plan_renditions(info.width, info.height)]
    top = rungs[0]
    if info.height and can_pass_through(info, top.height, top.bitrate, gap):
        rungs[0] = Rung(top.height, max(top.bitrate, math.ceil(info.video_bitrate / 1000)), copy=True)
    return rungs


def ladder_command(
    ffmpeg: str,
    source: str | Path,
    out_dir: str | Path,
    rungs: list[Rung],
    segment_seconds: int = 6,
    video_index: int = -1,
) -> list[str]:
    """One ffmpeg run for the whole ladder: the source is read and decoded
    once, and every encoded rung is scaled from that single decode."""
    out_dir = Path(out_dir)
    video = f"0:{video_index}" if video_index >= 0 else "0:v:0"
    command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(source)]

    encoded = [rung for rung in rungs if not rung.copy]
    if encoded:
        # Same-sized pictures for every rung: 8-bit 4:2:0, the only thing the
        # Main profile (and every browser) takes, whatever the source was.
        chains = [f"scale=-2:{rung.height},format=yuv420p" for rung in encoded]
        if len(encoded) == 1:
            graph = f"[{video}]{chains[0]}[v0]"
        else:
            labels = "".join(f"[in{i}]" for i in range(len(encoded)))
            graph = f"[{video}]split={len(encoded)}{labels};" + ";".join(
                f"[in{i}]{chain}[v{i}]" for i, chain in enumerate(chains)
            )
        command += ["-filter_complex", graph]

    index = 0
    for rung in rungs:
        variant_dir = out_dir / f"{rung.height}p"
        if rung.copy:
            command += ["-map", video, "-c:v", "copy"]
        else:
            command += [
                "-map", f"[v{index}]",
                "-c:v", "libx264", "-preset", X264_PRESET, "-profile:v", "main",
                "-crf", str(X264_CRF),
                "-maxrate", f"{int(rung.bitrate * 1.07)}k",
                "-bufsize", f"{rung.bitrate * 2}k",
                # A keyframe at every segment boundary: segments of the same
                # length in every rung, so a player switches cleanly.
                "-force_key_frames", f"expr:gte(t,n_forced*{segment_seconds})",
            ]
            index += 1
        command += [
            "-map", "0:a:0?", "-c:a", "aac", "-b:a", f"{AUDIO_BITRATE_KBPS}k", "-ac", "2",
            "-f", "hls",
            "-hls_time", str(segment_seconds),
            "-hls_playlist_type", "vod",
            "-hls_segment_filename", str(variant_dir / "seg_%05d.ts"),
            str(variant_dir / "index.m3u8"),
        ]
    return command


def measured_bandwidth(variant_dir: str | Path) -> tuple[int, int]:
    """(peak, average) bit rate of a rendition in bit/s, from its segments.

    The master playlist must advertise what a player will really pull: with
    constant-quality encoding and copied rungs the nominal rate is only an
    upper bound. (0, 0) when the playlist cannot be read.
    """
    variant_dir = Path(variant_dir)
    try:
        lines = (variant_dir / "index.m3u8").read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0, 0
    peak = total_bits = total_seconds = 0.0
    duration = None
    for line in lines:
        line = line.strip()
        match = re.match(r"#EXTINF:([0-9.]+)", line)
        if match:
            duration = float(match.group(1))
        elif line and not line.startswith("#") and duration:
            try:
                bits = (variant_dir / line).stat().st_size * 8
            except OSError:
                return 0, 0
            peak = max(peak, bits / duration)
            total_bits += bits
            total_seconds += duration
            duration = None
    if not total_seconds:
        return 0, 0
    return math.ceil(peak), math.ceil(total_bits / total_seconds)


def to_hls(
    source: str | Path,
    out_dir: str | Path,
    width: int = 0,
    height: int = 0,
    segment_seconds: int = 6,
    timeout: int = 60 * 60,
    info: Probe | None = None,
) -> Path:
    """Package one video as an adaptive-bitrate HLS ladder.

    Returns the path of ``master.m3u8``. A player that understands HLS picks a
    rung from it and switches as bandwidth changes; without a master playlist
    there is nothing to adapt between. ``timeout`` is per encoded rung, as
    when each rung had a run of its own. ``info`` is the source's probe (read
    here when not given); ``width`` and ``height`` win over it when set.

    ``out_dir`` holds nothing but this ladder and starts empty: reprocessing a
    video leaves no segment of the previous ladder behind.
    """
    ffmpeg = _binary("ffmpeg")
    out_dir = Path(out_dir)
    if out_dir.is_dir():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if info is None:
        info = probe(source)
    if width and height:
        info = replace(info, width=width, height=height)

    top = plan_renditions(info.width, info.height)[0]
    worth_checking = info.codec in PASSTHROUGH_CODECS and info.height == top[0]
    gap = keyframe_gap(source, info.duration) if worth_checking else 0.0
    rungs = plan_ladder(info, gap)
    for rung in rungs:
        (out_dir / f"{rung.height}p").mkdir(parents=True, exist_ok=True)
    logger.info(
        "hls ladder for %s: %s", Path(source).name,
        ", ".join(f"{r.height}p {'copied' if r.copy else 'encoded'}" for r in rungs),
    )

    command = ladder_command(ffmpeg, source, out_dir, rungs, segment_seconds, info.video_index)
    result = subprocess.run(
        command, capture_output=True, text=True,
        timeout=timeout * max(1, sum(1 for rung in rungs if not rung.copy)),
    )
    missing = [r.height for r in rungs if not (out_dir / f"{r.height}p" / "index.m3u8").exists()]
    if result.returncode != 0 or missing:
        raise PipelineError(f"hls encode failed: {result.stderr.strip()[-300:]}")

    master = out_dir / "master.m3u8"
    lines = ["#EXTM3U", "#EXT-X-VERSION:3"]
    for rung in rungs:
        variant_width = 0
        if info.width and info.height:
            variant_width = round(info.width * rung.height / info.height)
            variant_width -= variant_width % 2  # encoders need an even width
        attributes = f"RESOLUTION={variant_width}x{rung.height}," if variant_width else ""
        codecs = variant_codecs(out_dir / f"{rung.height}p")
        if codecs:
            attributes += f'CODECS="{codecs}",'
        peak, average = measured_bandwidth(out_dir / f"{rung.height}p")
        if peak:
            attributes += f"AVERAGE-BANDWIDTH={average},"
        bandwidth = peak or (rung.bitrate + AUDIO_BITRATE_KBPS) * 1000
        lines.append(f"#EXT-X-STREAM-INF:{attributes}BANDWIDTH={bandwidth}")
        lines.append(f"{rung.height}p/index.m3u8")

    master.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return master


# --------------------------------------------------------------------------
# posters for things that are not a video frame
# --------------------------------------------------------------------------

#: Aurora palette shared with the brand: mint, cyan, violet.
WAVE_COLOURS = ((46, 230, 166), (34, 211, 238), (124, 108, 255))
POSTER_SIZE = (1280, 720)
POSTER_BACKGROUND = (5, 7, 12)


def extract_cover(source: str | Path, target: str | Path) -> Path:
    """Write the cover art embedded in an audio file (ID3 APIC, MP4 covr)."""
    ffmpeg = _binary("ffmpeg")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            ffmpeg, "-y", "-i", str(source), "-map", "0:v:0", "-frames:v", "1",
            "-vf", "scale='min(1280,iw)':-2", "-q:v", "3", str(target),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0 or not target.exists() or target.stat().st_size == 0:
        raise PipelineError("no usable cover art in the file")
    return target


def _gradient(size: tuple[int, int]):
    """A left-to-right image running through the brand colours."""
    from PIL import Image

    width, height = size
    strip = Image.new("RGB", (width, 1))
    pixels = strip.load()
    stops = len(WAVE_COLOURS) - 1
    for x in range(width):
        position = x / max(1, width - 1) * stops
        index = min(int(position), stops - 1)
        blend = position - index
        start, end = WAVE_COLOURS[index], WAVE_COLOURS[index + 1]
        pixels[x, 0] = tuple(round(a + (b - a) * blend) for a, b in zip(start, end))
    return strip.resize((width, height))


def waveform_poster(source: str | Path, target: str | Path) -> Path:
    """Draw the audio as a waveform on a dark, faintly gridded 16:9 card.

    ffmpeg renders the amplitude envelope; Pillow colours it with the brand
    gradient and adds a soft reflection, so audio sits in the library next to
    video without looking like a missing thumbnail.
    """
    from PIL import Image, ImageDraw, ImageFilter

    ffmpeg = _binary("ffmpeg")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    width, height = POSTER_SIZE
    wave_height = 300
    raw = target.with_name(f".{target.stem}-wave.png")

    result = subprocess.run(
        [
            ffmpeg, "-y", "-i", str(source),
            "-filter_complex",
            f"aformat=channel_layouts=mono,showwavespic=s={width - 160}x{wave_height}"
            ":colors=white:scale=sqrt:draw=full",
            "-frames:v", "1", str(raw),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    try:
        if result.returncode != 0 or not raw.exists():
            raise PipelineError(f"waveform failed: {result.stderr.strip()[-300:]}")

        with Image.open(raw) as rendered:
            mask = rendered.convert("L")

        card = Image.new("RGB", POSTER_SIZE, POSTER_BACKGROUND)
        grid = ImageDraw.Draw(card)
        for x in range(0, width, 40):
            grid.line([(x, 0), (x, height)], fill=(12, 17, 27))
        for y in range(0, height, 40):
            grid.line([(0, y), (width, y)], fill=(12, 17, 27))

        top = (height - wave_height) // 2
        colour = _gradient(mask.size)

        glow = Image.new("L", POSTER_SIZE, 0)
        glow.paste(mask, (80, top))
        glow = glow.filter(ImageFilter.GaussianBlur(18)).point(lambda v: int(v * 0.55))
        card.paste(_gradient(POSTER_SIZE), (0, 0), glow)
        card.paste(colour, (80, top), mask)

        reflection = mask.transpose(Image.Transpose.FLIP_TOP_BOTTOM).point(lambda v: int(v * 0.18))
        lower = top + wave_height + 6
        visible = max(0, min(wave_height, height - lower))
        if visible:
            card.paste(colour.crop((0, 0, mask.width, visible)), (80, lower),
                       reflection.crop((0, 0, mask.width, visible)))

        card.save(target, "JPEG", quality=88, optimize=True, progressive=True)
    finally:
        raw.unlink(missing_ok=True)
    return target


def image_poster(source: str | Path, target: str | Path, longest: int = 1280) -> tuple[int, int]:
    """Write a JPEG thumbnail of an image and return the original size.

    EXIF orientation is applied first, so a portrait phone photo is not shown
    on its side. Transparency is flattened onto the page background.
    """
    from PIL import Image, ImageOps

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened)
            size = image.size
            image.thumbnail((longest, longest))
            if image.mode in ("RGBA", "LA", "P"):
                image = image.convert("RGBA")
                flat = Image.new("RGB", image.size, POSTER_BACKGROUND)
                flat.paste(image, mask=image.getchannel("A"))
                image = flat
            image.convert("RGB").save(target, "JPEG", quality=86, optimize=True, progressive=True)
    except Exception as exc:  # noqa: BLE001 - Pillow raises many unrelated types
        raise PipelineError(f"could not read the image: {exc}") from exc
    return size


def pdf_available() -> bool:
    return bool(shutil.which("pdftoppm"))


def pdf_poster(source: str | Path, target: str | Path) -> Path:
    """Render the first page of a PDF (needs poppler's pdftoppm)."""
    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        raise PipelineUnavailable("pdftoppm is not installed")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    prefix = target.with_suffix("")
    result = subprocess.run(
        [pdftoppm, "-jpeg", "-jpegopt", "quality=85", "-f", "1", "-l", "1",
         "-singlefile", "-scale-to", "1280", str(source), str(prefix)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    produced = prefix.with_suffix(".jpg")
    if result.returncode != 0 or not produced.exists():
        raise PipelineError(f"could not render the first page: {result.stderr.strip()[-200:]}")
    if produced != target:
        produced.replace(target)
    return target


# --------------------------------------------------------------------------
# seek previews
# --------------------------------------------------------------------------

STORYBOARD_TILE = (160, 90)
STORYBOARD_COLUMNS = 10
STORYBOARD_MAX_TILES = 100
#: From tiles this far apart on, only keyframes are decoded: a long class
#: then costs a fraction of a full decode, and a tile a few seconds off is
#: still the right picture for a seek preview.
STORYBOARD_KEYFRAMES_FROM = 4.0


def plan_storyboard(duration: float) -> tuple[float, int]:
    """Pick (interval in seconds, number of tiles) for a clip of this length."""
    if duration <= 0:
        return 0.0, 0
    interval = max(1.0, duration / STORYBOARD_MAX_TILES)
    interval = round(interval, 2)
    count = max(1, min(STORYBOARD_MAX_TILES, int(duration // interval) + (1 if duration % interval else 0)))
    return interval, count


def storyboard(source: str | Path, target: str | Path, duration: float) -> dict:
    """Write one sprite sheet of evenly spaced thumbnails for the seek bar.

    Returns the metadata a player needs to find a tile: interval, grid shape,
    tile size and count. One request covers the whole timeline.
    """
    ffmpeg = _binary("ffmpeg")
    interval, count = plan_storyboard(duration)
    if not count:
        raise PipelineError("cannot plan a storyboard without a duration")

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    tile_w, tile_h = STORYBOARD_TILE
    columns = min(STORYBOARD_COLUMNS, count)
    rows = (count + columns - 1) // columns

    keyframes_only = ["-skip_frame", "nokey"] if interval >= STORYBOARD_KEYFRAMES_FROM else []
    result = subprocess.run(
        [
            ffmpeg, "-y", *keyframes_only, "-i", str(source), "-an",
            "-vf",
            f"fps=1/{interval},scale={tile_w}:{tile_h}:force_original_aspect_ratio=decrease,"
            f"pad={tile_w}:{tile_h}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"tile={columns}x{rows}",
            "-frames:v", "1", "-q:v", "5", str(target),
        ],
        capture_output=True,
        text=True,
        timeout=60 * 30,
    )
    if result.returncode != 0 or not target.exists():
        raise PipelineError(f"storyboard failed: {result.stderr.strip()[-300:]}")

    return {
        "interval": interval,
        "cols": columns,
        "rows": rows,
        "width": tile_w,
        "height": tile_h,
        "count": count,
    }
