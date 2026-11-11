"""Game-state logic for the card manager (no Discord code in here).

State lives in a plain text file (scenario.txt) with these sections:

    ## Active list   - the cards chosen for this scenario (defines the card numbers 1..N)
    ## In-hand
    ## Discard
    ## Burned        - lost cards
    ## Unavailable   - every other card in the cards folder (rewritten by the bot)

Lines starting with a single '#' are comments. Any active card that is not in a
pile yet (e.g. a fresh game) starts in hand; cards in a pile that are no longer
in the active list are dropped, so swapping a card in the active list before
play "just works".
"""
from __future__ import annotations

import copy
import random
from pathlib import Path

HEADER = "## File should store the hand state of the character."
SECTIONS = ("Active list", "In-hand", "Discard", "Burned", "Unavailable")
COMMENTS = {
    "Active list": "# Reminder - user should replace one of these with Call of the nether before playing",
    "In-hand": "# Starts as all 9",
    "Unavailable": "# every other card in the cards folder",
}


class GameError(Exception):
    """A move that is not allowed; the message is shown to the player."""


IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
MAX_PIECES = 13  # Discord allows 20 reactions per message; 7 are controls


def pretty(card: str) -> str:
    if " " in card or card != card.lower():  # items keep their file name
        return card
    name = card[3:] if card.startswith("fc-") else card
    return name.replace("-", " ").title()


