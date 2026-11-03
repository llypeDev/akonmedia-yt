"""Build the QQplay brand kit: logo, app icons, web fonts and icon sprite.

Every file this script writes is generated and committed; none is hand-edited.

    static/brand/                    logo mark, wordmarks, favicon, PNG app
                                     icons, Open Graph image, web manifest
    static/vendor/fonts/             woff2 subsets of Inter, Space Grotesk and
                                     JetBrains Mono, with their licences
    static/css/fonts.css             @font-face rules for those subsets
    static/icons/sprite.svg          Lucide icons as <symbol> elements
    static/vendor/LUCIDE-LICENSE.txt licence for the sprite

The wordmark is set in Space Grotesk and converted to outlines, so it renders
identically everywhere and never depends on a font being installed. Fonts (SIL
Open Font License) are downloaded to ``.build/fonts`` on first run and checked
against known hashes; Lucide (ISC) is fetched at a pinned version and cached in
``.build``.

    pip install -r requirements-dev.txt        # cairosvg also needs libcairo2
    python tools/build_logo.py                 # everything
    python tools/build_logo.py brand icons     # only some parts

The mark is a "stream ladder": a play button cut into three rungs, the
adaptive-bitrate ladder every video is encoded into.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import io
import json
import math
import os
import pathlib
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUILD = ROOT / ".build"
FONT_CACHE = BUILD / "fonts"
BRAND = ROOT / "static" / "brand"
VENDOR = ROOT / "static" / "vendor"
WEBFONTS = VENDOR / "fonts"
CSS_DIR = ROOT / "static" / "css"
ICONS = ROOT / "static" / "icons"

GOOGLE_FONTS = "https://raw.githubusercontent.com/google/fonts/main/ofl"

#: Source fonts. The hash pins the exact bytes the committed assets were built
#: from; when upstream publishes a new release the build stops and says so.
FONTS = {
    "inter": {
        "url": f"{GOOGLE_FONTS}/inter/Inter%5Bopsz%2Cwght%5D.ttf",
        "file": "Inter[opsz,wght].ttf",
        "sha256": "29160a80ff49ddcab2c97711247e08b1fab27a484a329ce8b813d820dc559031",
        "license_url": f"{GOOGLE_FONTS}/inter/OFL.txt",
        "family": "Inter",
        "version": "4.001",
        "home": "https://github.com/rsms/inter",
    },
    "space-grotesk": {
        "url": f"{GOOGLE_FONTS}/spacegrotesk/SpaceGrotesk%5Bwght%5D.ttf",
        "file": "SpaceGrotesk[wght].ttf",
        "sha256": "acad6de1fc93436f5c0f1f4137751ef04f1aea3063e7036535970ffcfbd79f72",
        "license_url": f"{GOOGLE_FONTS}/spacegrotesk/OFL.txt",
        "family": "Space Grotesk",
        "version": "2.000",
        "home": "https://github.com/floriankarsten/space-grotesk",
    },
    "jetbrains-mono": {
        "url": f"{GOOGLE_FONTS}/jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf",
        "file": "JetBrainsMono[wght].ttf",
        "sha256": "48715a42ec242c21e9f02692891e147d022299a52e48d5e413e1a942193ffeda",
        "license_url": f"{GOOGLE_FONTS}/jetbrainsmono/OFL.txt",
        "family": "JetBrains Mono",
        "version": "2.211",
        "home": "https://github.com/JetBrains/JetBrainsMono",
    },
}

LUCIDE_VERSION = "1.59.0"
LUCIDE_CDN = f"https://cdn.jsdelivr.net/npm/lucide-static@{LUCIDE_VERSION}"

# --------------------------------------------------------------------------
# palette
# --------------------------------------------------------------------------

#: The name on the wordmark, in the SVG titles, the web manifest and the social
#: card. The wordmark sets the first BRAND_SPLIT letters in the text colour and
#: the rest in the aurora gradient: "QQ" + "play".
BRAND_NAME = "QQplay"
BRAND_SPLIT = 2

INK = "#050A12"
NIGHT = "#05070C"
AURORA = ("#2EE6A6", "#22D3EE", "#7C6CFF")       # mint, cyan, violet
AURORA_DEEP = ("#0EA372", "#0891B2", "#5B4CF0")  # same hues, readable on white
TEXT_ON_DARK = "#E8EEF6"
TEXT_ON_LIGHT = "#0A0F1A"
MUTED = "#8B98A9"

# --------------------------------------------------------------------------
# geometry of the mark
# --------------------------------------------------------------------------

TILE_RADIUS = 0.225      # corner radius of the squircle, as a fraction of its size
TILE_SMOOTHING = 0.6     # continuous-curvature corners, like modern app icons

#: The play triangle on a 64-unit grid. It sits right of centre on purpose:
#: a triangle's visual weight is near its back edge. ``bands`` are the rungs,
#: top to bottom; the space between them is the cut.
GLYPH = {
    "x0": 20.0, "x1": 52.0, "y0": 12.0, "y1": 52.0,
    "corner": 3.2,   # fillet on the two back corners
    "tip": 2.2,      # fillet on the point
    "cut": 0.35,     # a hint of softness where a rung is cut
    "bands": ((12.0, 24.5), (27.5, 36.5), (39.5, 52.0)),
}
#: Favicon: the same glyph with every cut on a whole pixel at 16 and 32 px,
#: so the rungs stay crisp in a browser tab.
GLYPH_SMALL = {**GLYPH, "tip": 2.0, "cut": 0.0, "bands": ((12.0, 24.0), (28.0, 36.0), (40.0, 52.0))}


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def num(value: float) -> str:
    """Compact, stable number formatting for path data."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


