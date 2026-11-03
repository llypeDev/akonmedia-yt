"""Build the avatar gallery: the characters someone can pick for their profile.

Every file this script writes is generated and committed; none is hand-edited.

    static/avatars/<key>.svg    one character on a gradient, 100x100, full bleed

The keys are what ``User.avatar`` stores (see ``apps/users/avatars.py``), so a
character can be redrawn here but never renamed or removed once people use it.

    python tools/build_avatars.py
    python tools/build_avatars.py --sheet sheet.html    # a page with all of them

Each character is plain shapes on the portal's gradients: they stay crisp at
any size, weigh about a kilobyte, and need no font or emoji to be installed.
"""
from __future__ import annotations

import argparse
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "static" / "avatars"

INK = "#1b1630"
WHITE = "#ffffff"
BLUSH = "#ff7aa2"


# ------------------------------------------------------------------ pieces


def eye(x: float, y: float, r: float = 4, ink: str = INK) -> str:
    """A round eye with a catch-light."""
    return (
        f'<circle cx="{x}" cy="{y}" r="{r}" fill="{ink}"/>'
        f'<circle cx="{x + r * 0.35:.2f}" cy="{y - r * 0.38:.2f}" r="{r * 0.34:.2f}" fill="{WHITE}"/>'
    )


def eyes(y: float, gap: float, r: float = 4, ink: str = INK, cx: float = 50) -> str:
    return eye(cx - gap, y, r, ink) + eye(cx + gap, y, r, ink)


def blush(y: float, gap: float, r: float = 3.6, opacity: float = 0.5) -> str:
    return (
        f'<circle cx="{50 - gap}" cy="{y}" r="{r}" fill="{BLUSH}" opacity="{opacity}"/>'
        f'<circle cx="{50 + gap}" cy="{y}" r="{r}" fill="{BLUSH}" opacity="{opacity}"/>'
    )


def smile(y: float, half: float = 6, depth: float = 5, width: float = 2.6, ink: str = INK) -> str:
    return (
        f'<path d="M{50 - half} {y} Q50 {y + depth} {50 + half} {y}" fill="none" '
        f'stroke="{ink}" stroke-width="{width}" stroke-linecap="round"/>'
    )


def open_smile(y: float, half: float = 7, depth: float = 9) -> str:
    return (
        f'<path d="M{50 - half} {y} Q50 {y + depth} {50 + half} {y} Z" fill="{INK}" '
        f'stroke="{INK}" stroke-width="1.5" stroke-linejoin="round"/>'
        f'<path d="M{50 - half * 0.55:.2f} {y + depth * 0.5:.2f} Q50 {y + depth * 0.82:.2f} '
        f'{50 + half * 0.55:.2f} {y + depth * 0.5:.2f} Q50 {y + depth * 0.32:.2f} '
        f'{50 - half * 0.55:.2f} {y + depth * 0.5:.2f} Z" fill="{BLUSH}"/>'
    )


def mirror(path_left: str, fill: str) -> str:
    """The same shape on both sides of the face."""
    return (
        f'<path d="{path_left}" fill="{fill}"/>'
        f'<path d="{path_left}" fill="{fill}" transform="matrix(-1 0 0 1 100 0)"/>'
    )


# -------------------------------------------------------------- characters


def robot() -> str:
    body = "#e6edf7"
    return (
        '<path d="M50 23V13" stroke="#cfd8e6" stroke-width="3.2" stroke-linecap="round"/>'
        '<circle cx="50" cy="11" r="5" fill="#ff5c9a"/>'
        '<circle cx="51.6" cy="9.4" r="1.6" fill="#fff" opacity=".8"/>'
        '<rect x="30" y="76" width="40" height="32" rx="12" fill="#cfd8e6"/>'
        '<circle cx="50" cy="88" r="3.6" fill="#22d3ee"/>'
        '<rect x="15" y="40" width="9" height="16" rx="3.5" fill="#b8c4d6"/>'
        '<rect x="76" y="40" width="9" height="16" rx="3.5" fill="#b8c4d6"/>'
        f'<rect x="21" y="22" width="58" height="52" rx="15" fill="{body}"/>'
        '<rect x="28.5" y="31" width="43" height="32" rx="10" fill="#142033"/>'
        '<rect x="36.5" y="40" width="7.5" height="10" rx="3.2" fill="#2ee6a6"/>'
        '<rect x="56" y="40" width="7.5" height="10" rx="3.2" fill="#2ee6a6"/>'
        '<path d="M44 55.5Q50 59.5 56 55.5" fill="none" stroke="#2ee6a6" stroke-width="2.6" stroke-linecap="round"/>'
    )