class GameState:
    def __init__(self, state_path: Path, cards_dir: Path, items_dir: Path | None = None):
        self.path = Path(state_path)
        self.cards_dir = Path(cards_dir)
        self.items_dir = Path(items_dir) if items_dir else None
        self.active: list[str] = []  # ability cards (numbered 1..N)
        self.items: list[str] = []  # every file in the items folder (lettered A, B, ...)
        self.hand: list[str] = []
        self.discard: list[str] = []
        self.burned: list[str] = []
        self.load()

    # ---------- persistence ----------
    def all_cards(self) -> list[str]:
        return sorted(p.stem for p in self.cards_dir.glob("*.png"))

    def _item_files(self) -> dict[str, Path]:
        if not self.items_dir or not self.items_dir.is_dir():
            return {}
        files = [p for p in self.items_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS]
        return {p.stem: p for p in sorted(files, key=lambda p: p.stem.lower())}

    @property
    def pieces(self) -> list[str]:
        """Everything that shows up on the board: cards first, then items."""
        return self.active + self.items

    def image_path(self, piece: str) -> Path:
        if piece in self._item_paths:
            return self._item_paths[piece]
        return self.cards_dir / f"{piece}.png"

    def is_item(self, piece: str) -> bool:
        return piece in self.items

    @property
    def unavailable(self) -> list[str]:
        return [c for c in self.all_cards() if c not in self.active]

    def load(self) -> None:
        sections: dict[str, list[str]] = {}
        current = None
        if self.path.exists():
            for raw in self.path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if line.startswith("## "):
                    current = line[3:].strip().lower()
                    sections.setdefault(current, [])
                elif line and not line.startswith("#") and current is not None:
                    sections[current].append(line.lower().removesuffix(".png"))
        self._item_paths = self._item_files()
        self.items = list(self._item_paths)
        card_names = self.all_cards()
        clash = {c.lower() for c in card_names} & {i.lower() for i in self.items}
        if clash:
            raise GameError(f"Card and item share a file name: {', '.join(sorted(clash))}")
        lookup = {n.lower(): n for n in card_names + self.items}

        def get(name: str, allowed: list[str]) -> list[str]:
            out: list[str] = []
            for raw in sections.get(name.lower(), []):
                key = raw.lower()
                for ext in IMAGE_EXTS:
                    key = key.removesuffix(ext)
                c = lookup.get(key)
                if c is None:
                    raise GameError(f"scenario file lists '{raw}' but no such card or item file exists")
                if c not in allowed:
                    raise GameError(f"'{raw}' is not allowed in the {name} section")
                if c not in out:
                    out.append(c)
            return out

        self.active = get("Active list", card_names)
        if len(self.active) > 10:
            raise GameError("The active list can hold at most 10 cards (Discord has 10 number reactions).")
        if len(self.pieces) > MAX_PIECES:
            raise GameError(f"{len(self.active)} cards + {len(self.items)} items is too many: Discord allows "
                            f"20 reactions per message, so cards + items can be at most {MAX_PIECES}.")
        used: set[str] = set()
        piles = {}
        everything = card_names + self.items
        for key, name in (("hand", "In-hand"), ("discard", "Discard"), ("burned", "Burned")):
            piles[key] = [c for c in get(name, everything) if c in self.pieces and c not in used]
            used.update(piles[key])
        self.hand, self.discard, self.burned = piles["hand"], piles["discard"], piles["burned"]
        # anything active (cards and items) that is in no pile starts in hand
        self.hand += [c for c in self.pieces if c not in used]
        self._sort()
        self.save()

    def _sort(self) -> None:
        order = {c: i for i, c in enumerate(self.pieces)}
        for pile in (self.hand, self.discard, self.burned):
            pile.sort(key=order.__getitem__)

    def save(self) -> None:
        self._sort()
        piles = {
            "Active list": self.active,
            "In-hand": self.hand,
            "Discard": self.discard,
            "Burned": self.burned,
            "Unavailable": self.unavailable,
        }
        lines = [HEADER, ""]
        for name in SECTIONS:
            lines.append(f"## {name}")
            lines.extend(piles[name])
            if name in COMMENTS:
                lines.append(COMMENTS[name])
            lines.append("")
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("\n".join(lines), encoding="utf-8")
        tmp.replace(self.path)

    # ---------- helpers ----------
    def label(self, piece: str) -> str:
        """Stable badge: 1..N for cards (active list order), A, B, ... for items."""
        i = self.pieces.index(piece)
        return str(i + 1) if i < len(self.active) else chr(ord("A") + i - len(self.active))

    def location(self, card: str) -> str:
        for name in ("hand", "discard", "burned"):
            if card in getattr(self, name):
                return name
        raise GameError(f"{pretty(card)} is not an active card or item")

    def snapshot(self):
        return copy.deepcopy((self.hand, self.discard, self.burned))

    def restore(self, snap) -> None:
        self.hand, self.discard, self.burned = copy.deepcopy(snap)
        self.save()

    def _cards_in(self, pile: list[str]) -> list[str]:
        return [c for c in pile if c not in self.items]

    @property
    def exhausted(self) -> bool:
        """Only ability cards count, not items."""
        return len(self._cards_in(self.hand)) < 2 and len(self._cards_in(self.discard)) < 2

    # ---------- moves ----------
    def play(self, cards: list[str], lose: bool) -> None:
        for c in cards:
            if c not in self.hand:
                raise GameError(f"{pretty(c)} is not in your hand")
        for c in cards:
            self.hand.remove(c)
            (self.burned if lose else self.discard).append(c)
        self.save()

    def short_rest(self) -> str:
        """Lose one random discard card; the rest of the discard returns to hand."""
        pool = self._cards_in(self.discard)
        if len(pool) < 2:
            raise GameError("You need at least 2 cards (not counting items) in your discard pile to rest.")
        lost = random.choice(pool)  # items are never part of the random draw
        self.discard.remove(lost)
        self.burned.append(lost)
        self.hand += self.discard
        self.discard = []
        self.save()
        return lost

    def long_rest(self, lose: str) -> None:
        """Lose a chosen discard card; the rest of the discard returns to hand."""
        if len(self._cards_in(self.discard)) < 2:
            raise GameError("You need at least 2 cards (not counting items) in your discard pile to rest.")
        if lose not in self.discard:
            raise GameError(f"{pretty(lose)} is not in your discard pile")
        self.discard.remove(lose)
        self.burned.append(lose)
        self.hand += self.discard
        self.discard = []
        self.save()

    def move(self, card: str, dest: str) -> None:
        """Manual correction: put a card in hand / discard / burned."""
        self.location(card)
        for name in ("hand", "discard", "burned"):
            pile = getattr(self, name)
            if card in pile:
                pile.remove(card)
        getattr(self, dest).append(card)
        self.save()

    def reset(self) -> None:
        self.hand, self.discard, self.burned = list(self.pieces), [], []
        self.save()