def write_text(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"  {path.relative_to(ROOT)}  {path.stat().st_size:,} B")


def write_bytes(path: pathlib.Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    print(f"  {path.relative_to(ROOT)}  {path.stat().st_size:,} B")


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "qqplay-build"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def gradient(element_id: str, colors, *, x1="0", y1="0", x2="1", y2="1", user_space=False) -> str:
    units = ' gradientUnits="userSpaceOnUse"' if user_space else ""
    stops = "".join(
        f'<stop offset="{num(index / (len(colors) - 1))}" stop-color="{color}"/>'
        for index, color in enumerate(colors)
    )
    return (
        f'<linearGradient id="{element_id}" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}"{units}>'
        f"{stops}</linearGradient>"
    )


# --------------------------------------------------------------------------
# shapes
# --------------------------------------------------------------------------

def squircle(x: float, y: float, w: float, h: float, radius: float,
             smoothing: float = TILE_SMOOTHING) -> str:
    """Rounded rectangle with smoothed (continuous-curvature) corners.

    Each corner is a short circular arc flanked by two cubic curves that ease
    into the straight edges, instead of a plain arc that meets them abruptly.
    """
    budget = min(w, h) / 2
    p = min((1 + smoothing) * radius, budget)
    smoothing = min(smoothing, p / radius - 1) if radius else 0
    arc_measure = 90 * (1 - smoothing)
    arc_length = math.sin(math.radians(arc_measure / 2)) * radius * math.sqrt(2)
    alpha = (90 - arc_measure) / 2
    p3_to_p4 = radius * math.tan(math.radians(alpha / 2))
    beta = 45 * smoothing
    c = p3_to_p4 * math.cos(math.radians(beta))
    d = c * math.tan(math.radians(beta))
    b = (p - arc_length - c - d) / 3
    a = 2 * b

    def pt(corner, d_in, d_out, back, forward):
        return (
            corner[0] - d_in[0] * back + d_out[0] * forward,
            corner[1] - d_in[1] * back + d_out[1] * forward,
        )

    corners = [
        ((x + w, y), (1, 0), (0, 1)),      # top right
        ((x + w, y + h), (0, 1), (-1, 0)),  # bottom right
        ((x, y + h), (-1, 0), (0, -1)),     # bottom left
        ((x, y), (0, -1), (1, 0)),          # top left
    ]

    def fmt(point):
        return f"{num(point[0])} {num(point[1])}"

    start = pt(*corners[-1], 0, p)
    parts = [f"M{fmt(start)}"]
    for corner, d_in, d_out in corners:
        parts.append(f"L{fmt(pt(corner, d_in, d_out, p, 0))}")
        parts.append(
            "C" + " ".join(
                fmt(pt(corner, d_in, d_out, back, fwd))
                for back, fwd in ((p - a, 0), (p - a - b, 0), (p - a - b - c, d))
            )
        )
        parts.append(
            f"A{num(radius)} {num(radius)} 0 0 1 {fmt(pt(corner, d_in, d_out, d, p - a - b - c))}"
        )
        parts.append(
            "C" + " ".join(
                fmt(pt(corner, d_in, d_out, back, fwd))
                for back, fwd in ((0, p - a - b), (0, p - a), (0, p))
            )
        )
    parts.append("Z")
    return "".join(parts)


def rounded_polygon(points: list[tuple[float, float, float]]) -> str:
    """Closed path through ``(x, y, radius)`` vertices with filleted corners."""
    count = len(points)
    vectors = []
    for index in range(count):
        px, py, _ = points[index - 1]
        cx, cy, radius = points[index]
        nx, ny, _ = points[(index + 1) % count]
        to_prev = (px - cx, py - cy)
        to_next = (nx - cx, ny - cy)
        len_prev = math.hypot(*to_prev)
        len_next = math.hypot(*to_next)
        u_prev = (to_prev[0] / len_prev, to_prev[1] / len_prev)
        u_next = (to_next[0] / len_next, to_next[1] / len_next)
        cos_angle = max(-1.0, min(1.0, u_prev[0] * u_next[0] + u_prev[1] * u_next[1]))
        angle = math.acos(cos_angle)
        tangent = radius / math.tan(angle / 2) if radius and angle > 1e-6 else 0.0
        # direction of travel decides which way the arc turns
        cross = (cx - px) * (ny - cy) - (cy - py) * (nx - cx)
        vectors.append({
            "point": (cx, cy), "u_prev": u_prev, "u_next": u_next,
            "angle": angle, "tangent": tangent, "sweep": 1 if cross > 0 else 0,
        })

    # Neighbouring fillets must not overlap on a shared edge.
    for index in range(count):
        here, after = vectors[index], vectors[(index + 1) % count]
        edge = math.dist(here["point"], after["point"])
        demand = here["tangent"] + after["tangent"]
        if demand > edge:
            factor = edge / demand
            here["tangent"] *= factor
            after["tangent"] *= factor

    parts = []
    for index, vertex in enumerate(vectors):
        cx, cy = vertex["point"]
        tangent = vertex["tangent"]
        if tangent <= 1e-6:
            parts.append(f"{'M' if index == 0 else 'L'}{num(cx)} {num(cy)}")
            continue
        radius = tangent * math.tan(vertex["angle"] / 2)
        enter = (cx + vertex["u_prev"][0] * tangent, cy + vertex["u_prev"][1] * tangent)
        leave = (cx + vertex["u_next"][0] * tangent, cy + vertex["u_next"][1] * tangent)
        parts.append(f"{'M' if index == 0 else 'L'}{num(enter[0])} {num(enter[1])}")
        parts.append(
            f"A{num(radius)} {num(radius)} 0 0 {vertex['sweep']} {num(leave[0])} {num(leave[1])}"
        )
    parts.append("Z")
    return "".join(parts)


def _clip(polygon, keep, intersect):
    """One Sutherland-Hodgman pass. Vertices are (x, y, radius)."""
    output = []
    for index, current in enumerate(polygon):
        previous = polygon[index - 1]
        if keep(current):
            if not keep(previous):
                output.append(intersect(previous, current))
            output.append(current)
        elif keep(previous):
            output.append(intersect(previous, current))
    return output


EPSILON = 1e-6


def _band(polygon, top: float, bottom: float, cut_radius: float):
    def at_y(level):
        def intersect(a, b):
            t = (level - a[1]) / (b[1] - a[1])
            return (a[0] + (b[0] - a[0]) * t, level, cut_radius)
        return intersect

    # The tolerance keeps an original corner that sits exactly on a band edge,
    # instead of replacing it with two coincident cut points (a sharp corner).
    clipped = _clip(polygon, lambda p: p[1] >= top - EPSILON, at_y(top))
    return _clip(clipped, lambda p: p[1] <= bottom + EPSILON, at_y(bottom))


def play_glyph(size: float, spec: dict = GLYPH, *, scale: float = 1.0,
               offset=(0.0, 0.0), solid: bool = False) -> list[str]:
    """The glyph for a ``size`` tile: one path per rung (or one if ``solid``).

    ``scale`` shrinks or grows the glyph around the tile centre.
    """
    unit = size / 64

    def place(x, y):
        return (
            offset[0] + (32 + (x - 32) * scale) * unit,
            offset[1] + (32 + (y - 32) * scale) * unit,
        )

    k = scale * unit
    x0, y0 = place(spec["x0"], spec["y0"])
    x1, _ = place(spec["x1"], spec["y0"])
    _, y1 = place(spec["x0"], spec["y1"])
    corner, tip = spec["corner"] * k, spec["tip"] * k
    triangle = [(x0, y0, corner), (x1, (y0 + y1) / 2, tip), (x0, y1, corner)]
    if solid:
        return [rounded_polygon(triangle)]

    pieces = []
    for top, bottom in spec["bands"]:
        _, band_top = place(0, top)
        _, band_bottom = place(0, bottom)
        pieces.append(rounded_polygon(_band(triangle, band_top, band_bottom, spec["cut"] * k)))
    return pieces


# --------------------------------------------------------------------------
# type as outlines
# --------------------------------------------------------------------------

_font_cache: dict = {}


def font_file(key: str) -> pathlib.Path:
    spec = FONTS[key]
    path = FONT_CACHE / spec["file"]
    if not path.exists():
        FONT_CACHE.mkdir(parents=True, exist_ok=True)
        print(f"downloading {spec['url']}")
        path.write_bytes(fetch(spec["url"]))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != spec["sha256"] and not ARGS.allow_font_update:
        raise SystemExit(
            f"{path.name} changed upstream (sha256 {digest}).\n"
            "Review the new release, then update FONTS in tools/build_logo.py "
            "or re-run with --allow-font-update."
        )
    return path


def static_font(key: str, weight: float):
    """A static instance of a variable font, cached per weight."""
    cache_key = (key, weight)
    if cache_key not in _font_cache:
        from fontTools.ttLib import TTFont
        from fontTools.varLib.instancer import instantiateVariableFont

        font = TTFont(font_file(key))
        location = {"wght": weight}
        if "opsz" in [axis.axisTag for axis in font["fvar"].axes]:
            location["opsz"] = 32
        _font_cache[cache_key] = instantiateVariableFont(font, location)
    return _font_cache[cache_key]


def shape(key: str, text: str, weight: float):
    """Glyph ids, clusters and advances in font units, kerning applied."""
    import uharfbuzz as hb

    blob = hb.Blob.from_file_path(str(font_file(key)))
    face = hb.Face(blob)
    font = hb.Font(face)
    font.set_variations({"wght": weight})
    buffer = hb.Buffer()
    buffer.add_str(text)
    buffer.guess_segment_properties()
    hb.shape(font, buffer, {"kern": True, "liga": True})
    glyphs = [
        (info.codepoint, info.cluster, pos.x_advance, pos.x_offset, pos.y_offset)
        for info, pos in zip(buffer.glyph_infos, buffer.glyph_positions)
    ]
    return glyphs, face.upem


def set_type(key: str, text: str, *, size: float, weight: float, x: float,
             baseline: float, tracking: float = 0.0, splits: tuple[int, ...] = ()):
    """Outline ``text``. Returns one (path, start_x, end_x) run per split segment.

    ``splits`` are character offsets where a new run (a new fill) starts.
    ``tracking`` is extra space between letters, in em.
    """
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen

    glyphs, upem = shape(key, text, weight)
    font = static_font(key, weight)
    glyph_set = font.getGlyphSet()
    scale = size / upem
    boundaries = sorted(set(splits))

    runs: list[dict] = []
    cursor = 0.0
    for index, (gid, cluster, advance, x_off, y_off) in enumerate(glyphs):
        segment = sum(1 for b in boundaries if cluster >= b)
        while len(runs) <= segment:
            runs.append({"pen": SVGPathPen(glyph_set, ntos=num), "start": None, "end": x})
        run = runs[segment]
        origin_x = x + (cursor + x_off) * scale
        if run["start"] is None:
            run["start"] = origin_x
        name = font.getGlyphName(gid)
        glyph_set[name].draw(
            TransformPen(run["pen"], (scale, 0, 0, -scale, origin_x, baseline - y_off * scale))
        )
        cursor += advance + (tracking * upem if index < len(glyphs) - 1 else 0)
        run["end"] = x + cursor * scale
    return [(run["pen"].getCommands(), run["start"], run["end"]) for run in runs]


# --------------------------------------------------------------------------
# brand assets
# --------------------------------------------------------------------------

WORDMARK_HEIGHT = 48.0
WORDMARK_GAP = 13.0
WORDMARK_SIZE = 29.0
WORDMARK_TRACKING = -0.018


def mark_layers(prefix: str, size: float, *, x: float = 0, y: float = 0,
                shine: bool = True, spec: dict = GLYPH) -> tuple[str, str]:
    """(defs, body) for the coloured tile with the glyph on top."""
    tile = squircle(x, y, size, size, size * TILE_RADIUS)
    defs = [gradient(f"{prefix}-fill", AURORA)]
    body = [f'<path d="{tile}" fill="url(#{prefix}-fill)"/>']
    if shine:
        # a soft highlight from the top left gives the tile some depth
        defs.append(
            f'<radialGradient id="{prefix}-shine" cx="0.22" cy="0.08" r="0.95">'
            '<stop offset="0" stop-color="#FFFFFF" stop-opacity="0.32"/>'
            '<stop offset="0.6" stop-color="#FFFFFF" stop-opacity="0"/>'
            "</radialGradient>"
        )
        body.append(f'<path d="{tile}" fill="url(#{prefix}-shine)"/>')
    pieces = play_glyph(size, spec, offset=(x, y))
    body.append(f'<path d="{"".join(pieces)}" fill="{INK}"/>')
    return "".join(defs), "".join(body)


def svg_document(width: float, height: float, defs: str, body: str, *,
                 label: str | None = BRAND_NAME, extra: str = "") -> str:
    accessible = (
        f' role="img" aria-label="{label}"' if label else ' aria-hidden="true"'
    )
    title = f"  <title>{label}</title>\n" if label else ""
    defs_block = f"  <defs>{defs}</defs>\n" if defs else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {num(width)} {num(height)}" '
        f'width="{num(width)}" height="{num(height)}"{accessible}{extra}>\n'
        f"{title}{defs_block}  {body}\n</svg>\n"
    )


