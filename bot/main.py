"""Discord front-end: one interactive board message per channel, driven by emoji reactions."""
from __future__ import annotations

import asyncio
import io
import os
from dataclasses import dataclass, field
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

from .game import GameError, GameState, pretty
from .render import render_board

ROOT = Path(__file__).resolve().parent.parent
NUMBERS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
PLAY, LOSE, SHORT, LONG, CANCEL, REROLL = "✅", "🔥", "💤", "🛌", "❌", "🔄"
CONTROLS = [PLAY, LOSE, SHORT, LONG, CANCEL, REROLL]

load_dotenv(ROOT / ".env")
game = GameState(ROOT / os.getenv("STATE_FILE", "scenario.txt"), ROOT / os.getenv("CARDS_DIR", "cards"))
OWNER_ID = int(os.getenv("OWNER_ID", "0")) or None  # optional: only this user may press reactions

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
lock = asyncio.Lock()


@dataclass
class Session:
    message: discord.Message
    selected: list[str] = field(default_factory=list)  # cards queued to play
    long_rest: bool = False  # waiting for the player to pick the discard card to lose
    reroll_snapshot: object = None  # state before a short rest, for the 1-damage redraw
    status: str = "Pick cards with the number reactions, then ✅ (discard) or 🔥 (lose)."


sessions: dict[int, Session] = {}  # channel id -> latest board


def names(cards: list[str]) -> str:
    return ", ".join(f"**{game.number(c)}** {pretty(c)}" for c in cards) or "—"


def content(s: Session) -> str:
    parts = [
        f"✋ **Hand ({len(game.hand)}):** {names(game.hand)}",
        f"🗑️ **Discard ({len(game.discard)}):** {names(game.discard)}",
        f"🔥 **Lost ({len(game.burned)}):** {names(game.burned)}",
    ]
    if game.exhausted:
        parts.append("💀 **EXHAUSTED** — fewer than 2 cards in hand and discard.")
    if s.selected:
        parts.append(f"▶️ **Selected:** {names(s.selected)}")
    parts.append(f"\n{s.status}")
    parts.append(f"-# {'/'.join(NUMBERS[:1])}-{len(game.active)} select · {PLAY} discard · {LOSE} lose · "
                 f"{SHORT} short rest · {LONG} long rest · {CANCEL} cancel · {REROLL} redraw short rest (1 dmg)")
    return "\n".join(parts)


async def refresh(s: Session, highlight=()) -> None:
    img = await asyncio.to_thread(render_board, game, tuple(s.selected), tuple(highlight))
    f = discord.File(io.BytesIO(img), filename="cards.png")
    await s.message.edit(content=content(s), attachments=[f])


async def unreact(s: Session, user_id: int, emojis: list[str]) -> None:
    user = discord.Object(id=user_id)
    for e in emojis:
        try:
            await s.message.remove_reaction(e, user)
        except discord.HTTPException:
            pass


async def open_board(channel) -> Session:
    img = await asyncio.to_thread(render_board, game)
    msg = await channel.send(file=discord.File(io.BytesIO(img), filename="cards.png"))
    s = Session(message=msg)
    sessions[channel.id] = s
    await refresh(s)
    for e in NUMBERS[: len(game.active)] + CONTROLS:
        await msg.add_reaction(e)
    return s


async def handle(s: Session, emoji: str, user_id: int, added: bool) -> None:
    numbers = NUMBERS[: len(game.active)]
    # --- number reactions ---
    if emoji in numbers:
        card = game.active[numbers.index(emoji)]
        if s.long_rest:
            if not added:
                return
            if card not in game.discard:
                await unreact(s, user_id, [emoji])
                s.status = f"⚠️ Long rest: {pretty(card)} is not in your discard pile — pick a discard card to lose (or {CANCEL})."
            else:
                game.long_rest(card)
                s.long_rest, s.reroll_snapshot = False, None
                s.status = f"🛌 Long rest done. Lost **{pretty(card)}**; the rest of your discard returned to hand."
                await unreact(s, user_id, [emoji])
                await refresh(s, highlight=[card])
                return
            await refresh(s)
            return
        if added:
            if card in game.hand:
                if card not in s.selected:
                    s.selected.append(card)
            else:
                await unreact(s, user_id, [emoji])
                s.status = f"⚠️ {pretty(card)} is not in your hand."
        elif card in s.selected:
            s.selected.remove(card)
        await refresh(s)
        return

    if not added:
        return
    await unreact(s, user_id, [emoji])

    if emoji in (PLAY, LOSE):
        if s.long_rest:
            s.status = f"⚠️ Finish your long rest first (pick a discard card) or press {CANCEL}."
        elif not s.selected:
            s.status = "⚠️ Select cards with the number reactions first."
        else:
            cards, s.selected = s.selected, []
            game.play(cards, lose=emoji == LOSE)
            s.reroll_snapshot = None
            s.status = f"{'🔥 Lost' if emoji == LOSE else '✅ Discarded'}: {names(cards)}"
            await unreact(s, user_id, [NUMBERS[game.number(c) - 1] for c in cards])
    elif emoji == SHORT:
        snap = game.snapshot()
        lost = game.short_rest()
        s.reroll_snapshot, s.long_rest = snap, False
        s.status = (f"💤 Short rest: randomly lost **{pretty(lost)}**; the rest of your discard returned to hand.\n"
                    f"Press {REROLL} to take 1 damage and redraw a different random card instead.")
        await unreact(s, user_id, [NUMBERS[game.number(c) - 1] for c in s.selected])
        s.selected = []
        await refresh(s, highlight=[lost])
        return
    elif emoji == REROLL:
        if s.reroll_snapshot is None:
            s.status = "⚠️ Nothing to redraw — do a short rest first."
        else:
            game.restore(s.reroll_snapshot)
            lost = game.short_rest()
            s.reroll_snapshot = None
            s.status = f"🔄 (−1 HP) Redrew: lost **{pretty(lost)}** instead."
            await refresh(s, highlight=[lost])
            return
    elif emoji == LONG:
        if len(game.discard) < 2:
            s.status = "⚠️ You need at least 2 cards in your discard pile to rest."
        else:
            await unreact(s, user_id, [NUMBERS[game.number(c) - 1] for c in s.selected])
            s.selected, s.long_rest, s.reroll_snapshot = [], True, None
            s.status = f"🛌 **Long rest:** react with the number of the DISCARD card to lose ({CANCEL} to cancel)."
    elif emoji == CANCEL:
        s.long_rest = False
        s.status = "Cancelled."
    await refresh(s)


