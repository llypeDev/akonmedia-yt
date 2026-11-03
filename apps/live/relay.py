"""Streaming from a browser: MediaRecorder chunks in, RTMP out.

The control room's script records the camera or the screen with MediaRecorder
and posts one chunk a second (see ``views.cast_chunk``), each saved as
``<n>.chunk`` in the session's folder. This process feeds those chunks, in
order, to ffmpeg, which encodes them the way OBS would (H.264 with a keyframe
every two seconds, AAC) and publishes to the ingest server with the live's
stream key. From there on the live works exactly as with any other encoder.

One relay per browser session, started detached from the web process::

    python -m apps.live.relay spawn --dir <session folder> --target <rtmp url>

It stops when the browser says so (an ``end`` file), when no chunk arrives for
a while (the tab was closed), or when ffmpeg gives up. ``status.json`` in the
folder tells the web process how it is doing; ``ffmpeg.log`` keeps ffmpeg's
own words. Kept free of Django, like ``apps.media.pipeline``.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

POLL_SECONDS = 0.05
#: Waiting for the first chunk after the session was opened.
FIRST_CHUNK_SECONDS = 30
#: No chunk for this long: the browser is gone.
IDLE_SECONDS = 15
#: A chunk lost on the way is skipped after this long rather than stalling the
#: stream for good.
GAP_SECONDS = 4
STATUS = "status.json"


def command(ffmpeg: str, target: str) -> list[str]:
    return [
        ffmpeg, "-hide_banner", "-loglevel", "warning", "-y",
        "-fflags", "+genpts+discardcorrupt",
        "-analyzeduration", "2000000", "-probesize", "2000000",
        "-i", "pipe:0",
        "-map", "0:v:0?", "-map", "0:a:0?",
        # At most 1280 pixels wide, even sizes, a steady 30 frames a second.
        "-vf", "scale='trunc(min(1280,iw)/2)*2':-2,fps=30",
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
        "-profile:v", "main", "-pix_fmt", "yuv420p",
        "-g", "60", "-keyint_min", "60", "-sc_threshold", "0",
        "-b:v", "2500k", "-maxrate", "2800k", "-bufsize", "5000k",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
        "-f", "flv", target,
    ]


def read_status(folder: Path) -> dict:
    try:
        return json.loads((Path(folder) / STATUS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_status(folder: Path, state: str, **extra) -> None:
    folder = Path(folder)
    payload = {"state": state, "at": time.time(), **extra}
    temporary = folder / f".{STATUS}.tmp"
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    temporary.replace(folder / STATUS)


def _sequence(path: Path) -> int:
    try:
        return int(path.stem)
    except ValueError:
        return -1


def _next_waiting(folder: Path, expected: int):
    later = [n for n in (_sequence(p) for p in folder.glob("*.chunk")) if n > expected]
    return min(later) if later else None


def _log_tail(folder: Path) -> str:
    try:
        lines = (folder / "ffmpeg.log").read_text(encoding="utf-8", errors="replace").strip().splitlines()
    except OSError:
        return ""
    return " / ".join(lines[-3:])[-300:]


def run(folder: Path, target: str) -> int:
    folder = Path(folder)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        write_status(folder, "failed", reason="unavailable", error="ffmpeg is not installed")
        return 1
    with (folder / "ffmpeg.log").open("ab") as log:
        process = subprocess.Popen(
            command(ffmpeg, target), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log
        )
        write_status(folder, "running", pid=os.getpid(), fed=0)
        expected = 0
        progress = time.monotonic()
        gap_since = None
        reason = "stopped"
        try:
            while True:
                chunk = folder / f"{expected:08d}.chunk"
                if chunk.exists():
                    data = chunk.read_bytes()
                    chunk.unlink(missing_ok=True)
                    try:
                        process.stdin.write(data)
                        process.stdin.flush()
                    except OSError:
                        reason = "failed"
                        break
                    expected += 1
                    progress = time.monotonic()
                    gap_since = None
                    if expected % 5 == 1:
                        write_status(folder, "running", pid=os.getpid(), fed=expected)
                    continue
                if process.poll() is not None:
                    reason = "failed"
                    break
                waiting = _next_waiting(folder, expected)
                if waiting is None and (folder / "end").exists():
                    break
                if waiting is not None:
                    gap_since = gap_since or time.monotonic()
                    if time.monotonic() - gap_since > GAP_SECONDS:
                        expected, gap_since = waiting, None
                        continue
                limit = FIRST_CHUNK_SECONDS if expected == 0 else IDLE_SECONDS
                if time.monotonic() - progress > limit:
                    reason = "idle"
                    break
                time.sleep(POLL_SECONDS)
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            for leftover in folder.glob("*.chunk"):
                leftover.unlink(missing_ok=True)
            if reason == "failed":
                write_status(folder, "failed", reason=reason, fed=expected, error=_log_tail(folder))
            else:
                write_status(folder, "stopped", reason=reason, fed=expected)
    return 0 if reason != "failed" else 1


def spawn(folder: Path, target: str) -> None:
    """Run the relay detached: the caller returns at once and the relay does
    not become a zombie of the web process when it ends (double fork)."""
    if not hasattr(os, "fork"):  # pragma: no cover - not a Linux host
        subprocess.Popen([sys.executable, "-m", "apps.live.relay", "run", "--dir", str(folder),
                          "--target", target], start_new_session=True)
        return
    child = os.fork()
    if child:
        os.waitpid(child, 0)
        return
    os.setsid()
    if os.fork():
        os._exit(0)
    devnull = os.open(os.devnull, os.O_RDWR)
    for stream in (0, 1, 2):
        os.dup2(devnull, stream)
    try:
        code = run(folder, target)
    except Exception as exc:  # noqa: BLE001 - the web process reads the status
        write_status(folder, "failed", reason="failed", error=f"{type(exc).__name__}: {exc}"[:300])
        code = 1
    os._exit(code)


def start(folder: Path, target: str, cwd: Path) -> None:
    """Start a detached relay from the web process."""
    write_status(folder, "starting")
    subprocess.run(
        [sys.executable, "-m", "apps.live.relay", "spawn", "--dir", str(folder), "--target", target],
        cwd=str(cwd),
        check=True,
        timeout=15,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def cleanup(root: Path, max_age_seconds: int) -> int:
    """Remove finished or abandoned session folders."""
    root = Path(root)
    removed = 0
    if not root.is_dir():
        return 0
    now = time.time()
    for live_dir in root.iterdir():
        if not live_dir.is_dir():
            continue
        for session in live_dir.iterdir():
            if not session.is_dir():
                continue
            status = read_status(session)
            age = now - session.stat().st_mtime
            finished = status.get("state") in ("stopped", "failed")
            if age > max_age_seconds and (finished or age > max_age_seconds * 24):
                shutil.rmtree(session, ignore_errors=True)
                removed += 1
        if not any(live_dir.iterdir()):
            live_dir.rmdir()
    return removed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=["spawn", "run"])
    parser.add_argument("--dir", required=True)
    parser.add_argument("--target", required=True)
    args = parser.parse_args(argv)
    if args.mode == "spawn":
        spawn(Path(args.dir), args.target)
        return 0
    return run(Path(args.dir), args.target)


if __name__ == "__main__":
    sys.exit(main())
