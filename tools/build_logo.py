"""Build the AkonMedia wordmark as outlines.

The logo must render identically everywhere, so the type is converted to paths
instead of relying on whatever font the viewer has installed. Run this once when
the wordmark changes; the generated SVGs are committed.

The font (Inter, SIL Open Font License) is downloaded on first run and is only
used here to produce outlines — it is not redistributed with the output.

    python tools/build_logo.py
"""
import pathlib
import urllib.request

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

ROOT = pathlib.Path(__file__).resolve().parent.parent
FONT = ROOT / ".build" / "fonts" / "Inter.ttf"
FONT_URL = (
    "https://raw.githubusercontent.com/google/fonts/main/ofl/inter/"
    "Inter%5Bopsz%2Cwght%5D.ttf"
)
OUT = ROOT / "static" / "brand"

SIZE = 27.0
GAP = 14.0
ICON = 48.0
PAD_RIGHT = 12.0
BASELINE = 33.0


def word_path(font, text: str) -> tuple[str, float]:
    """Return (path data, advance width) for text in font units."""
    glyphs = font.getGlyphSet()
    cmap = font.getBestCmap()
    pen = SVGPathPen(glyphs)

    x = 0
    for char in text:
        name = cmap[ord(char)]
        glyph = glyphs[name]
        glyph.draw(TransformPen(pen, (1, 0, 0, 1, x, 0)))
        x += glyph.width
    return pen.getCommands(), x


def ensure_font() -> None:
    if FONT.exists():
        return
    FONT.parent.mkdir(parents=True, exist_ok=True)
    print(f"downloading {FONT_URL}")
    FONT.write_bytes(urllib.request.urlopen(FONT_URL, timeout=60).read())


def build(variant: str, first_fill: str, second_fill: str) -> None:
    font = TTFont(FONT)
    font = instantiateVariableFont(font, {"wght": 700, "opsz": 16})
    upm = font["head"].unitsPerEm

    scale = SIZE / upm
    first, first_width = word_path(font, "Akon")
    second, second_width = word_path(font, "Media")

    text_x = ICON + GAP
    first_advance = first_width * scale
    second_x = text_x + first_advance

    total_width = second_x + second_width * scale + PAD_RIGHT
    width = round(total_width, 2)

    def word(data: str, x: float) -> str:
        return (
            f'  <g transform="translate({x:.2f} {BASELINE}) scale({scale:.6f} {-scale:.6f})">\n'
            f'    <path d="{data}" fill="{first_fill if x == text_x else second_fill}"/>\n'
            f"  </g>"
        )

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} 48" width="{width}" height="48" role="img" aria-label="AkonMedia">
  <title>AkonMedia</title>
  <defs>
    <linearGradient id="akonmark-{variant}" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#3ad07f"/>
      <stop offset="1" stop-color="#1f9c58"/>
    </linearGradient>
  </defs>
  <rect width="48" height="48" rx="12" fill="url(#akonmark-{variant})"/>
  <path d="M21 15.75 L33 24 L21 32.25 Z"
        fill="#07130c" stroke="#07130c" stroke-width="3.75" stroke-linejoin="round"/>
{word(first, text_x)}
{word(second, second_x)}
</svg>
"""
    target = OUT / f"logo-wordmark-{variant}.svg"
    target.write_text(svg, encoding="utf-8", newline="\n")
    print(f"{target.name}: {width} x 48, {len(first) + len(second)} glyph points")


if __name__ == "__main__":
    ensure_font()
    build("dark", "#e8eaed", "#3ad07f")
    build("light", "#0b0d10", "#1f9c58")
