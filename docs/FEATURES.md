# Features

What the toolkit does, and what each setting is for. For exact commands see **[COMMANDS.md](COMMANDS.md)**.
For the moderation tools in depth see **[MODERATION.md](MODERATION.md)**. For how it's wired together see
**[ARCHITECTURE.md](ARCHITECTURE.md)**.

It's three programs working together: a C# plugin inside the game server, a Python bot on your PC that
owns all the saved data, and a browser command centre. Together they add progression, fair matches,
moderation, and live oversight to a stock dedicated server.

Most settings apply live from the command centre. A few need a restart to take effect, and those are
marked **(restart)** below.

---

## Persistent ranks

**Purpose: reward time and scoring over the long term.** The more a player plays, and the more they
score across matches, the higher their lifetime rank climbs. It's the loyalty and progression metric,
and it is *not* used to balance teams.

Every player gets a permanent profile tied to their SteamID. Lifetime points sit on an 11-rank ladder
(Officer Cadet up to Air Chief Marshal). Points come from real in-game score plus bonuses for wins,
top-three placement, showing up at kickoff, and PvP kills. Crossing a tier announces a colour-coded
**RANK UP** and updates the player's name tag, scoreboard, and kill feed. The bot owns the data and
snapshots it daily.

**Settings**
- **Win Points** — points to every player on the winning side at match end.
- **1st / 2nd / 3rd Place Bonus** — bonus points for the top three scorers of the match.
- **Start-of-Match Bonus** — points to everyone present at kickoff (fires at the one-minute mark). *(restart)*
- **Start-Bonus Window** — how long after a mission starts still counts as "kickoff" for that bonus. *(restart)*
- **PvP Kill Bonus** — bonus points for downing an enemy player in PvP. *(restart)*
- **Underdog Kill Bonus per Player** — extra kill points for each player your side is outnumbered by. *(restart)*

## Real per-player scoring

A stock server only reports faction scores, never per-player. The plugin runs inside the game, reads
each player's real internal score, and streams it to the bot. So ranks and skill use the game's own
numbers, not estimates. Radar-spotting and jamming rewards are filtered out to kill a score-farming
exploit. Kills, captures, supply, repair, and rescue all count.

## NuclearSkill rating

**Purpose: reward flying well, and balance teams fairly.** Skill measures how good a player is in the
air: scoring on a sortie and getting back without being shot down. It is separate from rank, and it is
what team balance uses to decide who to move. A new player who flies well is weighted by real ability,
not by playtime.

