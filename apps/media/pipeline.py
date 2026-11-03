"""Thin wrappers around ffmpeg and ffprobe.

Kept free of Django so it can be exercised directly in tests and from a shell.
Every function raises :class:`PipelineUnavailable` when the binaries are missing
rather than failing with a confusing CalledProcessError.
"""
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


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
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
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
    )


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


#: (height, video bitrate in kbit/s). A source never gets a rendition taller
#: than itself, so nothing is upscaled.
LADDER = ((1080, 5000), (720, 2800), (480, 1400), (360, 800))

MAX_RENDITIONS = 3
AUDIO_BITRATE_KBPS = 128


def plan_renditions(width: int, height: int) -> list[tuple[int, int]]:
    """Pick the rungs that make sense for a source of this size."""
    if not height:
        return [(720, 2800)]

    rungs = [(h, bitrate) for h, bitrate in LADDER if h <= height]
    if not rungs:
        rungs = [(360, 800)]
    return rungs[:MAX_RENDITIONS]


def to_hls(
    source: str | Path,
    out_dir: str | Path,
    width: int = 0,
    height: int = 0,
    segment_seconds: int = 6,
) -> Path:
    """Encode one MP4 into an adaptive-bitrate HLS ladder.

    Returns the path of ``master.m3u8``. A player that understands HLS picks a
    rung from it and switches as bandwidth changes; without a master playlist
    there is nothing to adapt between.
    """
    ffmpeg = _binary("ffmpeg")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rungs = plan_renditions(width, height)
    encoded: list[tuple[int, int]] = []

    for target_height, bitrate in rungs:
        variant_dir = out_dir / f"{target_height}p"
        variant_dir.mkdir(parents=True, exist_ok=True)
        playlist = variant_dir / "index.m3u8"

        result = subprocess.run(
            [
                ffmpeg, "-y", "-i", str(source),
                "-vf", f"scale=-2:{target_height}",
                "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "main",
                "-b:v", f"{bitrate}k",
                "-maxrate", f"{int(bitrate * 1.07)}k",
                "-bufsize", f"{bitrate * 2}k",
                "-c:a", "aac", "-b:a", f"{AUDIO_BITRATE_KBPS}k", "-ac", "2",
                "-hls_time", str(segment_seconds),
                "-hls_playlist_type", "vod",
                "-hls_segment_filename", str(variant_dir / "seg_%05d.ts"),
                str(playlist),
            ],
            capture_output=True,
            text=True,
            timeout=60 * 60,
        )

        if result.returncode != 0 or not playlist.exists():
            raise PipelineError(
                f"{target_height}p encode failed: {result.stderr.strip()[-300:]}"
            )
        encoded.append((target_height, bitrate))

    master = out_dir / "master.m3u8"
    lines = ["#EXTM3U", "#EXT-X-VERSION:3"]
    for target_height, bitrate in encoded:
        variant_width = 0
        if width and height:
            variant_width = round(width * target_height / height)
            variant_width -= variant_width % 2  # encoders need an even width
        if variant_width:
            attributes = f"RESOLUTION={variant_width}x{target_height},"
        else:
            attributes = ""
        lines.append(
            f"#EXT-X-STREAM-INF:{attributes}BANDWIDTH={(bitrate + AUDIO_BITRATE_KBPS) * 1000}"
        )
        lines.append(f"{target_height}p/index.m3u8")

    master.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return master
