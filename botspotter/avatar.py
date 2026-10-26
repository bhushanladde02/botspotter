"""Procedural robot avatars — one deterministic face per bot.

No third-party imagery: every avatar is drawn from the bot's fingerprint
digest, so there is nothing to license and the same bot always gets the
same face (the visual twin of "same bot, same name, forever").

Design: the *adjective* of the name sets the colour (Copper → copper, Neon →
acid green…); bits of the digest pick head shape, eyes, antenna, mouth,
cheeks and one accessory. ~64·6·6·5·6·4·8 ≈ 440k distinct faces.

    GET /avatar/<slug>.svg   (api/app.py)
"""
from __future__ import annotations

from .names import ADJECTIVES

# adjective -> (body colour, accent colour). Unlisted adjectives fall back to a hash hue.
PALETTE = {
    "Amber": ("#ffbf47", "#7a4b00"), "Arctic": ("#dff6ff", "#3b8ea5"), "Azure": ("#3d8bff", "#0b2e6b"),
    "Bold": ("#ff4d4d", "#5a0000"), "Brass": ("#c9a227", "#5a4400"), "Bright": ("#fff36b", "#7a6a00"),
    "Bronze": ("#b0783a", "#4a2e10"), "Cobalt": ("#2b4bff", "#0a1560"), "Copper": ("#b87333", "#4e2d0e"),
    "Coral": ("#ff7f66", "#7a2e22"), "Crimson": ("#d1203b", "#4d0612"), "Crystal": ("#cfe9ff", "#4a7fa6"),
    "Dusk": ("#7b5ea7", "#2b1f45"), "Ember": ("#ff6a1f", "#6a2200"), "Frost": ("#e8f4ff", "#5f8fb3"),
    "Gilded": ("#e6c200", "#6b5a00"), "Golden": ("#f5c400", "#6b5400"), "Granite": ("#8c8c94", "#3a3a40"),
    "Hazel": ("#a67c52", "#4a3520"), "Indigo": ("#4b3fbf", "#1c1550"), "Iron": ("#6f7378", "#2b2d30"),
    "Ivory": ("#f6f1e1", "#9a8f6a"), "Jade": ("#3fb98b", "#124d37"), "Jet": ("#2b2b30", "#c6ff4d"),
    "Lunar": ("#d9d9e3", "#5c5c70"), "Marble": ("#eceef2", "#7d8291"), "Midnight": ("#1b1f4a", "#8ea2ff"),
    "Misty": ("#b9c7d1", "#4e6470"), "Neon": ("#c6ff4d", "#2f4d00"), "Nimble": ("#7ee081", "#1f5a21"),
    "Obsidian": ("#14141a", "#ff4dd2"), "Onyx": ("#1d1d24", "#4df0ff"), "Opal": ("#e9e3ff", "#7a6aa8"),
    "Pale": ("#f2e9dc", "#8a7a66"), "Pearl": ("#f3f0ea", "#a39a8e"), "Plum": ("#8e3b7a", "#3a1431"),
    "Quartz": ("#f7d6e0", "#8a4b5e"), "Quiet": ("#9fb4a3", "#3f5244"), "Rapid": ("#ff9f1c", "#6b3d00"),
    "Ruby": ("#e0115f", "#5a0526"), "Rusty": ("#b7410e", "#4a1a05"), "Sable": ("#2a2118", "#c9a227"),
    "Saffron": ("#f4c430", "#6b5200"), "Scarlet": ("#ff2400", "#660f00"), "Shadow": ("#3a3a44", "#9aa0b4"),
    "Silent": ("#6b7a8f", "#26303d"), "Silver": ("#c0c0c8", "#4d4d57"), "Slate": ("#5a6b7a", "#1f2a33"),
    "Smoky": ("#7a7267", "#2e2a26"), "Solar": ("#ffd166", "#7a5a00"), "Steel": ("#8da3b5", "#2f4151"),
    "Storm": ("#5c6b8a", "#1c2333"), "Swift": ("#4dd0ff", "#0b4a63"), "Teal": ("#1f9e9e", "#0a3b3b"),
    "Tidal": ("#2a9df4", "#0b3d66"), "Topaz": ("#ffc87c", "#7a4d10"), "Velvet": ("#6a1b4d", "#2b0a1f"),
    "Violet": ("#8f5bff", "#2e1466"), "Wild": ("#5bbf3a", "#1f4d12"), "Zinc": ("#a9b2b8", "#3f474c"),
    "Electric": ("#4df0ff", "#003b45"), "Cosmic": ("#5a2ca0", "#ff4dd2"), "Pixel": ("#ff4dd2", "#4a0f3d"),
    "Turbo": ("#ff5e00", "#5a2000"),
}

