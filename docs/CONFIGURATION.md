# Configuration

This is the full settings reference for the Nuclear Option toolkit. Every kept setting is listed below with its default, what it changes, and where you set it.

## Where settings live

There are four places settings live:

- **Plugin settings** (live). These are read by the in-game plugin every tick. You change them in the **Web Command Centre -> ⚙ Settings -> Game Settings** and they apply immediately. They are saved to the plugin config file `BepInEx/config/anz.nukestats.cfg` on the game server. Their starting on/off state is chosen in the setup wizard.
- **Bot settings.** Some of these are constants inside the bot (`no_mapvote_bot.py`); to change one you edit the value and **restart the bot**. Others are small JSON files (for example `votemap_config.json`, `rank_ladder.json`) that you edit through the Web Command Centre and the bot re-reads.
- **Web Command Centre settings.** How the dashboard binds and behaves. Some are in your `config.json`; a couple are stored in your browser.
- **Updater settings.** In `config.json` under `update`, read by the opt-in updater.

Config files written by the setup wizard live in a per-folder data directory, `<server folder>/.nost-data` by default. `config.json` holds no secrets and is safe to share; the SFTP password and Pterodactyl API key live in a separate `secrets.json` (locked down to owner-only). The environment variable `NOST_DATA_DIR` relocates this directory, and every generated launcher sets it so the bot, web dashboard, and updater all read the same data.

## What happens to your settings on an update

Re-running the installer or deploying a new plugin does not wipe your configuration:

- `DedicatedServerConfig.json` is read, backed up to a timestamped `.bak`, and merged so your existing values survive.
- The bot owns your ranks and skill data and snapshots them daily.

- The plugin config file (`anz.nukestats.cfg`) survives a **plugin update/deploy**: the guarded deploy job (`run.bat --deploy-plugin`, also used by the Schedule modal) replaces only the plugin DLL and never touches the config file, and every dashboard edit is saved key-by-key by the plugin itself. A **full re-install through the setup wizard** is different: it rewrites `anz.nukestats.cfg` from your wizard choices, so note down any hand-tuned plugin values before re-running the wizard.

## Reading the tables

- **Live** = applies immediately when you save it in the dashboard.
- **Needs SERVER restart** = saved right away, but only takes effect after the game server restarts.
- **Needs BOT restart** = a bot constant; edit the value in the bot, then restart the bot.

---

## Plugin settings

All of these are live plugin settings unless a row says otherwise. Change them in **Web Command Centre -> ⚙ Settings -> Game Settings**; they are saved to `anz.nukestats.cfg`.

### Chat & kill feed

| Setting | Default | What it controls | Where |
|---|---|---|---|
| `Chat.Reformat` | `true` | Rewrites chat to show name + rank in the rank colour (overridden by Rank-in-Name). | Dashboard (live) / setup wizard |
| `Chat.RankInName` | `true` | Puts the rank tag inside the player name (e.g. `[ACM] Brick`) so native chat and text-to-speech still work; overrides Reformat when on. | Dashboard (live) |
| `Chat.ProfanityFilter` | `true` | Replaces a whole message with a canned line if it contains a racist slur; ordinary swearing is untouched. | Dashboard (live) |
| `KillFeed.Custom` | `true` | Suppresses the native kill feed (it floods with AI kills) and the pilot-capture spam; announces kill streaks and ship sinks of every ship class with the toolkit's own lines instead. Rank name-tags are separate: `Chat.RankInName` alone owns them. | Dashboard (live) |
| `KillFeed.<line>.Mode` | `vanilla` | Per-line feed mode, one setting for each of the 8 plugin feed lines (`splash`, `splash_underdog`, `teamkill`, `ai_kill`, `went_down`, `streak`, `ship_sink`, `kill_bonus`): `vanilla` = the default wording, `custom` = use the Text template, `off` = hide the line. | Web CC Killfeed editor (live) |
| `KillFeed.<line>.Text` | `""` (empty) | The custom wording for that line (used when its Mode is `custom`), built from placeholder chips: `{killer}` `{killer_plane}` `{victim}` `{victim_plane}` `{weapon}` `{streak}` `{ship}` `{points}`. Empty = the vanilla wording. | Web CC Killfeed editor (live) |

### Scoring

| Setting | Default | What it controls | Where |
|---|---|---|---|
| `Scoring.WinPoints` | `200` | Points to every player on the winning side at match end (0-2000). | Dashboard (live) |
| `Scoring.FirstPlace` | `500` | Bonus for the match's top scorer (0-5000). | Dashboard (live) |
| `Scoring.SecondPlace` | `250` | Bonus for the second-highest scorer. | Dashboard (live) |
| `Scoring.ThirdPlace` | `100` | Bonus for the third-highest scorer. | Dashboard (live) |
| `Skill.CaptureBonus` | `250` | NuclearSkill points added to that life's score for capturing a base. | Dashboard (live) |
| `Skill.WinBonus` | `200` | NuclearSkill points added to a winner's final life at match end. | Dashboard (live) |
| `Skill.LossBonus` | `50` | NuclearSkill points added to a loser's final life at match end. | Dashboard (live) |

