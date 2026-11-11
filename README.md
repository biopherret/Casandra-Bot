# Casandra card bot

A Discord bot for tracking a custom Gloomhaven class's cards (hand / discard / lost) and doing rests with emoji reactions.

## Setup
1. Create a bot at <https://discord.com/developers/applications>, enable **Message Content Intent**, and invite it to your server with the permissions *Send Messages, Attach Files, Add Reactions, Manage Messages* (it needs Manage Messages to clear your reactions after each action).
2. `pip install -r requirements.txt`
3. `cp .env.example .env` and put your bot token in `DISCORD_TOKEN` (optionally your Discord user id in `OWNER_ID` so only you can press the reactions).
4. Before playing, edit `## Active list` in `scenario.txt` (swap one card for `fc-call-of-the-nether`). Cards not in a pile start in hand.
5. `python -m bot.main`

## Use
Type `!cards` to post the board: an image of every active card grouped into **In hand / Discard / Lost**, each with a fixed number badge (1–N, the order of the active list), plus a text list. React to the board message:

| Reaction | Action |
|---|---|
| 1️⃣–🔟 | select / deselect a card (gold outline) |
| ✅ | play selected cards → discard |
| 🔥 | play selected cards → lost |
| ↩️ | return the selected discard/lost cards to hand |
| 💤 | short rest: a random discard card is lost, the rest return to hand |
| 🔄 | after a short rest: take 1 damage and redraw a different random card |
| 🛌 | long rest: then react with the number of the **discard** card to lose; the rest return to hand |
| ❌ | cancel a long rest |

Rests need at least 2 cards in the discard pile; the board warns when you're exhausted.
Other commands: `!card <n|name>` (full-size card), `!move hand|discard|lost <n|name>` (fix a mistake), `!reset`, `!help`.

State is saved to `scenario.txt` after every action (the `Unavailable` section is regenerated from the cards folder), so you can restart the bot any time — just run `!cards` again for a fresh board.