EYES = ["round", "visor", "sleepy", "wide", "wink", "x"]
HEADS = ["box", "dome", "tv", "capsule", "hex", "egg"]
ANTENNAE = ["ball", "bolt", "prop", "none", "twin"]
MOUTHS = ["grin", "flat", "zigzag", "o", "grill", "smirk"]
ACCESSORIES = ["none", "bowtie", "monocle", "tophat", "headphones", "mustache", "crown", "scarf"]


def _bits(digest: str, i: int, n: int) -> int:
    return int(digest[i * 2 : i * 2 + 2], 16) % n


def _text_colour(hex_colour: str) -> str:
    r, g, b = int(hex_colour[1:3], 16), int(hex_colour[3:5], 16), int(hex_colour[5:7], 16)
    return "#07090f" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#f3f3f3"


def avatar_svg(digest: str, name: str = "", size: int = 160) -> str:
    adj = (name.split(" ", 1)[0] if name else "")
    body, accent = PALETTE.get(adj, ("#%s" % digest[:6], "#%s" % digest[6:12]))
    head = HEADS[_bits(digest, 2, len(HEADS))]
    eyes = EYES[_bits(digest, 3, len(EYES))]
    ant = ANTENNAE[_bits(digest, 4, len(ANTENNAE))]
    mouth = MOUTHS[_bits(digest, 5, len(MOUTHS))]
    acc = ACCESSORIES[_bits(digest, 6, len(ACCESSORIES))]
    cheeks = _bits(digest, 7, 3) == 0
    eye_c = _text_colour(body)
    glow = "#c6ff4d" if acc != "crown" else "#ffd166"
    s: list[str] = []
    A = s.append

    # ---- head
    if head == "box":
        A(f'<rect x="20" y="30" width="60" height="52" rx="8" fill="{body}"/>')
    elif head == "dome":
        A(f'<path d="M20 82 V58 a30 30 0 0 1 60 0 V82 Z" fill="{body}"/>')
    elif head == "tv":
        A(f'<rect x="16" y="32" width="68" height="50" rx="6" fill="{body}"/><rect x="24" y="40" width="52" height="34" rx="4" fill="{accent}" opacity=".35"/>')
    elif head == "capsule":
        A(f'<rect x="22" y="26" width="56" height="58" rx="28" fill="{body}"/>')
    elif head == "hex":
        A(f'<polygon points="50,24 82,42 82,74 50,90 18,74 18,42" fill="{body}"/>')
    else:  # egg
        A(f'<ellipse cx="50" cy="58" rx="30" ry="33" fill="{body}"/>')
    # ears / side bolts
    A(f'<rect x="12" y="50" width="8" height="14" rx="2" fill="{accent}"/><rect x="80" y="50" width="8" height="14" rx="2" fill="{accent}"/>')

    # ---- antenna
    if ant == "ball":
        A(f'<line x1="50" y1="30" x2="50" y2="14" stroke="{accent}" stroke-width="3"/><circle cx="50" cy="11" r="5" fill="{glow}"/>')
    elif ant == "bolt":
        A(f'<polygon points="50,8 44,22 50,20 46,32 58,16 52,18 56,8" fill="{glow}"/>')
    elif ant == "prop":
        A(f'<line x1="50" y1="30" x2="50" y2="18" stroke="{accent}" stroke-width="3"/><ellipse cx="50" cy="16" rx="18" ry="4" fill="{glow}" opacity=".9"/><circle cx="50" cy="16" r="3" fill="{accent}"/>')
    elif ant == "twin":
        A(f'<line x1="36" y1="30" x2="30" y2="14" stroke="{accent}" stroke-width="3"/><line x1="64" y1="30" x2="70" y2="14" stroke="{accent}" stroke-width="3"/><circle cx="29" cy="12" r="4" fill="{glow}"/><circle cx="71" cy="12" r="4" fill="{glow}"/>')

    # ---- eyes
    if eyes == "round":
        A(f'<circle cx="38" cy="54" r="7" fill="{eye_c}"/><circle cx="62" cy="54" r="7" fill="{eye_c}"/><circle cx="40" cy="52" r="2.5" fill="{body}"/><circle cx="64" cy="52" r="2.5" fill="{body}"/>')
    elif eyes == "visor":
        A(f'<rect x="28" y="48" width="44" height="12" rx="6" fill="{eye_c}"/><rect x="34" y="51" width="10" height="6" rx="3" fill="{glow}"/><rect x="56" y="51" width="10" height="6" rx="3" fill="{glow}"/>')
    elif eyes == "sleepy":
        A(f'<path d="M30 56 q8 -8 16 0" stroke="{eye_c}" stroke-width="3" fill="none"/><path d="M54 56 q8 -8 16 0" stroke="{eye_c}" stroke-width="3" fill="none"/>')
    elif eyes == "wide":
        A(f'<circle cx="38" cy="54" r="9" fill="{eye_c}"/><circle cx="62" cy="54" r="9" fill="{eye_c}"/><circle cx="38" cy="54" r="3.5" fill="{accent}"/><circle cx="62" cy="54" r="3.5" fill="{accent}"/>')
    elif eyes == "wink":
        A(f'<circle cx="38" cy="54" r="7" fill="{eye_c}"/><circle cx="40" cy="52" r="2.5" fill="{body}"/><path d="M54 54 h16" stroke="{eye_c}" stroke-width="3"/>')
    else:  # x
        A(f'<path d="M32 48 l12 12 M44 48 l-12 12 M56 48 l12 12 M68 48 l-12 12" stroke="{eye_c}" stroke-width="3"/>')
    if cheeks:
        A(f'<circle cx="30" cy="64" r="4" fill="#ff4dd2" opacity=".55"/><circle cx="70" cy="64" r="4" fill="#ff4dd2" opacity=".55"/>')

    # ---- mouth
    if mouth == "grin":
        A(f'<path d="M36 70 q14 12 28 0" stroke="{eye_c}" stroke-width="3" fill="none"/>')
    elif mouth == "flat":
        A(f'<rect x="38" y="69" width="24" height="3" rx="1.5" fill="{eye_c}"/>')
    elif mouth == "zigzag":
        A(f'<polyline points="36,70 42,75 48,70 54,75 60,70 66,75" stroke="{eye_c}" stroke-width="3" fill="none"/>')
    elif mouth == "o":
        A(f'<circle cx="50" cy="72" r="5" fill="{eye_c}"/>')
    elif mouth == "grill":
        A(f'<rect x="36" y="67" width="28" height="10" rx="2" fill="{eye_c}"/><path d="M42 67 v10 M48 67 v10 M54 67 v10 M60 67 v10" stroke="{body}" stroke-width="2"/>')
    else:  # smirk
        A(f'<path d="M40 72 q10 6 22 -2" stroke="{eye_c}" stroke-width="3" fill="none"/>')

    # ---- accessory
    if acc == "bowtie":
        A(f'<polygon points="38,86 50,91 38,96" fill="#ff4dd2"/><polygon points="62,86 50,91 62,96" fill="#ff4dd2"/><circle cx="50" cy="91" r="2.5" fill="{accent}"/>')
    elif acc == "monocle":
        A(f'<circle cx="62" cy="54" r="11" stroke="#ffd166" stroke-width="2.5" fill="none"/><line x1="70" y1="62" x2="76" y2="84" stroke="#ffd166" stroke-width="1.5"/>')
    elif acc == "tophat":
        A(f'<rect x="30" y="8" width="40" height="22" rx="2" fill="#14141a"/><rect x="22" y="28" width="56" height="5" rx="2" fill="#14141a"/><rect x="30" y="22" width="40" height="5" fill="{glow}"/>')
    elif acc == "headphones":
        A(f'<path d="M18 56 a32 32 0 0 1 64 0" stroke="{accent}" stroke-width="4" fill="none"/><rect x="10" y="50" width="10" height="18" rx="4" fill="{accent}"/><rect x="80" y="50" width="10" height="18" rx="4" fill="{accent}"/>')
    elif acc == "mustache":
        A(f'<path d="M36 66 q7 -6 14 0 q7 -6 14 0 q-7 6 -14 2 q-7 4 -14 -2" fill="{eye_c}"/>')
    elif acc == "crown":
        A(f'<polygon points="30,30 36,14 46,26 50,10 54,26 64,14 70,30" fill="#ffd166"/><circle cx="50" cy="12" r="2.5" fill="#ff4dd2"/>')
    elif acc == "scarf":
        A(f'<rect x="26" y="82" width="48" height="8" rx="3" fill="#ff4dd2"/><rect x="58" y="86" width="9" height="12" rx="2" fill="#ff4dd2"/>')

    inner = "".join(s)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 100 100" role="img" aria-label="avatar of {name}">'
            f'<rect width="100" height="100" rx="18" fill="#0e1220"/>{inner}</svg>')