### Team balance (PvP)

| Setting | Default | What it controls | Where |
|---|---|---|---|
| `Balance.Enforce` | `true` | Master on/off for auto-balancing (block-join, and move with AutoMove on). | Dashboard (live) |
| `Balance.MaxDifference` | `2` | How many players one side may lead before balancing acts (2 = only a 3+ gap acts). | Dashboard (live) |
| `Balance.AutoMove` | `true` | Actually move the best-fit player when a side is too far ahead (off = block-join only). | Dashboard (live) |
| `Balance.MoveOnlyUnspawned` | `true` | Legacy, no effect in 1.0: flying players are eligible for a balance move (a moved flyer gets a 10-second warning, then eject). | Dashboard (live) |
| `Balance.RecheckSeconds` | `6` | Seconds between auto-balance checks (2-60). | Dashboard (live) |
| `Balance.MoveDebounce` | `20` | Minimum seconds between two moves, to stop churn (5-120). | Dashboard (live) |
| `Balance.GraceSeconds` | `180` | Legacy/unused: old silent hold before a move, superseded by WarnSeconds (may be hidden in 1.0). | Dashboard (live) |
| `Balance.MinPlayers` | `6` | Auto-balance never runs below this many humans; small lobbies are left alone (2-32). | Dashboard (live) |
| `Balance.WarnSeconds` | `300` | After teams go unbalanced, warn and wait this long before moving anyone (default 5 min). | Dashboard (live) |
| `Balance.MoveExemptGames` | `2` | Once moved, a player is exempt for this many games, spreading the burden (0-5). | Dashboard (live) |
| `Balance.NewJoinerSeconds` | `900` | Never move a player who joined less than this many seconds ago (default 15 min; 0 = off). | Dashboard (live) |
| `Skill.BalanceBySkill` | `true` | Balance by NuclearSkill rating instead of server rank when choosing who to move. | Dashboard (live) |
| `Swap.Altitude` | `2500` | Altitude (m) the brief Cricket spawns at during `!swapteam`/`!forceteamswap` before ejecting; raise to 3000 if a crash is seen (1500-5000). | Dashboard (live) |

### Squads

| Setting | Default | What it controls | Where |
|---|---|---|---|
| `Squad.MaxSize` | `4` | Max players in a `!squadup` group (2-8); squadmates get weak balance immunity. | Dashboard (live) |
| `Squad.InviteSeconds` | `90` | How long a `!squadup` invite stays open for the invitee to accept with `!y` (15-300). | Dashboard (live) |

### Match & map rotation (plugin side)

| Setting | Default | What it controls | Where |
|---|---|---|---|
| `Mission.PvpStartingRank` | `3` | PvP: every player starts at least this in-game rank (funds/loadout floor); 0 = off, PvE unaffected (0-10). In the dashboard this row appears under the "Rank + Fund catch-up" group below. | Dashboard (live) |
| `Forfeit.Enabled` | `true` | PvP: lets a team vote to surrender via `!forfeit` (majority needed). | Dashboard (live) |
| `Forfeit.CooldownSeconds` | `90` | Seconds before a team can start another forfeit vote; vote window is min(60, this) (30-600). | Dashboard (live) |
| `PvP.TimeoutResult` | `true` | PvP: on mission timeout with no winner, the team with the higher faction score on the scoreboard wins (exact tie = draw); off = just rotate. | Dashboard (live) |
| `Match.TimeoutLeadSeconds` | `120` | Fire the timeout result this many seconds BEFORE MaxTime so the map vote runs before auto-rotate (0 = exactly at MaxTime). | Dashboard (live) |

