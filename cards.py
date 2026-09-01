"""
Printable member card.

Design notes, so the next person does not undo them by accident:

  - The client's name is the headline and is fitted to the full inner width of
    the card. A card is read across a counter at arm's length; the one thing
    that must be legible from there is who it belongs to. Long names wrap to a
    second line rather than shrinking below the point where the academy title
    out-ranks them.
  - Serif throughout for the names, mono only for the token and labels. An art
    academy card should not look like a gym receipt.
  - Fonts are resolved from a candidate list. Cards are generated wherever the
    app runs, which may be Windows with no DejaVu installed, so each role falls
    back through Linux, Windows and macOS options before giving up.
  - The member number is printed large and clearly. Reception types it in when
    the scanner and camera are both unavailable, so it has to be readable
    across a counter, not hidden in small print.
"""

import os
import sys

import qrcode
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------- canvas
CARD_W = 660          # height follows the content; see build_card
MARGIN = 58
FRAME = 26
ROW_W = CARD_W - MARGIN * 2

PAPER = "#FCFAFD"
INK = "#1B1220"
MUTE = "#8B8090"
RULE = "#E7DEEA"
ACCENT = "#87438E"          # the purple of the logo
LOGO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "logo.png")

# ---------------------------------------------------------------- fonts
_SERIF = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/usr/share/fonts/truetype/crosextra/Caladea-Regular.ttf",
    "C:/Windows/Fonts/georgia.ttf",
    "C:/Windows/Fonts/times.ttf",
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
]
_SERIF_B = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/truetype/crosextra/Caladea-Bold.ttf",
    "C:/Windows/Fonts/georgiab.ttf",
    "C:/Windows/Fonts/timesbd.ttf",
    "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
]
_SANS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]
_MONO = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "C:/Windows/Fonts/consola.ttf",
    "C:/Windows/Fonts/cour.ttf",
    "/System/Library/Fonts/Menlo.ttc",
]
_cache = {}


def _font(role, size):
    key = (role, size)
    if key in _cache:
        return _cache[key]
    for path in {"serif": _SERIF, "serif_b": _SERIF_B,
                 "sans": _SANS, "mono": _MONO}[role]:
        if os.path.exists(path):
            try:
                _cache[key] = ImageFont.truetype(path, size)
                return _cache[key]
            except Exception:
                continue
    _cache[key] = ImageFont.load_default()
    return _cache[key]


# ---------------------------------------------------------------- text helpers
def _tracked_width(draw, text, font, track):
    if not text:
        return 0
    return sum(draw.textlength(c, font=font) for c in text) + track * (len(text) - 1)


def _draw_tracked(draw, xy, text, font, fill, track):
    """Letter-spaced text. PIL has no tracking, so characters are placed one
    at a time — worth it for the academy line, which is the card's masthead."""
    x, y = xy
    for c in text:
        draw.text((x, y), c, font=font, fill=fill)
        x += draw.textlength(c, font=font) + track


def _fit(draw, text, width, role="serif_b", start=56, minimum=22):
    size = start
    while size > minimum:
        f = _font(role, size)
        if draw.textlength(text, font=f) <= width:
            return f
        size -= 1
    return _font(role, minimum)


def _fit_name(draw, name, width, start=64, floor=32):
    """
    Fit the client's name to the full inner width — growing a short name as
    well as shrinking a long one, so every card looks deliberate rather than
    having "Ali Sami" adrift in white space.

    Past the floor it wraps to two lines instead of shrinking further; below
    that size the academy title starts out-ranking the person, which inverts
    the hierarchy the card exists to express.
    """
    f = _fit(draw, name, width, start=start, minimum=floor)
    if draw.textlength(name, font=f) <= width:
        return [name], f

    words = name.split()
    if len(words) < 2:
        return [name], _fit(draw, name, width, start=floor, minimum=18)

    best, best_gap = 1, None
    for i in range(1, len(words)):
        gap = abs(len(" ".join(words[:i])) - len(" ".join(words[i:])))
        if best_gap is None or gap < best_gap:
            best, best_gap = i, gap
    lines = [" ".join(words[:best]), " ".join(words[best:])]

    size = start
    while size > 20:
        f = _font("serif_b", size)
        if max(draw.textlength(l, font=f) for l in lines) <= width:
            return lines, f
        size -= 1
    return lines, _font("serif_b", 20)


def _centre(draw, y, text, font, fill):
    w = draw.textlength(text, font=font)
    draw.text(((CARD_W - w) / 2, y), text, font=font, fill=fill)