The rating is your average points **per life** (spawn until you're shot down or eject). It survives
disconnects and match ends, counts after 5 lives, and shows as a clean 0–10. Players check it with
`!skill`.

**Settings**
- **Capture Skill Bonus** — skill points added to a life's score for capturing a base.
- **Win Skill Bonus** — skill points added to a winner's final life at match end.
- **Loss Skill Bonus** — skill points added to a loser's final life at match end.
- **Balance by Skill Rating** — team balance picks who to move by skill rating instead of rank. (Leave on.)

## PvP team balance

**Purpose: keep PvP matches fair.** It stops one side badly outnumbering the other, and uses skill (not
rank) to even the teams out. PvE is never balanced.

Joining the fuller side bounces you straight to spectate. If someone *leaving* unbalances the teams, the
system warns the lobby, waits, then swaps one player from the bigger side. It picks the player who best
evens the skill totals, and keeps their points and skill-life through an over-ocean swap. New joiners
and `!squadup` groups are protected, and anyone moved recently sits out the next couple of games.

**Settings**
- **Team Balance Enabled** — turn PvP balancing on or off.
- **Max Team Size Gap** — how far one side may lead before balancing acts (2 = a 2-player gap is fine).
- **Auto-Move Players** — actually move the best-fit player. Off = only block joins to the fuller side.
- **Only Move Players in Spawn Menu** — only move players who aren't currently flying.
- **Balance by Skill Rating** — choose who to move by skill rating, not rank.
- **Min Players for Balancing** — don't balance below this many humans. Small lobbies are left alone.
- **Balance Warning Hold** — warn the lobby, then wait this long before moving anyone (default 5 min).
- **Move Cooldown (Games)** — once moved, a player isn't moved again for this many games.
- **New-Joiner Protection** — never move a player who joined less than this long ago (default 15 min). 0 = off.
- **Balance Check Interval** — seconds between balance checks.
- **Min Seconds Between Moves** — minimum gap between two moves, to stop churn.
- **Team-Swap Spawn Altitude** — height the brief swap aircraft spawns at before ejecting. Raise it if you see a crash.

## Squads

**Purpose: let friends stay on the same team.** A squad gets a light protection from being split by
balancing.

**Settings**
- **Max Squad Size** — most players in a `!squadup` group.
- **Squad Invite Timeout** — how long an invite stays open to accept with `!y`.

## Match flow

**Purpose: shape how a match starts, ends, and rotates.** Sets mission length, the map vote, timeouts,
and the PvP funds floor.

When a mission ends the bot opens a map vote in chat for the next mission. A timed-out PvE co-op can be
ruled a defeat, and a timed-out PvP match can be decided on score. PvP matches start every player at a
minimum in-game rank so nobody is stuck with a starter loadout.

**Settings**
- **Mission Time Limit** — mission length in seconds (default 3h). Must match the server's MaxTime. *(restart)*
- **Map-Vote Ballot Length** — how long the end-of-match map vote stays open. *(restart)*
- **!votemap Poll Length** — length of the mid-match `!votemap` yes/no poll. *(restart)*
- **PvP Timeout Decides Winner** — on a PvP timeout, the higher total score wins (exact tie = draw). Off = just rotate.
- **Timeout Lead (before mission end)** — fire the timeout result this many seconds early, so the map vote can run before the game auto-rotates.
- **PvP Starting Rank Floor** — every player starts a PvP match at at least this in-game rank. 0 = off; PvE unaffected.
- **Forfeit Voting Enabled** — let a PvP team vote to surrender with `!forfeit` (majority needed).
- **Forfeit Vote Cooldown** — seconds before a team can start another forfeit vote.

## Map voting

**Purpose: let players pick the next mission.** Every map change is decided in chat, no admin needed.

At the end of a mission the bot posts a ballot of options; players vote in chat for 60 seconds. Anyone
can also call a mid-match vote with `!votemap`, which runs a quick yes/no approval poll first. (Lengths
are set under **Match flow** above.)

## AI aircraft limiter

**Purpose: protect framerate and keep airfields clear.** It caps AI aircraft and removes ones stuck on
the runway, so high player counts stay smooth without an admin culling by hand. It only ever removes
AI, never players.

**Settings**
- **AI Limiter Enabled** — turn the limiter on or off.
- **AI Per-Team Cap** — most AI aircraft flying per faction; the excess (grounded first) is removed.
- **Total Aircraft Cap** — most aircraft total (AI + players). Over the cap, AI is removed from the busiest side.
- **Stuck-AI Clear Time** — a grounded AI that hasn't moved for this long is cleared off the runway. 0 = off.
- **Stuck-AI Move Radius** — how far a grounded AI must move to count as "not stuck".

## Performance & cleanup

**Purpose: keep the server responsive and tidy.**

**Settings**
- **Server Tick Rate (Hz)** — engine updates per second. Higher = snappier AI and missile reactions, at more CPU cost (default 60). *(restart — needs `run.bat --rewrite-wrapper` then a server restart.)*
- **Stats Snapshot Interval** — seconds between full per-player stats snapshots sent to the bot.
- **Clean Up Ejected Pilots** — despawn lingering ejected pilots to cut clutter.
- **Ejected Pilot Lifetime** — how long a dismounted pilot may linger before cleanup.

## Chat, kill feed & messages

**Purpose: make chat readable and the kill feed useful.**

Player chat can show each player's rank, ordinary swearing is left alone while slurs are blocked, and
the spammy native kill feed is replaced with the things players actually care about.

**Settings**
- **Embed Rank in Player Name** — puts the rank tag inside the name (e.g. `[ACM] Brick`) so native chat and text-to-speech still work.
- **Reformat Chat as [Name - Rank]** — alternative chat format showing name and rank in their rank colour. (Overridden by the option above.)
- **Slur Filter** — replaces a whole message if it contains a racist slur. Normal swearing is untouched.
- **Custom Kill Feed** — hides the AI-spam native feed and instead announces kill streaks and carrier/destroyer sinks.

## Server-message manager

**Purpose: run your own automated chat without touching code.** Advertise a Discord, post rules, or
nudge players on a schedule.

Define messages that fire every few minutes, daily at a set time, or on match start and end, with an
optional colour. You can also re-word, re-time, or disable the built-in messages (welcome, periodic
nudge, rankings post, spectate tip).

## Live tactical map

**Purpose: see the whole battle in one view.** Admins and spectators can tell where everyone is.

The command centre shows a top-down battle map on a calibrated terrain image: faction base rings, and
smooth blips for every player (named), AI aircraft (unnamed, so you can tell bot from human), and ship.
Pan, zoom, go fullscreen, read a grid reference under the cursor.

## Web command centre

**Purpose: run the whole server from one browser tab.** Live status, a player table, an activity feed,
a filtered console, the kill feed, and the map.

From here you broadcast chat, change or end maps, grant points, move/kick/ban players, schedule
restarts, and edit settings live. It's read-only on the rank data: every change is queued to the bot,
the single owner, so nothing races. It also has real power control wired to your hosting panel.

## Scheduling

**Purpose: restart or update on your terms, without dropping players unannounced.**

Schedule a one-off restart or plugin update for a chosen time. The bot warns players 5 and 1 minute
before, runs a guarded restart, and confirms the server came back before finishing.

## Moderation: teamkill & anti-grief

**Purpose: deal with friendly fire and deliberate griefing automatically.**

Friendly fire is punished on an escalating per-match ladder: eject and warn, then kick and rank reset,
then a persistent ban. A separate two-factor anti-grief auto-kick removes the one player trying to
flood and crash the server, not the lobby, while exempting admins and never false-kicking. Full detail
in **[MODERATION.md](MODERATION.md)**.

**Settings**
- **Teamkill Punishment** — turn the friendly-fire ladder on or off.
- **Admin SteamIDs** — SteamIDs allowed to use in-game team commands (`!move`, `!spec`, `!join`, `!balance`).
- **In-Game Vote-Kick** — the game's own player vote-to-kick. *(restart)*
- **Auto-Kick Griefers** — auto-kick a single player mass-commanding units to brick the server, and file a report.
- **Require Active Flooding** — kick only on a sustained order rate (recommended), not just for owning lots of units.
- **Flood Order Rate (per sec)** — the sustained order rate that trips the auto-kick.
- **Owned-Unit Kick Threshold** — only used when Require Active Flooding is off: kick anyone owning more units than this.
- **Hard-Ban on Trip** — also ban a tripped offender, not just kick once. Default off.
- **Report Only (no kick)** — detect and report but don't kick, to validate the threshold first.
- **Exempt Admins from Auto-Kick** — never auto-kick an admin (recommended on).

## Flood guard

**Purpose: stop unit-order spam from mass-disconnecting the lobby.** This fixed a recurring
match-start crash, and it shields against deliberate command-flooding.

**Settings**
- **Flood Guard Enabled** — rate-limit fleet move-orders. *(restart — gates a patch.)*
- **Fleet Orders Per Second** — sustained orders accepted per second per player (a human issues well under 1).
- **Fleet Order Burst** — how many orders may burst before the excess is dropped. Early orders are never dropped.
- **Log Dropped Orders** — log who is being throttled.
- **Drop Dead-NetId RPCs** — drop orders aimed at already-destroyed objects, a disconnect-storm amplifier. *(restart — gates a patch.)*
- **Unit Command Policy** — which units players may move. Default allows only player-deployed ground vehicles, which blocks the worst flooding.
- **Command Allowlist (jsonKeys)** — when the policy is set to allowlist, the exact unit types to allow.
- **Command Policy Diagnostics** — log each order's unit type and allow/drop decision. Turn on briefly to find unit names, then off.
- **Raise Reliable Send Buffer** — raise the per-connection send buffer so a command burst is absorbed instead of disconnecting everyone. Leave on. *(restart)*
- **Reliable Send Buffer Limit** — the buffer cap (game default 3000; toolkit default 12000). *(restart)*

## PvE rules

**Purpose: give co-op a real win-or-lose result.**

**Settings**
- **PvE Timeout = Defeat** — when a co-op mission timer runs out and the humans haven't won, rule it a defeat instead of silently rotating.

## Public server directory (opt-in)

**Purpose: help players find your server.** Nuclear Option has no direct-connect, so the directory
lists your server by **name and region** and players look it up by name in-game.

It's off by default. When you turn it on, only your server's name, region, and plugin version are
published, never its IP or any player data. The live listing is on the project's
[main page](../README.md) and the [public directory](https://tomosbombos.github.io/nuclear-option-servers/).

**Settings**
- **List Server Publicly** — opt in to the public directory. Set your region too.
- **Server Region** — your region for the directory (required when listing is on).
