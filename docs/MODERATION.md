# Moderation

How the toolkit keeps a server clean: team-kill enforcement, the nuke-aware team-kill log, how strategic weapons are treated, anti-grief auto-kick, and the ban/kick actions an owner runs by hand. This page is written for a server owner, not a programmer.

## Who does what

Two programs share the work.

- **The plugin** runs inside the game server. It watches the match in real time and does the actual enforcement: it detects friendly fire, punishes team-killers, tracks who launched a nuke, and can auto-kick a griefer. Plugin actions only apply while at least one player is online.
- **The bot** runs on your PC. It reads the plugin's events, writes them into a log, and gives you the buttons to ban, unban, kick, and clear reports from the Web Command Centre.

So enforcement is the plugin's job; the log and the manual controls are the bot's job.

---

## Team-kill enforcement

Stops players from repeatedly killing their own teammates.

**How it works:** The plugin detects a friendly-fire kill and punishes the offender on an escalating ladder, counted per SteamID. When it acts, it sends the event to the bot, which records it (see the team-kill log below).

The ladder is:

| Offence | Action |
|---|---|
| 1st | Eject the offender from their aircraft, plus a warning. |
| 2nd | Kick, and reset their in-game rank to 0. |
| 3rd | Persistent ban. |

**How to use it:** It runs automatically. You control it with one setting.

**Settings:**
- `Teamkill.Enforce` - default `true` - turns friendly-fire auto-punishment on or off - Web dashboard, Settings (applies live).

**`!notk`:** Any player can type `!notk` in chat to read the friendly-fire policy. It is only an explainer that the bot whispers back. It does not opt anyone out, and it does not do the enforcing — the plugin does.

---

## The nuke-aware team-kill log

Records every team-kill enforcement event so you can see what happened and who did it.

**How it works:** When the plugin punishes a team-kill it sends the bot a team-kill event. The bot writes one entry that captures:

- the offender,
- the teammate who was killed,
- the offence count (1st, 2nd, or 3rd),
- the action taken (warn / kick / ban),
- the **method** — how the kill happened (for example the weapon name, SAM, or CRAM).

The method field is what makes the log "nuke-aware": if the kill was caused by a nuke, the log shows the nuke as the method.

**How nuke collateral is attributed.** A nuke does not have to score a direct hit to count as friendly fire. The plugin does two things:

- **Launch attribution.** When a missile or nuke is spawned, the plugin records who launched it.
- **Collateral tracking.** After a blast, the plugin holds a shockwave window and records every kill inside it, keeping a per-blast list of victims. Kills in that window are attributed back to the launcher.

Put together: if a player drops a nuke near their own team, the teammates caught in the blast are counted as that player's team-kills, and the log names the launcher and shows the nuke as the method. This feeds the team-kill log, the stats, and moderation.

**Collateral cap.** There is an anti-abuse cap that limits how much blast collateral is counted, so one nuke in a chaotic fight does not over-penalize. That cap is applied to the log only when collateral enforcement is switched on.

**Settings:**
- `Teamkill.CollateralEnforce` - default `true` - judge blast collateral before punishing: a friendly kill that came with equal-or-more enemy kills in the same blast window is reported to Moderation (with every unit that died in the blast listed), **not** punished; only a kill that hit friendlies alone climbs the warn/kick/ban ladder. Overwhelming collateral (many enemy kills, few friendly) is logged silently with no report. Off = every counted friendly kill goes straight to the classic ladder, and the verdicts are only logged - Web dashboard, Settings (applies live).

**Where the log lives:** Entries are saved by the bot and shown in the Web Command Centre Moderation / Reports tab (see below).

---

## How strategic weapons are treated

There is no limiter or guard on strategic nuke use — the toolkit never blocks or rations a launch. What it does do:

- **Fair team-kill handling.** A friendly kill caused by a strategic launcher or an auto-engaging deployed defence is classified as automated: it is reported to Moderation, but it never moves the owner up the warn/kick/ban ladder.
- **No feed spam.** Shot-downs caused by strategic launchers are coalesced instead of flooding the killfeed, and there is no strategic-strike broadcast.
- **Nuke collateral attribution.** Covered above (the nuke-aware team-kill log): blast kills are attributed back to the launcher and judged by the collateral-verdict rules.

**Settings:** None. This behaviour is built in.

---

## Anti-grief auto-kick

Catches a single connection that floods unit-move commands (a macro, a held key, or a UI loop) to lag or crash the server, and kicks that one offender — not the lobby.

**How it works:** There are two cooperating detectors.

- **Plugin detector** (`Grief.*`): watches for a sustained flood of move-orders and, by default, requires active flooding rather than just owning a lot of units, so a legitimate base-builder is not kicked. On a trip it files a report, warns the player, and kicks (and can also ban).
- **Bot detector** (`grief_flood.json`): reads the game's own "RPC rate limit exceeded" console lines. When one SteamID crosses the threshold it files a report and kicks, bans, or just reports, depending on the configured action. A circuit breaker suppresses mass-kicks during a server-wide storm.

Both file into the same Reports tab, and both exempt admins by default.

**How to use it:** It runs automatically. Validate your thresholds with report-only mode before you let it kick.

**Settings (plugin side, Web dashboard Settings → Anti-Grief, live):**
- `Flood.FleetOrdersPerSec` - default `1` - max accepted unit commands/sec; excess = drop + immediate kick.
- `Flood.FleetOrderBurst` - default `1` - token-bucket capacity (1 = no burst allowance).
- `Grief.AutoKick` - default `true` - master switch for detect + report + immediate kick on excess.
- `Grief.RequireActiveFlooding` - default `true` - kick only on order-rate excess, ignoring raw unit count (recommended on).
- `Grief.OwnedUnitThreshold` - default `12` - only used when `RequireActiveFlooding` is off: owning more than this many live ground vehicles trips a kick.
- `Grief.HardBan` - default `false` - also ban a tripped offender, not just kick once.
- `Grief.ReportOnly` - default `false` - detect and report but do not kick (use to validate thresholds first).
- `Grief.ExemptAdmins` - default `true` - never auto-kick a SteamID in `Admin.SteamIds`.
- `Grief.BreakerDistinctPlayers` - default `3` - circuit breaker: if this many distinct players trip the detector within the breaker window, it is treated as a server-wide lag/order spike, not grief - kicks and bans in that window are suppressed (reports still file); 0 = off.
- `Grief.BreakerWindowSeconds` - default `6` - the rolling window (seconds) the circuit breaker counts distinct trippers over.
- (`Grief.FloodOrdersPerSec` is a legacy cfg alias; canonical rate is `Flood.FleetOrdersPerSec`.)

**Settings (bot side, `grief_flood.json`, edit then restart the bot):**
- `enabled` - default `true` - master switch.
- `action` - default `kick` - what to do on a trip: `kick`, `ban`, or `report`.
- `drops_per_window` / `window_sec` - default `30` / `3.0` - trip when a SteamID exceeds this many dropped commands within this many seconds.
- `cooldown_sec` - default `30` - do not re-act on the same SteamID within this window.
- `exempt_admins` - default `true` - never auto-act on an admin.
- `rpc_allow` - default `[CmdSetDestination]` - only these commands can trip a kick.
- `breaker_distinct` / `breaker_window_sec` - default `3` / `6.0` - circuit breaker: suppress all flood-kicks during a server-wide storm.

---

## Admin actions

### Who counts as an admin

- `Admin.SteamIds` - default empty - comma-separated SteamID64s allowed to run the gated in-game commands: `!move`, `!spec`, `!join`, `!balance`, admin `!swapteam`/`!forceteamswap`, `!skyswap`, and `!setrank`/`!setfunds`/`!addfunds` - Web dashboard Settings, or the setup wizard's admin field.

Admins are also exempt from anti-grief auto-kick by default.

### Actions from the Web Command Centre