def wordmark_parts(variant: str) -> tuple[float, str, str]:
    """(width, defs, body) of the wordmark at a height of 48 user units."""
    on_dark = variant == "dark"
    prefix = f"am-w{variant[0]}"
    defs, body = mark_layers(prefix, WORDMARK_HEIGHT)

    # Centre the capitals on the tile: Space Grotesk caps are 0.7 em tall.
    baseline = WORDMARK_HEIGHT / 2 + WORDMARK_SIZE * 0.7 / 2
    text_x = WORDMARK_HEIGHT + WORDMARK_GAP
    (head, _, _), (tail, tail_start, tail_end) = set_type(
        "space-grotesk", BRAND_NAME, size=WORDMARK_SIZE, weight=700, x=text_x,
        baseline=baseline, tracking=WORDMARK_TRACKING, splits=(BRAND_SPLIT,),
    )
    colors = AURORA if on_dark else AURORA_DEEP
    defs += gradient(
        f"{prefix}-type", colors, user_space=True,
        x1=num(tail_start), y1="0", x2=num(tail_end), y2="0",
    )
    body += (
        f'<path d="{head}" fill="{TEXT_ON_DARK if on_dark else TEXT_ON_LIGHT}"/>'
        f'<path d="{tail}" fill="url(#{prefix}-type)"/>'
    )
    width = math.ceil(tail_end + 1)
    return width, defs, body