def cat() -> str:
    fur = "#fff3e3"
    return (
        f'<ellipse cx="50" cy="102" rx="31" ry="22" fill="{fur}"/>'
        + mirror("M23 44L27 13L47 30Z", fur)
        + mirror("M29 36L30.5 21L40 29.5Z", "#ffb3c1")
        + f'<ellipse cx="50" cy="53" rx="30" ry="26" fill="{fur}"/>'
        f'<path d="M36.5 51Q41 45.5 45.5 51M54.5 51Q59 45.5 63.5 51" fill="none" stroke="{INK}" stroke-width="3" stroke-linecap="round"/>'
        f'<path d="M47.5 57.5H52.5L50 60.5Z" fill="{BLUSH}" stroke="{BLUSH}" stroke-width="1.2" stroke-linejoin="round"/>'
        f'<path d="M44.5 62Q47.2 65 50 62Q52.8 65 55.5 62" fill="none" stroke="{INK}" stroke-width="2.1" stroke-linecap="round"/>'
        f'<path d="M30 58L17 56M30 62.5L17 64.5M70 58L83 56M70 62.5L83 64.5" stroke="{INK}" stroke-width="1.6" stroke-linecap="round" opacity=".45"/>'
        + blush(60, 15)
    )


def bear() -> str:
    fur, light = "#9b5f43", "#e7b48f"
    return (
        f'<ellipse cx="50" cy="103" rx="32" ry="22" fill="{fur}"/>'
        f'<circle cx="27" cy="31" r="11.5" fill="{fur}"/><circle cx="73" cy="31" r="11.5" fill="{fur}"/>'
        f'<circle cx="27" cy="31" r="6" fill="{light}"/><circle cx="73" cy="31" r="6" fill="{light}"/>'
        f'<circle cx="50" cy="54" r="30" fill="{fur}"/>'
        f'<ellipse cx="50" cy="64" rx="13.5" ry="10.5" fill="{light}"/>'
        f'<ellipse cx="50" cy="59.5" rx="4.8" ry="3.4" fill="{INK}"/>'
        + smile(64.5, 4.5, 4, 2.2)
        + eyes(49, 11, 3.9)
    )


def ghost() -> str:
    sheet = "#f7f5ff"
    return (
        f'<path d="M23 96V49A27 27 0 0 1 77 49V96Q70.2 89 63.5 96Q56.8 103 50 96Q43.2 89 36.5 96Q29.8 103 23 96Z" fill="{sheet}"/>'
        f'<ellipse cx="41" cy="50" rx="4.2" ry="5.8" fill="{INK}"/><ellipse cx="59" cy="50" rx="4.2" ry="5.8" fill="{INK}"/>'
        '<circle cx="42.4" cy="47.8" r="1.5" fill="#fff"/><circle cx="60.4" cy="47.8" r="1.5" fill="#fff"/>'
        f'<ellipse cx="50" cy="63" rx="4.2" ry="5.2" fill="{INK}"/>'
        + blush(58.5, 16, 3.4, 0.45)
        + '<path d="M80 22l1.6 3.6 3.6 1.6-3.6 1.6L80 32.4l-1.6-3.6-3.6-1.6 3.6-1.6z" fill="#fff" opacity=".9"/>'
    )


def alien() -> str:
    skin = "#a3f06b"
    return (
        f'<ellipse cx="50" cy="104" rx="26" ry="22" fill="#8fdc5a"/>'
        f'<path d="M40 27L31 12M60 27L69 12" stroke="{skin}" stroke-width="3.2" stroke-linecap="round"/>'
        '<circle cx="30" cy="10.5" r="4.6" fill="#ffb547"/><circle cx="70" cy="10.5" r="4.6" fill="#ffb547"/>'
        f'<ellipse cx="50" cy="52" rx="28" ry="30" fill="{skin}"/>'
        f'<ellipse cx="38.5" cy="50" rx="8.2" ry="10.5" transform="rotate(-18 38.5 50)" fill="{INK}"/>'
        f'<ellipse cx="61.5" cy="50" rx="8.2" ry="10.5" transform="rotate(18 61.5 50)" fill="{INK}"/>'
        '<circle cx="41" cy="46" r="2.6" fill="#fff"/><circle cx="64" cy="46" r="2.6" fill="#fff"/>'
        + smile(68.5, 4.5, 3.5, 2.3)
    )