Vote timing (`MAP_VOTE_DURATION`, `POST_VOTE_MAP_CHANGE_DELAY`) is bot-owned and applies live - see [Bot settings](#match-map--voting-bot-side) below.

### Rank + Fund catch-up

These settings appear in the dashboard under a dedicated **"Rank + Fund catch-up"** group in Game Settings (together with `Mission.PvpStartingRank`, which moves into the same group). The group is injected by the dashboard at runtime, so it shows up even if your shipped settings catalogue predates it - you change everything in the same Game Settings menu, and all of it applies live.

- **Rank catch-up** raises the in-game starting-rank floor as the match runs, so latecomers and low-rank players are not stuck at the bottom of a long match. It is a floor only - nobody is ever lowered - each new floor is announced in chat, and it resets on every mission change. It is **off by default**.
- **Rank funds** grants in-game money when a player's in-game rank goes up: ranks gained x `RankFundsPerRank` x 1,000,000. The rank a player is first seen at grants nothing, and the same rank is never paid twice in a match (this survives a reconnect and resets on mission change).

| Setting | Default | What it controls | Where |
|---|---|---|---|
| `Mission.PvpRankCatchupMinutes` | `0` | Every this many minutes of match time the starting-rank floor rises +1; 0 = off. | Dashboard (live) |
| `Mission.PvpRankCatchupMaxRank` | `6` | The rising floor stops at this in-game rank. | Dashboard (live) |
| `Scoring.RankFundsPerRank` | `30` | In-game funds granted per rank gained, in millions (30 = 30,000,000); 0 = funds off. | Dashboard (live) |
| `Scoring.RankFundsMode` | `catchup_raised` | When funds pay out: `catchup_raised` = only players the catch-up floor lifts, paid for the lift; `any_rankup` = any player who reaches a new rank, however they got there; `catchup_all` = every connected player gets one rank of funds each time the floor steps up. An unknown value falls back to `catchup_raised`. | Dashboard (live) |

### PvE co-op

| Setting | Default | What it controls | Where |
|---|---|---|---|
| `PvE.TimeoutForceDefeat` | `true` | When the PvE mission timer runs out and humans haven't won, declare defeat instead of silently rotating. | Dashboard (live) |

### Moderation

| Setting | Default | What it controls | Where |
|---|---|---|---|
| `Admin.SteamIds` | `""` (empty) | Comma-separated SteamID64s allowed to use the gated in-game commands: `!move`/`!spec`/`!join`/`!balance`, admin `!swapteam`/`!forceteamswap`/`!skyswap`, and `!setrank`/`!setfunds`/`!addfunds`. Also set via the wizard's admin SteamID field. | Dashboard (live) / setup wizard |
| `Teamkill.Enforce` | `true` | Auto-punishes friendly fire: 1st = eject + warning, 2nd = kick + rank reset, 3rd = persistent ban. | Dashboard (live) |
| `Teamkill.MinDamage` | `100` | Minimum credited damage for a friendly kill to count as a teamkill (a deliberate kill credits ~100-140, a graze under 100). A kill below the floor is flagged in Moderation, never punished. 0 = off. | Dashboard (live) |
| `Teamkill.CollateralEnforce` | `true` | Judges each friendly kill by what the same blast also killed: friendlies only = the punish ladder; enemies too (equal or more) = collateral, reported to Moderation but not punished; overwhelming collateral = logged silently. Off = verdicts are only logged and every counted friendly kill runs the classic ladder. | Dashboard (live) |
| `Teamkill.CollateralWindow` | `2.5` | Seconds before a friendly kill in which the same player's other kills count toward the collateral verdict. | Dashboard (live) |
| `Teamkill.CollateralWindowNuclear` | `20` | Seconds counted each way around a friendly kill for nuke-scale blasts (the shockwave kills over tens of seconds); also delays a nuke verdict/warning by this long. | Dashboard (live) |
| `Teamkill.SilentMinEnemies` | `10` | A blast that killed at least this many enemies (and SilentRatio times the friendly count) gets a silent verdict: a log line only, no Moderation entry. 0 = tier off, every collateral verdict is logged in Moderation. | Dashboard (live) |
| `Teamkill.SilentRatio` | `5` | Companion to SilentMinEnemies: enemy kills must also be at least this many times the friendly count for the silent verdict. | Dashboard (live) |
| `Teamkill.CollateralMaxPerMatch` | `3` | Anti-abuse cap: exonerating collateral verdicts one player can receive per match before further friendly kills are judged on the normal ladder regardless. 0 = uncapped. | Dashboard (live) |
| `Teamkill.BigUnitExempt` | `true` | If the same blast also killed a big enemy objective (carrier, destroyer, or another ship class), the friendly kill counts as collateral of that strike: flagged in Moderation, never punished. | Dashboard (live) |
| `Teamkill.DamageCalibration` | `true` | Logs a `[dmgcal]` line for every player-caused unit death, used to calibrate the MinDamage floor. Log-only, no gameplay effect. | Dashboard (live) |
| `DedicatedServerConfig.VoteKick` | `false` | Enable/disable the game's built-in player vote-to-kick. Applies on config reload / next mission / restart. | `run.bat --set-votekick on\|off` |

### AI & performance

The AILimit group is a performance guard: it only ever removes AI aircraft, never players.

| Setting | Default | What it controls | Where |
|---|---|---|---|
| `Stats.SnapshotSeconds` | `10` | Seconds between full per-player stats snapshots emitted to the bot (2-60). | Dashboard (live) |
| `Cleanup.DismountedPilots` | `true` | Periodically despawn lingering ejected pilots to cut clutter/load. | Dashboard (live) |
| `Cleanup.PilotLifetimeSeconds` | `300` | Seconds a dismounted pilot may linger before cleanup (30-1800). | Dashboard (live) |
| `AILimit.Enforce` | `true` | Caps AI aircraft and clears stuck ones; only removes AI, never players. | Dashboard (live) |
| `AILimit.PerTeamAICap` | `32` | Max AI aircraft flying per faction; excess (grounded/lowest first) is destroyed (0-100). | Dashboard (live) |
| `AILimit.TotalAircraftCap` | `64` | Max total aircraft (AI + players); over it, AI is removed from the busiest side, never a player (0-200). | Dashboard (live) |
| `AILimit.StuckSeconds` | `45` | A grounded AI idle this long is cleared to free a clogged runway; 0 = off (0-300). | Dashboard (live) |
| `AILimit.StuckRadiusMetres` | `25` | Movement radius (m) under which a grounded AI counts as "not moving" (5-200). | Dashboard (live) |

Server tick rate (`TICK_RATE`) is a bot value, listed under [Bot settings](#server-tuning-bot-constants).

### Anti-grief / flood protection (one settings group)

Web CC exposes a single **Anti-Grief** tab covering rate-limit + auto-kick + unit-command policy + buffer raise. Canonical rate key is `Flood.FleetOrdersPerSec` (default **1**). Excess orders are dropped **and** the offender is kicked **immediately** (Layer A); `Grief.FloodOrdersPerSec` is a legacy cfg alias only (hidden in UI). Circuit breaker still suppresses mass-kicks on congestion storms.

| Setting | Default | What it controls | Where |
|---|---|---|---|
| `Flood.Enforce` | `true` | Rate-limits fleet move-orders; excess dropped (+ immediate kick if AutoKick). | Dashboard (live; patch binds at load) |
| `Flood.FleetOrdersPerSec` | `1` | Max accepted unit commands/sec/player; kick on excess (1-20). | Dashboard (live) |
| `Flood.FleetOrderBurst` | `1` | Token-bucket capacity; 1 = no burst allowance (1-40). | Dashboard (live) |
| `Flood.LogDrops` | `true` | Logs (throttled) name/SteamID of any player whose flooding orders are dropped. | Dashboard (live) |
| `Flood.DropDeadNetIdRpcs` | `true` | Silently drops RPCs aimed at already-destroyed objects to kill a disconnect-storm amplifier. | Dashboard, needs SERVER restart |
| `Command.Policy` | `HeliDroppedOnly` | Which units players may move via CmdSetDestination. Options: All, RateLimitOnly, HeliDroppedOnly (recommended), AllowlistTypes, Disabled. | Dashboard (live) |
| `Command.AllowedJsonKeys` | `""` (empty) | Only for Policy=AllowlistTypes: comma-separated UnitDefinition.jsonKey values to allow (empty = all ground vehicles). | Dashboard (live) |
| `Command.DiagLog` | `false` | Logs resolved unit type + ALLOW/DROP per command order to `BepInEx/LogOutput.log` (verbose; turn on briefly to discover unit jsonKeys, then off). | Dashboard (live) |
| `Mirage.RaiseReliableSendBuffer` | `true` | Anti mass-disconnect: raise the per-connection reliable-send-buffer cap so a command/RPC burst is absorbed. | Dashboard, applies at next match host |
| `Mirage.ReliableSendBufferLimit` | `12000` | Target cap for the reliable-send-buffer (game default 3000; recommended 8000-24000). Clamped to >=3000, never lowers a higher value. | Dashboard, applies at next match host |
| `Grief.AutoKick` | `true` | Master on/off for immediate kick + report on first excess order. | Dashboard (live) |
| `Grief.OwnedUnitThreshold` | `12` | Only when RequireActiveFlooding is OFF: owning more than this many live ground vehicles is auto-kicked (1-200). | Dashboard (live) |
| `Grief.RequireActiveFlooding` | `true` | Recommended ON: kick on order-rate excess alone. OFF also kicks on owned-count. | Dashboard (live) |
| `Grief.HardBan` | `false` | If ON, a tripped offender is also banned (kicked on rejoin), not just kicked once. | Dashboard (live) |
| `Grief.ReportOnly` | `false` | If ON, detect + report to the Reports tab but do NOT kick (validate the threshold first). | Dashboard (live) |
| `Grief.ExemptAdmins` | `true` | Recommended ON: never auto-kick a SteamID in `Admin.SteamIds`. | Dashboard (live) |
| `Grief.BreakerDistinctPlayers` | `3` | Circuit breaker: if this many distinct players trip the detector within the breaker window, it is treated as a server-wide lag/order spike, not grief - kicks and bans in that window are suppressed (reports still file). 0 = off. | Dashboard (live) |
| `Grief.BreakerWindowSeconds` | `6` | The rolling window (seconds) the circuit breaker counts distinct trippers over. | Dashboard (live) |

### Admin sky-drop (`!skyswap`)

Plugin config, section `[Admin]`. `!skyswap` drops the target player into a fully-armed jet high in the sky - and it moves them to the enemy side first: the other team in PvP, the AI faction in PvE. Only when no enemy destination exists do they stay on their own team and just get the jet. The loadout cost is refunded, and the drop is life- and points-neutral.

| Setting | Default | What it controls | Where |
|---|---|---|---|
| `Admin.SkyAircraft` | `Ifrit` | Which aircraft to spawn (matched by unit name/code substring; `Ifrit` = KR-67 Ifrit). | Plugin cfg `[Admin]` |
| `Admin.SkyAltitude` | `12000` | World-Y spawn altitude in metres. | Plugin cfg `[Admin]` |
| `Admin.SkySpeed` | `180` | Forward launch speed (m/s) so the jet doesn't stall on air spawn; 0 = drop stationary. | Plugin cfg `[Admin]` |
| `Admin.SkyPrimaryWeapon` | `Scimitar` | Weapon loaded on every compatible missile station except the secondary ones (AAM-36 Scimitar); empty = leave the default loadout. | Plugin cfg `[Admin]` |
| `Admin.SkySecondaryWeapon` | `Scythe` | Weapon loaded on the SkySecondaryStations count of stations (AAM-29 Scythe); empty = primary on all stations. | Plugin cfg `[Admin]` |
| `Admin.SkySecondaryStations` | `1` | How many stations get the secondary weapon. | Plugin cfg `[Admin]` |

Four drop-point settings decide **where** a swapped or sky-dropped player appears, per map and per faction, so they land over their own side. The same points position the brief Cricket used by `!swapteam` / `!forceteamswap`. Values are `x,z` world metres; a malformed value falls back to a safe open-ocean point for that map (Carrier Duel counts as Ignus).

| Setting | Default | What it controls | Where |
|---|---|---|---|
| `Admin.SkyDropHeartlandPala` | `-5000,-15000` | Drop point for a player moved to PALA (Primeva) on Heartland — over the PALA bases in the north (in-game north = negative z; grid J7). | Plugin cfg `[Admin]` |
| `Admin.SkyDropHeartlandBdf` | `-5000,45000` | Drop point for BDF (Boscali) on Heartland — over the BDF bases in the south (in-game south = positive z; grid D7). | Plugin cfg `[Admin]` |
| `Admin.SkyDropIgnusPala` | `-75000,0` | Drop point for PALA on Ignus (far west). | Plugin cfg `[Admin]` |
| `Admin.SkyDropIgnusBdf` | `75000,0` | Drop point for BDF on Ignus (far east). | Plugin cfg `[Admin]` |

---

## Bot settings

These are constants inside the bot (`no_mapvote_bot.py`) unless the row is a JSON file. To change a constant, edit the value and **restart the bot**. JSON files are edited through the Web Command Centre and re-read by the bot.

### Scoring & ranks (bot constants)

| Setting | Default | What it controls | Where |
|---|---|---|---|
| `USE_PLUGIN_SCORE` | `True` | Use the plugin's real in-game score deltas for lifetime points (the derived capture/win point math is switched off; win/loss counts are still recorded). | Bot constant, needs BOT restart |
| `KILL_BONUS` | `50` | Base points for downing an enemy player in PvP. | Bot constant, needs BOT restart |
| `UNDERDOG_PER_PLAYER` | `10` | Extra kill points per rank tier the killer sits BELOW the victim on the 11-tier ladder (rewards the underdog). | Bot constant, needs BOT restart |
| `WIN_POINTS` / `CAPTURE_POINTS` | `2` / `1` | Derived win/capture awards; inert under the default `USE_PLUGIN_SCORE=True`. | Bot constant, needs BOT restart |
| `GAIN_CLAMP_MAX` | `1000` | Max score banked per tick. | Bot constant, needs BOT restart |
| `SPIKE_THRESHOLD` | `1000` | A single-tick gain above this is flagged as a possible score exploit. | Bot constant, needs BOT restart |
| `SKILL_MIN_LIVES` | `5` | Completed lives required before a skill rating is shown/counted. | Bot constant, needs BOT restart |
| `SHOW_RANK_ON_CHAT` / `RANK_CHAT_THROTTLE` | `False` / `0` | Whether the bot posts a rank tag after normal chat (off, because the plugin now bakes the tag inline). | Bot constant, needs BOT restart |
| `PLUGIN_RANK_PUSH_INTERVAL` | `120` | Seconds between pushes of the chat-rank lookup file to the plugin. | Bot constant, needs BOT restart |
| `rank_ladder.json` | built-in 11-tier ladder | Overrides the ladder tiers and rank-up message template (fails open to the built-in default). | Web CC Ranks modal |
| `prestige_template` | `[{abbr} - {n}*]` | The rank-tag wording for a prestiged player; must contain `{n}` (the prestige count). Stored in `rank_ladder.json`. | Web CC Ranks modal |

### Award toggles (`award_config.json`)

Each bonus-point source can be switched off individually. Turning one off stops new points from that source only: rank display, `!rank`/`!leaderboard`, and cross-server rank carry are unaffected. Change them in **Web Command Centre -> ⚙ Settings -> Game Settings -> Scoring & Ranks**; the bot saves them to `award_config.json`.

| Setting | Default | What it controls | Where |
|---|---|---|---|
| `Award.KILL_BONUS_ON` | `1` (on) | The PvP kill bonus (`KILL_BONUS` points per kill). | Web CC Game Settings -> Scoring & Ranks |
| `Award.UNDERDOG_ON` | `1` (on) | The extra underdog kill points (killer ranked below the victim). | Web CC Game Settings -> Scoring & Ranks |
| `Award.START_BONUS_ON` | `1` (on) | The start-of-match participation bonus. | Web CC Game Settings -> Scoring & Ranks |
| `Award.CAPTURE_BONUS_ON` | `1` (on) | The per-capture bonus folded into the life score. | Web CC Game Settings -> Scoring & Ranks |
| `Award.WIN_POINTS_ON` | `1` (on) | The match-end win and placement points. | Web CC Game Settings -> Scoring & Ranks |

### Match milestones & start bonus (bot constants)

| Setting | Default | What it controls | Where |
|---|---|---|---|
| `START_BONUS_PTS` | `250` | Points to every player present at kickoff (fires around the 1-minute mark). | Bot constant, needs BOT restart |
| `START_BONUS_WINDOW` | `60` | Seconds into a new mission that still count as "start of match" for the kickoff bonus. | Bot constant, needs BOT restart |
| `STAY_MARKS` | `6300/7500/8700s` | Elapsed mission times that fire "stay for next match" reminders. | Bot constant, needs BOT restart |
| `WARN_THRESHOLDS` | `3600/1200/600/300/60` | Remaining-time thresholds (s) that fire mission-time warnings. | Bot constant, needs BOT restart |

### Match, map & voting (bot side)

Vote timing is now exactly two live values, set in the dashboard (the Mission Pool timing card, or Game Settings -> End of Match & Votes) and persisted in the deploy-protected `.nost-data/votemap_timing.json`. The old separate `VOTE_DURATION` and `APPROVAL_DURATION` constants are gone - both are aliases of the single `MAP_VOTE_DURATION`.

| Setting | Default | What it controls | Where |
|---|---|---|---|
| `MAP_VOTE_DURATION` | `30` | Ballot length (s) for BOTH the end-of-match map vote and `!votemap` (also the yes/no approval poll). Min 10, max 300. | `.nost-data/votemap_timing.json` via Web CC (live) |
| `POST_VOTE_MAP_CHANGE_DELAY` | `15` | Seconds after the ballot closes before the winning map loads. Min 5, max 300. | `.nost-data/votemap_timing.json` via Web CC (live) |
| `PostMissionDelay` | derived (vote + delay = `45`) | The server's own post-mission delay is derived from the two values above and pushed automatically (and re-checked at bot startup and after restarts), so the map can never change before the ballot closes. It is hidden from the Server Config modal - never set it by hand. | Automatic (bot-owned) |
| `MISSION_MAX_TIME` | `10800` | Fallback mission length in seconds (3 h). The match length the bot actually assigns when it queues a map comes from the Map Pool modal's per-type match minutes (`coop_minutes` / `builtin_minutes`, default 180 min each). | Bot constant, needs BOT restart |
| `ROLLOVER_SECONDS` | `10` | How short the current mission is cut to roll over to the winning map. | Bot constant, needs BOT restart |
| `POST_VOTE_COOLDOWN` | `90` | Per-player anti-spam cooldown (s) between repeat `!votemap` calls. | Bot constant, needs BOT restart |

### Votemap ballot (`votemap_config.json`)

Edit these in the Web Command Centre Votemap settings (Map Pool modal); the bot re-reads the file.

| Setting | Default | What it controls | Where |
|---|---|---|---|
| `enabled` | `true` | Master kill-switch; off = no auto map-vote. | `votemap_config.json` (Web CC) |
| `coop_count` / `pvp_count` | `4` / `2` | How many PvE co-op and PvP built-in options appear on the ballot. | `votemap_config.json` (Web CC) |
| `coop_mode` / `pvp_mode` | `balanced` / `fixed` | Selection mix per pool (coop: balanced/random/weighted; pvp: fixed/random/weighted). | `votemap_config.json` (Web CC) |
| `include_pvp` / `include_custom` | `true` / `true` | Toggle the PvP slots, and whether enabled custom USER missions enter the co-op pool. | `votemap_config.json` (Web CC) |
| `guaranteed` / `avoid_recent` | `[]` / `0` | Pin named missions onto every ballot; suppress the last N winners. | `votemap_config.json` (Web CC) |
| `coop_weights` / `pvp_weights` | `{}` / `{}` | Relative likelihoods for the `weighted` modes (co-op by category, PvP by mission). Empty = all equal. | `votemap_config.json` (Web CC) |
| `mission_weights` | `{}` | Per-map appearance chance for the random fill slots: 1 = normal, 2 = twice as likely, 0 = never offered (unless pinned). Empty = all equal. | `votemap_config.json` (Web CC) |
| `coop_minutes` / `builtin_minutes` | `180` / `180` | Match length (minutes) the bot assigns when it queues a co-op/custom map or a built-in op (clamped 10-600). `MISSION_MAX_TIME` is only a fallback. | `votemap_config.json` (Web CC) |
| `boot_map` | `""` (off) | Mission the server rotates to on a (re)start or when a vote makes no pick; empty = leave the next mission to the server's own rotation. | `votemap_config.json` (Web CC) |
| `force_pvp_enabled` | `true` | High-population override to a PvP-heavy ballot. | `votemap_config.json` (Web CC) |
| `force_pvp_players` | `24` | Players online at/above which the PvP-heavy ballot is forced. | `votemap_config.json` (Web CC) |
| `force_pvp_coop` / `force_pvp_pvp` | `0` / `6` | Co-op and PvP slot counts used while the high-population override is active. | `votemap_config.json` (Web CC) |

### Messages & help (JSON files + bot constants)

| Setting | Default | What it controls | Where |
|---|---|---|---|
| `system_messages.json` | all enabled except `rankfunds` | Per-message enable/text/interval/delay overrides for the 13 built-in messages (welcome, thanks, leaderboard, spectate tip, rank-ups, match end, and more). | Web CC Messages tab |
| `server_messages.json` | none | Owner-defined custom messages with interval/daily/event triggers. | Web CC Messages tab |
| `help_config.json` | all shown | Show/hide individual `!help` commands (help and votemap lines are locked/auto). | Web CC Help editor |
| `THANKS_INTERVAL` | `900` | Default cadence (s) for the periodic "thanks/commands" message. | Bot constant, needs BOT restart |
| `LEADERBOARD_INTERVAL` | `1800` | Default cadence (s) for the auto leaderboard post. | Bot constant, needs BOT restart |
| `SPECTIP_INTERVAL` | `1020` | Default cadence (s) for the spectate/team tip. | Bot constant, needs BOT restart |

### Joins & activity logging (bot constants)

| Setting | Default | What it controls | Where |
|---|---|---|---|
| `JOIN_POLL_INTERVAL` | `5` | How often (s) the bot refreshes the player roster. | Bot constant, needs BOT restart |
| `WELCOME_DELAY` | `5.0` | Delay (s) before welcoming a new joiner (once their name is known). | Bot constant, needs BOT restart |
| `LOG_CONVERSATION` | `True` | Log player chat + bot replies to `activity.log` (False = curated events only). | Bot constant, needs BOT restart |

### Anti-grief command-flood (bot side, `grief_flood.json`)

This is the bot's own detector, which reads the game's `[RateLimitAttribute] ... RPC rate limit exceeded` console lines. It is separate from the plugin's `Grief.*` settings above.

| Setting | Default | What it controls | Where |
|---|---|---|---|
| `enabled` | `true` | Enable the bot-side command-flood detector. | `grief_flood.json` |
| `action` | `kick` | Action on a trip: `kick`, `ban`, or `report`. | `grief_flood.json` |
| `drops_per_window` | `30` | RPC drops within the window that trip the detector. | `grief_flood.json` |
| `window_sec` | `3.0` | Sliding window (s) for counting drops. | `grief_flood.json` |
| `cooldown_sec` | `30` | Cooldown (s) before re-acting on the same offender. | `grief_flood.json` |
| `exempt_admins` | `true` | Never kick admins. | `grief_flood.json` |
| `rpc_allow` | `[CmdSetDestination]` | Only these RPCs can trip a kick. | `grief_flood.json` |
| `breaker_distinct` | `3` | Distinct offenders within the breaker window that trip the circuit breaker. | `grief_flood.json` |
| `breaker_window_sec` | `6.0` | Circuit-breaker window (s): suppress all flood-kicks during a server-wide storm. | `grief_flood.json` |

### Server tuning (bot constants)

| Setting | Default | What it controls | Where |
|---|---|---|---|
| `TICK_RATE` | `60` | Server engine updates/sec (30-120); higher = snappier AI/missile reactions at more CPU cost. Save, then run `run.bat --rewrite-wrapper` and **restart the game server** (a plain restart keeps the old rate). | Bot value, rewrite-wrapper + SERVER restart |

---

## Web Command Centre settings

The dashboard has **no login or authentication on any route**. Access control is entirely the network bind: anyone who can reach the port has full admin control (power start/stop, bans, config edits, granting points). This is fine on a home or LAN network behind a router, but **do not run it on a publicly-discoverable, internet-facing IP address** — if your host has a public IP, bind to `127.0.0.1` (see `web.host` below) and reach it over a VPN or authenticated proxy.

| Setting | Default | What it controls | Where |
|---|---|---|---|
| `web.port` (env `PORT` / `NOCC_PORT`) | `8770` | TCP port the dashboard listens on (`http://localhost:8770`). The wizard auto-picks the first free port from 8770 so a second install doesn't clash; launchers pin it via `NOCC_PORT`. | `config.json` `web{}` / env |
| `web.host` (env `NOCC_HOST`) | `0.0.0.0` (all interfaces / LAN) | Bind interface. Set to `127.0.0.1` to lock the dashboard to the host machine only. | `config.json` `web{}` / env |
| `server.power` | `pterodactyl` (or `local`) | How the dashboard controls the game process: Pterodactyl panel API vs a local process on this PC. | `config.json` `server{}` |
| `nocc_theme` | `dark` | Persists the chosen light/dark dashboard theme. | Browser localStorage (theme button) |
| `nocc_actfilter` | all categories on | Client-side show/hide of activity-feed line categories. | Browser localStorage (category chips) |
| Console view (raw vs filtered) | `filtered` | Show noise-filtered console output or every raw line. | Console card "filtered/raw" button |
| `console_filters.json` | none | Hide console lines matching your added normalised patterns. | Console line context menu |

---

## Updater settings

Read by the opt-in updater (`installer/updater.py`). The install is fully offline; nothing auto-updates. The updater only offers full GitHub releases (drafts/pre-releases are ignored).

| Setting | Default | What it controls | Where |
|---|---|---|---|
| `update.github_repo` | `TomosBombos/nuclear-option-toolkit` | The repo the updater reads releases from (change only for your own fork). | `config.json` (setup wizard) |
| `update.auto_check` | `false` | Check for updates on launch (it still asks before applying anything). | `config.json` (setup wizard) |
| `GITHUB_TOKEN` | unset | Optional token for private repos or higher rate limits. | Environment variable |

---

## Server & connection config

Written by the setup wizard. `config.json` holds no secrets and is safe to share; the SFTP password and Pterodactyl API key go to `secrets.json` (owner-only). Ports are also written into `DedicatedServerConfig.json`. Each of these has an environment-variable override (shown where relevant) that the bot reads first.

| Setting | Default | What it controls | Where |
|---|---|---|---|
| `server.sftp_host` / `sftp_port` / `sftp_user` | — / `2022` / — | SFTP endpoint for uploading the toolkit into a Pterodactyl container (port is usually 2022, not 22). | `config.json` (Connection step) |
| `secrets.sftp_pass` | — | SFTP password (= Pterodactyl panel account password). Stored only in `secrets.json`, never `config.json`. | `secrets.json` |
| `server.log_path` | `logs/console.log` | Path to the server console log the bot tails. | `config.json` |
| `server.rcmd_host` / `rcmd_port` | = `sftp_host` / `5550` (Pterodactyl); `127.0.0.1` / `5504` (own-PC) | Where the bot sends in-game admin commands via the relay (own-PC uses loopback, no port-forward needed). | `config.json` |
| `server.panel_url` / `server_id` | — / — | Pterodactyl panel base URL + server id for power control (stop/start around deploy). | `config.json` |
| `secrets.api_key` | — | Pterodactyl CLIENT API key (starts `ptlc_`) for power control. `secrets.json` only. | `secrets.json` (also `apiKey.txt`/`panel.txt` at root for power) |
| `server.game_port` / `query_port` | `7777` / `7778` (UDP) | Game and query ports; must differ. Forward both as UDP for internet play. | `config.json` + `DedicatedServerConfig.json` |
| `server.server_name` / `max_players` / `password` | — / `16` / `""` | Server identity written into `DedicatedServerConfig.json`. | `config.json` + `DedicatedServerConfig.json` |
| `server.power` | `pterodactyl` (external) / `local` (own-PC) | Panel API vs local process for power control. | `config.json` |

### Runtime environment variables

These are set by the generated launchers (`run.bat`, `webcc.bat`, START THIS SERVER) or by hand.

| Setting | Default | What it controls | Where |
|---|---|---|---|
| `NOST_DATA_DIR` | `<server folder>/.nost-data` | Where `config.json` + `secrets.json` are read/written. Must be set before the bot imports or SFTP/relay credentials go stale. Every launcher sets it per-folder so sibling installs don't clash. | Launcher env / CLI |
| `NOCC_PORT` | = `web.port` (8770) | Pins the Web Command Centre port for this folder. | `webcc.bat` / START THIS SERVER |
| `NO_LOCAL_CONSOLE` | (empty) | If set to a local console path, the bot tails locally and targets `127.0.0.1` instead of using SFTP. | Env / CLI |
| `NO_ADMIN_SIDS` | (from `config` / `admin_sids`) | Space-separated SteamIDs treated as admins for activity flagging and grief exemption. | Env / CLI |
| `CONSOLE_POLL_INTERVAL` | `1.5` | How often (s) the bot reads new console lines. | Env / CLI |

> Note: the environment variables `NO_RCMD_HOST`/`NO_RCMD_PORT` and `NO_SFTP_HOST`/`NO_SFTP_PORT`/`NO_SFTP_USER`/`NO_SFTP_PASS`/`NO_SFTP_LOGPATH` are the direct overrides for the corresponding `server.*` config values above (defaults: rcmd port `5550`, SFTP port `2022`; the SFTP password lives in `secrets.json`).