def build_brand() -> None:
    import cairosvg

    print("brand")
    BRAND.mkdir(parents=True, exist_ok=True)

    # Mark, 64 x 64
    defs, body = mark_layers("am-mark", 64)
    mark_svg = svg_document(64, 64, defs, body)
    write_text(BRAND / "logo-mark.svg", mark_svg)

    # Single-colour mark: the rungs are holes, so it works as a mask or in a
    # watermark tinted with CSS (`color`).
    tile = squircle(0, 0, 64, 64, 64 * TILE_RADIUS)
    holes = "".join(play_glyph(64))
    write_text(
        BRAND / "logo-mark-mono.svg",
        svg_document(64, 64, "", f'<path d="{tile}{holes}" fill="currentColor" fill-rule="evenodd"/>'),
    )

    # Favicon: the same mark with its cuts snapped to whole pixels at 16 and
    # 32 px, and no highlight (it would only blur at that size).
    defs, body = mark_layers("am-fav", 64, shine=False, spec=GLYPH_SMALL)
    favicon_svg = svg_document(64, 64, defs, body)
    write_text(BRAND / "favicon.svg", favicon_svg)

    # Wordmarks
    for variant in ("dark", "light"):
        width, defs, body = wordmark_parts(variant)
        write_text(
            BRAND / f"logo-wordmark-{variant}.svg",
            svg_document(width, WORDMARK_HEIGHT, defs, body),
        )

    # Raster icons
    def png(svg: str, size: int, height: int | None = None) -> bytes:
        return cairosvg.svg2png(
            bytestring=svg.encode("utf-8"), output_width=size, output_height=height or size
        )

    write_bytes(BRAND / "favicon-32.png", png(favicon_svg, 32))
    write_bytes(BRAND / "icon-192.png", png(mark_svg, 192))
    write_bytes(BRAND / "icon-512.png", png(mark_svg, 512))

    def full_bleed(prefix: str, scale: float) -> str:
        defs = gradient(f"{prefix}-fill", AURORA)
        glyph = play_glyph(512, scale=scale)
        body = (
            f'<rect width="512" height="512" fill="url(#{prefix}-fill)"/>'
            f'<path d="{"".join(glyph)}" fill="{INK}"/>'
        )
        return svg_document(512, 512, defs, body, label=None)

    # iOS rounds the corners itself and shows transparency as black.
    write_bytes(BRAND / "apple-touch-icon.png", png(full_bleed("am-apple", 0.92), 180))
    # Maskable: launchers may crop to a circle of 80 %, so the glyph shrinks
    # to stay well inside it.
    write_bytes(BRAND / "icon-maskable-512.png", png(full_bleed("am-mask", 0.82), 512))

    # favicon.ico for clients that ask for it at the site root
    from PIL import Image

    frames = [Image.open(io.BytesIO(png(favicon_svg, size))).convert("RGBA") for size in (16, 32, 48)]
    ico = io.BytesIO()
    frames[-1].save(ico, format="ICO", sizes=[(16, 16), (32, 32), (48, 48)],
                    append_images=frames[:-1])
    write_bytes(BRAND / "favicon.ico", ico.getvalue())

    write_bytes(BRAND / "og-image.png", png(og_image_svg(), 1200, 630))

    manifest = {
        "name": BRAND_NAME,
        "short_name": BRAND_NAME,
        "description": "Stream, curate and deliver video, audio and images.",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "theme_color": NIGHT,
        "background_color": NIGHT,
        "icons": [
            {"src": "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "icon-maskable-512.png", "sizes": "512x512", "type": "image/png",
             "purpose": "maskable"},
            {"src": "favicon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"},
        ],
    }
    write_text(BRAND / "site.webmanifest", json.dumps(manifest, indent=2) + "\n")


