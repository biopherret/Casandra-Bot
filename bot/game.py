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


def pretty(card: str) -> str:
    name = card[3:] if card.startswith("fc-") else card
    return name.replace("-", " ").title()


class GameState:
    def __init__(self, state_path: Path, cards_dir: Path):
        self.path = Path(state_path)
        self.cards_dir = Path(cards_dir)
        self.active: list[str] = []
        self.hand: list[str] = []
        self.discard: list[str] = []
        self.burned: list[str] = []
        self.load()

    # ---------- persistence ----------
    def all_cards(self) -> list[str]:
        return sorted(p.stem for p in self.cards_dir.glob("*.png"))

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
        known = set(self.all_cards())

        def get(name: str) -> list[str]:
            out: list[str] = []
            for c in sections.get(name.lower(), []):
                if c not in known:
                    raise GameError(f"scenario file lists '{c}' but cards/{c}.png does not exist")
                if c not in out:
                    out.append(c)
            return out

        self.active = get("Active list")
        if len(self.active) > 10:
            raise GameError("The active list can hold at most 10 cards (Discord has 10 number reactions).")
        used: set[str] = set()
        piles = {}
        for key, name in (("hand", "In-hand"), ("discard", "Discard"), ("burned", "Burned")):
            piles[key] = [c for c in get(name) if c in self.active and c not in used]
            used.update(piles[key])
        self.hand, self.discard, self.burned = piles["hand"], piles["discard"], piles["burned"]
        # anything active that is in no pile starts in hand
        self.hand += [c for c in self.active if c not in used]
        self._sort()
        self.save()

    def _sort(self) -> None:
        order = {c: i for i, c in enumerate(self.active)}
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
    def number(self, card: str) -> int:
        """Stable 1-based number of a card (position in the active list)."""
        return self.active.index(card) + 1

    def location(self, card: str) -> str:
        for name in ("hand", "discard", "burned"):
            if card in getattr(self, name):
                return name
        raise GameError(f"{card} is not an active card")

    def snapshot(self):
        return copy.deepcopy((self.hand, self.discard, self.burned))

    def restore(self, snap) -> None:
        self.hand, self.discard, self.burned = copy.deepcopy(snap)
        self.save()

    @property
    def exhausted(self) -> bool:
        return len(self.hand) < 2 and len(self.discard) < 2

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
        if len(self.discard) < 2:
            raise GameError("You need at least 2 cards in your discard pile to rest.")
        lost = random.choice(self.discard)
        self.discard.remove(lost)
        self.burned.append(lost)
        self.hand += self.discard
        self.discard = []
        self.save()
        return lost

    def long_rest(self, lose: str) -> None:
        """Lose a chosen discard card; the rest of the discard returns to hand."""
        if len(self.discard) < 2:
            raise GameError("You need at least 2 cards in your discard pile to rest.")
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
        self.hand, self.discard, self.burned = list(self.active), [], []
        self.save()
