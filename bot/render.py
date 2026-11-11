"""Draws the card board (hand / discard / lost) as one PNG with number badges."""
from __future__ import annotations

import io
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from .game import GameState

WIDTH = 1180
GAP = 12
BG = (32, 34, 40)
PANEL = (44, 47, 56)
GOLD = (255, 200, 40)
SECTIONS = (
    # attr, title, thumbnail width, colour
    ("hand", "IN HAND", 210, (90, 200, 120)),
    ("discard", "DISCARD", 140, (230, 160, 60)),
    ("burned", "LOST", 140, (220, 80, 80)),
)


@lru_cache(maxsize=8)
def _font(size: int):
    for name in ("DejaVuSans-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default(size)


@lru_cache(maxsize=64)
def _thumb(path: str, width: int) -> Image.Image:
    img = Image.open(path).convert("RGBA")
    return img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)


def render_board(game: GameState, selected=(), highlight=()) -> bytes:
    """selected = cards ringed in gold (queued to play); highlight = cards ringed in white."""
    layout = []  # (title, colour, cards, w, cols, rows, h)
    y = GAP
    for attr, title, w, colour in SECTIONS:
        cards = getattr(game, attr)
        cols = max(1, (WIDTH - GAP) // (w + GAP))
        rows = max(1, -(-len(cards) // cols))
        h = round(887 * w / 633)
        layout.append((title, colour, cards, w, cols, y, h))
        y += 40 + rows * (h + GAP) if cards else 40 + 30
    height = y + GAP

    canvas = Image.new("RGB", (WIDTH, height), BG)
    d = ImageDraw.Draw(canvas)
    for title, colour, cards, w, cols, top, h in layout:
        d.text((GAP, top), f"{title} ({len(cards)})", fill=colour, font=_font(26))
        if not cards:
            d.text((GAP + 4, top + 38), "- empty -", fill=(120, 124, 135), font=_font(18))
            continue
        for i, card in enumerate(cards):
            x = GAP + (i % cols) * (w + GAP)
            cy = top + 40 + (i // cols) * (h + GAP)
            img = _thumb(str(game.cards_dir / f"{card}.png"), w)
            if card in selected or card in highlight:
                ring = GOLD if card in selected else (255, 255, 255)
                d.rounded_rectangle((x - 5, cy - 5, x + w + 5, cy + h + 5), 10, outline=ring, width=5)
            canvas.paste(img, (x, cy), img)
            r = 20 if w > 150 else 16
            d.ellipse((x + 4, cy + 4, x + 4 + 2 * r, cy + 4 + 2 * r), fill=(20, 20, 24), outline=colour, width=3)
            d.text((x + 4 + r, cy + 4 + r), str(game.number(card)), fill="white", font=_font(r + 4), anchor="mm")
    buf = io.BytesIO()
    canvas.save(buf, "PNG", optimize=True)
    return buf.getvalue()