def og_image_svg() -> str:
    """1200 x 630 social card: dark grid, aurora glow, wordmark, tagline."""
    width, height = 1200, 630
    defs = []
    body = [f'<rect width="{width}" height="{height}" fill="{NIGHT}"/>']

    grid = []
    for gx in range(0, width + 1, 40):
        grid.append(f"M{gx} 0V{height}")
    for gy in range(15, height + 1, 40):
        grid.append(f"M0 {gy}H{width}")
    body.append(
        f'<path d="{"".join(grid)}" stroke="#FFFFFF" stroke-opacity="0.05" stroke-width="1"/>'
    )
    defs.append(
        '<radialGradient id="og-fade" cx="0.5" cy="0.5" r="0.62">'
        f'<stop offset="0.35" stop-color="{NIGHT}" stop-opacity="0"/>'
        f'<stop offset="1" stop-color="{NIGHT}" stop-opacity="1"/>'
        "</radialGradient>"
    )
    body.append(f'<rect width="{width}" height="{height}" fill="url(#og-fade)"/>')

    for name, (cx, cy, r, color, opacity) in {
        "mint": (140, 10, 560, AURORA[0], 0.2),
        "cyan": (470, -40, 360, AURORA[1], 0.08),
        "violet": (1080, 650, 620, AURORA[2], 0.26),
    }.items():
        defs.append(
            f'<radialGradient id="og-{name}" cx="{cx}" cy="{cy}" r="{r}" gradientUnits="userSpaceOnUse">'
            f'<stop offset="0" stop-color="{color}" stop-opacity="{opacity}"/>'
            f'<stop offset="1" stop-color="{color}" stop-opacity="0"/>'
            "</radialGradient>"
        )
        body.append(f'<rect width="{width}" height="{height}" fill="url(#og-{name})"/>')

    # viewfinder brackets
    inset, arm = 44, 26
    brackets = []
    for bx, by, dx, dy in (
        (inset, inset, 1, 1), (width - inset, inset, -1, 1),
        (inset, height - inset, 1, -1), (width - inset, height - inset, -1, -1),
    ):
        brackets.append(f"M{bx} {by + dy * arm}V{by}H{bx + dx * arm}")
    body.append(
        f'<path d="{"".join(brackets)}" fill="none" stroke="#FFFFFF" stroke-opacity="0.22" '
        'stroke-width="2"/>'
    )

    mark_width, mark_defs, mark_body = wordmark_parts("dark")
    scale = 2.5
    mark_x = (width - mark_width * scale) / 2
    mark_y = 250 - WORDMARK_HEIGHT * scale / 2
    defs.append(mark_defs)
    body.append(
        f'<g transform="translate({num(mark_x)} {num(mark_y)}) scale({num(scale)})">{mark_body}</g>'
    )

    # aurora rule
    defs.append(gradient("og-rule", AURORA, x1="0", y1="0", x2="1", y2="0"))
    body.append(f'<rect x="{(width - 72) / 2}" y="364" width="72" height="4" rx="2" fill="url(#og-rule)"/>')

    tagline = "STREAM · CURATE · DELIVER"
    (path, start, end), = set_type(
        "jetbrains-mono", tagline, size=24, weight=500, x=0, baseline=0, tracking=0.32,
    )
    text_width = end - start
    body.append(
        f'<g transform="translate({num((width - text_width) / 2)} 432)">'
        f'<path d="{path}" fill="{MUTED}"/></g>'
    )

    return svg_document(width, height, "".join(defs), "".join(body))


