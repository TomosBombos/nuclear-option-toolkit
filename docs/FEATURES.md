# Features

What the toolkit does, in plain English. For exact commands see **[COMMANDS.md](COMMANDS.md)**; for the
moderation tools in depth see **[MODERATION.md](MODERATION.md)**; for how it's wired see
**[ARCHITECTURE.md](ARCHITECTURE.md)**.

It's **three cooperating programs** — a C# plugin inside the game server, a Python bot on your PC that
owns all the saved data, and a browser command centre — that together add progression, fair matches,
moderation, and live oversight to a stock dedicated server.

## Persistent ranks & economy
Every player gets a permanent SteamID profile with lifetime points on an **11-tier rank ladder**
(Officer Cadet → Air Chief Marshal). Points come from real in-game performance plus bonuses — match
wins, top-3 placement, a start-of-match bonus, and PvP kill bounties (with an underdog bonus for
downing a higher-ranked pilot). Crossing a tier announces a colour-coded **RANK UP** and updates the
player's name tag, scoreboard, and kill feed. The bot is the single owner of the data and snapshots it
daily.

## Real per-player scoring
A stock server only reports faction scores, never per-player. The plugin runs **inside the game**,
reads each player's real internal score, and streams it to the bot — so ranks and skill are based on
the game's own authoritative numbers, not estimates. (Radar-spotting and jamming rewards are filtered
out to kill a score-farming exploit; kills, captures, supply, repair, and rescue all count.)

## NuclearSkill rating (0–10)
A score for *how well* you fly: your average points **per life** (spawn → shot down or eject). It
survives disconnects and match-ends, becomes official after 5 lives, and shows as a clean 0–10 via
`!skill`. It also drives team balance, so a new player who flies well is weighted by real ability, not
playtime.

## PvP team balance
Keeps sides even (PvE is never balanced). Joining the fuller side is **instantly bounced to
spectate**; if a *leave* unbalances the teams, then after a warning the system **swaps one player**
from the bigger side — chosen to best even the skill totals — keeping their points and skill-life via a
seamless over-ocean swap. **Protection tiers** shield recent joiners (~15 min) and **`!squadup`** friend
groups (up to 4), and anyone moved recently is exempt for a couple of games.

## Map voting
When a mission ends the bot opens a **six-option ballot** (random co-op + fixed PvP) in chat; players
vote `!1`–`!6` for 60 seconds. Any player can call a mid-match vote with **`!votemap`** (a quick yes/no
approval poll first). The community picks the next map entirely through chat.

## AI aircraft limiter
Caps AI aircraft per-team and total, and clears **runway-stuck AI** — **only ever removing AI, never
players**. It keeps framerate healthy and airfields clear at high player counts without an admin culling
by hand. All caps and timings are adjustable live.

## Live tactical map
The command centre shows a **live top-down battle map** on a calibrated terrain image: faction base
rings, and smoothly-gliding blips for every player (named), AI aircraft (unnamed — the bot-vs-human
tell), and ship. Pan, zoom, fullscreen, a hierarchical grid, and a cursor grid-ref give admins and
spectators real situational awareness.

## Web command centre
A browser dashboard (`127.0.0.1:8770`) for one-screen control: live status, a player table, activity
feed, filtered console, kill feed, and the map. From here you broadcast chat, change/end maps, grant
points, move/kick/ban players, schedule restarts, and edit dozens of settings live. It's **read-only on
the rank data** — changes are queued to the bot (the single owner) so nothing races — and has real
**power control** wired to your hosting panel.

## Server-message manager
Define your own **automated chat** (every N minutes, daily at a set time, on match start/end, with an
optional colour), and **re-text / re-time / disable the built-in messages** (welcome, periodic nudge,
auto-leaderboard post, spectate tip). Advertise a Discord or post rules without touching code.

## Scheduling
Schedule **one-off restarts or plugin updates** at a chosen time; the bot warns players 5 and 1 minute
before, then runs the guarded restart, verifying the server came back before finishing.

## Moderation: teamkill & anti-grief
Friendly fire is auto-punished on an escalating per-match ladder (eject → kick + rank reset → ban). A
two-factor **anti-grief auto-kick** removes the one player trying to flood/crash the server — not the
lobby — while exempting admins and never false-kicking. → Full detail in **[MODERATION.md](MODERATION.md)**.

## Global leaderboard & directory (opt-in)
Optionally publish your rankings to a **shared cross-server board** (the `!global` command and a public
page show the top pilots), and list your server — by name + region, never its IP — in a public
directory. Enabling the board locks gameplay settings so everyone competes fairly. Both are **off by
default and inert** until you configure them — the live listings live on the project's
[main page](../README.md).
