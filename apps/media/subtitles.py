"""Subtitle parsing and serialisation.

Supports WebVTT and SubRip, which share the same cue body once the timestamp
line is understood. The parser is deliberately forgiving: real files in the wild
carry BOMs, Windows line endings and stray blank cues.
"""
import re

TIMESTAMP = re.compile(
    r"(?P<start>\d{1,2}:\d{2}:\d{2}[.,]\d{1,3})\s*-->\s*(?P<end>\d{1,2}:\d{2}:\d{2}[.,]\d{1,3})"
)


class SubtitleFormatError(ValueError):
    pass


def parse_timestamp(raw: str) -> int:
    """'00:01:02,500' or '00:01:02.500' to milliseconds."""
    cleaned = raw.strip().replace(",", ".")
    parts = cleaned.split(":")
    if len(parts) != 3:
        raise SubtitleFormatError(f"unrecognised timestamp: {raw!r}")
    hours, minutes, seconds = parts
    whole, _, fraction = seconds.partition(".")
    fraction = (fraction + "000")[:3]
    return (
        int(hours) * 3_600_000
        + int(minutes) * 60_000
        + int(whole) * 1000
        + int(fraction)
    )


def format_timestamp(milliseconds: int, separator: str = ".") -> str:
    milliseconds = max(0, int(milliseconds))
    hours, rest = divmod(milliseconds, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{millis:03d}"


def parse(text: str) -> list[dict]:
    """Return cues as ``{"start_ms", "end_ms", "text"}`` in file order."""
    if not text:
        return []

    normalised = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    lines = normalised.split("\n")

    cues: list[dict] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        match = TIMESTAMP.search(line)
        if not match:
            index += 1
            continue

        start = parse_timestamp(match.group("start"))
        end = parse_timestamp(match.group("end"))

        body: list[str] = []
        index += 1
        while index < len(lines) and lines[index].strip() != "":
            body.append(lines[index].rstrip())
            index += 1

        cues.append({"start_ms": start, "end_ms": end, "text": "\n".join(body).strip()})

    return cues


def to_vtt(cues) -> str:
    """Serialise cues as WebVTT, ready for a <track> element."""
    output = ["WEBVTT", ""]
    for cue in cues:
        start = format_timestamp(cue["start_ms"])
        end = format_timestamp(cue["end_ms"])
        output.append(f"{start} --> {end}")
        output.append(cue["text"] or "")
        output.append("")
    return "\n".join(output)


def to_srt(cues) -> str:
    output = []
    for number, cue in enumerate(cues, start=1):
        output.append(str(number))
        output.append(
            f"{format_timestamp(cue['start_ms'], ',')} --> {format_timestamp(cue['end_ms'], ',')}"
        )
        output.append(cue["text"] or "")
        output.append("")
    return "\n".join(output)