# --------------------------------------------------------------------------
# web fonts
# --------------------------------------------------------------------------

#: Basic Latin, Latin-1, Latin Extended-A, combining marks (decomposed text,
#: e.g. file names from macOS), General Punctuation, arrows, euro, trade mark,
#: minus. Covers Portuguese and every other Western European language.
UNICODE_RANGES = (
    (0x0000, 0x00FF), (0x0100, 0x017F), (0x0300, 0x036F), (0x2000, 0x206F),
    (0x2190, 0x21FF), (0x20AC, 0x20AC), (0x2122, 0x2122), (0x2212, 0x2212),
)

WEBFONT_SPECS = (
    # key, output file, axis limits, extra OpenType features to keep
    ("inter", "inter-latin-var.woff2", {"opsz": 14, "wght": (400, 800)},
     ["tnum", "pnum", "lnum", "case", "zero", "cv11"]),
    ("space-grotesk", "space-grotesk-latin-var.woff2", {"wght": (400, 700)},
     ["tnum", "pnum", "case", "ss01"]),
    ("jetbrains-mono", "jetbrains-mono-latin-var.woff2", {"wght": (400, 700)},
     ["zero"]),
)


def unicode_range_css() -> str:
    def one(lo, hi):
        return f"U+{lo:04X}" if lo == hi else f"U+{lo:04X}-{hi:04X}"
    return ", ".join(one(lo, hi) for lo, hi in UNICODE_RANGES)