def astronaut() -> str:
    suit = "#f2f5fb"
    stars = "".join(
        f'<circle cx="{x}" cy="{y}" r="{r}" fill="#fff" opacity=".85"/>'
        for x, y, r in ((14, 18, 1.6), (85, 15, 1.2), (89, 68, 1.5), (11, 74, 1.1), (24, 9, 0.9))
    )
    return (
        stars
        + f'<rect x="25" y="82" width="50" height="30" rx="14" fill="{suit}"/>'
        '<circle cx="39" cy="92" r="3.6" fill="#ff5c7a"/>'
        '<rect x="77" y="44" width="7" height="14" rx="2.5" fill="#cfd8e6"/>'
        f'<circle cx="50" cy="52" r="31" fill="{suit}"/>'
        '<circle cx="50" cy="52" r="31" fill="none" stroke="#cfd8e6" stroke-width="2"/>'
        '<rect x="28" y="36" width="44" height="32" rx="16" fill="url(#visor)"/>'
        '<path d="M35 47Q37 41 45 40.5" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" opacity=".6"/>'
        '<circle cx="62" cy="58" r="1.8" fill="#fff" opacity=".5"/>'
    )


def frog() -> str:
    skin, dark = "#6ee06f", "#4cc35a"
    return (
        f'<ellipse cx="50" cy="101" rx="30" ry="18" fill="{dark}"/>'
        f'<circle cx="34" cy="33" r="12.5" fill="{skin}"/><circle cx="66" cy="33" r="12.5" fill="{skin}"/>'
        f'<ellipse cx="50" cy="58" rx="35" ry="24" fill="{skin}"/>'
        '<circle cx="34" cy="33" r="8.2" fill="#fff"/><circle cx="66" cy="33" r="8.2" fill="#fff"/>'
        + eye(35, 34, 4.6) + eye(65, 34, 4.6)
        + f'<path d="M33 61Q50 75 67 61" fill="none" stroke="{INK}" stroke-width="3" stroke-linecap="round"/>'
        + blush(63, 21, 4.2)
    )


def bunny() -> str:
    fur, inner = "#fbfaff", "#ffc2d6"
    return (
        f'<ellipse cx="50" cy="103" rx="28" ry="20" fill="{fur}"/>'
        f'<ellipse cx="37" cy="23" rx="8.5" ry="21" transform="rotate(-11 37 23)" fill="{fur}"/>'
        f'<ellipse cx="63" cy="23" rx="8.5" ry="21" transform="rotate(11 63 23)" fill="{fur}"/>'
        f'<ellipse cx="37" cy="24" rx="4.2" ry="15" transform="rotate(-11 37 24)" fill="{inner}"/>'
        f'<ellipse cx="63" cy="24" rx="4.2" ry="15" transform="rotate(11 63 24)" fill="{inner}"/>'
        f'<ellipse cx="50" cy="59" rx="27" ry="24" fill="{fur}"/>'
        + eyes(55, 9, 3.7)
        + f'<ellipse cx="50" cy="62.5" rx="3.2" ry="2.3" fill="{BLUSH}"/>'
        f'<path d="M46.5 65.5Q48.3 68.5 50 65.5Q51.7 68.5 53.5 65.5" fill="none" stroke="{INK}" stroke-width="1.9" stroke-linecap="round"/>'
        + blush(63, 14, 3.4)
    )


def penguin() -> str:
    dark, face = "#23263a", "#ffffff"
    return (
        f'<ellipse cx="50" cy="66" rx="32" ry="40" fill="{dark}"/>'
        f'<ellipse cx="41" cy="53" rx="12.5" ry="14" fill="{face}"/><ellipse cx="59" cy="53" rx="12.5" ry="14" fill="{face}"/>'
        f'<ellipse cx="50" cy="66" rx="18" ry="13" fill="{face}"/>'
        f'<ellipse cx="50" cy="96" rx="19" ry="15" fill="{face}"/>'
        + eyes(51, 8.5, 3.6)
        + '<path d="M44 59.5Q50 55 56 59.5Q50 65.5 44 59.5Z" fill="#ffb547"/>'
        + blush(60, 15, 3.2)
    )


def fox() -> str:
    fur, light = "#ff8a3d", "#fff4ea"
    return (
        f'<ellipse cx="50" cy="104" rx="28" ry="22" fill="{fur}"/>'
        + mirror("M21 45L25 13L45 31Z", fur)
        + mirror("M27.5 37L29 21.5L38.5 30.5Z", "#3a2a2a")
        + f'<path d="M17 45Q19 28 50 28Q81 28 83 45Q81 63 50 78Q19 63 17 45Z" fill="{fur}"/>'
        f'<path d="M22 50Q38 55 50 78Q62 55 78 50Q75 66 50 80Q25 66 22 50Z" fill="{light}"/>'
        + eyes(46, 11, 3.7)
        + f'<ellipse cx="50" cy="69.5" rx="4.2" ry="3.1" fill="{INK}"/>'
    )