# ---------------------------------------------------------------- the card
def build_card(client_id: int, name: str, token: str, plan: str,
               expires_on: str, out_dir="cards", name_ar: str = None,
               class_name: str = None, colour: str = None) -> str:
    """
    A client holds one card per class, so the class name is printed prominently
    and the accent takes that class's colour — at the desk the two cards must
    be tellable apart at a glance.
    """
    os.makedirs(out_dir, exist_ok=True)
    name = (name or "").strip()
    accent = colour or ACCENT

    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    lines, name_font = _fit_name(probe, name, ROW_W)

    # Names now scale up as well as down, so the finished height depends on the
    # content. Draw onto a generous canvas, note where the content ends, then
    # crop and add the frame. Computing the height up front means maintaining
    # the same layout arithmetic in two places, and it drifts.
    card = Image.new("RGB", (CARD_W, 2000), PAPER)
    d = ImageDraw.Draw(card)

    y = 62

    # --- the academy logo, in place of a typeset masthead ---------------
    if os.path.exists(LOGO):
        logo = Image.open(LOGO).convert("RGBA")
        target_w = int(ROW_W * 0.2)
        ratio = target_w / logo.width
        logo = logo.resize((target_w, int(logo.height * ratio)), Image.LANCZOS)
        # Composite rather than paste so the transparent background picks up
        # the card's paper colour instead of a white block.
        card.paste(logo, ((CARD_W - logo.width) // 2, y), logo)
        y += logo.height + 16
    else:
        f = _font("serif_b", 25)
        _centre(d, y, "MB BALLET ACADEMY", f, accent)
        y += 46

    # Which card this is. A client taking two classes carries two cards, and
    # the difference has to be obvious before it is scanned.
    if class_name:
        cf = _fit(d, class_name.upper(), ROW_W, role="serif_b", start=20, minimum=13)
        cw = _tracked_width(d, class_name.upper(), cf, 3.0)
        _draw_tracked(d, ((CARD_W - cw) / 2, y), class_name.upper(), cf, accent, 3.0)
        y += cf.size + 12

    d.line([(CARD_W - 46) / 2, y, (CARD_W + 46) / 2, y], fill=accent, width=2)
    y += 26

    # --- the name, full width -----------------------------------------
    for line in lines:
        _centre(d, y, line, name_font, INK)
        y += name_font.size + 10

    # --- member number, printed to be read and typed --------------------
    y += 16
    lab = _font("mono", 12)
    lw = _tracked_width(d, "MEMBER NUMBER", lab, 2.2)
    _draw_tracked(d, ((CARD_W - lw) / 2, y), "MEMBER NUMBER", lab, MUTE, 2.2)
    y += 20

    num_font = _font("mono", 32)
    num = f"{client_id:05d}"
    nw = _tracked_width(d, num, num_font, 5)
    _draw_tracked(d, ((CARD_W - nw) / 2, y), num, num_font, accent, 5)
    y += 46

    # --- QR on its own tile -------------------------------------------
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                       box_size=10, border=1)
    qr.add_data(token)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color=INK, back_color="#FFFFFF").convert("RGB")
    QR = 520
    qr_img = qr_img.resize((QR, QR), Image.NEAREST)

    pad = 20
    tile = [(CARD_W - QR) // 2 - pad, y, (CARD_W + QR) // 2 + pad, y + QR + pad * 2]
    print(tile)
    d.rectangle(tile, fill="#FFFFFF", outline=RULE, width=1)
    card.paste(qr_img, ((CARD_W - QR) // 2, y + pad))
    y = tile[3] + 40

    # --- details ------------------------------------------------------
    d.line([MARGIN, y, CARD_W - MARGIN, y], fill=RULE, width=1)
    y += 24

    label = _font("mono", 12)
    value = _font("serif_b", 25)
    mid = CARD_W / 2

    d.text((MARGIN, y), "PLAN", font=label, fill=MUTE)
    d.text((mid + 14, y), "VALID UNTIL", font=label, fill=MUTE)
    y += 22

    plan_font = _fit(d, plan or "—", mid - MARGIN - 24, start=25, minimum=13)
    d.text((MARGIN, y), plan or "—", font=plan_font, fill=INK)
    d.text((mid + 14, y), expires_on, font=value, fill=INK)
    y += 46

    # subtle divider between the two columns
    d.line([mid, tile[3] + 40 + 8, mid, y - 4], fill=RULE, width=1)

    d.line([MARGIN, y, CARD_W - MARGIN, y], fill=RULE, width=1)
    y += 20

    # --- token, quietly ------------------------------------------------
    tok_font = _font("mono", 12)
    _centre(d, y, token[:20], tok_font, "#B3ABA0")
    _centre(d, y + 17, token[20:], tok_font, "#B3ABA0")
    y += 46

    _centre(d, y, "Lost this card? We can revoke it and issue a new one.",
            _font("sans", 12), MUTE)
    y += 18

    # --- crop to the content, then frame it ----------------------------
    height = y + FRAME + 26
    card = card.crop((0, 0, CARD_W, height))
    d = ImageDraw.Draw(card)

    # A thin double rule. Reads as a certificate rather than a receipt.
    d.rectangle([FRAME, FRAME, CARD_W - FRAME - 1, height - FRAME - 1],
                outline=RULE, width=1)
    d.rectangle([FRAME + 5, FRAME + 5, CARD_W - FRAME - 6, height - FRAME - 6],
                outline=RULE, width=1)
    d.rectangle([FRAME + 5, height - FRAME - 11, CARD_W - FRAME - 6, height - FRAME - 6],
                fill=accent)

    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in (class_name or "all"))
    path = os.path.join(out_dir, f"client_{client_id:05d}_{slug}.png")
    card.save(path, dpi=(300, 300))
    return path