def build_fonts() -> None:
    from fontTools import subset
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont

    print("fonts")
    WEBFONTS.mkdir(parents=True, exist_ok=True)
    rules = [
        "/* Vendored web fonts, generated by tools/build_logo.py. Do not edit.\n"
        " * Inter, Space Grotesk and JetBrains Mono are licensed under the SIL Open\n"
        " * Font License 1.1; the licences sit next to the files in ../vendor/fonts/.\n"
        " */\n"
    ]
    unicodes = [cp for lo, hi in UNICODE_RANGES for cp in range(lo, hi + 1)]

    for key, filename, limits, features in WEBFONT_SPECS:
        spec = FONTS[key]
        # recalcTimestamp=False keeps head.modified from the source, so a
        # rebuild produces the same bytes.
        font = instantiateVariableFont(TTFont(font_file(key), recalcTimestamp=False), limits)
        # Round-trip through bytes: the subsetter expects fully loaded tables,
        # and a freshly instanced font still holds lazy ones.
        staged = io.BytesIO()
        font.save(staged)
        staged.seek(0)
        font = TTFont(staged, recalcTimestamp=False)

        options = subset.Options()
        options.flavor = "woff2"
        options.layout_features = list(options.layout_features) + features
        options.hinting = False
        options.desubroutinize = True
        options.notdef_outline = True
        options.name_IDs = [0, 1, 2, 3, 4, 5, 6, 13, 14]
        options.name_languages = [0x0409]
        subsetter = subset.Subsetter(options)
        subsetter.populate(unicodes=unicodes)
        subsetter.subset(font)

        target = WEBFONTS / filename
        buffer = io.BytesIO()
        font.flavor = "woff2"
        font.save(buffer)
        write_bytes(target, buffer.getvalue())

        license_name = f"{spec['file'].split('[')[0]}-OFL.txt"
        write_text(WEBFONTS / license_name, fetch(spec["license_url"]).decode("utf-8"))

        wght = limits["wght"]
        rules.append(
            "@font-face {\n"
            f'  font-family: "{spec["family"]}";\n'
            "  font-style: normal;\n"
            f"  font-weight: {wght[0]} {wght[1]};\n"
            "  font-display: swap;\n"
            f'  src: url("../vendor/fonts/{filename}") format("woff2");\n'
            f"  unicode-range: {unicode_range_css()};\n"
            "}\n"
        )

    write_text(CSS_DIR / "fonts.css", "\n".join(rules))


# --------------------------------------------------------------------------
# icon sprite
# --------------------------------------------------------------------------

