# Nuclear Option Community Server Toolkit

> Authoritative documentation for the three-process toolkit that turns a vanilla *Nuclear Option* Pterodactyl-hosted dedicated server into a managed community server: persistent ranks, a real-score economy, skill ratings, team balance, anti-grief enforcement, a live battle map, and a browser admin console.
>
> **Current plugin version: `anz.nukestats` 0.9.14** (the `[BepInPlugin]` attribute is authoritative).

---

## 1. Overview

The toolkit is built from **three independent processes** that never call each other directly. They communicate only through (a) the game's console log, (b) the game's remote-command TCP port, and (c) a set of shared files — some local to the admin PC, some dropped onto the game container over SFTP. This file-and-log coupling is deliberate: each process can crash, restart, or be redeployed without taking the others down.

| Process | Runs on | Language / host | Role |
|---|---|---|---|
| **NukeStats plugin** | Remote Linux game container | C# / BepInEx + HarmonyLib | Lives inside the game. Patches game methods, emits `[NOSTATS]` telemetry, enforces rules (teamkill, balance, AI cap, flood guard), reformats chat, runs the live-map feeds. |
| **The bot** (`no_mapvote_bot.py`) | Local admin PC | Python daemon | The brain. Tails the console over SFTP, drives the remote-command port via a relay, owns `ranks.json` + the ledgers, runs map votes, ingests `[NOSTATS]`, and runs the guarded deploy pipeline. |
| **Web command centre** (`cc_web.py` + `webcc.html`) | Local admin PC (browser at `127.0.0.1:8770`) | Flask + single-page JS | The face. Read-only over live state; queues admin intents to the bot; proxies Pterodactyl power; renders the 60fps live map. |

### 1.1 Architecture diagram

```
                        REMOTE LINUX CONTAINER (Pterodactyl)
        ┌──────────────────────────────────────────────────────────────┐
        │  Nuclear Option dedicated server (Assembly-CSharp / Unity)     │
        │   ▲ Harmony patches + [Server] API calls                       │
        │   │                                                            │
        │  ┌┴───────────────────────────┐                                │
        │  │  NukeStats plugin v0.9.14    │  Debug.Log("[NOSTATS] {...}")  │
        │  │  (BepInEx)                  ├───────────────┐                │
        │  └─────────────▲──────────────┘               │                │
        │   reads:        │ reads/deletes:               ▼                │
        │   plugin_ranks  │ plugin_cmd_<id>.txt    /logs/console.log      │
        │   plugin_skill  │                        (Unity -logFile)       │
        │   plugin_bans*  │                              │                │
        │   plugin_squads*│                              │                │
        │  ServerRemoteCommands :5504 (localhost only)   │                │
        │        ▲                                       │                │
        │   relay :5550 (0.0.0.0)  no_relay.py/.pl       │                │
        └────────┼───────────────────────────────────────┼───────────────┘
                 │ length-framed JSON (TCP)               │ SFTP tail (paramiko)
   host fwd <SERVER_HOST>:5550                             │  + SFTP file drops
                 │                                        │
        ┌────────┴────────────────────────────────────────┴──────────────┐
        │  LOCAL ADMIN PC                                                  │
        │                                                                 │
        │  ┌───────────────────────────────────────────────────────────┐ │
        │  │  no_mapvote_bot.py  (the bot daemon)                       │ │
        │  │   • map-vote state machine    • [NOSTATS] ingest           │ │
        │  │   • ranks.json (SOLE WRITER)  • ledgers + match history     │ │
        │  │   • guarded deploy pipeline   • Pterodactyl power (API)     │ │
        │  └───▲───────────────┬─────────────────────────▲──────────────┘ │
        │      │ consumes:     │ publishes (local files): │ consumes:      │
        │      │ admin_cmds    │ dashboard_state.json     │ schedule.json  │
        │      │ schedule.json │ activity.log             │                │
        │      │               │ console_mirror.log       │                │
        │      │               │ ranks.json (read-only ↓) │                │
        │  ┌───┴───────────────▼──────────────────────────┴──────────────┐ │
        │  │  cc_web.py (Flask :8770)  ──imports──►  bot module constants │ │
        │  │   reads state/activity/console/ranks  writes admin_cmds +   │ │
        │  │   schedule.json   proxies Pterodactyl power   serves map     │ │
        │  └───────────────────────────▲──────────────────────────────────┘ │
        └───────────────────────────────┼────────────────────────────────┘
                                        │ HTTP (localhost)
                                  ┌─────┴──────┐
                                  │  Browser   │  webcc.html SPA
                                  │ (admin)    │  live map · players · console
                                  └────────────┘
        * plugin_bans.txt / plugin_squads.txt are plugin-self-managed (not bot-written)
```

### 1.2 The three data directions in one sentence each

- **Plugin → bot:** one-way `[NOSTATS] {json}` telemetry through `console.log`, tailed over SFTP. There is no socket from the plugin back to the bot.
- **Bot → plugin:** SFTP file drops (`plugin_ranks.txt`, `plugin_skill.txt`, transient `plugin_cmd_<id>.txt`) plus a full process restart to swap the DLL.
- **Web CC ⇄ bot:** shared local files (`dashboard_state.json` / `activity.log` / `console_mirror.log` / `ranks.json` out; `admin_commands.jsonl` / `schedule.json` in). The web CC also imports the bot *module* in-process to reuse constants and the `RemoteCommand` class.

---

## 2. Component: NukeStats BepInEx plugin (`NukeStatsPlugin.cs`)

The plugin is a single `BaseUnityPlugin` (`[BepInPlugin("anz.nukestats", "NukeStats", "0.9.14")]`, `NukeStatsPlugin.cs:45-48`). Every behaviour is a HarmonyLib patch or a `[Server]`-API call. All tunables live in `BepInEx/config/anz.nukestats.cfg`.

### 2.1 Bootstrap & scheduling

#### Harmony patch bootstrap + manual dead-netId patch
**What:** Applies all `[HarmonyPatch]` hooks, then manually patches the internal Mirage `RpcHandler.HandleRpc` for flood-guard Layer B.
**How:** `Awake()` binds config, `LoadBans`/`LoadSquads`, runs `_harmony.PatchAll()`, then a second fail-open try-block uses `AccessTools.TypeByName("Mirage.RemoteCalls.RpcHandler")` to find and patch the private `HandleRpc` with `DeadNetIdDropPatch.Prefix` (`:217-254`). There is deliberately **no** `OnDestroy`/`UnpatchSelf`.
**Why:** On this dedicated server the manager `GameObject` is destroyed shortly after `Awake`. Unpatching in `OnDestroy` was removing every hook (debug traces showed methods re-patched with 0 prefixes). Harmony patches are static/process-lived, so they correctly survive object destruction.

#### HQTick central scheduler
**What:** Drives every periodic subsystem from `FactionHQ.Update`.
**How:** `HQTickPatch` (`FactionHQ.Update` Postfix, `:2948-2976`) runs once per frame (a `frameCount` gate, since `Update` fires per-HQ = twice/frame) and calls, in order: `PvETimeoutTick`, `MaybeSnapshot`, `MaybeCleanupPilots`, `MaybeBalance`, `PumpBounces`, `PumpKillStreaks`, `PumpStrategic`, `SkillTick`, `PosTick`, `TkTick`, `AiLimitTick`, `PollCommands`.
**Why:** The plugin's own `MonoBehaviour.Update` never ticks on the headless server. A shared `Time.time` gate inside each subsystem throttles it to its own interval regardless of which path fires; `FactionHQ.Update` is a method the server genuinely calls every frame during a live mission.

### 2.2 Telemetry sensors

#### Real-score stats sensor (`snap`/`score`)
**What:** Emits every human player's real `PlayerScore`, `PlayerRank`, `Teamkills`, faction, raw name and current aircraft as `[NOSTATS]` JSON.
**How:** `EmitOne` builds `{t,id,n,f,s,rk,tk,ac}`; `EmitAll` iterates `Humans()`. `MaybeSnapshot` is gated by a shared static `Time.time` clock (`_nextSnapShared`, min 2s, default `SnapshotSeconds=10`) and driven from both `Update()` and `HQTickPatch` (`:265-282`, `:433-462`). `Out()` uses `UnityEngine.Debug.Log` so the line reaches Unity's `-logFile`.
**Why:** `Console.WriteLine` reaches only process stdout, not the `-logFile`, so the bot would never see it — hence `Debug.Log`.