def slime() -> str:
    goo = "#ff7ab8"
    return (
        f'<path d="M50 20C58 33 81 44 81 66C81 85 67 95 50 95C33 95 19 85 19 66C19 44 42 33 50 20Z" fill="{goo}"/>'
        '<path d="M31 60Q31 49 39.5 42" fill="none" stroke="#fff" stroke-width="4" stroke-linecap="round" opacity=".55"/>'
        '<circle cx="34" cy="68" r="2.2" fill="#fff" opacity=".55"/>'
        + eyes(64, 8.5, 4.2)
        + smile(73, 5, 4.5, 2.5)
    )


def owl() -> str:
    feathers, belly = "#6b57e0", "#a596ff"
    return (
        mirror("M26 36L21 17L39 28Z", feathers)
        + f'<ellipse cx="50" cy="64" rx="33" ry="38" fill="{feathers}"/>'
        f'<ellipse cx="50" cy="86" rx="19" ry="17" fill="{belly}"/>'
        f'<path d="M42 82Q45 85 48 82M52 82Q55 85 58 82M47 90Q50 93 53 90" fill="none" stroke="{feathers}" stroke-width="1.8" stroke-linecap="round" opacity=".7"/>'
        '<circle cx="37.5" cy="51" r="11.5" fill="#fff"/><circle cx="62.5" cy="51" r="11.5" fill="#fff"/>'
        + eye(38.5, 52, 5.4) + eye(61.5, 52, 5.4)
        + '<path d="M45.5 60H54.5L50 67Z" fill="#ffb547" stroke="#ffb547" stroke-width="1.4" stroke-linejoin="round"/>'
    )


def panda() -> str:
    dark, fur = "#1f2233", "#fbfbff"
    return (
        f'<ellipse cx="50" cy="103" rx="30" ry="20" fill="{fur}"/>'
        f'<circle cx="27" cy="32" r="10.5" fill="{dark}"/><circle cx="73" cy="32" r="10.5" fill="{dark}"/>'
        f'<circle cx="50" cy="56" r="29.5" fill="{fur}"/>'
        f'<ellipse cx="39" cy="53" rx="7.2" ry="9.4" transform="rotate(-28 39 53)" fill="{dark}"/>'
        f'<ellipse cx="61" cy="53" rx="7.2" ry="9.4" transform="rotate(28 61 53)" fill="{dark}"/>'
        '<circle cx="40" cy="52" r="2.6" fill="#fff"/><circle cx="60" cy="52" r="2.6" fill="#fff"/>'
        f'<ellipse cx="50" cy="63" rx="4.2" ry="3" fill="{dark}"/>'
        + smile(67, 4, 3.4, 2, dark)
        + blush(64, 17, 3.4, 0.45)
    )


def chick() -> str:
    down, wing = "#ffd43b", "#f5c000"
    return (
        f'<path d="M48 29Q44 18 51 14Q49 21 55 22Q50 23 51 29Z" fill="{down}"/>'
        f'<ellipse cx="22.5" cy="66" rx="6.5" ry="11" transform="rotate(-22 22.5 66)" fill="{wing}"/>'
        f'<ellipse cx="77.5" cy="66" rx="6.5" ry="11" transform="rotate(22 77.5 66)" fill="{wing}"/>'
        f'<circle cx="50" cy="60" r="31" fill="{down}"/>'
        f'<ellipse cx="50" cy="104" rx="26" ry="16" fill="{down}"/>'
        + eyes(53, 9.5, 3.8)
        + '<path d="M44.5 61H55.5L50 68Z" fill="#ff8a3d" stroke="#ff8a3d" stroke-width="1.6" stroke-linejoin="round"/>'
        + blush(62, 17, 3.6)
    )


def sun() -> str:
    gold = "#ffd43b"
    rays = "".join(
        f'<rect x="47.5" y="9" width="5" height="11" rx="2.5" fill="{gold}" transform="rotate({angle} 50 52)"/>'
        for angle in range(0, 360, 45)
    )
    return (
        rays
        + f'<circle cx="50" cy="52" r="25" fill="{gold}"/>'
        f'<rect x="33" y="44" width="15" height="10.5" rx="4.5" fill="{INK}"/>'
        f'<rect x="52" y="44" width="15" height="10.5" rx="4.5" fill="{INK}"/>'
        f'<path d="M48 47.5H52M33 46.5L27 44.5M67 46.5L73 44.5" stroke="{INK}" stroke-width="2.2" stroke-linecap="round"/>'
        '<path d="M36 46.8L40 46.8" stroke="#fff" stroke-width="1.6" stroke-linecap="round" opacity=".6"/>'
        '<path d="M55 46.8L59 46.8" stroke="#fff" stroke-width="1.6" stroke-linecap="round" opacity=".6"/>'
        + smile(61.5, 7, 5.5, 2.6)
    )