#: sprite id -> Lucide icon name
ICON_MAP = {
    "home": "house", "compass": "compass", "flame": "flame", "film": "film",
    "music": "music", "image": "image", "file-text": "file-text",
    "list-video": "list-video", "library": "library", "upload": "cloud-upload",
    "dashboard": "layout-dashboard", "shield": "shield-check", "settings": "settings",
    "user": "user", "log-out": "log-out", "log-in": "log-in", "user-plus": "user-plus",
    "sun": "sun", "moon": "moon", "menu": "menu", "x": "x", "search": "search",
    "play": "play", "pause": "pause", "share": "share-2", "link": "link", "copy": "copy",
    "check": "check", "code": "code-xml", "pencil": "pencil", "trash": "trash-2",
    "eye": "eye", "eye-off": "eye-off", "lock": "lock", "globe": "globe",
    "clock": "clock", "calendar": "calendar", "message": "message-square",
    "plus": "plus", "list-plus": "list-plus", "arrow-up": "arrow-up",
    "arrow-down": "arrow-down", "chevron-down": "chevron-down",
    "chevron-right": "chevron-right", "chevron-left": "chevron-left",
    "more": "ellipsis-vertical", "refresh": "refresh-cw", "alert": "triangle-alert",
    "info": "info", "activity": "activity", "hard-drive": "hard-drive",
    "users": "users", "layers": "layers", "download": "download",
    "external-link": "external-link", "sliders": "sliders-horizontal",
    "sparkles": "sparkles", "zap": "zap", "captions": "captions",
    "keyboard": "keyboard", "server": "server", "cpu": "cpu", "send": "send",
    "reply": "reply", "filter": "list-filter", "sort": "arrow-down-wide-narrow",
    "timer": "timer", "hash": "hash", "chart": "chart-column",
    "check-circle": "circle-check", "x-circle": "circle-x", "loader": "loader-circle",
    "mail": "mail", "key": "key-round", "monitor-play": "monitor-play",
    "folder": "folder", "grid": "layout-grid", "rows": "rows-3",
    "skip-forward": "skip-forward", "image-up": "image-up", "heart": "heart",
    "bell": "bell", "radio": "radio", "tv": "tv", "mic": "mic",
    "video": "video", "screen-share": "screen-share", "stop": "circle-stop",
}

SVG_NS = "http://www.w3.org/2000/svg"
STROKE_ATTRS = {"fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin"}


def lucide_icon(name: str) -> list[str]:
    cache = BUILD / f"lucide-{LUCIDE_VERSION}" / f"{name}.svg"
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(fetch(f"{LUCIDE_CDN}/icons/{name}.svg"))
    source = re.sub(r"<!--.*?-->", "", cache.read_text(encoding="utf-8"), flags=re.S)
    root = ET.fromstring(source)
    children = []
    for child in root:
        tag = child.tag.split("}", 1)[-1]
        attrs = " ".join(
            f'{key}="{html.escape(value, quote=True)}"'
            for key, value in child.attrib.items() if key != "class"
        )
        if len(child):
            raise SystemExit(f"lucide icon {name} has nested elements; extend the parser")
        children.append(f"<{tag} {attrs}/>")
    return children


def build_icons() -> None:
    print("icons")
    symbols = []
    for sprite_id, lucide_name in ICON_MAP.items():
        children = "".join(lucide_icon(lucide_name))
        symbols.append(
            f'  <symbol id="i-{sprite_id}" viewBox="0 0 24 24">'
            '<g fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
            f'stroke-linejoin="round">{children}</g></symbol>'
        )
    sprite = (
        f'<svg xmlns="http://www.w3.org/2000/svg">\n'
        f"  <!-- Lucide v{LUCIDE_VERSION} (ISC, see static/vendor/LUCIDE-LICENSE.txt). "
        "Generated by tools/build_logo.py. -->\n"
        + "\n".join(symbols)
        + "\n</svg>\n"
    )
    write_text(ICONS / "sprite.svg", sprite)
    write_text(VENDOR / "LUCIDE-LICENSE.txt", fetch(f"{LUCIDE_CDN}/LICENSE").decode("utf-8"))


# --------------------------------------------------------------------------

PARTS = {"brand": build_brand, "fonts": build_fonts, "icons": build_icons}
ARGS = argparse.Namespace(allow_font_update=False)


def main(argv: list[str] | None = None) -> None:
    global ARGS
    if os.environ.get("PYTHONHASHSEED") != "0":
        # fontTools iterates over sets of glyph names in places; a fixed hash
        # seed makes the woff2 output byte-identical from one run to the next.
        os.environ["PYTHONHASHSEED"] = "0"
        os.execv(sys.executable, [sys.executable, *sys.argv])
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("parts", nargs="*", metavar="part",
                        help=f"what to build: {', '.join(PARTS)} (default: everything)")
    parser.add_argument("--allow-font-update", action="store_true",
                        help="accept source fonts whose hash differs from the pinned one")
    ARGS = parser.parse_args(argv)
    unknown = [part for part in ARGS.parts if part not in PARTS]
    if unknown:
        parser.error(f"unknown part(s): {', '.join(unknown)}")
    for part in ARGS.parts or PARTS:
        PARTS[part]()


if __name__ == "__main__":
    sys.exit(main())