#### Humans() enumeration + frame cache
**What:** Returns all human players (SteamID-keyed; AI/unjoined excluded), cached per frame.
**How:** Reads the game's own `UnitRegistry.playerLookup.Values` (the same source `ChatManager` uses, so `Player.Owner` is valid for whispers); falls back to `FindObjectsOfType<Player>` if empty; filters out empty/`"0"` sids; cached by `_humansCacheTime == Time.time` (`:358-394`).
**Why:** `FindObjectsOfType` returned copies whose `.Owner` was null in the poll context (whispers silently no-op'd); `playerLookup` gives objects with a valid `.Owner`. The frame cache avoids a dozen scene scans per HQ tick.

#### Live-map feeds — `pos` / `air` / `ent`
**What:** Compact world-position feeds for the live map: flying player positions (`pos`), AI/player aircraft counts per side (`air`), per-entity AI-aircraft + ship positions (`ent`).
**How:** `PosTick` (2s) emits `pos` with each flying player's `id,x,z` + kind. `EmitAir` (once per `AiLimitTick`) emits per-side `ai`/`pl` counts plus `teamcap`/`totcap`. `EmitEntities` emits array `a` of AI aircraft `{i,x,z,f,k,g}` (`i` = `GetInstanceID` interpolation key, `g` = grounded) and array `s` of all ships `{i,x,z,f,c}` via `FindObjectsOfType<Ship>` (`:464-748`). `AcKind` classifies plane `p` vs heli `h`; `ShipClass` classifies carrier/destroyer/argus/corvette/cursor.
**Why:** A player with no fresh `pos` = not flying, so the command centre shows them dead/ejected until respawn — no explicit despawn message is needed. Each entity is per-unit guarded so one throw skips one unit, never the whole feed.

### 2.3 Scoring, ranks & skill

#### End-of-game authoritative winner + points/placement awards
**What:** On a `Victory` `DeclareEndGame`, determines the winning faction authoritatively and emits `win` + `award` events: `+WinPoints` to every winner plus 1st/2nd/3rd placement bonuses by `PlayerScore`.
**How:** `DeclareEndGamePatch.Postfix` → `OnDeclareEndGame(hq, endType)` (`:1062-1106`). With a 20s debounce and `endType==Victory`, it emits a final `snap`, a `win` line, awards `WinPoints` to players whose HQ equals the winning HQ, sorts by `ScoreOf`, and awards `FirstPlace`/`SecondPlace`/`ThirdPlace` as `award` events (reasons `1st`/`2nd`/`3rd`), then `end`.
**Config:** `[Scoring] WinPoints(200) FirstPlace(500) SecondPlace(250) ThirdPlace(100)`.
**Why:** The winning faction is read from the HQ that declared the end (no faction-0 guessing). Since v0.8.7 there is **no** match-end eject/bank — a skill life persists and ends only on death/air-eject.

#### NuclearSkill — persistent points-per-life event detector
**What:** Detects life-end events (death/eject) and capture bonuses so the bot can compute a points-per-life rating. The running per-life *score* itself lives in the bot.
**How:** `SkillTick` (1 Hz) tracks per-sid `Life{alive,airborne}`; losing an airborne plane with no admin-move marker emits a `life` event reason `eject`. A real death is closed earlier in `CheckTeamkill` with reason `death` (so fast death→respawn cycles each count). Ground dismounts, disconnects, match-end and balance/admin moves are all life-neutral. `OnCapture` emits `capbonus` folded into the current life (`:1108-1236`).
**Config:** `[Skill] CaptureBonus(250) WinBonus(200) LossBonus(50) BalanceBySkill(true)`.
**Why:** The running per-life score lives in the bot (`rec[curLife]`) so it survives disconnects and match-ends; the plugin is purely the event detector. Ending on the death event (not the 1 Hz scan) fixes under-counting for fast respawns. Disconnects are deliberately not dropped so the bot keeps accumulating across reconnects.

#### Skill/rank weight loading for balance
**What:** Provides a per-player numeric weight used to choose who to auto-balance, preferring skill over server rank.
**How:** `Weight(p)` uses skill weighting by default: `LoadSkillMap` parses `plugin_skill.txt` (`sid|rating`), computes `_skillAvg`, returns the player's rating or the server average if unranked. Fallback is `RankWeight` (1..11) from `plugin_ranks.txt`'s 4th field via `LoadRankMap`. Both files reload only when their `LastWriteTimeUtc.Ticks` change (`:1238-1261`, `:1461-1479`).
**Why:** Unranked players default to the server-average skill so they aren't unfairly weighted as 0 or 1.

#### Admin in-game rank / funds commands
**What:** Sets a player's in-game rank (`SetRank` with `scoreOffset` so it sticks) or in-game funds (`Allocation`: set or add).
**How:** `SetPlayerRank` → `target.SetRank(rank,true)`; `SetPlayerFunds` → `SetAllocation` (set) or `AddAllocation` (delta) (`:1847-1869`, `:1554-1566`). Reachable via chat (`!setrank`/`!setfunds`/`!addfunds`) and the command channel.
**Why:** Both call the game's own `[Server]` methods. `SetRank(true)` writes a `scoreOffset` so the rank sticks (the game only auto-bumps rank *up* from score). This is separate from the bot's persistent **server** rank in `ranks.json`.

#### Rank floor on mission start
**What:** Ensures each player starts at least the mission's `playerStartingRank`; on PvP maps floors everyone to `PvpStartingRank`. Also drives per-game balance/teamkill/forfeit resets.
**How:** `StartingRankFloorPatch` (`NetworkManagerNuclearOption.ServerMissionStartPlayer` Postfix, `:3140-3172`). On a new mission it calls `AdvanceGame`, `ClearMatchTeamkills`, `ClearForfeitVotes`; computes `want = playerStartingRank` (raised to `PvpStartingRank` on PvP); if below, `SetRank(want, setScoreOffset:true)`.
**Config:** `[Mission] PvpStartingRank(3)`.
**Why:** The game seeds starting rank only when `!saveData.Rejoined`; a reconnecting player who dropped during faction-select keeps a saved rank of 0, stranding them at 0 on rank-2/3 missions. No-op for anyone already at/above the floor.

### 2.4 Chat & naming

#### Chat reformat — `[Name - Rank]` in rank colour
**What:** When `RankInName` is off, rewrites chat as `[Name - Rank] message` in the rank's colour and emits a `chat` telemetry line.
**How:** `ChatReformatPatch` (`ChatManager.UserCode_CmdSendChatMessage_-456754112` Prefix) → `FormatAndBroadcast` (`:2703-2752`). Returns native if `RankInName`/`ReformatChat` off or for `!`-prefixed messages; otherwise light-throttles (0.4s/sid), emits `{t:chat,id,n,msg,all}`, builds the colourised line from `plugin_ranks.txt`, and broadcasts via `RpcServerMessage` (all-chat) or per-teammate `RpcTargetServerMessage` (ally), suppressing the native line.
**Why:** The native path renders `Name:` + faction colour client-side and strips rich text, so it can't be restyled in place. Commands/votes must **not** be rerouted or the bot can't tally them.

#### RankInName — embed rank into the player's name (preferred mode)
**What:** Embeds the rank into the player's NAME (e.g. `[ACM] Maverick`) so native chat AND the game's text-to-speech work. Overrides Reformat.
**How:** `NameInjectPatch` (`Player.UserCode_CmdSetPlayerName_-1114485719` Prefix) → `InjectRankIntoName` rewrites the name on the first set only; `Prefixed()` builds `[ABBR] raw` from `plugin_ranks.txt`, capped at 32 chars (the game runs `SanitizeRichText(32)`) by trimming the raw tail (`:960-1023`).
**Why:** Tag is applied once at join; re-tagging mid-session fires a spurious duplicate "joined the game" on each client (the server is headless, can't intercept). Live rank still shows via the aircraft label, which refreshes every spawn. When `RankInName` is on, `FormatAndBroadcast` returns native so the bot reads chat via the native log line (`CHAT_RE`), avoiding double-logging.

#### Profanity (racist-slur) gate
**What:** Replaces an entire message with a canned line if any token resolves to a racist slur; ordinary swearing is untouched.
**How:** `IsRacist` (called first in `ChatReformatPatch`, replacing `__0` by ref so the cleaned text both broadcasts and is what the bot reads) runs two passes over `NormalizeForSlur`'d tokens: a full anchored per-token match against `TokenRx` (each char expanded `c+` so repeats/leet-doubles match) and a strong substring match against `StrongRx` over the de-spaced whole message (catches `fucknigger` / `n i g g e r`). `NormalizeForSlur` maps leetspeak, Cyrillic homoglyphs and accented latin to a-z; `SlurAllowlist` exempts innocent embeds like *snigger* (`:2776-2912`).
**Config:** `[Chat] ProfanityFilter(true)`; replacement = "I am an idiot and need help!".
**Why:** The in-game filter doesn't work. It deliberately does not touch ordinary swearing — only racial/ethnic slurs — and is curated liberal on spellings while avoiding innocent collisions (raccoon, Pakistan, Japan, Nigeria).

#### Rich aircraft labelling
**What:** On spawn, stamps a player's aircraft networked `unitName` with rank/name/plane (or plane only when the custom feed is on).
**How:** `AircraftLabelPatch` (`Player.SetAircraft` Postfix) → `LabelAircraft`; rank read from `plugin_ranks.txt`, refreshed every spawn (`:923-958`).
**Why:** `unitName` is also used on radar/target/refuel-rearm labels (accepted trade-off). Live rank shows because the label refreshes every spawn even though the chat-name rank is set once at join.

#### In-game chat command handler
**What:** Handles public commands (`!autobalance`/`!ab`, `!forfeit`/`!ff`/`!surrender`, bare `!spec`, `!squadup`/`!squad`/`!su`, `!y`/`!yes`) and admin commands (`!move`/`!team`/`!join`, `!spec [player]`, `!balance`, `!setrank`, `!setfunds`/`!addfunds`, `!swapteam`/`!forceteamswap`), suppressing them from chat.
**How:** `TryHandleChatCommand` parses `!`-prefixed messages; admin verbs check `IsAdmin` (`AdminSteamIds` config); `Resolve()` substring-matches a player (`:2564-2673`).
**Why:** `!y` accepts a pending squad invite only; with no live invite it returns false so `!y` flows through to the bot (the map-vote approval poll also tallies `!y`).

### 2.5 Custom kill feed

#### Custom kill feed — streaks + capital-ship sinks + native suppression
**What:** Suppresses the native global kill feed (which floods with AI) and instead broadcasts kill-streak callouts (5/10/25/50, escalating colour) and CARRIER/DESTROYER sink announcements; also strips player names from radar/map unit labels.
**How:** `KillFeedSuppressPatch` (`MessageManager.RpcKillMessage` Prefix) returns false when `CustomKillFeed` is on. `RegisterStreakKill` counts non-pilot kills into a rolling 5s window; `PumpKillStreaks` announces the count once a burst settles (~0.8s) at each new tier via `BroadcastAll`. `MaybeAnnounceShipSink` dedupes by ship instance and announces carrier(`#FF1493`)/destroyer(`#FF8C00`) sinks. `PilotMsgSuppressPatch` hides the rescue/capture feed line (`:805-921`).
**Config:** `[KillFeed] Custom(true)`.
**Why:** The strategic-launcher announce was removed 2026-06-24 (user: "didn't work how I'd hoped"); `PumpStrategic` now just drains the counter. The personal "you killed X" display is a separate RPC, unaffected by `RpcKillMessage` suppression.

#### Killfeed/down telemetry (`kill` + `down`)
**What:** Emits enemy player-vs-player kills (`kill`) and every human death with who/what downed them (`down`, incl. friendly-fire flag).
**How:** `KillPatch` (`FactionHQ.ReportKillAction` Postfix) → `OnKill` emits `kill` only for human-vs-human enemy kills. `CheckTeamkill` emits `down` for every human death with `v/vn/k/ks/kp/ff` (`kp=1` killer is a player with sid `ks`; `ff=1` friendly fire) (`:777-803`, `:1355-1365`).
**Why:** Ejected/rescued pilots are hidden entirely from the kill event; `down` covers AI/SAM/environment deaths that `kill` cannot express.

### 2.6 Enforcement & moderation

#### Teamkill enforcement + persistent bans
**What:** Auto-punishes friendly fire with per-match escalation: 1st = eject + private warning, 2nd = kick (+ rank reset on rejoin, next is a ban), 3rd+ = persistent ban.
**How:** `TeamkillPatch` (`Unit.ReportKilled` Prefix) → `CheckTeamkill` finds the dead unit's top damager via reflected private `Unit.damageCredit` + `UnitRegistry.TryGetPersistentUnit`; if that damager is a Player on the SAME faction as the dead unit, the killer's sid is queued. `TkTick` escalates (`n==1` eject+warn, `n==2` rank-zero + delayed kick, `n>=3` ban + `SaveBans` + kick). A 2 Hz scan kicks any banned sid on sight (`:1263-1451`).
**Config:** `[Teamkill] Enforce(true)`. Persists to `plugin_bans.txt`; per-match counts reset via `ClearMatchTeamkills` on a new mission.
**Why:** TK is rare/intentional in this game so auto-enforcement is safe; failures no-op so there's never a false kick. The teamkill-warning eject is life-neutral so it never ruins a skill-life.

#### AI aircraft limiter
**What:** Performance precaution that caps AI aircraft per-team and total, and clears stuck grounded AI to free runways — only ever removes AI, never players.
**How:** `AiLimitTick` (5s) scans `FindObjectsOfType<Aircraft>`, grouped per HQ. **Rule C:** a grounded AI not moving beyond `AiStuckRadius` for `AiStuckSeconds` is cleared. **Rule A:** trim each side's AI above `AiPerTeamCap` (grounded/lowest-alt first). **Rule B:** while total > `AiTotalCap`, remove AI from the busiest side. Removal = `Aircraft.DisableUnit()` falling back to `StartEjectionSequence`; per-tick budget 12; a SAFETY check skips any `ac` with a Player (`:496-616`).
**Config:** `[AILimit] Enforce(true) PerTeamAICap(32) TotalAircraftCap(64) StuckSeconds(45) StuckRadiusMetres(25)`.
**Why:** Scan interval was raised 3s→5s: with mission AI caps now below the limiter cap, the limiter rarely acts, so the full-scene scan can run less often (fewer hitches/GC).

#### Dismounted-pilot cleanup
**What:** Periodically despawns ejected pilots lingering on the map.
**How:** `MaybeCleanupPilots` (≤ every 30s) tracks `PilotDismounted` first-seen times; any older than `PilotLifetime` calls `Networkplayer.RemovePilotDismounted` then `Destroy(gameObject)` (`:1025-1059`).
**Config:** `[Cleanup] DismountedPilots(true) PilotLifetimeSeconds(300)`.
**Why:** Captures/rescues usually happen well within 300s.

#### Anti-exploit — suppress radar/spotting + jamming score
**What:** Skips all score/funds/popup for Recon (radar detection) and Jamming rewards — the score-explosion vector — while leaving kills/captures/supply/refuel/repair/rescue untouched.
**How:** `SuppressSpottingScorePatch` (`FactionHQ.RewardPlayer` Prefix): if `missionType` is 2 (Recon) or 3 (Jamming) it sets a `[ThreadStatic] Suppressed` flag and returns false. `RewardPlayerPatch.Postfix` consumes `Suppressed` so suppressed rewards emit no `score` event; otherwise it emits `EmitOne(p,"score")` (`:3056-3102`).
**Why:** `RewardType` verified via ilspycmd: `None=0,Kill=1,Recon=2,Jamming=3,Supply=4,Refuel=5,Repair=6,RescuePilots=7,CapturePilots=8,CaptureLocation=9`. Recon fires on every detection and accumulates fast with many AI. `FactionHQ.RewardPlayer` is the SOLE score funnel — there is deliberately no `Player.AddScore` patch (patching it doubled every score event, removed in 0.4.0). Self-destruct-weapon kills route through `RewardType.Kill` and are intentionally not affected.

#### PvE timeout-defeat
**What:** In PvE co-op, declares the human team DEFEATED (the AI faction "wins") when the mission timer expires; no effect in PvP.
**How:** `PvETimeoutTick` (1 Hz) requires `TimeoutForceDefeat` on and `GameResolution.Ongoing`; requires exactly one joinable side + at least one AI side; if `Time.timeSinceLevelLoad` exceeds `CurrentMissionMaxTime` it reflects `FactionHQ.DeclareEndGame("Victory")` on the AI HQ (`:1871-1947`).
**Config:** `[PvE] TimeoutForceDefeat(false)` — default OFF until verified live.
**Why:** All reflection helpers fail safe (null/-1) so a wrong member name can never fire a false defeat.

### 2.7 Team balance & movement

#### PvP team auto-balance (LEAVE-triggered) with warning hold
**What:** On a player LEAVE that leaves a side more than `MaxDifference` ahead, warns the lobby and after a hold moves one big-side player to the smaller side, picking whoever best evens total skill.
**How:** `MaybeBalance` (`RecheckSeconds=6`) only acts in PvP. Never balances under `BalanceMinPlayers(6)` humans. It arms only when total side count DECREASES; once armed and uneven it broadcasts a one-time warning, HOLDs `BalanceWarnSeconds(300)`, then `BalanceOnce(false)`. `BalanceOnce` picks the least-protected, non-move-exempt big-side player whose `Weight` best matches `target=(sumBig-sumSmall)/2` and force-swaps them via `BeginSwap` (`:1949-1998`, `:2247-2305`).
**Config:** `[Balance] Enforce MaxDifference(2) AutoMove RecheckSeconds(6) MoveDebounce(20) MinPlayers(6) WarnSeconds(300) MoveExemptGames(2) NewJoinerSeconds(900)` (`GraceSeconds`/`MoveOnlyUnspawned` legacy).
**Why:** Autobalance fires ONLY on a leave; joining the fuller side is handled instantly by the join blocker. The two mechanisms are cleanly split. The picked player is force-swapped straight to the smaller side (keeps points + skill-life) rather than sent to spectate.

#### Auto-balance protection tiers — new-joiner + squad + move-exemption
**What:** Layers protection so auto-balance avoids moving recent joiners, squad members, and recently-moved players.
**How:** `ProtTier`: new joiner (`< NewJoinerSeconds=900s`, tracked via `_firstSeen`) = tier 2; in a squad = tier 1; unprotected = tier 0. `BalanceOnce` filters out `MoveExempt` players (moved within `MoveExemptGames=2` games) then picks within the lowest non-empty tier (`:2000-2066`).
**Why:** A new joiner is moved only if every other non-exempt option is also a new joiner; a squad member only if no unprotected non-exempt option exists. Move-exemption spreads the burden so the same person isn't repeatedly moved.

#### Join blocker / wrong-team bounce (instant spectate)
**What:** When a player joins the side already more than `MaxDifference` ahead, immediately bounces them to spectate (no warning).
**How:** `BlockJoinPatch` (`Player.UserCode_CmdSetFaction_-1594139491` Postfix) queues via `QueueBounceCheck`; `PumpBounces` checks next tick and, if the joined side is too far ahead, `DoMoveNow(p,null)` sends them to spectate with a `TellPlayer` instruction (`:1633-1708`).
**Why:** A player can't spawn within one frame of joining, so the bounce lands before they're in a jet. This is the only thing that fires on a join.

#### Move orchestration + MovePlayer surgery
**What:** Moves a player to a faction or spectate; a flying player gets a 10s warning then is ejected so the move takes effect.
**How:** `RequestMove`: spectate is immediate; a team move of a flyer broadcasts a 10s warning and queues a `Pending` move. `MovePlayer` does manual faction surgery (the game's `SetFaction` refuses a change once HQ is set): `from.RemovePlayer` → reflected HQ SyncVar setter → `to.AddPlayer` + `RequestTrackingStates` (`:1572-1631`, `:1821-1845`).
**Why:** The clean `HQ` property's private setter forwards to the SyncVar and marks it dirty so it syncs to clients. `AdminEject` before the move keeps it life-neutral.

#### Team-swap (`!swapteam`/`!forceteamswap`) — over-ocean Cricket spawn
**What:** Moves a player to the other team and resets their client spawn-menu UI to the new faction WITHOUT losing points or their open skill-life, by spawning a brief CI-22 Cricket high over open ocean then ejecting them.
**How:** `BeginSwap` runs a `SwapJob` state machine pumped 1 Hz by `PumpSwaps`. `SpawnCricket` calls `NetworkSceneSingleton<Spawner>.i.SpawnAircraft(player, cricketPrefab, …, GlobalPosition over ocean, spawningHangar=null, destHQ, …)`. The owning client's `OnStartClient` teleports its plane, attaches HUD + `DynamicMap.SetFaction` (the UI reset), then `AdminEject` drops them to the new team's spawn menu. `SwapPos` picks one quiet open-ocean corner per map (Heartland SW `-33000,-40000`; Ignus deep-south `8000,-33000`) at `SwapAltitude` (`:2314-2478`).
**Config:** `[Swap] Altitude(2500)`.
**Why:** The Cricket spawns HIGH over OPEN OCEAN far from every base so the brief un-piloted moment + auto-eject can never crash into terrain/base/aircraft. Every eject is `GuardEject`-protected. Auto-balance reuses `BeginSwap` (admin=null) to force-swap the picked player.

#### `_adminEjectGuard` — life/killfeed-neutral ejects
**What:** Marks admin/balance/swap ejects so the on-death path doesn't bank a phantom death or spam the killfeed.
**How:** `GuardEject` stamps `sid→expiry` (now+6s, covers the async `ReportKilled` after `StartEjectionSequence`). `CheckTeamkill` returns early (suppressing both the death and the killfeed) when the victim is admin-ejecting. `_balancing` additionally neutralises the 1 Hz `SkillTick` scan (`:1124-1142`, `:1310-1313`).
**Why:** `_balancing` alone only covers the slower scan; the on-death patch fires first and would otherwise bank a phantom death + spam chat — so an airborne eject needs the guard to be truly life- AND feed-neutral.

### 2.8 Social & voting

#### Forfeit voting (`!forfeit`/`!ff`/`!surrender`)
**What:** Lets a PvP team vote to surrender (loss for them, win for the other team) on a majority.
**How:** `HandleForfeit` starts/extends a per-faction `ForfeitVote`; cooldown `ForfeitCooldownSeconds(90)` before a new vote can start; window = `min(60, cd)`. Tally counts only voters still on the team; `need = team.Count/2+1`. On pass it `BroadcastAll`s the forfeit and `ForceVictory(otherHQ)` (`:2480-2562`).
**Config:** `[Forfeit] Enabled(true) CooldownSeconds(90)`.
**Why:** PvP only; the tally tracks live team size so someone leaving lowers the threshold. Forfeit = the OTHER team's HQ declares Victory (same path as a normal win).

#### `!squadup` system
**What:** Lets up to `MaxSize` friends form a squad with weak auto-balance immunity; persists across matches and restarts.
**How:** `HandleSquadup`: bare=status, `<player>`=invite (90s window), leave/quit/disband=leave. `TryAcceptSquad` (`!y`) consumes a live invite. Persists to `plugin_squads.txt` (tab-separated `sid~name` tokens; 1-person squads dropped) (`:2068-2241`).
**Config:** `[Squad] MaxSize(4) InviteSeconds(90)`.
**Why:** Squad immunity is weaker than new-joiner protection: a member is moved only if no unprotected, non-exempt player is available. Forming a new squad requires the inviter still present so an absent player isn't resurrected into a squad.

### 2.9 Network flood guard

#### Layer A — fleet-order rate limit (`FleetOrderFloodPatch`)
**What:** Per-player token-bucket rate limit on fleet move-orders (`CmdSetDestination`) to stop a runaway order spam from overflowing the reliable send buffer and mass-disconnecting the lobby at match start.
**How:** `FleetOrderFloodPatch` (`UnitCommand.UserCode_CmdSetDestination_1791143641` Prefix) → `AllowFleetOrder`, a per-sid leaky token bucket (capacity `FloodBurst`, refill `FloodPerSec`/s, starts FULL); when empty it returns false to drop the excess order server-side, throttle-logged once per 5s per player (`:299-335`).
**Config:** `[Flood] Enforce(true) FleetOrdersPerSec(3) FleetOrderBurst(6) LogDrops(true)`.
**Why:** The game's own limiter is per-UNIT, so commanding many ships multiplies its cap and dead-unit orders bypass it. A human commander issues well under 1/s; the observed flood was ~19/s, so 3/s leaves a large margin. The bucket starts full so a player's first orders are never dropped.

#### Layer B — dead-netId ServerRpc drop (`DeadNetIdDropPatch`)
**What:** Silently drops ServerRpcs aimed at a netId with no live object, removing the log/error/allocation amplifier that under a flood exhausts the `ByteBuffer` pool and overflows send buffers.
**How:** Manually patched onto the internal `Mirage.RemoteCalls.RpcHandler.HandleRpc`. The Prefix binds `_objectLocator` once per instance, creates a delegate to `IObjectLocator.TryGetIdentity`; if the netId has no live identity it sets `__result=false` and returns false. Fail-open: if it can't bind it auto-disables, leaving Layer A as the primary guard (`:336-353`, `:3210-3253`).
**Config:** `[Flood] DropDeadNetIdRpcs(true)`.
**Why:** RPCs to a dead netId never reach the per-unit handler (they exit `HandleRpc` before dispatch), so Layer A cannot see them — this is the only place to catch that path. The game drops these anyway but first LOGS, pushes a client `SetError`, and builds a network reader; under a flood that storm is the amplifier. Dropping silently removes it.

### 2.10 Command channel

#### File-based command channel (`plugin_cmd_*.txt`)
**What:** Lets the command centre/bot drive in-game actions (balance, tell, spec, move/join, setrank, setfunds/addfunds) by dropping one file per command in the game root.
**How:** `PollCommands` (1 Hz) reads/sorts `plugin_cmd_*.txt` (id-prefixed = chronological), runs each non-`#` line through `ExecCommand`, and DELETES the file (no dedup/replay). `ExecCommand` parses `verb|steamId|arg` (`:1488-1570`).
**Why:** Writing those files needs SFTP/console access, so they're implicitly trusted. One file per command + delete-after-process means there's no dedup/replay to get wrong.

---

## 3. Component: the bot (`no_mapvote_bot.py`)

A single-threaded, self-healing Python daemon. Its `main()` loop is a 3-state machine (IDLE → APPROVAL → VOTING) that tails the console, ingests telemetry, runs votes, maintains the economy, and publishes the dashboard feeds.

### 3.1 Gameplay & economy

#### Map-vote state machine (IDLE → APPROVAL → VOTING)
**What:** Runs the next-map ballot. On every "Mission complete" it posts the rank roster and opens a 4+2 ballot (2 random Escalation co-op, 2 random Terminal Control co-op, 2 fixed PvP); players vote `!1..!6`. `!votemap` triggers a mid-match vote, first running a yes/no approval poll (`!y`/`!n`) unless the caller is alone.
**How:** `MISSION_END_RE` ("Mission complete") → `match_finalize` + `announce_rank_roster` + `open_map_vote`. `build_ballot()` samples `ESCALATION_MISSIONS` + `TERMINAL_CONTROL_MISSIONS` (rejecting `>MAX_DARK_PER_VOTE` dark maps and the previous pair) then appends 2 fixed `PVP_OPTIONS`. `apply_winner()` tallies with `Counter`, breaks ties by earliest `first_vote_at`, falls back to random. `force_switch` cuts the mission via `set_time_remaining(ROLLOVER_SECONDS)`; `suppress_mission_end_until` swallows the self-induced "Mission complete" (`:742-2742`).
**Config:** `VOTE_DURATION=60 APPROVAL_DURATION=60 ROLLOVER_SECONDS=10 POST_VOTE_COOLDOWN=90 MAX_DARK_PER_VOTE=3 MISSION_MAX_TIME=10800`.
**Why:** `apply_winner` sets `CURRENT_MISSION = friendly_label(name)` not `_plain(label)` on purpose: the ballot label carries a `[PVP]` suffix, and a changing `CURRENT_MISSION` key would reset the mission-time-warning dedupe set and double-fire warnings. The mission-end vote bypasses the cooldown (which gates only `!votemap`).

#### `[NOSTATS]` plugin ingest parser
**What:** Parses the plugin's `[NOSTATS] {json}` lines (the real per-player score feed). Handles `snap/score/win/award/end/life/capbonus/kill/down/pos/air/ent/chat`.
**How:** `NOSTATS_RE` extracts JSON; `handle_stats_line()` dispatches on `obj['t']`. `snap/score` accumulates real score into lifetime points: `rec['ms']` is the last in-match score credited; on `s>prev` it awards the increase (and feeds `curLife` and `SCORE_ACCUM`); first sighting adopts `s` as baseline crediting nothing; `s<prev` rebaselines. `end` clears `STATS_META`/`LIVE_SCORE` but deliberately keeps `ms` (`:466-2382`).
**Config:** `USE_PLUGIN_SCORE=True`; `SPIKE_THRESHOLD=1000.0`.
**Why:** `USE_PLUGIN_SCORE=True` switches OFF the derived capture/win points (the plugin is now authoritative). The `end` handler deliberately does **not** zero the `ms` baseline to avoid double-counting lingering post-mission snaps (which carry the final score for ~80s). A single-tick gain `> SPIKE_THRESHOLD` is flagged live as an exploit tripwire (`pts:0` ledger line) after the 2026-06-24 score explosion.

#### Lifetime ranks (11 tiers) + `ranks.json` ownership
**What:** Maintains a persistent SteamID-keyed economy with 11 rank tiers from Officer Cadet (0) to Air Chief Marshal (100000). Awards points, announces `** RANK UP **` in the tier colour. `ranks.json` is the single source of truth; the bot is the sole writer.
**How:** `RANKS` is an 11-tuple ladder; `rank_index_for()` finds the tier; `award_points()` adds points and returns `(old_idx,new_idx)`. `save_ranks()` writes atomically (`.tmp` + `os.replace` with retry) keeping a `.bak` + once-daily `ranks_backup_<date>.json` snapshot; `_maybe_save_ranks()` throttles score writes to ≥5s. `push_plugin_ranks()` writes `sid|ABBR|colour|idx|FullName` to `plugin_ranks.txt` (`:178-1726`).
**Config:** thresholds `0/50/200/500/1000/2500/5000/10000/25000/50000/100000`; `PLUGIN_RANK_PUSH_INTERVAL=120`.
**Why:** `ranks.json` must never be silently overwritten with empty/corrupt data — hence the `.bak` + daily snapshot before every write, and the refusal to back up an empty/unreadable current file. The command centre is a separate process and must NEVER write `ranks.json` directly — all manual changes flow through `process_admin_commands`. `_strip_rank_tag()` prevents the plugin's `[ABBR]` chat tag leaking back into names.

#### Points ledger + skill ledger (audit trails)
**What:** Append-only audit logs. `points_ledger.jsonl` records every discrete point event with a category; `skill_ledger.jsonl` records every banked life; `match_history.json` holds one record per match. Powers `!why`, `--audit`, and per-player history.
**How:** `ledger_award()` appends `{ts,match,steamid,name,pts,category,reason,balance}`. Per-match score is accumulated in `SCORE_ACCUM` and flushed as ONE aggregated `score` line per player at `match_finalize`. Informational categories (capture, score-spike) carry `pts:0` (`:1198-1349`).
**Why:** The `pts:0`-for-informational rule keeps the `--audit` invariant (sum of ledger pts == ranks points) holding since the real lifetime credit arrives via the snap stream. `match_finalize` recovers from a corrupt `match_history.json` by moving it to `.corrupt` and starting fresh.

#### NuclearSkill rating (points-per-life) + 0–10 ranking
**What:** A per-pilot rating = average points per life, surfaced as a 0–10 score for `!skill`, the leaderboard, and skill-based balance. A "life" runs from spawn until shot down or mid-air eject.
**How:** Each score gain feeds `rec['curLife']`; the plugin's `life` event (reason `death`/`eject`) BANKS `curLife` into `rec['skillPoints']`, increments `lives`, records `lastLife` (if >0), resets `curLife`. `skill_rating()=skillPoints/lives` once `lives>=SKILL_MIN_LIVES`. `skill_ranking()` linearly maps the rating onto 0–10. `push_plugin_skill()` writes `sid|rating` (`:1488-1813`).
**Config:** `SKILL_MIN_LIVES=5`; `SKILL_RESET_FLAG=skill_reset_v087.done`.
**Why:** The v0.8.7 model is persistent points-per-DEATH: a life survives disconnects AND match-ends. `maybe_reset_skills()` runs once (flag-file guarded) to zero data computed under the old match-bank rules. A scoreless life must not clobber `lastLife`.

#### Start-of-match bonus (1-minute gate) + win/placement bonuses
**What:** Grants `START_BONUS_PTS` to every present player once the match has been live ~1 minute (not immediately), with a one-time thank-you, plus "stay" reminders at 105/125/145 min. Win/placement bonuses come from the plugin's `award` events.
**How:** `check_match_milestones()` keys off the mission elapsed clock; a new mission = the clock jumping back to ~0. The bonus fires once when `START_BONUS_WINDOW <= elapsed <= +120s`. Adopting an in-progress mission pre-suppresses the bonus and passed reminders (`:1871-1932`).
**Config:** `START_BONUS_PTS=250 START_BONUS_WINDOW=60 STAY_MARKS=[6300,7500,8700]`.
**Why:** The 1-minute gate is deliberate: a quick restart/redeploy that never reaches 1 min hands out no bonus, so back-to-back restarts stop repeatedly paying the start bonus.

#### Kill bonus + underdog incentive (PvP)
**What:** On a plugin `kill` event, announces a coloured "splashed" line and awards `KILL_BONUS` plus an underdog bonus scaled by how many rank tiers the killer is BELOW the victim.
**How:** `handle_stats_line` `kill` branch records the killer for the webcc killfeed, computes `underdog_bonus(kid,vid) = UNDERDOG_PER_PLAYER * (victim_tier - killer_tier)` when positive, awards `KILL_BONUS+extra` (`:1556`, `:1402`).
**Config:** `KILL_BONUS=50 UNDERDOG_PER_PLAYER=10`.
**Why:** Underdog scaling is by rank TIER index; returns 0 if the killer is same-or-higher tier — a PvP incentive rewarding lower-ranked pilots for downing higher-ranked ones.

### 3.2 Player-facing systems

#### In-game chat commands
**What:** Answers `!rank`, `!skill`, `!points`, `!leaderboard`, `!why`, `!help`, `!balance`, `!notk`, `!votemap` (plus `!y`/`!n` and `!1..!6` tokens). Replies are routed to all-chat.
**How:** Each parsed chat line is lowercased and matched against literal command strings. `whisper()` sends via `send-chat-message` (not `rc.say`, to avoid double-logging) and logs one `[BOT]` activity line. `!rank` uses `rank_progress()`; `!skill` uses skill rating/ranking; `!points` shows `curLife` vs `lastLife`; `!why` dumps `recent_ledger_for(4)` (`:2219-2540`).
**Config:** `WHISPER_VIA_TELL=False`.
**Why:** `whisper` deliberately routes to all-chat because the plugin `tell` command no-ops on v0.7.4. **Note:** `!squadup` is advertised in `help_lines` but has no handler in the bot — it is plugin-side. Replies sanitize `|` to `/` so they can't break the pipe-delimited `plugin_cmd` protocol.

#### Welcome system (5s delayed queue)
**What:** Welcomes each player once per session ~5s after first seen, showing rank + points (and an `!help` nudge for new pilots), and logs joins/leaves. A quick join/leave produces no welcome.
**How:** `queue_welcome(sid,name)` stores `(deadline=now+WELCOME_DELAY, name)`. The main loop drains the queue: if the player is still in `ROSTER_BY_SID` at the deadline, `say_welcome()` fires. On leave, `WELCOMED.discard` + `WELCOME_QUEUE.pop` so a rejoin is re-welcomed (`:1370-2633`).
**Config:** `WELCOME_DELAY=5.0 JOIN_POLL_INTERVAL=5`.
**Why:** The delay lets the client/chat finish loading so the player actually sees the welcome. Iterating the full current set means a player first seen before their name synced still gets welcomed once the name resolves — avoiding "A pilot" welcomes.

#### Activity feed + dashboard publishing
**What:** Writes one tidy timestamped English line per meaningful event to `activity.log`; mirrors raw console lines; emits `dashboard_state.json` for the web CC.
**How:** `activity(msg, tag)` appends to `ACTIVITY_FILE` (best-effort). `mirror_console_batch()` writes a poll's console lines in one open/write/close; `trim_console_mirror` keeps it bounded. `write_dashboard_state()` atomically emits the mission/vote header + per-player table + map blips + killfeed (`:260-2456`).
**Config:** `LOG_CONVERSATION=True STATE_WRITE_INTERVAL=1 _CONSOLE_MIRROR_MAX=2MB`.
**Why:** All publishing is best-effort and must never crash the bot. `dashboard_state.json` is written atomically with a 5× retry on Windows `PermissionError` because a reader can hold the file open at `os.replace` time. Plugin `chat` events are re-reported because the plugin's reformatted chat suppresses the normal log line.

#### Mission timer + timed chat warnings
**What:** Announces "Mission time: N minutes remaining" as it crosses 60/20/10/5/1 min, once each per mission. Keeps `CURRENT_MISSION` fresh.
**How:** Every 15s, `get-mission-time` → `find_number()`; `refresh_current_mission()` settles `CURRENT_MISSION` FIRST, then `check_mission_time_warnings()` resets its fired-set on name change and announces each `WARN_THRESHOLD` crossed (`check_mission_time_warnings:1851`, `find_number:583`).
**Config:** `WARN_THRESHOLDS=[3600,1200,600,300,60]`.
**Why:** `refresh_current_mission` runs before the warning check so the dedupe key is the final mission name, never the transient post-vote name, preventing a double warning. `find_number()` recursively searches the JSON reply so the exact schema needn't be known.

#### Team-balance helpers the bot drives
**What:** The bot doesn't move players for autobalance; it pushes the data the plugin needs (`plugin_ranks.txt` rank index + `plugin_skill.txt` skill) and relays admin team actions, plus periodic PvP balance/spectate tips.
**How:** `admin_team()` relays move/spec/join/balance/setrank/setfunds/addfunds via `_drop_plugin_cmd` (`plugin_cmd_<id>.txt`). The spectator tip only appears in PvP (`:2154-2266`).
**Config:** `SPECTIP_INTERVAL=1020` (17 min).
**Why:** The actual autobalance/move logic lives in the plugin; the bot's role is to keep the data files fresh and relay admin commands.

### 3.3 Admin queue & scheduling

#### `admin_commands.jsonl` queue processing (grant / team / changemap)
**What:** Applies admin actions queued by the web CC: grant/subtract points, team moves, and a forced map change. The bot is the sole writer of `ranks.json` so all manual point changes funnel here.
**How:** `process_admin_commands()` tails `admin_commands.jsonl` by byte offset (skipping pre-existing lines at startup). `admin_grant()` resolves the player via `resolve_player()` (exact SteamID, raw SteamID, exact name, unique prefix/substring, then fuzzy via difflib ≥0.82). `changemap` calls `force_change_map()` and returns True so the loop suppresses the auto mission-end vote and forces state IDLE (`:2070-2114`).
**Why:** `resolve_player` requires a unique match at every step so it never silently grants the wrong player (game names are often truncated). A `changemap` must override the auto map-vote, hence suppression + cooldown + state=IDLE.

#### Scheduled restarts/updates executor (community-facing warnings)
**What:** Executes one-off server restarts/updates the web CC schedules, warning players 5 min and 1 min before, then firing the guarded deploy.
**How:** `check_schedule()` reads `schedule.json` each 15s; warns at `SCHED_WARN` thresholds (deduped per item), and when due posts a chat warning and launches `deploy.bat` as a detached subprocess (so the daemon survives the bounce and reconnects) (`:1970-2705`).
**Config:** `SCHEDULE_FILE=schedule.json SCHED_WARN=[300,60]`.
**Why:** Both `restart` and `update` funnel through the same `deploy.bat` → relay-verified pipeline. Firing via a detached subprocess is intentional so the deploy can stop/start the server while the bot keeps running.

### 3.4 Ops, deploy & infrastructure

#### SFTP console.log tail (chat/event ingest)
**What:** Reads new console-log lines over SFTP — the bot's only inbound channel from the game.
**How:** `SFTPConsoleSource` keeps one paramiko SSH+SFTP session and tails `SFTP_LOG_PATH` (`/logs/console.log`) by byte offset: the first poll seeds `pos=size` to skip backlog; thereafter reads `[pos..size)`, splits on newline, retains the partial tail. Detects rotation/truncation (`size<pos → pos=0`) and reconnects on any exception (`:647-685`).
**Why:** Polling over SFTP was chosen because the host exposes no console websocket. Seeding `pos` to EOF on first read avoids replaying stale votes on start. stdin/out/err are reconfigured to utf-8/replace at import so a non-Latin player name can never crash the tail.

#### Localhost relay + length-framed remote-command protocol (`RemoteCommand`)
**What:** The bot's only OUTBOUND control channel: sends ServerCommands (`send-chat-message`, `get-player-list`, `set-time-remaining`, `set-next-mission`, etc.) and reads structured replies. Authoritatively answers "is the game actually serving?".
**How:** The game's remote-command port binds container-localhost only (`127.0.0.1:5504`). A wrapper-launched in-container relay (`no_relay.py`/`.pl`/ncat/socat) re-exposes it on `0.0.0.0:5550`; the host forwards `<SERVER_HOST>:5550`. `RemoteCommand.send()` writes a 4-byte LE length + JSON `{name, arguments}`; the reply = 4-byte LE status (`2000`=Success) + 4-byte LE body length + JSON. `_relay_alive()` calls `get_players()` and treats any list as "up" (`:508-565`).
**Why:** The relay exists purely because `ServerRemoteCommands` binds localhost-only and the panel startup command can't be edited. The relay (not panel state) is the authoritative serving check because panel state flaps.

#### Pterodactyl power control (client API)
**What:** Starts/stops/restarts the game server and reads its power state via the Pterodactyl client API, Cloudflare-aware.
**How:** `_pt_cfg()` loads the API token from `apiKey.txt` and base URL + server id from `panel.txt`. `_pt_api()` issues urllib JSON requests with `Authorization: Bearer`, JSON Accept/Content-Type, and a spoofed desktop-Chrome User-Agent (`_PT_UA`), 15s timeout. `_pt_power_signal` POSTs `{signal}` to `/power`; `_pt_state` GETs `/resources` for `current_state`. `disable_panel_restart()` flips the panel's own Restart schedule to `is_active=false` (`:3017-3230`).
**Why:** The custom Chrome User-Agent satisfies Cloudflare in front of the panel. Panel power state is unreliable for this egg (it flaps to "starting" on mission reloads), which is exactly why the deploy verifies liveness through the relay.

#### Daily plugin-deploy pipeline (`deploy_plugin_job`)
**What:** The guarded daily job that stages a new BepInEx DLL (only if changed) and restarts the server so the new DLL loads, never knowingly leaving the server offline.
**How:** Takes a 15-min stale-guarded lock (`pending_plugin.dll.lock`). Computes sha256 of `pending_plugin.dll` vs `deployed_plugin.sha256`. If updating, uploads the DLL FIRST while still up via atomic SFTP put-to-tmp + `posix_rename` over `BepInEx/plugins/NukeStats.dll` (mmap-safe). Then power-cycles: `stop` → poll `_pt_state` for offline (~90s) → `start` → poll `_relay_alive()` (~120s). On relay-verified success it writes `deployed_plugin.sha256` + `deployed_plugin.json` and archives the DLL. **Guardrail:** from the stop onward, every failure path force-sends START (`:3089-3210`).
**Why:** BepInEx has no hot-reload, so a DLL swap requires a process restart. Upload-before-stop minimizes the window. The relay is the authoritative serving check; the force-START guardrail is the central safety invariant.

#### 05:00 Windows Scheduled Task + `deploy.bat`
**What:** Fires the deploy pipeline automatically every day at 05:00 AEST (low-traffic).
**How:** Scheduled Task `NukeOption_DailyPluginDeploy` (daily 05:00 +10:00) runs `deploy.bat`, a one-liner that calls `run.bat --deploy-plugin` so the `NO_SFTP_*` env is populated for the upload.
**Why:** Routing through `run.bat` (not python directly) is deliberate: only `run.bat` sets the SFTP credentials the atomic upload needs. Per memory the pipeline has rarely staged a real new DLL (usually "unchanged → restart only"), but the task is live and succeeding.

#### Server bootstrap: wrapper install / revert / check
**What:** One-off installer that makes the un-editable panel launch command run the game WITH the bot's required flags and the relay, plus revert and a diagnostic.
**How:** `setup_server()` renames the real ELF `NuclearOptionServer.x86_64` → `NuclearOptionServer` (dropping `.x86_64` still maps to `NuclearOptionServer_Data`) and installs a `/bin/sh` wrapper AT the original name. The wrapper sets `LD_LIBRARY_PATH`, truncates logs, launches the first available relay tool (`0.0.0.0:5550→127.0.0.1:5504`), `tail -F`s `console.log` to the panel, then execs `./NuclearOptionServer -logFile ./logs/console.log -limitframerate 30 -ServerRemoteCommands 5504` (`:3789-3961`).
**Why:** Unity derives its data folder from the executable name minus extension, so simply dropping `.x86_64` keeps `_Data` valid — wrapping a fixed launch command without symlinks. The whole approach exists because the Pterodactyl egg's startup command is not editable.

#### Live config-edit CLIs
**What:** Surgically edit remote server/plugin config (`DedicatedServerConfig.json`, mission JSONs, BepInEx cfg) with strong anti-corruption guards.
**How:** Each reads the remote file, makes a minimal marker-anchored single-token change, re-parses to confirm valid JSON, runs a full deep-diff against the intended change (refusing to upload if anything else moved), and writes a local backup under `_server_backup/` first. `set_votekick` flips `VoteKick.Enabled` then calls `reload-config`; `_edit_faction_values` bounds AI-limit edits to a single faction's text span (`:3258-3678`).
**Why:** The pattern is paranoid (surgical edit + JSON round-trip + deep-diff + backup) because a corrupted `DedicatedServerConfig` can stop the server from starting. Votekick was disabled defensively in response to the 2026-06-26 mass-DC investigation.

#### Self-heal, keep-alive wrapper, and interactive command centre
**What:** Keeps the bot alive across bugs and process death, and provides a coloured interactive console.
**How:** The CLI default branch wraps `main()` in `while True: try/except` — KeyboardInterrupt exits cleanly; any other Exception logs, posts an activity line, and restarts in 5s. `run_keepalive.bat` is the outer net: it loops `run.bat`, relaunches after 5s on nonzero exit, stops on clean exit 0. `command_centre()` (`--centre`) is an in-process REPL exposing the 19 ServerCommands via aliases, gating destructive ones behind a typed `yes` confirm (`:4733` (self-heal) / `command_centre:4128-4167`).
**Why:** Two layers of resilience by design: in-process self-heal for Python exceptions, and the keepalive outer loop for kill/OOM/reboot. A clean Ctrl-C (exit 0) deliberately ends the keepalive loop so you can stop on purpose.

---

## 4. Component: Web Command Centre (`cc_web.py` + `webcc.html`)

A Flask backend on `127.0.0.1:8770` serving a single-page admin dashboard. The web process is **read-only with respect to live game state and `ranks.json`**: it tails the bot's feed files and queues admin intents back to the bot, so the two processes never race on persistent state.

### 4.1 Backend (`cc_web.py`)

#### Flask app + config bootstrap
**What:** Boots a single-page app: serves `webcc.html` at `/`, exposes the JSON API, loads the baked map atlas + Pterodactyl config.
**How:** `Flask(__name__, static_folder=None)`; imports `no_mapvote_bot as bot` to reuse its constants (`RANK_FILE`, `ADMIN_CMD_FILE`, `RCMD_HOST/PORT`, `RANKS`, mission lists, `CENTRE_SERVER_CMDS`) and the `RemoteCommand` class. `app.run(host='127.0.0.1', port=8770, threaded=True)` (`cc_web.py:20-636`).
**Why:** `static_folder=None` so Flask doesn't shadow the `/api` routes; bound to localhost only. Reusing the bot module avoids re-deriving rank tiers, mission lists, and the relay client.

#### `/api/state` — unified dashboard snapshot
**What:** The 1s poll endpoint returning the entire live state: server/mission/timer/players/entities/air/killfeed from `dashboard_state.json` plus tailed activity + filtered console, a derived `map_key`, `server_age` freshness, and deploy/staging status.
**How:** Reads `dashboard_state.json` into `st`; appends `st['activity']=_tail(activity.log,80)` and `st['console']=_console_view(_tail(console_mirror.log,400), raw)`. Derives `map_key` from the mission name; `server_age = now - st.ts` (UI treats <8s as fresh); `st['deploy']=_deploy_status()`. `?raw=1` disables console filtering (`cc_web.py:413-428`).
**Why:** The web process is read-only on live state; `server_age` lets the header dot show online/stale/offline without a separate health call.

#### `/api/cmd` dispatcher — command routing
**What:** Single POST endpoint handling EVERY command name, in three classes: (a) read-only local (leaderboard/ranks from `ranks.json`), (b) relayed to game via the socket (say/rankpreview/nextmap/endmission + raw wire cmds like kick/ban), (c) queued to the bot via `admin_commands.jsonl` (changemap/grant/balance/setrank/setfunds/addfunds/move/join/spec).
**How:** Parses `{name,args,sid,...}`. `say` → `_send_cmd('send-chat-message', orange [Admin] text)` + mirrors to `activity.log`; `nextmap` → `set-next-mission`; `endmission` → `set-time-remaining 5`; `changemap`/`grant`/`balance`/`setrank`/… → `_queue_admin(...)`. Anything else falls through to a raw wire command via `CENTRE_SERVER_CMDS`, with `ok = (code==2000)` (`cc_web.py:460-578`).
**Why:** Actions that mutate `ranks.json` or need SFTP/cut-over orchestration are QUEUED so the running bot (single owner of ranks + SFTP + map-vote suppression) performs them. `changemap` goes through the bot (not `_send_cmd`) so the bot can suppress its own auto map-vote during the cut-over. Pure game RPCs go straight down the relay. `_rc_lock` serialises the shared socket (Flask is `threaded=True`).

#### `_queue_admin` (admin queue writer)
**What:** Appends one JSON record per line to the bot-owned queue for grant/team/changemap.
**How:** `_queue_admin(rec)` stamps `rec['ts']` and appends `json.dumps(rec)+'\n'` to `bot.ADMIN_CMD_FILE`. For `setrank/setfunds/addfunds` the numeric value is smuggled in the `faction` field (`cc_web.py:247-250`).
**Why:** The central decoupling: the web proc enqueues intent; the bot (holding ranks + relay + SFTP) applies it, keeping a single writer for persistent state.

#### Player resolution + faction normalisation
**What:** Turns a free-text player query into a concrete sid using the live roster; normalises faction aliases.
**How:** `_resolve_player(q)`: digit query → sid match; else case-insensitive substring with exact-match preference; returns `(None,msg)` on 0 or >1 matches. `_faction_norm` maps `boscali/bdf/bosc/blue→boscali`, `primeva/pala/prim/red→primeva` (`cc_web.py:216-275`).
**Why:** Ambiguity is reported, not guessed (>1 match → "be more specific"), preventing the wrong player being moved/banned. When the popup supplies sid directly, resolution is skipped.

#### Ranks / leaderboard (read-only)
**What:** Computes the points leaderboard, the per-life skill leaderboard, and the full ranks table from `ranks.json` without ever writing it.
**How:** `_leaderboard()`: top-8 by points plus a skill board for players with `lives>=SKILL_MIN_LIVES`, each tagged via `bot.rank_index_for` + `bot.RANKS`. Served inline by `/api/cmd` (`cc_web.py:279-321`).
**Why:** Read-only by design; leaderboards are pure projections so they're safe to compute here and return synchronously.

#### Pterodactyl power + resources proxy (`/api/power`, `/api/resources`)
**What:** Real start/stop/restart/kill and a live CPU/MEM/uptime/state readout via the Pterodactyl client API.
**How:** `_pt_load()` (30s-cached, lock-guarded) reads `apiKey.txt` + `panel.txt`. `_pt_call` signs with Bearer + Chrome UA + default SSL. `_pt_power` validates the signal then POSTs `/power`; `_pt_resources` GETs `/resources` → `{state,cpu,mem_mb,uptime_s}` (`cc_web.py:324-405`).
**Why:** Browser-like UA + SSL context defeat Cloudflare checks; caching avoids hammering the panel each 5s poll. `panel.txt`'s two-line form lets the admin paste a browser URL directly.

#### Live plugin-version chip + deploy/staging status
**What:** Shows the live plugin version in the header and whether a NEW DLL is staged for the next deploy.
**How:** `_deploy_status()` reads `deployed_plugin.json` for the live version; if `pending_plugin.dll` exists it sha256s it, reads `pending_plugin.json` (version/note/staged_at + sha cross-check → `meta_ok`), compares to `deployed_plugin.sha256`, sets `new=True` when the staged sha differs. Returned in `/api/state` as `st['deploy']` (`cc_web.py:34-82`).
**Why:** sha comparison (not just version string) means a rebuilt-but-same-version DLL still reads as "new"; `meta_ok` flags a sidecar/DLL mismatch.

#### Schedule modal + endpoints (`/api/schedule` GET/POST/delete)
**What:** Lets the admin schedule one-off restarts or staged-plugin updates; the bot polls `schedule.json` and executes them.
**How:** `_read_schedule`/`_write_schedule` do an atomic read/replace. POST validates `type ∈ {restart,update}`, parses `when` as `%Y-%m-%d %H:%M`, rejects past times and update-without-desc, then appends `{id:'sch_<hexms>',type,when,desc,status:'pending',created}` (`cc_web.py:253-629`).
**Why:** Same UI-here/executed-by-bot split as `admin_commands.jsonl`: the dangerous restart runs through the bot's guarded pipeline, not the web proc. Atomic `.tmp`+`os.replace` prevents a half-written file.

### 4.2 Front-end (`webcc.html`)

#### Live map — slippy pan/zoom over a baked terrain PNG
**What:** A canvas map: pan (drag), zoom (wheel/dblclick/buttons toward cursor), recenter to terrain (◎), fit-whole-grid (⤢), and a fullscreen mode flanked by killfeed + player-list panels.
**How:** `/api/map?key=` returns atlas metadata; `/api/mapimg?key=` serves `<key>_map.png`. The PNG is the ONLY thing that scales (`drawImage` at `view.scale`); all overlays draw in SCREEN space so they stay crisp. `fitView()`=terrain fit, `fitFullScale/minScale` bound zoom-out to the whole grid, `zoomAt()` keeps the cursor world-point fixed, `clampView()` limits panning (`webcc.html:336-393`).
**Why:** Screen-space overlays + a single scaled PNG keep grid/labels pixel-crisp at any zoom. A `ResizeObserver` + debounced resize re-render so the canvas never lags its container.

#### Map overlays — hierarchical grid, gutters, base rings, cursor ref
**What:** Draws the major A1.. grid, a faint 10×10 minor sub-grid with SAR-style addresses (Aa10..), top/left coordinate gutters, faction-coloured base rings, and a live cursor grid-ref readout.
**How:** `drawMinorGrid()` only draws when a minor cell is ≥14px on screen, labels at ≥46px; address = `MajorRow + minorRow + ((MajorCol+1)*10+minorCol)`. Bases from `atlas.bases`: Boscali `#7CA6FF`, Neutral `#aeb6c2`, else PALA `#FFD21E`, drawn at constant screen size (`webcc.html:339-518`).
**Why:** The minor grid is viewport-culled and zoom-gated so it appears only when there's room. Base colours encode faction (blue Boscali / yellow PALA / grey Neutral).

#### Interpolated blips — players, AI aircraft, ships (60fps glide)
**What:** Renders player aircraft (ringed disc + plane `▲` / heli `+` + bright name label), AI aircraft (ring-less glyph, no label), and ships (class-shaped silhouettes), all gliding smoothly between the ~1s/~5s poll anchors.
**How:** `drawMap()` records each player's `prev/curr {x,z,ts}` in `posHistory` keyed by sid; `recordEntities()` does the same for `st.entities {a,s}` keyed by `'a'/'s'+i`. `render()` interpolates each blip ONE observed interval in the past so `mapRenderTime` falls between `prev→curr`. Dead players (`p.grounded`) draw a frozen red `✝`. A `rafLoop` runs only when there are blips and the tab is visible (`webcc.html:382-554`).
**Why:** "One interval in the past" is the standard technique to glide across the real data gap rather than extrapolating; the gap adapts to the actual cadence. Named=player vs unnamed=AI is the deliberate bot-vs-player tell.

#### Players panel (table + click-to-act popup)
**What:** A table of online pilots (name, faction tag, rank, points, aircraft, in-game rank, match points, skill, grid coords); clicking a row opens an action popup (grant / move / spectate / copy SteamID / kick / ban).
**How:** `renderPlayers()` builds rows from `st.players`; faction via `facShort()`→`BDF`/`PALA`. Row onclick → `playerPopup(p)` whose buttons call `sendCmd()` with `{sid,name,...}` so the backend skips name resolution. kick/ban require `confirm()` and go down the raw wire path (`webcc.html:621-858`).
**Why:** Passing sid+name directly from the popup avoids the ambiguous-name path entirely.

#### Activity / Console panels (tail + noise filter)
**What:** Activity shows colour-chipped chat/joins/kills/ranks/votes/admin lines; Console shows server stdout with a noise filter that collapses known-benign engine/AI/Steam spam into a summary line, with a raw/filtered toggle.
**How:** `_console_view` → `_classify()` buckets lines, promotes error-token lines to `error`; when filtered, only show/error lines pass while the rest are tallied into a `— filtered N× —` summary. Both feeds use signature-diff to skip rebuilds and pin-to-bottom only when already near bottom (`cc_web.py:106-156`, `webcc.html:592-619`).
**Why:** The classifier mirrors the documented benign-noise list so the admin sees real errors, not vanilla spam. `say`-broadcasts are mirrored into `activity.log` by the backend because admin broadcasts are server RPCs the bot can't re-parse as chat.

#### AI-count / air-traffic panel
**What:** A strip under the map showing AI vs player aircraft per faction with the limiter caps, turning a chip red at/over cap.
**How:** `renderAir(st.air)` reads `{s:[{n,ai,pl}], ai,pl,totcap,teamcap}`; sorts sides by faction name to stop dictionary-order chip jitter (`webcc.html:555-570`).
**Why:** Fixed side ordering is deliberate — the plugin emits sides in dictionary order which flips between ticks.

#### Command bar — input, autocomplete, palette, `/api/commands` catalog
**What:** A command input with context-aware autocomplete (Tab/arrows/Enter), an output log, and an "≡ all commands" palette listing every server + bot/local command with args/desc and a danger marker.
**How:** `/api/commands` = `_catalog()` = the bot's `CENTRE_SERVER_CMDS` (minus raw `say`, replaced by the local say that adds `[Admin]` + activity mirror) tagged `group:'server'`, plus `_LOCAL_CMDS` tagged `group:'bot'`, each with an `ac` autocomplete hint (mission/player/steamid/pf/pn). `suggestions()` drives the dropdown (`cc_web.py:159-191`, `webcc.html:740-800`).
**Why:** The catalog merges live wire commands with synthetic local ones so one bar covers both relay RPCs and bot-queued actions. Dropping raw `say` avoids a duplicate without the `[Admin]` prefix/mirror.

#### Change-Map button + modal
**What:** A header button opening a searchable mission picker that ENDS the current match and cuts over immediately (without the auto map-vote overriding it).
**How:** `pickChangeMap()` confirms then `sendCmd('changemap',[m])`; backend `changemap` → `_resolve_mission()` then `_queue_admin({action:'changemap',name:full})` — routed through the BOT (`webcc.html:802-809`, `cc_web.py:501-507`).
**Why:** Deliberately queued to the bot, not sent as a direct `set-next-mission` RPC, because the bot's auto map-vote would otherwise re-override the choice.

#### Header status + resource sparklines + smooth timer
**What:** Header shows server online/stale/offline dot, mission, a smoothly counting-down time-left, player count, live plugin version, and CPU/MEM sparklines.
**How:** `tick()` (1s) sets the dot from `server_up` + `server_age<8` and interpolates wall-clock seconds since `time_at` (the ~15s-stale bot sample) so the timer ticks each second instead of freezing then jumping. `tickRes()` (5s) hits `/api/resources` and pushes cpu/mem into rolling 120-point histories drawn by `spark()` (`webcc.html:676-738`).
**Why:** Timer extrapolation only runs while the feed is fresh (don't run the clock during an outage).

---

## 5. Component: Live-map pipeline (terrain atlas + renderer + telemetry)

A baked terrain atlas generated offline from in-game grid screenshots, calibrated to the game's own printed grid labels, that the web CC pans/zooms over while drawing a live `a1..p` grid plus blips fed by the plugin's `pos`/`air`/`ent` telemetry.

#### Terrain atlas + PNG generation (`build_map_atlas.py`)
**What:** Offline build that turns the two source screenshots (`map-build/heartland.png`, `map-build/ignus.png`) into the runtime artifacts: a clean green-on-black PNG per map (`heartland_map.png` / `ignus_map.png` in ROOT) and `map_atlas.py` (an `ATLAS` dict of name, cols/rows, calibrated bounds, `xmin/cell/znorth`, base catalogue, and a coarse `terr` grid for the legacy TUI).
**How:** `main()` loops `MAPS`, calls `build_terrain(m)` which opens the screenshot, calibrates via `label_calibrate.calibrate()`, computes a per-pixel greenness map, optionally strips/de-artifacts the baked grid, downsamples, removes specks/thin lines, and renders a recolored RGBA PNG. Outputs are written to ROOT where cc_web/bot read them (`build_map_atlas.py:210-410`).
**Why:** Rebuild with `python map-build\build_map_atlas.py`. The `terr` ASCII grid is kept only for the legacy TUI, not the web map.

#### Per-map calibration pinned to printed grid labels
**What:** Establishes the exact pixel↔world affine transform per screenshot by reading the game's OWN printed row letters (E..L) and column numbers in the margins. This is what makes blips, bases and the grid line up with real game coordinates.
**How:** `label_calibrate.detect_labels()` scans margins for near-white text, clusters runs, takes weighted centroids; `calibrate()` least-squares-fits `z = az*py + bz` (row E = grid index 4) and `x = ax*px + bx` (col 4..); bounds `x0=bx, x1=ax*W+bx, z0=bz, z1=az*H+bz` (`label_calibrate.py:38-84`).
**Why:** PER-MAP `xmin` is load-bearing and differs: Heartland col1 = `xmin -70000` (verified by 12 airbase refs); Ignus is wider, col1 = `xmin -110000` (verified via Broken Atoll world x). Calibration validated against a RANSAC ground-truth fit.

#### Faithful green-on-black terrain render
**What:** Produces a clean topographic PNG keeping green where the source is green and black where it is water/void — deliberately NOT solid-filling the interior — and strips the screenshot's baked grid/labels/markers so the web map draws its own grid.
**How:** `greenness()` computes per-pixel green-dominance; non-green (water, UI, markers) reads 0. The work map is downsampled taking the brightest green per cell; components/specks and thin line-fragments removed via BFS; a final recolor maps `g<=18 → WATER (6,13,22)` and ramps brighter greens (`build_map_atlas.py:106-332`).
**Why:** Two grid-removal strategies coexist: Heartland uses `strip_grid=True` (detect+interpolate the real baked grid lines) plus optional morphological deartifact; Ignus is left on the ORIGINAL filter path so its already-perfect render stays byte-for-byte identical.

#### Base catalogue (12 Heartland refs + Ignus detected/admin bases)
**What:** Populates each atlas's `bases` list — faction-coloured airfield/heliport rings. Heartland uses 12 exact admin-supplied grid refs (ground truth); Ignus auto-detects coloured markers and adds admin-supplied bases the detector misses.
**How:** `bases_for()` branches on map key. Heartland: `HEART_BASES` (12 entries) → `ref_world()`. Ignus: `analyze_maps.detect()` finds yellow (Primeva)/purple (Boscali) markers, converted to world; `drop_refs={'H5','H18'}` excludes two SHIPS mis-detected as airbases; `IGNUS_EXTRA` admin bases decoded via `decode_ref()` (`build_map_atlas.py:355-378`).
**Why:** Heartland refs are admin ground truth so bases are placed exactly. Feldspar International was moved from the cell centre (over water) to its actual airfield SW of centre, rendered Neutral (grey).

#### Grid system (a1..p major + 10×10 minor)
**What:** A hierarchical grid drawn live over the terrain: major cells A1..P{gcols} (rows A..P = 16) each subdividing into a 10×10 minor grid shown only when zoomed in. Terrain + pips occupy the exact calibrated sub-region while the grid is roamable out to the full map extent.
**How:** Atlas stores `xmin/cell(10000)/znorth(80000)/gcols`. webcc derives the full extent `ext()` from `xmin + gcols*cell`; `grid_ref()` maps world→major label; `drawMinorGrid()` renders the sub-grid past a zoom threshold (`build_map_atlas.py:33-41`, `webcc.html:339-518`).
**Why:** `gcols` = the FULL in-game grid width (Heartland 15, Ignus 23) so the web map can pan/zoom OUT to the whole extent even though terrain+pips stay at their calibrated sub-region (cols 4–11 Heartland / 4–19 Ignus).

#### Plugin `pos`/`air`/`ent` telemetry → live coordinates
**What:** Real-time feed: the plugin emits JSON the bot ingests into in-memory state and the web CC renders as blips. `pos` = flying-player positions (~2s), `air` = counts (perf panel), `ent` = AI-aircraft + ships (~5s).
**How:** Bot dispatch: `pos` updates `POS[sid]=(x,z,ts,k)` and clears `DOWNED`; `air` sets `AIR`; `ent` sets `ENT={'a':[…],'s':[…]}` with per-unit instance id `i`. `write_dashboard_state()` exposes players (x/z + dead/downed flag), air, entities (omitted when stale >15s). A life death/eject sets `DOWNED` so the map shows a player dead instantly (`no_mapvote_bot.py:240-414`, `:1464-1510`).
**Why:** `ent` units carry per-unit `i` specifically for client-side glide interpolation; AI/ships have no SteamID so render without a name label — the bot-vs-player tell. Stale pos (>~6s) ⇒ the player is no longer flying ⇒ rendered dead/ejected.

#### World-to-screen coordinate transform
**What:** The math converting a game world coordinate (x east, z north) to a canvas pixel, accounting for pan/zoom, so the PNG, the grid, and every blip stay registered. Z is inverted (north up).
**How:** `w2cx(wx) = mapM().ix + (wx-atlas.x0)*view.scale + view.panX`; `w2cy(wz) = mapM().iy + (atlas.z0-wz)*view.scale + view.panY`. The PNG is drawn at `w2cx(atlas.x0)/w2cy(atlas.z0)` scaled by `span()*view.scale`, so image and blips share one transform. Inverse `c2wx/c2wy` recover world from the cursor (`webcc.html:365-446`).
**Why:** A single transform anchored to the calibrated `x0/z0` (not the full grid extent) keeps PNG/grid/blips registered. Z is inverted because world-z increases north but canvas-y increases down. Interpolation renders one observed interval in the past so the factor falls between two known anchors.

---

## 6. Data Contracts & Integration

### 6.1 The `[NOSTATS]` wire schema

Every plugin→bot telemetry line is `[NOSTATS] {json}` written via `Debug.Log` to `/logs/console.log`. The bot matches `NOSTATS_RE = \[NOSTATS\]\s*(\{.*\})\s*$` and dispatches on the `t` field. All 13 line types:

| `t` | Cadence / trigger | Fields | Producer → Consumer effect |
|---|---|---|---|
| `snap` | every `SnapshotSeconds` (10s, min 2s) | `id,n,f,s,rk,tk,ac` | Full per-player snapshot. Bot accumulates lifetime points via the `ms` baseline delta and feeds `curLife`. |
| `score` | opportunistic, single player | `id,n,f,s,rk,tk,ac` | Same payload as `snap`; bot ingests identically. |
| `win` | end of game (debounced 20s, Victory) | `f` (winning faction) | Authoritative winner. Bot announces VICTORY, tallies wins/losses by comparing each player's last `STATS_META` faction. |
| `award` | end of game, per player | `id,n,pts,reason` (`win`/`1st`/`2nd`/`3rd`) | Bot applies pts → `ranks.json`, ledgers `place_1st/2nd/3rd/win`, announces rank-ups. |
| `end` | match boundary | *(none)* | Bot clears `STATS_META`/`LIVE_SCORE`; deliberately keeps the `ms` baseline (avoids double-count of lingering snaps). |
| `life` | death or mid-air eject | `id,r` (`death`/`eject`) | Bot banks `curLife`→`skillPoints`, +1 `lives`, appends `skill_ledger.jsonl`, marks player DOWNED + killfeed entry. |
| `capbonus` | location capture | `id,pts` | Bot folds pts into `curLife`; writes a `pts:0` audit ledger line. |
| `kill` | enemy human-vs-human kill | `kid,kn,vid,vn` | Bot announces "X splashed Y", computes underdog bonus, awards `KILL_BONUS+extra`. |
| `down` | every human death | `v,vn,k,ks,kp,ff` | Killfeed enrichment (player OR AI/SAM). `kp=1` killer is a player (sid `ks`); `ff=1` friendly fire. |
| `pos` | ~2s | `p:[{id,x,z,k}]` | Flying-player positions (`k`=`p`/`h`). Bot stores `POS[sid]`; absence ⇒ dead/ejected. |
| `air` | ~5s (after AI limiter) | `s:[{n,ai,pl}],ai,pl,teamcap,totcap` | Per-side counts + caps for the perf panel. |
| `ent` | ~5s (after `air`) | `a:[{i,x,z,f,k,g}],s:[{i,x,z,f,c}]` | AI aircraft + ships with per-unit `i` interpolation key. |
| `chat` | reformatted (rerouted) messages | `id,n,msg,all` | Re-reports rerouted chat (whose native log line was suppressed) to the activity feed. |

### 6.2 File-based channels (producer → consumer)

| Channel | Producer → Consumer | Format | Purpose |
|---|---|---|---|
| `/logs/console.log` | plugin (Unity log) → bot (SFTP tail) | text; `[NOSTATS] {json}` + native lines | The one-way telemetry pipe + native chat/capture/result/mission-end lines. |
| `plugin_ranks.txt` | bot (SFTP) → plugin (`LoadRankMap`) | `sid\|ABBR\|#colour\|idx(1..11)\|FullName` | Rank tags/colours for chat + labels; 4th field = balance weight. |
| `plugin_skill.txt` | bot (SFTP) → plugin (`LoadSkillMap`) | `sid\|rating(2dp)` | Per-pilot skill so the plugin can balance by skill (qualified pilots only). |
| `plugin_cmd_<id>.txt` | bot (SFTP) → plugin (`PollCommands`, deletes) | `verb\|steamId\|arg` (`\x1f`-split tell body) | One-shot admin command relay (move/spec/join/balance/tell/setrank/setfunds/addfunds/swapteam). |
| `plugin_bans.txt` | plugin ↔ plugin | one SteamID/line | Persistent teamkill bans (plugin self-managed, NOT bot-written). |
| `plugin_squads.txt` | plugin ↔ plugin | tab-separated `sid~name` tokens/line | Persistent `!squadup` groups (plugin self-managed). |
| `anz.nukestats.cfg` `[Admin] SteamIds` | operator → plugin | comma-sep SteamID list (BepInEx config, not a file) | Authorises in-game admin chat commands. |
| `ranks.json` | bot (sole writer) → cc_web (read-only) | `sid → {name,points,ms,curLife,skillPoints,lives,lastLife,wins,losses}` | The lifetime economy / source of truth. |
| `points_ledger.jsonl` | bot | `{ts,match,steamid,name,pts,category,reason,balance}` | Point-event audit; `--audit` invariant: Σpts == ranks points. |
| `skill_ledger.jsonl` | bot | `{ts,steamid,name,score,reason,counted}` | Per-life audit. |
| `match_history.json` | bot | list of per-match records | Win%, best, last-5. |
| `dashboard_state.json` | bot (atomic, ~1s) → cc_web (`/api/state`) | see §6.4 | The live snapshot the web CC renders. |
| `activity.log` | bot (+ cc_web appends `[ADMIN]`) → cc_web (tail 80) | `HH:MM:SS AM  [TAG]  msg` | Human-readable event feed. |
| `console_mirror.log` | bot (~2MB bounded) → cc_web (tail 400) | raw console lines | Lets the web CC show the console without its own SFTP creds. |
| `admin_commands.jsonl` | cc_web (append) → bot (byte-offset tail) | `{action:grant\|team\|changemap, …, ts}` | Web→bot command queue (all `ranks.json` mutations funnel here). |
| `schedule.json` | cc_web (atomic) → bot (poll + execute) | list of `{id,when,type,desc,status}` | Scheduled one-off restarts/updates. |
| `pending_plugin.dll` (+ `.json`) | build/op → bot deploy; cc_web reads (hash) | binary + `{version,note,sha256,staged_at}` | Staged plugin build for the next deploy. |
| `deployed_plugin.sha256` / `.json` | bot deploy → cc_web | hex sha / `{version,sha,deployed_at}` | Record of the LIVE build (panel state is unreliable). |
| `apiKey.txt` / `panel.txt` | admin → bot + cc_web | bearer key / panel URL+id | Pterodactyl client API config. |
| `map_atlas.py` + `<key>_map.png` | `build_map_atlas.py` → cc_web | `ATLAS` dict + RGBA PNG | Baked map calibration + terrain. |

### 6.3 Transport channels (non-file)

- **Remote-command port (bot/cc_web → game):** TCP to `RCMD_HOST <SERVER_HOST> : RCMD_PORT 5550` (host relay → container `127.0.0.1:5504`). Request frame = 4-byte LE length + JSON `{name, arguments:[str…]}`. Reply = 4-byte LE status (`2000`=Success) + 4-byte LE body length + JSON body. Carries `send-chat-message`, `set-next-mission`, `set-time-remaining`, `get-mission-time`, `get-player-list`, `kick-player`, `banlist-add`, `reload-config`. Also the authoritative `_relay_alive` liveness check.
- **Pterodactyl client API (bot/cc_web → panel):** HTTPS urllib with `Authorization: Bearer` + spoofed Chrome User-Agent (Cloudflare-aware). `POST /servers/{id}/power` (stop/start/restart/kill), `GET /resources` (state/cpu/mem/uptime), `GET/POST /schedules` (disable native restart).
- **In-process import:** `cc_web` does `import no_mapvote_bot as bot` to reuse `RemoteCommand`, `RCMD_HOST/PORT`, `RANK_FILE`, `ADMIN_CMD_FILE`, `RANKS`, `rank_index_for`, `CENTRE_SERVER_CMDS`, `STATUS_CODES`, `SKILL_MIN_LIVES`, and the mission lists.

### 6.4 `dashboard_state.json` shape (bot → web CC)

```
{ ts, bot_pid, server_up, mission, state, online_count,
  time_current, time_max, time_at, plugin_live, vote, approval,
  players:[{ sid, name, faction, aircraft, rank_abbr, rank_color, points,
             ingame_rank, match_points, teamkills, wins, losses, skill,
             x, z, grounded, klass, fresh }],
  air:{ s:[{n,ai,pl}], ai, pl, totcap, teamcap },
  entities:{ a:[{i,x,z,f,k,g}], s:[{i,x,z,f,c}] },
  killfeed:[{ vname, vfac, kname, kfac, kp, x, z, ts }] }
```

### 6.5 End-to-end walkthrough: one PvP kill

1. **In-game:** Pilot A downs enemy Pilot B. The game calls `FactionHQ.ReportKillAction` and `Unit.ReportKilled`.
2. **Plugin (kill):** `KillPatch` → `OnKill` confirms B is an enemy human and emits `[NOSTATS] {t:"kill",kid:A,kn:…,vid:B,vn:…}`.
3. **Plugin (down):** `CheckTeamkill` emits `[NOSTATS] {t:"down",v:B,vn:…,k:A's name,ks:A,kp:1,ff:0}`.
4. **Plugin (life):** `EndLife(B,"death")` emits `[NOSTATS] {t:"life",id:B,r:"death"}` and `GuardEject` is *not* set (a real death), so it counts.
5. **Transport:** all three lines land in `/logs/console.log` via `Debug.Log`.
6. **Bot ingest:** the SFTP tail reads the new bytes; `handle_stats_line` dispatches:
   - `kill` → records A as the killer, computes `underdog_bonus(A,B)`, `award_points(A, KILL_BONUS+extra)`, ledgers a `kill` line, announces "A splashed B" to chat (and a `** RANK UP **` if A crossed a tier).
   - `down` → `_record_killer` attaches A's info to the next killfeed entry (correlated within 8s).
   - `life` → banks B's `curLife` into `skillPoints`, +1 `lives`, appends `skill_ledger.jsonl`, marks B `DOWNED` and inserts a `KILLFEED` entry, sets `_SKILL_PUSH_FLAG`.
7. **Persistence:** `ranks.json` is saved (≥5s throttle), `points_ledger.jsonl`/`skill_ledger.jsonl` appended. `_RANK_PUSH_FLAG`/`_SKILL_PUSH_FLAG` coalesce a later SFTP push of `plugin_ranks.txt`/`plugin_skill.txt`.
8. **Publish:** `write_dashboard_state()` emits the new killfeed entry + A's updated points; B's `grounded=true` flag flips (no fresh `pos`).
9. **Web CC:** the browser's 1s `/api/state` poll reads `dashboard_state.json`; the killfeed panel shows the kill, B's blip freezes to a red `✝`, A's row updates. The activity panel shows the chat line tailed from `activity.log`.

---

## 7. DATA & FILES reference

`<root>` = `C:\Users\Server\Documents\Nuke Option Server`. `<game root>` = the remote container's game directory.

| File | Owner (writer) | Read by | Purpose |
|---|---|---|---|
| `<game root>/logs/console.log` | plugin (Unity `-logFile`) | bot (SFTP) | `[NOSTATS]` telemetry + native chat/event lines. |
| `<game root>/BepInEx/config/anz.nukestats.cfg` | BepInEx defaults / admin | plugin | All plugin tunables (see §2 config tags). |
| `<game root>/plugin_ranks.txt` | bot (SFTP) | plugin | Rank tags/colours/weights. |
| `<game root>/plugin_skill.txt` | bot (SFTP) | plugin | Per-pilot skill ratings for balance. |
| `<game root>/plugin_cmd_<id>.txt` | bot (SFTP) | plugin (deletes) | One-shot admin command relay. |
| `<game root>/plugin_bans.txt` | plugin | plugin | Persistent teamkill bans. |
| `<game root>/plugin_squads.txt` | plugin | plugin | Persistent squads. |
| `BepInEx/config/anz.nukestats.cfg` `[Admin] SteamIds` | operator | plugin | In-game admin authorisation (config key, not a file). |
| `<game root>/no_relay.py` / `.pl` | bot (`--setup-server`) | wrapper | Container TCP relay `5550→5504`. |
| `<game root>/logs/relay.log` | wrapper | bot (`--check-server`) | Relay tool selection + probe results. |
| `<root>\ranks.json` | bot (sole) | cc_web (RO) | Lifetime economy / source of truth (+`.bak` + daily `ranks_backup_<date>.json`). |
| `<root>\points_ledger.jsonl` | bot | bot, `--audit` | Point-event audit. |
| `<root>\skill_ledger.jsonl` | bot | bot | Per-life audit. |
| `<root>\match_history.json` | bot | bot | Per-match records (recovers to `.corrupt`). |
| `<root>\dashboard_state.json` | bot (atomic) | cc_web | Live snapshot. |
| `<root>\activity.log` | bot (+cc_web `[ADMIN]`) | cc_web | Human-readable feed. |
| `<root>\console_mirror.log` | bot (bounded) | cc_web | Console mirror. |
| `<root>\admin_commands.jsonl` | cc_web | bot | Web→bot command queue. |
| `<root>\schedule.json` | cc_web (atomic) | bot | Scheduled restarts/updates. |
| `<root>\pending_plugin.dll` (+`.json`) | build/op | bot deploy, cc_web | Staged plugin build. |
| `<root>\pending_plugin.dll.lock` | bot deploy | bot | Deploy mutex (stale 15 min). |
| `<root>\pending_plugin.dll.deployed-<ts>` | bot deploy | — | Archived deployed DLL. |
| `<root>\deployed_plugin.sha256` | bot deploy | bot, cc_web | Change detector for the daily job. |
| `<root>\deployed_plugin.json` | bot deploy | cc_web | LIVE build record for the version chip. |
| `<root>\deploy_plugin.log` | bot deploy | — | Deploy log (last 400 lines). |
| `<root>\apiKey.txt` | admin | bot, cc_web | Pterodactyl API bearer token. |
| `<root>\panel.txt` | admin | bot, cc_web | Panel base URL + server id. |
| `<root>\skill_reset_v087.done` | bot | bot | One-time skill-reset flag. |
| `<root>\keepalive.log` | `run_keepalive.bat` | — | Bot launch/exit/relaunch record. |
| `<root>\bot_output.log` | `run_keepalive.bat` | — | Captured bot stdout/stderr. |
| `<root>\_server_backup\` | config-edit CLIs | — | Backups of remote config before edits. |
| `<root>\map_atlas.py` | `build_map_atlas.py` | cc_web, bot, TUI | Baked map calibration (auto-generated). |
| `<root>\heartland_map.png` / `ignus_map.png` | `build_map_atlas.py` | cc_web | Clean terrain PNGs. |
| `<root>\map-build\heartland.png` / `ignus.png` | admin (screenshot) | `build_map_atlas.py` | SOURCE grid screenshots. |

---

## 8. `run.bat` CLI reference

`run.bat [args]` sets the `NO_SFTP_*` environment (host/port/user/pass/logpath — **credentials live in the file and are not reproduced here**) then runs `no_mapvote_bot.py` with all args passed through. Every SFTP-touching command must go through `run.bat` so credentials are present. With no args it starts the self-healing bot.

| Command | Purpose |
|---|---|
| *(no args)* | Start the bot daemon (IDLE/APPROVAL/VOTING loop). |
| `--deploy-plugin` | Guarded daily deploy: stage `pending_plugin.dll` if its sha differs, then stop→offline→start→relay-verify; force-START on any failure. |
| `--deploy-plugin-dry` | Pre-flight only: report panel state, pending-update status, relay-alive; no power/upload action. |
| `--put-atomic <local> <remote>` | Upload via `<remote>.deploytmp` + `posix_rename` (mmap-safe DLL swap). |
| `--put <local> <remote>` | Plain upload (preserves inode+mode — for executable scripts, then `--chmod-exec`). |
| `--get <remote> <local>` | Download a remote file. |
| `--ls [path]` | List a remote directory (dirs first). |
| `--cat <remote> [maxbytes]` | Print a remote text file (default 200 KB) — the way to inspect `BepInEx/LogOutput.log`. |
| `--chmod-exec <remote>` | `chmod 0755` a remote file. |
| `--setup-server` | Install the launch wrapper (adds `-ServerRemoteCommands 5504` + console log + relay; uploads relays). Idempotent/reversible. |
| `--revert-server` | Undo `--setup-server`. |
| `--check-server` | Diagnostic: launcher/`_Data`/log sizes+ages, console freshness, startup grep, relay tail. |
| `--set-votekick <on\|off>` | Flip `VoteKick.Enabled` (guarded) then `reload-config`. |
| `--set-server-name` | Replace `ServerName` (effective next full restart). |
| `--add-rotation <Name> [Group] [MaxTime]` | Idempotently append a `MissionRotation` entry. |
| `--set-ai-limits [--dry-run]` | Edit per-faction AI limits across co-op mission JSONs (faction-span-bounded, deep-diff verified). |
| `--set-balance-diff <n>` | Set plugin `[Balance] MaxDifference` (0..10; BepInEx file-watch applies live). |
| `--apply-map-changes [--dry-run]` | Apply staged mission/map position edits. |
| `--disable-panel-restart` | Set the panel's native Restart schedule `is_active=false`. |
| `--upload-bepinex` | Push the local BepInEx pack + built DLL (RUN ONLY WITH SERVER STOPPED). |
| `--centre` / `--center` | Interactive coloured REPL (19 ServerCommands via aliases, destructive confirms, local helpers). |
| `--testconn` | Verify the relay via `get-mission-time`. |
| `--testchat` | Tail the SFTP console 20s and print chat the parser sees. |
| `--testtunnel` | Probe an SSH `direct-tcpip` forward to `127.0.0.1:5504`. |
| `--findchat` | Pull the log and show chat-ish lines + whether the chat regex matches. |
| `--say <msg>` / `--cmd <name> [args]` / `--players` / `--colortest` / `--endmission` | One-shot relay sends. |
| `--probe-missions` | Discover valid built-in mission Group/Name via the relay. |
| `--selftest` | Offline parser checks (ballots, `extract_vote`, rank thresholds, event parsing). |
| `--check-ranks` / `--fix-ranks` / `--ranks` / `--audit` / `--matchtest` / `--scanlog` / `--ctxlog` / `--rankpreview` | Gameplay/data inspection CLIs. |

**Companion launchers:**
- `deploy.bat` — one-liner Scheduled-Task entrypoint: `run.bat --deploy-plugin`. Also runnable manually for a real deploy+restart.
- `run_keepalive.bat` — outer supervisor: loop `run.bat`, relaunch on nonzero exit (5s), stop on clean exit 0; logs to `keepalive.log` + `bot_output.log`.

**In-game chat (handled by bot/plugin):**
- *Bot:* `!rank`, `!skill`, `!points`, `!leaderboard`, `!why`, `!help`, `!balance`, `!notk`, `!votemap`, `!1..!6`, `!y`/`!n`.
- *Plugin public:* `!autobalance`/`!ab`, `!forfeit`/`!ff`/`!surrender`, bare `!spec`/`!spectate`, `!squadup`/`!squad`/`!su`, `!y`/`!yes`.
- *Plugin admin:* `!move`/`!team`/`!join <player> <faction>`, `!spec [player]`, `!balance`, `!setrank <player> <n>`, `!setfunds`/`!addfunds <player> <amount>`, `!swapteam`/`!forceteamswap <player>`.

---

## 9. Web `/api` reference (names + purpose only)

| Endpoint | Method | Purpose |
|---|---|---|
| `/` | GET | Serve `webcc.html`. |
| `/api/state` | GET | Unified 1s snapshot (state + activity + console + map_key + server_age + deploy). `?raw=1` disables console filtering. |
| `/api/cmd` | POST | The command dispatcher (see below). |
| `/api/commands` | GET | The command catalog (server + bot commands with args/desc/danger). |
| `/api/map?key=` | GET | Atlas metadata (bounds, bases, gcols) for a map. |
| `/api/mapimg?key=` | GET | The terrain PNG for a map. |
| `/api/power` | POST | Pterodactyl power signal (start/stop/restart/kill). |
| `/api/resources` | GET | Live state/cpu/mem/uptime. |
| `/api/schedule` | GET / POST | List / create a scheduled restart/update. |
| `/api/schedule/delete` | POST | Remove a scheduled item by id. |

`/api/cmd` command names, grouped by routing:

- **Read-only local (computed from `ranks.json`):** `leaderboard`/`lb`/`top`, `ranks`.
- **Relayed to game (remote-command socket):** `say` (adds `[Admin]` + activity mirror), `rankpreview`, `nextmap`, `endmission`, and any raw wire command from `CENTRE_SERVER_CMDS` (e.g. `kick-player`, `banlist-add`).
- **Queued to the bot (`admin_commands.jsonl`):** `changemap`, `grant`, `balance`, `setrank`, `setfunds`, `addfunds`, `move`, `join`, `team`, `spec`.

---

## 10. Design invariants (cross-cutting)

These principles recur across all three processes and are worth stating once:

1. **`ranks.json` has exactly one writer — the bot.** The web CC and plugin never touch it directly. All manual point changes funnel through `admin_commands.jsonl` so there is a single writer, a consistent ledger, and no cross-process race.
2. **The ledger invariant:** Σ(`points_ledger.jsonl` pts) == `ranks.json` points. Informational events (capture, score-spike) carry `pts:0` because the real credit arrives via the `snap` stream.
3. **The relay, not the panel, is the source of truth for "is the game serving."** Pterodactyl power state flaps; `_relay_alive()` (a `get-players` round-trip) is authoritative, and the deploy guardrail force-STARTs on any failure path.
4. **Atomic writes everywhere shared:** every shared file is written `.tmp` + `os.replace` (with a Windows `PermissionError` retry) so a reader never sees a half-written file.
5. **Fail-open enforcement:** teamkill, flood-guard Layer B, PvE timeout, and config edits all no-op on any error so the plugin never falsely kicks a player or corrupts a config.
6. **Life-neutral admin actions:** every admin/balance/swap eject is `GuardEject`-protected so it never banks a phantom death or spams the killfeed — skill-lives only end on a real death or a player's own mid-air eject.
7. **UI-here / executed-by-bot:** the web CC expresses *intent* (queue files); the bot, which holds the credentials, ranks, and orchestration, performs the *action*.