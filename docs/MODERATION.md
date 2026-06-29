# Moderation & Enforcement

A detailed guide to the toolkit's moderation tools — teamkill enforcement, anti-grief auto-kick, bans,
votekick, the flood guard, and the Reports tab. For the quick command list see
**[COMMANDS.md](COMMANDS.md)**.

Two things to keep in mind throughout:

- **There are two separate ban lists.**
  - **Plugin ban list** (`plugin_bans.txt`) — written and enforced by the NukeStats plugin (the
    teamkill ladder, anti-grief hard-ban, and the Reports-tab Ban button). The plugin kicks a listed
    SteamID on sight.
  - **Game-native ban list** (`ban_list.txt`) — the game's own list, driven by the `banlist-add` /
    `banlist-remove` commands and enforced by the game itself (it works even with nobody online).

  The web command centre keeps both in sync for explicit owner actions, but the *automated* ladders only
  write the plugin list.

- **Plugin-side actions need a player online to take effect.** While the server is empty the plugin's
  tick driver idles, so a plugin ban/kick/setting relays but doesn't *apply* until someone joins. The
  game-native `banlist-add` and votekick are not subject to this.

---

## 1. Teamkill enforcement

**Setting:** `Teamkill.Enforce` (default **on**, live-tunable).

**How friendly fire is detected.** A hook on every unit death (aircraft, ground vehicles, buildings)
finds the dead unit's **top damager**. It's a teamkill if that top damager is a human player on the
**same faction** as the dead unit (and not the victim's own aircraft). Using the top damager — not the
last hit — is what assigns blame correctly.

**The escalation ladder** (counted per match, per SteamID):

| Offence | Action |
|---|---|
| **1st** | **Eject** from the aircraft (life-neutral — it does *not* count as a death against your skill rating) + a private "first warning". |
| **2nd** | Rank reset to 0 on next sight + a private "next one is a BAN" + a short-delayed **kick**. |
| **3rd+** | Added to the **persistent ban list** (`plugin_bans.txt`) + kicked. |

**Persistence & re-enforcement.** Per-match counters reset on each new mission. Bans persist across
restarts (reloaded when the plugin starts) and are re-enforced — a banned SteamID is kicked on sight
roughly every two seconds, so rejoining doesn't help.

**`!notk`** is a player-facing **policy explainer**, not an opt-out — typing it whispers the full
friendly-fire policy. There is no way for a player to opt out of enforcement.

---

## 2. Anti-grief auto-kick

This protects against the failure mode where a single connection mass-commands units (a held key,
macro, or UI loop) and floods the network buffer, which historically mass-disconnected the **entire
lobby**. The goal: kick **only the one offender**, never the lobby, and never a legitimate
base-builder. It uses **two cooperating detectors** (the "two-factor, misfire-safe" design), plus the
buffer-protection layers below that absorb the flood regardless.

### Detector 1 — plugin-side (owns-units *and* flooding)

Runs every ~2 seconds. Config group `Grief.*`:

| Setting | Default | Meaning |
|---|---|---|
| `Grief.AutoKick` | **on** | Master enable for detect + report + kick. |
| `Grief.RequireActiveFlooding` | **on** | **Two-factor safety.** When on, it kicks only on *sustained move-order flooding*, ignoring the raw unit count. When off, it also trips on merely owning more than the threshold (more aggressive, can false-positive a base-builder). |
| `Grief.FloodOrdersPerSec` | **3** | The order rate (per second) that counts as flooding. Must be held ~4 seconds. A legitimate commander is well under 1/s; the game caps accepted orders at ~5/s, so 3/s sustained means a macro. |
| `Grief.OwnedUnitThreshold` | **12** | Owns more than this many live ground vehicles (only used when `RequireActiveFlooding` is off). |
| `Grief.HardBan` | **off** | If on, a tripped offender is also added to the plugin ban list (kicked on rejoin), not just kicked once. |
| `Grief.ReportOnly` | **off** | Detect + file a report but **don't kick** — use this to validate the threshold for a session before enabling kicks. |
| `Grief.ExemptAdmins` | **on** | Never auto-kick an admin SteamID. |

When it trips it files a **report** (visible in the Reports tab), privately tells the player, optionally
hard-bans, and queues a short-delayed kick. A re-act throttle prevents punishing the same player twice
for one event.

### Detector 2 — bot-side (the "reliable" detector)

The plugin's detector only sees the orders that pass the game's own rate limiter. The bot watches the
**upstream truth**: the game logs one line for *every* dropped over-rate command, and a macro generates
dozens per second. Configured in `grief_flood.json` (edit + restart the bot):

| Setting | Default | Meaning |
|---|---|---|
| `enabled` | on | Master enable. |
| `action` | `kick` | `kick` (recoverable) · `ban` (plugin ban) · `report` (detect-only). |
| `drops_per_window` / `window_sec` | `30` / `3.0` | Trip when one SteamID exceeds 30 dropped commands in 3 seconds (~25/s over the cap — macro only). |
| `cooldown_sec` | `30` | Don't re-act on the same SteamID within this window. |
| `exempt_admins` | on | Never auto-act on an admin. |

When it trips it adds a report and kicks/bans/logs per `action`, writing an `AUTO-KICK` / `AUTO-BAN` /
`FLOOD REPORT` line to the activity feed.

### Why it's misfire-safe

- The plugin path requires a **sustained high order-rate held ~4 seconds** (not a one-off "select-all +
  move once" burst).
- The bot path requires a **sustained storm of the game's own dropped-command lines** (a normal player
  generates ~0).
- Admins are exempt by default in both; a cooldown prevents repeated punishment for one event; both
  default to a **recoverable kick** (not a ban); and a report-only mode lets you validate thresholds
  against real play first.

### The buffer-protection layers (these never kick anyone)

They absorb a flood so it can't mass-disconnect the lobby in the first place:

- **Layer A — `Flood.Enforce`** (on): a per-player token-bucket rate limit that drops the *excess*
  orders of the offending connection server-side; never kicks, never touches other players.
- **Layer B — `Flood.DropDeadNetIdRpcs`** (on): silently drops commands aimed at a dead/unknown unit (a
  log/allocation amplifier under a flood).
- **Layer C — raise reliable send buffer** (on): raises the per-connection send-buffer cap so a
  transient burst is absorbed rather than overflowing into a lobby-wide disconnect.
- **Command policy** (default *heli-dropped only*): restricts which units can be commanded at all, on top
  of the rate limit.

---

## 3. Bans & unbans

### The two lists

| | Plugin ban list (`plugin_bans.txt`) | Game-native ban list (`ban_list.txt`) |
|---|---|---|
| **Written by** | teamkill 3rd offence, anti-grief hard-ban, the `ban` plugin command | the `banlist-add` / `banlist-remove` commands |
| **Enforced by** | the plugin kicking on sight (needs the plugin loaded + a player online) | the game itself (works with nobody online) |
| **Scope** | permanent until removed; survives restarts | permanent until removed |

All bans here are **SteamID-based and permanent** — there is no built-in timed ban and no stored
reason. The split is why the **Reports-tab Ban is the most thorough** (it writes *both* lists and works
even with the server empty), while the **player-popup Ban writes only the game list** and the
**automated ladders write only the plugin list**.

### How an owner bans / unbans

- **In game:** there is no chat ban command (the only player-facing kick is the game's vote-kick, §4).
- **Player popup** (Players table → click a pilot → Kick / Ban): a recoverable **Kick** and a
  game-native **Ban** (`banlist-add`), both confirm-gated.
- **Reports / Moderation tab → Ban:** bans on **both** lists at once (plugin + game), so it's immediate
  and survives an empty server. **Unban** reverses both. SteamIDs are validated before relaying.
- **Banned-players tab:** merges both lists (each row tagged plugin / game), with an **Unban** button and
  a free-text "Unban a SteamID" box to clear a ban that isn't shown in the file listing yet (e.g. a fresh
  vote-kick auto-ban held in memory by the game).

---

## 4. Votekick (the game's built-in player vote-to-kick)

This is the **game's own** feature, configured in `DedicatedServerConfig.json` under `VoteKick` —
separate from the bot's map vote and from all plugin enforcement. The toolkit's documented default is
**off** (owner-enabled).

Key settings: `Enabled`, `PassRatio` (fraction of yes-votes needed), `MinVotes` (minimum to start),
`VoteDuration`, and the **abuse safeguards** `NewVoteLockout` (time between votes), `RequesterCooldown`
(before the same person can start another), and `AutoBanThreshold` (the game auto-bans a player kicked
this many times — into the game's own ban list, which is why the "Unban a SteamID" box exists to clear
it).

**Enable/disable it** with `run.bat --set-votekick on|off`, which surgically flips only `VoteKick.Enabled`
with a full safety check (it re-parses the config and aborts the upload if anything else moved), keeps a
backup, and reloads the config without a full restart. It's also exposed in the ⚙ Settings menu.

---

## 5. The Reports / Moderation tab

Opened from the **🛡 Moderation** header button (the badge shows the outstanding report count). Two
sub-tabs:

**Reports.** Shows anti-grief auto-kick/flag events from both detectors — each with the time, player,
SteamID, reason, units owned, order rate, and the action taken (rendered **banned** / **kicked** /
**flagged**). Per row: **Ban** (confirm-gated — bans on both lists) or **Unban**, and **Clear** (remove
one). A **Clear all** wipes the list. All actions route through the bot (the single writer of the
reports file) so cleared reports don't reappear.

**Banned players.** Lists everyone banned, merged from both lists and tagged with which list each is on,
refreshed on demand. Each row has an **Unban** button (removes from both lists), plus the free-text
**Unban a SteamID** box.

Beyond this tab, the **⚙ Settings → Anti-Grief** group exposes every `Grief.*` knob live (so you can flip
`RequireActiveFlooding`, set `ReportOnly` for a validation night, raise `FloodOrdersPerSec` if you see
false positives, or enable `HardBan`), and **⚙ Settings → Moderation** holds `Teamkill.Enforce` and the
votekick toggle. If you make a setting *more lenient* than recommended, the UI warns you (it doesn't
disqualify you from the global leaderboard, but it can allow command-flooding).

---

## 6. New-joiner & squad protections

These don't punish anyone — they govern **who PvP auto-balance is allowed to move**, so the system
doesn't repeatedly yank new players or pre-made friend groups across teams. Balance moves are
*life-neutral* (they never hurt a skill rating) and PvP-only.

Protection tiers (strongest first), all inside a "don't move the same person twice in two games" rule:

| Tier | Who | Rule |
|---|---|---|
| **New joiner** (strongest) | connected less than `Balance.NewJoinerSeconds` ago (default **900s / 15 min**) | moved only if every other candidate is *also* a new joiner |
| **Squad** | in a `!squadup` group | moved only if no unprotected player is available |
| **Unprotected** | everyone else | picked first; within the tier, the move that best evens total skill is chosen |

Related balance settings: `Balance.Enforce` (on), `Balance.MaxDifference` (default **2** — balancing acts
only when a side is *more than* this many ahead), `Balance.MinPlayers` (default 6 — small lobbies left
alone), `Balance.WarnSeconds` (default 300 — a 5-minute warning before any move), `Skill.BalanceBySkill`
(on — weight by skill rather than rank), `Squad.MaxSize` (4), `Squad.InviteSeconds` (90). Auto-balance is
skipped entirely in PvE; joining the fuller side is an instant spectate with no warning.

---

## Moderation settings at a glance

| Setting | Default | Live? | What it does |
|---|---|---|---|
| `Teamkill.Enforce` | on | yes | Friendly-fire ladder (eject → kick + rank reset → ban). |
| `Grief.AutoKick` | on | yes | Anti-grief detect/report/kick master switch. |
| `Grief.RequireActiveFlooding` | on | yes | Two-factor: require sustained order-flood to kick. |
| `Grief.FloodOrdersPerSec` | 3 | yes | Order rate counted as flooding (held ~4s). |
| `Grief.HardBan` | off | yes | Also ban (not just kick) on a trip. |
| `Grief.ReportOnly` | off | yes | Detect + report, no kick (validation mode). |
| `Grief.ExemptAdmins` | on | yes | Never auto-kick an admin. |
| `Flood.Enforce` | on | yes | Per-player order rate limit (drops excess, no kick). |
| `DedicatedServerConfig.VoteKick` | off | restart | The game's built-in player vote-to-kick. |
| `Balance.NewJoinerSeconds` | 900 | yes | New-joiner move-immunity window. |
| `Squad.MaxSize` / `Squad.InviteSeconds` | 4 / 90 | yes | Squad size and invite timeout. |
| `grief_flood.json` (bot) | on, `kick`, 30/3s | bot restart | The upstream command-flood auto-kick. |