- **Player-actions popup** (click a player row): grant or deduct rank points, move the player to Boscali or Primeva, send them to spectator, copy their SteamID, **kick**, or **ban**. Kick and ban are sent to the server as whitelisted commands.
- **Moderation modal** (the header **🛡 Moderation** button; its badge shows how many reports are outstanding). Two tabs:
  - **Reports** — the anti-grief and team-kill entries, each with the method, unit count, order rate, and action taken. Per row you can **Ban**, **Unban**, **📌 Log** (send to the repeat-offender ban log), or **Clear**.
  - **Banned** — the banned-players list, drawn from both the plugin ban list and the game's own ban list, with an **Unban** button and a box to unban a SteamID by hand. It also shows a repeat-offender ban log.

  All of these actions go through the bot, which owns the reports file (`plugin_reports.json`) and the plugin ban list (`plugin_bans.txt`), so cleared reports do not reappear.

### Vote-kick (the game's built-in player vote-to-kick)

This is the game's own feature, off by default. It is separate from everything above.

- `DedicatedServerConfig.VoteKick` - default `false` - enables or disables the game's built-in player vote-to-kick - turn it on or off with `run.bat --set-votekick on` (or `off`); the change applies on config reload, the next mission, or a restart.

---

## How to read the log and act on it

1. Open the Web Command Centre in your browser.
2. Click **🛡 Moderation** in the header. The badge tells you how many reports are waiting.
3. On the **Reports** tab, read each row: who it was, what they did, the method (weapon / nuke / command flood), the offence count, and what the system already did.
4. Decide:
   - Nothing more needed (the auto-action handled it) — click **Clear** to file it away.
   - Repeat or serious offender — click **Ban** (bans them), or **📌 Log** to add them to the repeat-offender ban log.
   - Wrong call / a mistake — click **Unban**.
5. To reverse a ban later, use the **Banned** tab: find the SteamID and click **Unban**, or paste a SteamID into the unban box if it is not listed yet.

---

## Access and security

The Web Command Centre has **no login**. Anyone who can reach its address can ban, kick, unban, grant points, and change settings. Keep it bound to your own machine or a trusted network, and do not expose the dashboard port to the internet. (Bind interface is set with `web.host` in config or the `NOCC_HOST` environment variable; the default listens on all interfaces.)

---

## Moderation settings at a glance

| Setting | Default | Where | What it does |
|---|---|---|---|
| `Teamkill.Enforce` | `true` | Web dashboard (live) | Friendly-fire ladder: eject → kick + rank reset → ban. |
| `Teamkill.CollateralEnforce` | `true` | Web dashboard (live) | Judge blast collateral before punishing: collateral kills are reported, not punished; off = classic ladder, verdicts only logged. |
| `Admin.SteamIds` | empty | Web dashboard / wizard | Who may run in-game team commands; also exempt from anti-grief. |
| `Grief.AutoKick` | `true` | Web dashboard (live) | Anti-grief detect / report / immediate kick master switch. |
| `Grief.RequireActiveFlooding` | `true` | Web dashboard (live) | Kick only on order-rate excess. |
| `Flood.FleetOrdersPerSec` | `1` | Web dashboard (live) | Max cmds/sec; excess = drop + immediate kick. |
| `Flood.FleetOrderBurst` | `1` | Web dashboard (live) | Token-bucket capacity (1 = no burst). |
| `Grief.HardBan` | `false` | Web dashboard (live) | Also ban on a trip, not just kick. |
| `Grief.ReportOnly` | `false` | Web dashboard (live) | Detect and report, do not kick. |
| `Grief.ExemptAdmins` | `true` | Web dashboard (live) | Never auto-kick an admin. |
| `grief_flood.json` (bot) | on, `kick`, 30 / 3.0s | edit + restart bot | The bot's upstream command-flood auto-kick. |
| `DedicatedServerConfig.VoteKick` | `false` | `run.bat --set-votekick on|off` | The game's built-in player vote-to-kick. |