async def on_reaction(payload: discord.RawReactionActionEvent, added: bool) -> None:
    if payload.user_id == bot.user.id:
        return
    s = next((x for x in sessions.values() if x.message.id == payload.message_id), None)
    if s is None or (OWNER_ID and payload.user_id != OWNER_ID):
        return
    async with lock:
        try:
            await handle(s, str(payload.emoji), payload.user_id, added)
        except GameError as e:
            s.status = f"⚠️ {e}"
            await refresh(s)
        except discord.HTTPException as e:
            print("Discord error:", e)


@bot.event
async def on_raw_reaction_add(payload):
    await on_reaction(payload, True)


@bot.event
async def on_raw_reaction_remove(payload):
    await on_reaction(payload, False)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}. Active cards: {', '.join(game.active)}")


def resolve(arg: str) -> str:
    arg = arg.strip().lower().replace(" ", "-")
    if arg.isdigit() and 1 <= int(arg) <= len(game.active):
        return game.active[int(arg) - 1]
    for c in game.all_cards():
        if arg in (c, c.removeprefix("fc-")) or arg in c:
            return c
    raise GameError(f"No card matches '{arg}'.")


async def refresh_channel(ctx) -> None:
    s = sessions.get(ctx.channel.id)
    if s:
        await refresh(s)


@bot.command(name="cards", aliases=["hand", "board"])
async def cards_cmd(ctx):
    """Post a fresh interactive card board in this channel."""
    async with lock:
        game.load()
        await open_board(ctx.channel)


@bot.command(name="card")
async def card_cmd(ctx, *, which: str):
    """Show one card at full size: !card 3 or !card ray of light"""
    try:
        c = resolve(which)
    except GameError as e:
        return await ctx.send(f"⚠️ {e}")
    where = game.location(c) if c in game.active else "unavailable"
    await ctx.send(f"**{pretty(c)}** ({where})", file=discord.File(game.cards_dir / f"{c}.png"))


@bot.command(name="move")
async def move_cmd(ctx, dest: str, *, which: str):
    """Fix a mistake: !move hand|discard|lost <card number or name>"""
    dest = {"lost": "burned", "burned": "burned", "hand": "hand", "discard": "discard"}.get(dest.lower())
    if not dest:
        return await ctx.send("Usage: `!move hand|discard|lost <card number or name>`")
    try:
        async with lock:
            game.move(resolve(which), dest)
            await refresh_channel(ctx)
        await ctx.message.add_reaction("✅")
    except GameError as e:
        await ctx.send(f"⚠️ {e}")


@bot.command(name="reset")
async def reset_cmd(ctx):
    """Start over: every active card back in hand."""
    async with lock:
        game.reset()
        s = sessions.get(ctx.channel.id)
        if s:
            s.selected, s.long_rest, s.reroll_snapshot = [], False, None
            s.status = "🔄 Game reset — all cards in hand."
            await refresh(s)
    await ctx.message.add_reaction("✅")


@bot.command(name="help")
async def help_cmd(ctx):
    await ctx.send(
        "**Casandra card manager**\n"
        "`!cards` – post the card board (react to play)\n"
        "`!card <n|name>` – full-size card image\n"
        "`!move hand|discard|lost <n|name>` – fix a mistake\n"
        "`!reset` – everything back to hand\n"
        f"Reactions: number = select card · {PLAY} discard selected · {LOSE} lose selected · "
        f"{SHORT} short rest · {LONG} long rest · {CANCEL} cancel · {REROLL} redraw short rest"
    )


def run() -> None:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("Set DISCORD_TOKEN (in the environment or a .env file). See README.md.")
    bot.run(token)


if __name__ == "__main__":
    run()