def monster() -> str:
    skin = "#ff8a3d"
    return (
        mirror("M34 31L29 15L43 26Z", "#fff1d6")
        + f'<rect x="21" y="24" width="58" height="90" rx="27" fill="{skin}"/>'
        '<circle cx="31" cy="76" r="3" fill="#ffb27a"/><circle cx="68" cy="82" r="4" fill="#ffb27a"/>'
        '<circle cx="50" cy="48" r="13" fill="#fff"/>'
        + eye(51, 49.5, 6.4)
        + f'<path d="M35 64Q50 80 65 64Z" fill="{INK}" stroke="{INK}" stroke-width="2" stroke-linejoin="round"/>'
        '<path d="M41.5 64.5L44.5 70L47.5 64.5ZM52.5 64.5L55.5 70L58.5 64.5Z" fill="#fff"/>'
    )


#: key -> (drawing, background gradient). The order is the gallery's order.
CHARACTERS = {
    "robot": (robot, ("#22d3ee", "#7c6cff")),
    "cat": (cat, ("#ffb547", "#ff5c7a")),
    "bear": (bear, ("#2ee6a6", "#22d3ee")),
    "ghost": (ghost, ("#7c6cff", "#ff5c9a")),
    "alien": (alien, ("#3b82f6", "#7c6cff")),
    "astronaut": (astronaut, ("#1e1b4b", "#7c6cff")),
    "frog": (frog, ("#0ea5e9", "#22d3ee")),
    "bunny": (bunny, ("#f472b6", "#a78bfa")),
    "penguin": (penguin, ("#38bdf8", "#2ee6a6")),
    "fox": (fox, ("#2ee6a6", "#22d3ee")),
    "slime": (slime, ("#7c6cff", "#22d3ee")),
    "owl": (owl, ("#ffb547", "#ff8a65")),
    "panda": (panda, ("#a78bfa", "#22d3ee")),
    "chick": (chick, ("#ff5c9a", "#ffb547")),
    "sun": (sun, ("#38bdf8", "#7c6cff")),
    "monster": (monster, ("#22d3ee", "#7c6cff")),
}


def document(key: str) -> str:
    draw, (top, bottom) = CHARACTERS[key]
    defs = (
        f'<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="{top}"/><stop offset="1" stop-color="{bottom}"/></linearGradient>'
    )
    if key == "astronaut":
        defs += (
            '<linearGradient id="visor" x1="0" y1="0" x2="1" y2="1">'
            '<stop offset="0" stop-color="#22d3ee"/><stop offset="1" stop-color="#1e1b4b"/></linearGradient>'
        )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        f"<defs>{defs}</defs>"
        '<rect width="100" height="100" fill="url(#bg)"/>'
        f"{draw()}</svg>\n"
    )


def build() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for key in CHARACTERS:
        (OUT / f"{key}.svg").write_text(document(key), encoding="utf-8")
    stale = {path.stem for path in OUT.glob("*.svg")} - set(CHARACTERS)
    if stale:
        raise SystemExit(f"static/avatars has files no character draws: {sorted(stale)}")
    print(f"  static/avatars/  {len(CHARACTERS)} characters")


def sheet(path: pathlib.Path) -> None:
    """A page with every character at the sizes the portal uses."""
    rows = []
    for key in CHARACTERS:
        src = (OUT / f"{key}.svg").as_uri()
        cells = "".join(
            f'<img src="{src}" width="{size}" height="{size}" style="border-radius:50%">'
            for size in (128, 88, 48, 30)
        )
        rows.append(f"<div style='display:flex;gap:14px;align-items:center'>{cells}<code>{key}</code></div>")
    path.write_text(
        "<body style='margin:0;padding:20px;background:#0b1019;color:#e8eef6;font:14px sans-serif;"
        "display:grid;grid-template-columns:repeat(2,1fr);gap:14px'>" + "".join(rows) + "</body>",
        encoding="utf-8",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sheet", type=pathlib.Path, help="also write a preview page here")
    args = parser.parse_args()
    build()
    if args.sheet:
        sheet(args.sheet)
