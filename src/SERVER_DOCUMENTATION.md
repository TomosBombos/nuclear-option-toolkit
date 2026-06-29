# Nuclear Option Community Server Toolkit — Architecture & Operations Guide

This toolkit turns a stock **Nuclear Option dedicated server** into a persistent, community-friendly competitive server with real per-player scoring, server ranks, map voting, automated team balance, teamkill enforcement, a live tactical map, and a browser command centre — without modifying the game client. It is built from three cooperating pieces: a **server-side BepInEx plugin (NukeStats)** that runs inside the game process and senses real player state; an always-on **Windows orchestrator bot (`no_mapvote_bot.py`)** that owns all persistence (ranks, ledgers, match history) and drives map voting and admin commands; and a **web command centre (`cc_web.py` + `webcc.html`)** that gives operators a real-time dashboard, a calibrated live map, and Pterodactyl power control. The whole system is designed so that anyone can run their own server: this guide documents the architecture, the design history behind each decision, and an end-to-end setup walkthrough.

---

## Table of Contents

1. [Architecture at a Glance](#architecture-at-a-glance)
2. [The Game Server, Wrapper & Relay](#the-game-server-wrapper--relay)
3. [The NukeStats Plugin](#the-nukestats-plugin)
4. [The Bot (`no_mapvote_bot.py`)](#the-bot-no_mapvote_botpy)
5. [Web Command Centre, Live Map & Operations](#web-command-centre-live-map--operations)
6. [Design History & Rationale](#design-history--rationale)
7. [Set Up Your Own Server From Scratch](#set-up-your-own-server-from-scratch)
8. [Data Files & Contracts Reference](#data-files--contracts-reference)
9. [`[NOSTATS]` Event Reference](#nostats-event-reference)
10. [Glossary & Acronyms](#glossary--acronyms)

---

## Architecture at a Glance

The fundamental challenge this toolkit solves: **the game's remote-command port binds only to `127.0.0.1` and the host blocks SSH port forwarding**, and the game exposes only faction-level scores, not per-player performance. The solution is a multi-layer bridge between the Linux game container and a Windows PC running the bot, plus an in-process plugin that senses real player state and writes it to the same console log the bot already tails.

```
                         REMOTE CONTAINER (Linux, Pterodactyl)
                   ┌──────────────────────────────────────────┐
                   │  NuclearOptionServer.x86_64               │
                   │  (launch wrapper + game binary)           │
                   │                                           │
                   │  Binds:                                   │
                   │   - UDP 5504 (game traffic)               │
                   │   - TCP 127.0.0.1:5504 (remote-commands)  │
                   │                                           │
                   │  In-process: BepInEx + plugins            │
                   │   (NukeStats: scoring, chat, balance,     │
                   │    teamkill, AI limiter, live positions)  │
                   │                                           │
                   │  Emits [NOSTATS] {json} → logs/console.log│
                   └──────────────────────────────────────────┘
                        │                          │
            TCP 127.0.0.1:5504             [NOSTATS] + chat + events
            (localhost-only)               → logs/console.log
                        │                          │
              ┌─────────┴─────────┐                │
              │  Relay (in container)              │
              │  no_relay.py / .pl                 │
              │  listens 0.0.0.0:5550 ──► :5504    │
              └─────────┬─────────┘                │
                        │                          │ (SFTP read)
                   INTERNET                        │
                        │                          │
        ┌───────────────┴──────────────────────────┴───────────────┐
        │                    Windows PC                              │
        │                                                            │
        │   Bot (no_mapvote_bot.py)                                  │
        │     ├─ RemoteCommand client ──► relay:5550 (commands)      │
        │     ├─ SFTP tail of console.log (votes, chat, scores)      │
        │     ├─ owns ranks.json / ledgers / match_history.json      │
        │     ├─ writes dashboard_state.json (every 2s)              │
        │     └─ pushes plugin_ranks.txt / plugin_skill.txt (SFTP)   │
        │                                                            │
        │   Web Command Centre (cc_web.py :8770 + webcc.html)        │
        │     ├─ reuses the bot's RemoteCommand relay                │
        │     ├─ polls dashboard_state.json / activity.log           │
        │     ├─ live map (pan/zoom canvas over baked terrain PNG)   │
        │     └─ Pterodactyl power control (apiKey.txt + panel.txt)  │
        └────────────────────────────────────────────────────────────┘
```

**Three communication paths tie it together:**

1. **Commands (bot → game):** length-prefixed JSON over TCP, through the in-container relay that bridges the localhost-only command port to the WAN.
2. **Events (game → bot):** SFTP tail of `console.log`, carrying both native game messages (chat, votes, mission boundaries) and the plugin's `[NOSTATS]` telemetry on a single connection.
3. **Plugin config (bot → plugin):** the bot pushes `plugin_ranks.txt` / `plugin_skill.txt` over SFTP; the plugin reads them to render rank tags and weight team balance.

No game-client modification is required; all customization lives in the plugin (in-process sensor/enforcer) and the bot (persistence and ladder logic).

---

## The Game Server, Wrapper & Relay

The Nuclear Option dedicated server runs as a **containerized Linux service** (Pterodactyl-based). It communicates with the bot via a multi-layer architecture that bridges the remote server's localhost-only services and the Windows PC running the bot.

**Why this architecture exists:** the game's built-in remote-command port only binds to `127.0.0.1:5504` for security and cannot be reached externally; SSH port forwarding is blocked on the host. Therefore (1) an in-container **relay** bridges the localhost port to the outside world; (2) **console log tailing** happens over SFTP because the panel exposes a shell account but no stats/metrics API; (3) **BepInEx plugins** emit data to that same log, so one SFTP connection carries both game messages and real-time stats.

### Server Binary & Wrapper Script

The game ships as **`NuclearOptionServer.x86_64`** (Linux x64). On the live server this path is replaced with a **shell wrapper** that orchestrates startup — it starts the relay, ensures a stable log path, and then execs the real game binary:

```bash
#!/bin/sh
# Runs the relay, forwards console to a stable log, then execs the real game binary
mkdir -p ./logs
python3 no_relay.py 0.0.0.0:5550 127.0.0.1:5504 &
exec ./NuclearOptionServer.x86_64 \
    -logFile ./logs/console.log \
    -limitframerate 60 \
    -ServerRemoteCommands 5504 \
    "$@"
```

(The production deployment uses the Perl relay for single-process efficiency — see below.)

**Launch flags:**

- **`-logFile ./logs/console.log`** — writes all server output (plugin messages, chat, mission events, errors) to a stable filename the bot tails over SFTP.
- **`-limitframerate 60`** — caps the server tick rate at 60 Hz (prevents CPU runaway; affects the physics timestep).
- **`-ServerRemoteCommands 5504`** — opens the TCP command port on `127.0.0.1:5504` for the relay to expose.

**Pterodactyl startup command (BepInEx via Doorstop):**

```bash
export DOORSTOP_ENABLED="1"; \
export DOORSTOP_TARGET_ASSEMBLY="$(pwd)/BepInEx/core/BepInEx.Preloader.dll"; \
export LD_LIBRARY_PATH=".:$(pwd)/linux64:$LD_LIBRARY_PATH"; \
export LD_PRELOAD="libdoorstop.so:$LD_PRELOAD"; \
./NuclearOptionServer.x86_64
```

These env vars instruct the Doorstop library (loaded via `LD_PRELOAD`) to inject the BepInEx preloader into the Unity process **before the game's main thread runs**. The wrapper's relay + `-ServerRemoteCommands` flags inherit the Doorstop environment and work alongside plugin loading, so the process hosts both BepInEx (for in-process stats sensing) and the remote-command listener (for the bot's control channel).

### The Relay (`no_relay.py` / `no_relay.pl`)

A **transparent TCP relay** runs inside the container, forwarding external connections to the localhost command port.

**Python version (`no_relay.py`)** — single file, dependency-free. For each client connection it spawns a thread to pump client→upstream and pumps upstream→client in the accept loop. It is transparent: the bot's length-prefixed JSON passes straight through.

```python
#!/usr/bin/env python3
"""Tiny TCP relay so an off-box client can reach a localhost-only port."""
import socket, sys, threading

def _pump(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data: break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try: s.shutdown(socket.SHUT_RDWR)
            except OSError: pass

def _handle(client, target):
    try:
        upstream = socket.create_connection(target, timeout=10)
    except OSError as e:
        sys.stderr.write(f"[relay] upstream connect failed: {e}\n"); client.close(); return
    t = threading.Thread(target=_pump, args=(client, upstream), daemon=True); t.start()
    _pump(upstream, client)

def main():
    if len(sys.argv) != 3:
        sys.stderr.write("usage: no_relay.py LISTEN_HOST:PORT TARGET_HOST:PORT\n"); sys.exit(2)
    lh, lp = sys.argv[1].rsplit(":", 1)
    th, tp = sys.argv[2].rsplit(":", 1)
    target = (th, int(tp))
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((lh, int(lp))); srv.listen(16)
    sys.stdout.write(f"[relay] listening on {lh}:{lp} -> {th}:{tp}\n"); sys.stdout.flush()
    while True:
        try: client, _ = srv.accept()
        except OSError as e:
            sys.stderr.write(f"[relay] accept error: {e}\n"); continue
        threading.Thread(target=_handle, args=(client, target), daemon=True).start()
```

**Perl version (`no_relay.pl`)** — the live deployment. It uses `select()` (I/O multiplexing) instead of threads, making it single-process and more efficient on resource-constrained hosts. It is tuned for Nuclear Option's protocol quirk: **the game closes the TCP connection after every command response**, so the relay must keep the **client (bot) connection alive across requests** while opening a **fresh upstream to the game per request**. This preserves the client across the game's connection closes, avoiding the "just-written reply gets dropped" bug that plagued earlier versions.

**Startup:** `python3 no_relay.py 0.0.0.0:5550 127.0.0.1:5504` (or `perl no_relay.pl ...`). It listens on all interfaces port 5550 and forwards to the game's localhost port 5504.

### Remote-Command Protocol

The bot communicates with the game via **length-prefixed JSON over TCP**:

```
[4-byte status code (little-endian)]
[4-byte body length (little-endian)]
[JSON body (UTF-8)]
```

A status code of **2000 = Success**; other codes indicate errors (e.g., 4000 = command not found, 5000 = execution failed).

**Commands used by the bot:**

| Command | Arguments | Returns / effect |
|---|---|---|
| `get-mission-time` | — | `{"currentTime":s,"maxTime":s}` (remaining = `maxTime - currentTime`) |
| `get-mission` | — | current mission metadata (group, name) |
| `set-next-mission` | `["Group","Name"]` | queues the next map |
| `set-time-remaining` | `["seconds"]` | cuts the current mission short (rollover) |
| `send-chat-message` | `["text"]` | broadcasts chat to all players |
| `get-player-list` | — | array of `{steamid, displayName, faction, ...}` |

The bot connects to `RCMD_HOST:RCMD_PORT` (the relay endpoint) and issues these via its `RemoteCommand` class.

### `DedicatedServerConfig.json`

The game's config controls mission rotation, voting rules, and server identity. It is loaded **once at startup**; changes require a server restart (upload via SFTP / panel). The bot can **read** it (via `get-mission` / `get-player-list`) but does not write it directly during play.

```json
{
  "MissionDirectory": "/home/container/NuclearOption-Missions",
  "ServerName": "[YOUR SERVER | PvE & PvP | Persistent !rank | !votemap | !help]",
  "Port":      { "IsOverride": true, "Value": 5504 },
  "QueryPort": { "IsOverride": true, "Value": 5550 },
  "Password": "",
  "MaxPlayers": 32,
  "BanListPaths": ["ban_list.txt"],
  "DisableErrorKick": true,
  "NoPlayerStopTime": 30.0,
  "PostMissionDelay": 80.0,
  "RotationType": 2,
  "MissionRotation": [
    { "Key": {"Group":"User","Name":"Escalation Co-op as BDF - Afternoon"}, "MaxTime": 7200.0 }
  ],
  "VoteKick": {
    "Enabled": true, "PassRatio": 0.6, "MinVotes": 3, "AutoBanThreshold": 3,
    "VoteDuration": 45.0, "ResolutionDisplayTime": 20.0, "NewVoteLockout": 10.0,
    "RequesterCooldown": 300.0
  }
}
```

Key settings:

- **`Port` / `QueryPort`** — the game's main port **5504** (the localhost-only TCP remote-command service binds here, exposed via `-ServerRemoteCommands 5504`) and **`QueryPort` 5550** (the Steam server query, UDP). Note: port **5550 is also the relay's public TCP listener** (forwarding `0.0.0.0:5550 → 127.0.0.1:5504`) — **no clash**, because the Steam query is UDP while the relay/remote-commands are TCP. Set both with `IsOverride: true`.
- **`NoPlayerStopTime`** (30s) — idle (zero-player) mission auto-restart.
- **`PostMissionDelay`** (80s) — wait after a mission ends before loading the next. This must exceed the bot's 60s vote window with margin; a prior value of 60 caused the vote to race the auto-rotation.
- **`MissionRotation`** — the pool the bot pulls from; each entry has `Group`, `Name`, and `MaxTime` in seconds (this deployment uses **10800s / 3h**; the bot's `MISSION_MAX_TIME` override governs voted missions, this array is the fallback). When the timer expires the game silently rotates — unless the plugin's PvE `TimeoutForceDefeat` is enabled (then the human team is declared defeated).
- **`VoteKick`** — the game's built-in vote-kick (independent of the bot's map vote).

The bot can append missions with `run.bat --add-rotation "Mission Name" [Group] [MaxTime]` (edits the remote JSON, idempotent, backed up).

### BepInEx & Plugin Loading

**BepInEx** is a Unity plugin loader. It loads **at startup only** (no hot reload). Container layout:

```
BepInEx/
  core/   BepInEx.dll, BepInEx.Preloader.dll, 0Harmony.dll, ...
  plugins/ NukeStats.dll          ← the in-game sensor/enforcer
  config/  anz.nukestats.cfg      ← tuning parameters
libdoorstop.so                     ← the LD_PRELOAD hook
doorstop_config.ini
```

Load sequence: the panel startup command sets `LD_PRELOAD=libdoorstop.so` and `DOORSTOP_TARGET_ASSEMBLY=BepInEx/core/BepInEx.Preloader.dll`; when the game is exec'd, the loader pulls in `libdoorstop.so` before `main()`, which injects the preloader; the preloader scans `BepInEx/plugins/` and loads every `*.dll`; plugins run their `[BepInPlugin]` entry points and hook game methods via Harmony.

**Deployment safety — the mmap hazard.** Overwriting `NukeStats.dll` **in place** while the server runs can cause a `BadImageFormatException` because the live process has the old file mapped into memory. The fix is **`run.bat --put-atomic <local> <remote>`**: it uploads to a temporary `.deploytmp` file, then **atomically renames** it over the target via `posix_rename` (SFTP extension). The live process keeps its old inode; the new DLL loads on the next restart (BepInEx re-scans at startup). **Always use `--put-atomic` for plugin deploys, never plain `--put`.**

```bash
dotnet build NukeStats/NukeStats.csproj -c Release          # → NukeStats/bin/Release/NukeStats.dll
run.bat --put-atomic NukeStats/bin/Release/NukeStats.dll BepInEx/plugins/NukeStats.dll
# Next restart, BepInEx loads the new DLL.
```

Changes to `anz.nukestats.cfg` do **not** require a rebuild — BepInEx reads config at startup; just restart.

### Monitoring & Diagnostics (server side)

Console log lives at `logs/console.log` (container). Bot diagnostic flags:

- `run.bat --testconn` — verify the remote-command channel (sends `get-mission-time`).
- `run.bat --testchat` — tail chat for 20s.
- `run.bat --selftest` — offline self-test suite.
- `run.bat --scanlog NOSTATS` — confirm the plugin is emitting.

Healthy markers: `[relay] listening on 0.0.0.0:5550 -> 127.0.0.1:5504`, `[BepInEx]` load lines, and `[ServerRemoteCommands] New command connection received` when the bot connects.

---

## The NukeStats Plugin

The **NukeStats plugin** is a BepInEx/Harmony mod that runs **inside the dedicated server process** to provide real per-player scoring, unified chat reformat, automated team balance, skill tracking, teamkill enforcement, and AI/performance management. It exists because the game otherwise exposes only faction-level scores, not individual performance — making rank progression impossible without server-side introspection. The plugin stays **thin and event-driven** (a sensor and enforcer); the bot owns all persistence and ranking.

### Loading and Initialization

- **Entry point.** BepInEx mod (namespace `NukeStats`, GUID `anz.nukestats`, version 0.8.8). On `Awake()` it reads config from `BepInEx/config/anz.nukestats.cfg` (all tunables bound with defaults) and applies Harmony patches via `_harmony.PatchAll()` — 15+ methods across `FactionHQ`, `Player`, `Unit`, `ChatManager`, and `MessageManager`.
- **Deliberately NO `OnDestroy`/`UnpatchSelf`.** On the headless server, the BepInEx manager `GameObject` is destroyed shortly after load; an `UnpatchSelf` in `OnDestroy` was firing and **removing all hooks within milliseconds of applying them**. Harmony patches are static and persist at the process level, so they survive object destruction — the plugin never unpatches. (This was the root cause of a long debugging saga.)
- **Tick driver (`HQTickPatch`).** Since the plugin's own `MonoBehaviour.Update()` never fires on the dedicated server, all periodic work is driven from a Postfix on `FactionHQ.Update()` (called every frame during a live mission). Within one frame, each tick runs behind its own time gate: PvE-timeout check, 10s snapshot, dismounted-pilot cleanup, auto-balance (6s), join-bounce pump, kill-streak pump, skill life detection, ~2s position emit, teamkill enforcement, AI limiter (~5s), and admin-command poll.

### Real Per-Player Score Reading

The game's dedicated-server API only exposes faction-level `FactionHQ.factionScore`. Individual `Player.PlayerScore` exists server-side (a SyncVar, confirmed by decompiling `Assembly-CSharp.dll`) but is never exposed via remote commands or logs. Because the plugin runs **in-process**, it reads `Player.PlayerScore` directly, any time, with full authority and no I/O bottleneck (community servers have no EAC/BattlEye). It then **emits structured JSON to stdout via `Debug.Log()`** — Unity writes it to the `-logFile` (`console.log`), the same file the bot tails. Each event is prefixed with the marker `[NOSTATS] `; the bot's `NOSTATS_RE` regex parses it. (See the [`[NOSTATS]` Event Reference](#nostats-event-reference) for the full event catalog and fields.)

### Chat: Name Tagging, Reformat, and Profanity

Three modes, controlled by `Chat.RankInName` and `Chat.Reformat`:

- **Mode 1 — `RankInName = true` (default, enables TTS).** The rank abbreviation is embedded in the player's **chat name** at join (Prefix on `CmdSetPlayerName`), e.g. `[WGCDR] Tomo`. The game's native chat then shows the tag in faction colour and the built-in TTS speaks it. The bot reads chat from the native `CmdSendChatMessage` log line, so the plugin emits **no** `{"t":"chat"}` event in this mode (avoiding double-logging). The rank is read from `plugin_ranks.txt` (pushed by the bot).
- **Mode 2 — `ReformatChat = true` (used when `RankInName=false`).** Chat is **rerouted via server messages** (`RpcServerMessage`): the plugin intercepts `CmdSendChatMessage`, emits a `{"t":"chat"}` event, and broadcasts a custom-formatted, colour-coded line. **TTS does not run** in this mode. Commands (`!move`, `!votemap`) and votes (`!1`–`!6`) are detected and pass through unmodified so the bot still sees them in the native log.
- **Profanity filter (`IsRacist`, both modes).** A racist-slur gate runs before any chat branching. If a message normalises to a racist slur (after leetspeak/Cyrillic/spacing normalisation), the **entire message is replaced** with a harmless string. Ordinary swearing is **not** filtered. Detection uses two passes — an **anchored per-token** match against ~48 full slurs (with `+`-repeat expansion to catch `n+++i+++gg+++er`) plus a **de-spaced substring** match against distinctive roots (catches `fucknigger` and `n i g g e r`), with an allowlist for innocent words that embed a root (e.g., "snigger"). Validation: 58/58 slur variants caught, 83/83 innocent phrases clean.

### Chat Name & Kill-Feed Labelling

There is **one** player-name string, read by chat+TTS, the TAB scoreboard, and the faction info panel. With `RankInName=true`, the rank tag is *in* that string, so it appears everywhere and is spoken automatically (abbreviation only, e.g. "WGCDR", per user preference). The aircraft's networked `unitName` is a separate label set on every spawn: with `CustomKillFeed=true` it is the plane name only (so a pilot's name shows once via chat, not duplicated on radar/lock-on); with `CustomKillFeed=false` it is `<color=#rank>[ABBR]</color> Name [Plane]` so the rank colour shows through the native kill feed.

### NuclearSkill: Persistent Points-Per-Death Ranking

Skill is a **pure points-per-death ratio**: `rating = skillPoints / lives`, counted only at **≥5 lives**. The running per-life score lives in the **bot** (`rec["curLife"]` in `ranks.json`), fed by the same snap deltas that feed lifetime points — so it **survives disconnects AND match-ends**. A life ends and is banked **only on death or mid-air eject**; nothing else ends it.

This persistent model replaced an earlier one (plugin ended lives on every ejection and ejected everyone at match-end to bank scores) that had three flaws: a mid-life disconnect lost the accumulated score; a balance-move eject wrongly tanked a rating; and match-end ejecting required server coordination every round. Now the bot owns the score (travels across disconnects), admin moves don't end lives (marked life-neutral via the plugin's `_balancing` set), and there's no match-end eject.

Plugin contract: `{"t":"life","id":...,"r":"death"|"eject"}` (no score in the event — the bot owns it) and `{"t":"capbonus","id":...,"pts":250}` on base capture. Life **start** = player got an aircraft while not alive; **end** = aircraft gone while airborne and not in `_balancing`. Ground dismounts and disconnects do nothing (life stays open). When the plugin ejects a player for balance/probation/teamkill it calls `AdminEject`, which marks the sid in `_balancing` so the loss is counted life-neutral.

### Teamkill Enforcement

A Prefix on `Unit.ReportKilled` reads the dead unit's `damageCredit` (top damager, via reflection) to find the killer; if the killer is a human Player on the **same faction**, it's a teamkill (covers aircraft/vehicles/buildings). Per-match escalation (tracked in `_tkCount[sid]`): **1st** = eject + private "first warning"; **2nd** = private "next is a BAN" + delayed kick + flag rank-zero; **3rd** = add to `_tkBanned` + delayed kick. Bans persist in `plugin_bans.txt` (kicked on sight, re-queued if they rejoin). Per-match counters reset on a new mission; bans do not. Config: `[Teamkill] Enforce=true`.

### PvP Auto-Balance and Team Commands

Two mechanisms (config group `Balance`):

1. **Join guard** — when a player tries to join a faction that would make a side `> MaxDifference` (default 1) ahead, queue them for a bounce.
2. **Auto-balance** — every 6s, if a side is too far ahead and hasn't moved anyone in 20s (debounce), move the **rank/skill-optimal unspawned** player from the big side to spectate.

The **move primitive** bypasses the game's `SetFaction` guard via reflection (sets the HQ SyncVar directly, triggers the networked Dirty bit, re-registers tracking). A bounced joiner is moved to **spectate** (not the other team) to avoid orphaning a spawned aircraft — the guard is advisory. In-game admin commands (`!move`, `!spec`, `!join`, `!balance`) are gated by `[Admin] SteamIds`; `!autobalance`/`!ab` and self-`!spec` are public. The web command centre queues team actions via `admin_commands.jsonl` → the bot writes per-command `plugin_cmd_<id>.txt` files → the plugin's `PollCommands()` executes and deletes them. **Balance is skipped in PvE** (co-op is never balanced). Balance weight uses skill rating when `BalanceBySkill=true` (else server rank), picking the move that makes sides most even.

### Custom Kill Feed & Streak Announcements

With `CustomKillFeed=true`, a Prefix on `MessageManager.RpcKillMessage` suppresses the native global kill feed (which floods with AI), while the personal "you killed X" display is a separate RPC and is unaffected. Every kill registers into a 5s rolling window; once a burst settles, kill streaks are announced at tiers (5+/10+/25+/50+) in tier colours. Capital-ship sinks (carrier/destroyer) emit one deduped celebratory line. Player-vs-player kills emit `{"t":"kill",...}` for the bot to award a +50 bonus and announce "X just splashed Y!".

### AI Aircraft Limiter (v0.8.0+)

Caps AI aircraft to prevent over-spawning and runway clogging at high player counts. **It only removes AI, never players** (`Remove()` returns early if `ac.Player != null`). Three rules (~5s tick): per-team AI cap (`PerTeamAICap`, default 32); total aircraft cap (`TotalAircraftCap`, default 64 — when over, AI is pulled from the side with the most aircraft); and stuck-runway clear (a **grounded** AI that hasn't moved >25m for 45s is despawned). Removal uses the game's networked `DisableUnit()` (fallback `StartEjectionSequence()`), budgeted at 12 per tick. It emits the `{"t":"air",...}` count contract each tick for the web CC's air panel.

Verified game API (confirmed by compiling throwaway code against the game DLLs): `FindObjectsOfType<Aircraft>()` enumerates aircraft; `ac.Player == null` identifies AI; `ac.NetworkHQ` gives the faction; `ac.GlobalPosition()` returns x/y/z (y = altitude); `ac.IsLanded()` is the grounded check; `ac.DisableUnit()` is a public parameterless networked despawn; `ac.GetInstanceID()` is a stable per-aircraft key.

### Dismounted Pilot Cleanup

Ejected pilots lingering on the map are despawned for clutter/performance. Each `PilotDismounted` unit is timestamped on first sight; once older than `PilotLifetimeSeconds` (default 300s) it is removed via the same despawn the game uses on capture/landing. Config `[Cleanup] DismountedPilots=true`.

### PvE Timeout = Defeat (v0.8.8, default OFF)

When a PvE mission timer expires, the human team should lose rather than silently rotate. PvE is detected by enumerating `FactionHQ`s (AI side = `preventJoin==true`; human side = joinable); if exactly one joinable faction + ≥1 AI side is found, the timer has expired, and the game is still ongoing, the plugin calls `DeclareEndGame(Victory)` on the AI HQ → humans get "Mission Failed". All internal enums/classes are accessed via reflection with fail-safe no-ops, so it can't cause a false defeat or break on a game update. Config `[PvE] TimeoutForceDefeat=false` (flip to enable; no rebuild needed).

### End-of-Match Awards & Winner Determination

When a match ends, the game calls `DeclareEndGame("Victory")` on the **winning faction's HQ**. The plugin patches this and: emits a final `snap`, emits a `win` event naming the **authoritative** winner (bypassing the bot's old faction-0 guessing), awards `WinPoints` (default 200) to every player on the winning side, awards placement bonuses to the top 3 by score (1st=500, 2nd=250, 3rd=100, all tunable), and emits `end`.

### Build, Versioning & Patched Methods

Toolchain: .NET SDK 8 (`dotnet build`); the `.csproj` references game DLLs in `NukeStats/libs/` (pulled via `run.bat --get`, decompiled if member names change). Version milestones: v0.2–0.3 initial score snapshot + chat reformat; v0.4 rank-floor fix + RankInName; v0.5 profanity filter; v0.6 auto-balance + force-move + admin commands + custom kill feed; v0.7 NuclearSkill + teamkill enforcement; v0.8.0 AI limiter; v0.8.x score-exploit suppression (radar/spotting), mission-timer warnings, PvE timeout (held).

The 15 patched methods (v0.8.8): `FactionHQ.DeclareEndGame`, `Player.CmdSetFaction_*`, `FactionHQ.Update` (tick driver), `Player.SetAircraft`, `FactionHQ.ReportKillAction`, `MessageManager.RpcKillMessage`, `MessageManager.RpcPilotCaptureMessage`, `FactionHQ.ReportCaptureLocationAction`, `Unit.ReportKilled`, `FactionHQ.RewardPlayer`, `ChatManager.UserCode_CmdSendChatMessage_*`, `NetworkManagerNuclearOption.ServerMissionStartPlayer`, `Player.UserCode_CmdSetPlayerName_*`.

### Configuration (`anz.nukestats.cfg`)

All settings are tunable without a rebuild:

```ini
[Chat]
Reformat = true            ; Reroute chat as server messages
RankInName = true          ; Embed rank in player name (native chat + TTS)
ProfanityFilter = true     ; Replace messages containing racist slurs

[Scoring]
WinPoints = 200            ; Bonus per player on winning side
FirstPlace = 500
SecondPlace = 250
ThirdPlace = 100

[Stats]
SnapshotSeconds = 10       ; Seconds between full snapshots

[Balance]
Enforce = true
MaxDifference = 1          ; Max team-size gap
AutoMove = true
MoveOnlyUnspawned = true
RecheckSeconds = 6
MoveDebounce = 20

[Admin]
SteamIds = 7656119xxxxxxxxxx   ; Comma-separated admin SteamIDs

[Skill]
CaptureBonus = 250
WinBonus = 200
LossBonus = 50
BalanceBySkill = true

[KillFeed]
Custom = true

[Cleanup]
DismountedPilots = true
PilotLifetimeSeconds = 300

[Teamkill]
Enforce = true

[AILimit]
Enforce = true
PerTeamAICap = 32
TotalAircraftCap = 64
StuckSeconds = 45
StuckRadiusMetres = 25

[PvE]
TimeoutForceDefeat = false
```

---

## The Bot (`no_mapvote_bot.py`)

The bot is the always-on local orchestrator. It runs continuously on a Windows PC, tailing the game's console log over SFTP, parsing events, and sending commands via the relay. It **owns all server-side persistent state** (ranks, match history, ledgers) and drives the map-vote system, server ranks, in-game admin commands, and automated plugin deployment.

### Process Model

**Dual-channel design** — the game's console and command port are separate:

- **ACTIONS channel** — TCP `RemoteCommand` client sends JSON commands to the relay at `RCMD_HOST:RCMD_PORT` (which proxies to `localhost:5504` inside the container). Response = 4-byte status (2000 = success) + 4-byte body length + JSON.
- **VOTES channel** — reads lines from `console.log` over persistent SFTP via `SFTPConsoleSource`, tailing by byte offset (paramiko), reconnecting on failure. Credentials come from environment variables set by `run.bat` (`NO_SFTP_HOST`, `NO_SFTP_PORT`, `NO_SFTP_USER`, `NO_SFTP_PASS`, `NO_SFTP_LOGPATH`) — **never in code**. Local testing falls back to a file-tail `ConsoleSource`.

**Main loop** (~3.3 Hz, 0.3s sleep). Each cycle: console poll (every 1.5s — fetch new SFTP lines, mirror to `console_mirror.log`, parse votes/chat/events); vote/command handlers; admin-command drain (read new `admin_commands.jsonl` lines); player roster poll (every 5s — `get-player-list`, emit joins/leaves, queue 5s-delayed welcomes); periodic updates (dashboard every 2s; plugin rank/skill files every 120s; mission-time warnings every 15s; "thanks for playing" every 10 min; leaderboard every 30 min).

**Self-heal.** `main()` runs inside an outer `while True` that auto-restarts the bot on any uncaught exception (logging `[!] Bot hit an error…` to `activity.log`), surviving network blips, SFTP timeouts, and log rotations.

**Persistent SFTP session.** Hot paths (rank/skill pushes, whispers, team commands) reuse a single SSH/SFTP connection (`_BOT_SFTP`), checking liveness and reconnecting + retrying once on failure — avoiding a 100–300 ms handshake per command during a kill/rank-up burst.

### Map Vote, `!votemap`, Rotation & Mission Timer

On `[DedicatedServerManager] Mission complete`, the bot posts an end-of-mission rank roster, then `open_vote()` builds a ballot (keys 1–6): two random Escalation co-op missions, two random Terminal Control co-op missions, and two fixed PvP options (marked `[PVP]`). A **dark-map cap** ensures at most 3 of 4 options are Night/Thunderstorm/Overcast/Dusk; **no-repeat logic** avoids reusing the previous vote's pair. The vote runs `VOTE_DURATION = 60s`; players type `!1`–`!6` (bare numbers are ignored — no `!` = not a vote). Last vote wins per SteamID; ties break to the first-voted option, no votes → random.

On close, `apply_winner()` sends `set-next-mission(group, name, max_time)` and announces the result. If the vote was triggered mid-mission by **`!votemap`**, it also sends `set-time-remaining(10s)` to roll over immediately. `!votemap` while idle reads the roster: solo → auto-pass and open immediately; multiple players → a 30s approval poll needing a majority (caller auto-counts yes), then opens the map vote with `force_switch=True`.

**Mission-time warnings** — every 15s the bot polls `get-mission-time()` and announces once per mission at 60/20/10/5/1-minute thresholds (the warning set resets on mission change). `MISSION_MAX_TIME` = 10800s (3h) matches the server config; admins extend the pool with `--add-rotation`.

### Server Ranks: 11-Tier Ladder & Score Accumulation

Points are floats (0.1 precision). The 11 tiers:

| Threshold | Rank | Abbr | Colour |
|---|---|---|---|
| 0 | Officer Cadet | OFFCDT | `#8FA9C9` |
| 50 | Pilot Officer | PLTOFF | `#6E97D6` |
| 200 | Flying Officer | FLGOFF | `#4C84E4` |
| 500 | Flight Lieutenant | FLTLT | `#34C24A` |
| 1000 | Squadron Leader | SQNLDR | `#FF8C00` |
| 2500 | Wing Commander | WGCDR | `#C9A800` |
| 5000 | Group Captain | GPCAPT | `#FF3B3B` |
| 10000 | Air Commodore | AIRCDRE | `#B01818` |
| 25000 | Air Vice-Marshal | AVM | `#CD7F32` |
| 50000 | Air Marshal | AIRMSHL | `#D2D6DB` |
| 100000 | Air Chief Marshal | ACM | `#FFD700` |

**Real in-game score ingest** (`USE_PLUGIN_SCORE=True`). `handle_stats_line()` routes `[NOSTATS]` events: **snap/score** cache metadata and accumulate the score delta into lifetime `points` via a baseline (`ms` = last-credited in-match score, reset to 0 at match end; on a score decrease — reset/reconnect — it rebaselines without credit). **award** applies match-end bonuses and announces rank-ups. **win** tallies each online player's win/loss against the authoritative winner. **life** banks per-life score on death/eject only. **capbonus** adds to the current life. **kill** awards a PvP bonus (default +50, scaled by an underdog bonus when the killer's tier is below the victim's).

**Fallback (plugin OFF).** Without plugin score, captures award `CAPTURE_POINTS` to the capturing side (side tracked from the prior `Adding airbase … to <Side>HQ` line) and wins award `WIN_POINTS` to the winning side (winner inferred from `FinishGame <result>` + faction-0 mapping — the only PvP signal, confirmed correct by testing).

**Rank-ups & init.** `award_points()` compares old/new tier indices and, on a crossing, posts a colour-coded chat announcement + activity entry + save. Every player seen in `get-player-list` is ensured a rank-0 record via `ensure_player()`, so the roster captures everyone.

**Reducing points** is done through the grant queue: append `{"action":"grant","query":"<sid>","points":<negative>}`; this recomputes rank, saves, updates in-game tags, and appends the ledger (no demotion spam). The score-explosion exploit revealed the bot has **no per-snap delta clamp**; the fix was offered and declined, so it remains OPEN — watch for recurrence. On a reset, `ms` may stay inflated post-revert: **do not** set `ms=0` (the next snap would credit the whole new score) — **delete the key** to force a fresh baseline.

### NuclearSkill (Bot Side)

The bot accumulates per-life score in `rec["curLife"]` (fed by snap deltas), banking it into `rec["skillPoints"]` + incrementing `rec["lives"]` only on a `life` event with reason death/eject. Ground dismounts, disconnects, and balance moves never end a life. Rating = `skillPoints / lives`, counted only at ≥5 lives, scaled 0–10. Qualified players are pushed to `plugin_skill.txt` for skill-based balance.

### In-Game & Admin Commands

**Player commands (whispered/announced replies):** `!rank` (rank + points + to-next), `!skill` (rating + next pilot up, or "complete N more lives"), `!leaderboard` (top 5 by points + top 5 by skill), `!why` (last 4 ledger entries), `!help`, `!balance` (explainer), `!notk` (friendly-fire policy), `!spec` (go spectator).

**Admin-queue commands** (drained from `admin_commands.jsonl`): **grant** `{"action":"grant","query":"<sid_or_name>","points":<delta>}` and **team** `{"action":"team","verb":"move|spec|join|balance","sid":...,"faction":...}` (written as an atomic `plugin_cmd_<id>.txt` for the plugin).

**Welcome** — on join, a ~5s-delayed message shows the player's rank tag + name + points + "to next" (rank-0 also gets "+type !help"), deduped so a quick join/leave produces nothing.

### Deploy Automation CLIs (via `run.bat`)

**SFTP tooling:** `--put` (in-place), **`--put-atomic`** (temp + `posix_rename`, mmap-safe — use for DLLs), `--chmod-exec`, `--get`, `--ls`, `--cat`, `--cmd`.

**Mission/server config:** `--add-rotation`, `--set-server-name`, `--set-ai-limits [--dry-run]` (sets AI caps across PvE co-op missions; PvP skipped; deep-diff guarded).

**Deployment job:** **`--deploy-plugin`** (the daily ~05:00 job) stages a pending DLL atomically if changed, then **stop → wait offline → start → verify via relay**, with a guardrail that any failure from stop onward forces a START (never left offline). Uses the Pterodactyl client API (`apiKey.txt` + `panel.txt`, never echoing secrets) and verifies liveness via the relay (panel state is unreliable for this egg). `--deploy-plugin-dry` reports what *would* happen with no power/upload. `--disable-panel-restart` hands the 05:00 slot to the bot.

**Server setup (one-off):** `--setup-server` installs the launch wrapper (relay + stable log + exec) at the panel's entry point; `--revert-server` undoes it.

**Testing:** `--testconn`, `--testchat`, `--selftest`, `--rankpreview`, `--ranks`, `--players`, `--check-server`, `--scanlog`, `--ctxlog`, `--findchat`.

### Security (bot)

SFTP credentials live only in `run.bat` (read via env vars, never echoed). The relay is an unauthenticated proxy bridging the localhost-only port to the WAN. The admin queue and plugin-command files are local-only and unsigned (accessible only to the user and bot). Match/points/skill ledgers are append-only audit trails enabling post-hoc verification.

---

## Web Command Centre, Live Map & Operations

The web command centre, live map, and data infrastructure form a tightly integrated system: the backend reuses the bot's relay client, the frontend polls the bot's dashboard snapshots, and the map generator bakes terrain with label-detected calibration so blips align with the printed grid.

### Web Command Centre

**Stack.** It **replaced the Textual TUI** (`command_centre.py`) in June 2026 — the TUI was terminal-bound and could not reach Pterodactyl for real power control. The web CC uses vanilla HTML/CSS/JS + Flask + `urllib` (no extra deps; the server has neither `fastapi` nor `websockets`). Files: `cc_web.py` (Flask backend, port 8770), `webcc.html` (single-page frontend; vanilla JS, Canvas map, no build step), `webcc.bat` (launcher; kills stale `cc_web` processes first).

**Backend (`cc_web.py`).** It **reuses `bot.RemoteCommand`** and serializes access with a lock so the bot and CC don't race on the relay. Routes:

- **`GET /api/state`** — polls `dashboard_state.json`, tails `activity.log` (80 lines) and `console_mirror.log` (400 lines); returns `server_up`, `mission`, `time_current`/`time_max`, `online_count`, `players[]`, `map_key`, `server_age`. Frontend polls every 1.5s. `?raw=1` returns unfiltered console.
- **`GET /api/map?key=`** — atlas metadata (name, cols/rows, bounds, cell size, `gcols`, bases, `img`).
- **`GET /api/mapimg?key=`** — serves the pre-rendered terrain PNG (browser-cached).
- **`GET /api/commands`** — the full command catalog + missions + factions for the palette/autocomplete.
- **`POST /api/cmd`** — dispatcher. Local commands implemented server-side: `say` (broadcasts as `[Admin] …` and appends to activity.log), `nextmap`, `endmission` (sets time-remaining 5s), `leaderboard`/`ranks`, `rankpreview`, **`grant`** (queued to `admin_commands.jsonl` — the bot owns ranks.json), **`balance`/`move`/`join`/`spec`** (also queued), `copysid`. Server commands relay to the game.
- **`POST /api/power`** — start/stop/restart/kill via `_pt_power(signal)`.
- **`GET /api/resources`** — live CPU %, memory MB, uptime, power state from Pterodactyl (or `{configured:false}`).

**Console noise filter.** `_classify(line)` buckets each line (error/remote/weapon/ai/nostats/blast/steam/engine/show). The default view shows all errors and key categories in full while collapsing benign ones into a summary line (`— filtered  5 AI-units · 2 steam-net —`).

**Pterodactyl power control.** `_pt_load()` parses `apiKey.txt` (client key, `ptlc_…`) + `panel.txt` (URL, optionally with a server-ID hint), auto-discovers the server ID via `/api/client` if absent, and caches 30s. `_pt_call()` issues HTTP with a **real browser User-Agent** — the panel sits behind Cloudflare, which 403s default Python UAs (error 1010). `_pt_power()` POSTs the power signal; `_pt_resources()` GETs CPU/mem/uptime. **Gotcha:** `current_state` is unreliable on this egg (reports "starting" while fully serving); **trust the relay** (`get-player-list` success) for liveness — only `offline` after an explicit stop is dependable.

**Frontend (`webcc.html`).** Header (status, mission, timer, CPU/mem sparklines, power buttons) + a grid (left: live map + players table; right: activity, console tail, command bar). Polls `/api/state` every 1.5s, `/api/resources` every 5s, `/api/commands` once.

- **Players table** columns: Pilot (clickable), Fac, Rank (colour by tier), Pts, Aircraft, IG (in-game rank), Match (points this match), Skill (— until 5+ lives), Coords (grid ref). The popup offers grant points, move to faction / spectate, copy SteamID, kick/ban — all **queued via `admin_commands.jsonl`**.
- **Activity feed** — colour-coded `[TAG]` lines from `activity.log`. **Console** — filtered tail of `console_mirror.log`. Both use smart auto-scroll (follow only if already at the bottom).
- **Command bar & palette** — keyboard-filterable command list with args/description/danger; Tab-autocomplete by type (mission/player/faction); Enter to send; output rendered as echo/success/error or a formatted table.

### Live Map: Pan/Zoom Slippy Renderer

The Canvas map renders a **single pre-baked terrain PNG** at any zoom, with grid + blips drawn in **screen space** so they stay crisp.

- **Coordinates.** World x = E–W (east+), z = N–S (south+; z=+80000 is north). Bounds `x0/x1/z0/z1` come from the atlas. Grid: columns = numbers, rows = letters A–P, 10 km each, origin at `xmin` (**per-map**). Helpers `w2cx/w2cy` (world→screen), `c2wx/c2wy` (screen→world), `grid_ref(d,x,z)` ("E4").
- **Render pipeline:** clear to black (water) → clip to viewport → draw terrain PNG (smoothed, zoom-scaled) → grid lines (screen space, faint green) → base markers (faction-coloured rings + dot) → player blips (triangles flying, ✝ grounded) → unclip → gutters (row letters / column numbers) → live cursor grid-ref readout.
- **Interactions:** wheel zoom toward cursor, drag pan, dbl-click zoom 1.6×, buttons (zoom ±, recenter = fit-terrain, zoom-out = fit-full-grid), `ResizeObserver` redraw.
- **Calibration.** Terrain PNG bounds are **label-calibrated** (OCR-detected grid labels → least-squares pixel↔world fit, accurate to ~150 m). The grid model (`xmin`, `znorth`, `cell`, `gcols`) is per-map. Default view fits the calibrated terrain; pan/zoom roams the full `a1..pN` extent. Verified in-game: a known base blip lands on its printed cell.

### Live Map Pipeline (`build_map_atlas.py`)

**Problem.** A naive image-fit doesn't work — corner pixels don't map to exact cells, so islands drift and bases float off land. **Solution (label-calibrated):** detect the grid labels on the screenshot via OCR, then least-squares-fit pixel↔world coordinates so a base at "H12" plots on the H12 cell.

Workflow: read in-game screenshots (`heartland.png`, `ignus.png` in `map-build/`) → calibrate via `label_calibrate.py` → downsample + classify terrain (green land / black water / grey concrete) → render a **faithful** terrain PNG (keep green where green, black where black; **no flood-fill**, which would turn the ocean green and amplify cursor/grid artifacts) → detect airbase markers (colour-detected or supplied grid refs) → write `map_atlas.py` + `heartland_map.png` + `ignus_map.png` to the project root. Run: `python map-build\build_map_atlas.py`.

**`xmin` VARIES by map** (a subtle but critical detail): Heartland uses `-70000`, Ignus uses `-110000`, while `ZNORTH=80000` and `CELL=10000` are shared. The bot's grid math and the web map's `grid_ref()` both use per-map `xmin` so they agree. Per-map keys also include `gcols` (full grid width: 15 Heartland, 23 Ignus), working resolution, output width, green-brightness threshold, grid-line floor, and `drop_refs` (Ignus ships mis-detected as bases).

Calibration (`label_calibrate.py`): detect row-letter and column-number text in the margins, cluster centroids per label, least-squares-fit each axis (`world = a*pixel + b`), and validate square cells. Terrain (`greenness()` + cleaning): per-pixel green intensity (zeroed on grid lines), downsample keeping the brightest green, threshold to a land mask, drop speckles and thin line-fragments, then ramp black-water → green-land into an RGBA PNG. Base placement: Heartland uses known grid refs; Ignus uses colour-marker detection (yellow=Primeva, purple=Boscali) + admin-supplied refs, excluding `drop_refs`. Grid-ref decoding handles `xmin`-aware refs like `Jb79` (major+minor letter/digit) → world (x,z). Output `map_atlas.py` exposes `bounds`, the grid model, `bases`, and `img` per map.

### Operations: Deploy, Startup, Monitoring

**Automated plugin deploy.** A **Windows Scheduled Task** (`NukeOption_DailyPluginDeploy`, daily 05:00) runs `deploy.bat`, which calls `run.bat --deploy-plugin`. The bot checks for a staged `pending_plugin.dll` (SHA256 vs `deployed_plugin.sha256`), uploads it atomically if new, then stop → wait-offline → start → poll `get-player-list` until up (timeout 60s), logging to `deploy_plugin.log`. **Guardrails:** never leaves the server offline (a stuck restart aborts without killing it), idempotent (skips if SHA256 matches), and fully audit-logged. To stage: build the DLL, then `run.bat --put-atomic NukeStats/bin/Release/NukeStats.dll BepInEx/plugins/NukeStats.dll` — the next 05:00 run restarts the server.

**Startup launchers (`START HERE/`).** Idempotent launchers replaced an old keepalive babysitter that spawned duplicates. **`START EVERYTHING.bat`** kills stale keepalive/bot/CC processes, waits, then launches exactly one bot + one web CC. `1. Start Bot.bat` and `2. Start Web Command Centre.bat` start each unsupervised (the CC also kills old `cc_web.py`, opens `http://127.0.0.1:8770`, and reminds you to Ctrl+F5 if the map looks stale). The boot auto-start shortcut was moved out of `shell:startup` to prevent boot-time duplicates. **Do not** run `run_keepalive.bat` (redundant; the bot self-heals) or two copies of `START EVERYTHING.bat`.

**Monitoring.** Watch `activity.log` (normal: JOIN/LEFT, KILL, WIN/LOSS, RANK, VOTE, MAP; abnormal: JOIN/LEFT flicker, no KILL events, RANK-downs). Watch the filtered console (normal: `[NOSTATS]` snaps, `response: Success`, `Running Mission Players:N`; abnormal: `NukeStats.*` in an exception, a Python traceback, sustained silence). Confirm the bot is ticking via `dashboard_state.json.ts` advancing within 2s with `server_up`/`plugin_live` true. **Ignore** `current_state: "starting"` from the panel — trust the relay. Known-benign noise (do not re-investigate): AI `NullReference`s in TargetLeadTime/RailLaunch, Steam/Linux library warnings, the 05:00 reconnect (deploy reload), and empty-server match restarts.

**Backup & recovery.** `ranks.json` is snapshotted daily to `ranks_backup_<date>.json` (used to revert exploits; kept ~2 weeks). `admin_commands.jsonl` and `activity.log` are append-only. `dashboard_state.json` is ephemeral. To recover a corrupted ranks file: stop the bot, copy the closest backup over `ranks.json`, correct over-credits with negative grants, restart.

**Secret handling.** Secrets live in `apiKey.txt` (Pterodactyl client key), `panel.txt` (panel URL), and `run.bat` (`NO_SFTP_PASS`). **Never echo or print secrets.** `run.bat` uses `@echo off`. The client key is read-only (server/player lookup only — it cannot change panel settings); the SFTP password grants log access only (no game passwords, no local ranks).

---

## Design History & Rationale

The "why" behind the major design decisions — useful when adapting the toolkit or debugging unexpected behaviour:

- **Why a relay at all.** The game's remote-command port binds only to `127.0.0.1:5504` and the Pterodactyl host blocks SSH port forwarding. A transparent in-container TCP relay (`no_relay.py`/`.pl`) is the minimal way to expose that port to the off-box bot. The Perl version exists because the game **closes the TCP connection after every response**, which broke naive relays — the Perl `select()` loop keeps the client alive across requests while reopening upstream per request.
- **Why SFTP log tailing instead of an API.** The panel exposes a shell account but no stats/metrics API. Tailing one stable `console.log` over SFTP carries native game messages *and* the plugin's `[NOSTATS]` telemetry on a single connection — no second channel needed.
- **The OnDestroy/Unpatch bug.** The single hardest plugin bug: on a headless server the BepInEx manager object is destroyed seconds after load, so an `OnDestroy → UnpatchSelf` removed every hook almost immediately. The fix (and a hard rule going forward) is to **never unpatch** — Harmony patches are process-static and survive object destruction.
- **The HQTick driver.** Because `MonoBehaviour.Update()` never fires on the dedicated server, all periodic work hangs off a Postfix on `FactionHQ.Update()` — the one method the server reliably calls every frame during a mission.
- **Scoring/skill model evolution.** Score reading moved from "the game only logs faction score" to "read `Player.PlayerScore` directly in-process." Skill evolved from plugin-ended lives (which lost score on disconnect, mis-counted balance ejects, and required match-end coordination) to a **bot-owned per-life score** banked only on death/eject — durable across disconnects and match-ends, with admin moves explicitly life-neutral.
- **The mmap hazard → `--put-atomic`.** Overwriting a live, memory-mapped DLL in place corrupts the inode (`BadImageFormatException`). Uploading to a temp file and `posix_rename`-ing over the target lets the running process keep its old inode and the new DLL load cleanly on the next restart.
- **AI limiter.** Added (v0.8.0) to stop AI over-spawning and runway-clogging at high player counts; it only ever removes AI (never players) and was built against API confirmed via a dotnet build-probe against the game DLLs.
- **Score-explosion exploit.** A game-side score/money payout exploit was banked 1:1 because the bot has **no per-snap delta clamp**; ranks blew up. The revert procedure uses daily backups + negative grants; the no-clamp fix was offered and declined, so the vulnerability is still OPEN — monitor for recurrence and do not blindly set `ms=0` on revert (delete the key instead).
- **Deploy automation + idempotent launchers.** Manual deploys were error-prone and disruptive, so the 05:00 scheduled task automates stage → atomic upload → restart → relay-verify with a "never leave it offline" guardrail. The old keepalive babysitter spawned duplicate processes, so launchers were rewritten to kill stale processes and start exactly one of each.
- **Authoritative winner.** End-of-match used to guess the winner from a faction-0 mapping; the plugin now patches `DeclareEndGame` to emit the real winner, eliminating the guesswork for awards and win/loss tallies.

---

## Set Up Your Own Server From Scratch

This walkthrough is generalized — replace all `<PLACEHOLDERS>` with your own values and **never commit secrets**.

### 0. Prerequisites

- A **Nuclear Option dedicated server** on a Pterodactyl (or similar) Linux host, with **SFTP access** and a **client API key**.
- A **Windows PC** (always-on) with **Python 3.10+** (`pip install paramiko flask`) and the **.NET SDK 8** (only if you'll build the plugin).
- Local copies of this toolkit: `no_mapvote_bot.py`, `cc_web.py`, `webcc.html`, `run.bat`, `webcc.bat`, `deploy.bat`, the `NukeStats/` project, the `map-build/` generator, and the `START HERE/` launchers.

### 1. Install BepInEx + the NukeStats plugin (container side)

1. Install **BepInEx (Unity Mono, x64)** into the container root so you have `BepInEx/`, `libdoorstop.so`, and `doorstop_config.ini`.
2. Build the plugin locally: `dotnet build NukeStats/NukeStats.csproj -c Release` (place the game DLLs in `NukeStats/libs/` first).
3. Upload it **atomically**: `run.bat --put-atomic NukeStats/bin/Release/NukeStats.dll BepInEx/plugins/NukeStats.dll`.
4. Upload `anz.nukestats.cfg` to `BepInEx/config/` and set `[Admin] SteamIds = <YOUR_STEAMID>`.

### 2. Install the wrapper + relay (container side)

1. Upload `no_relay.py` (or `no_relay.pl`) to the container root.
2. Run **`run.bat --setup-server`** to install the launch wrapper at the panel's entry point (it starts the relay, sets `-logFile logs/console.log` + `-ServerRemoteCommands 5504`, and execs the game). Reversible via `--revert-server`.
3. Set the **panel Startup command** to the Doorstop launcher block (the `DOORSTOP_*` / `LD_PRELOAD` exports shown earlier) so BepInEx loads.

### 3. Configure `DedicatedServerConfig.json`

Edit and upload (via SFTP/panel) with your `ServerName`, `MaxPlayers`, `MissionRotation`, `Port`/`QueryPort` (5504 / 5550), `NoPlayerStopTime` (30), and **`PostMissionDelay` ≥ 80** (must exceed the 60s vote window). Restart the server so it takes effect. Add more missions later with `run.bat --add-rotation "Name" [Group] [MaxTime]`.

### 4. Configure & run the bot (Windows side)

1. In `run.bat`, set the SFTP environment variables (after `@echo off`, before launching Python):
   ```bat
   set NO_SFTP_HOST=<YOUR_SFTP_HOST>
   set NO_SFTP_PORT=<YOUR_SFTP_PORT>
   set NO_SFTP_USER=<YOUR_SFTP_USER>
   set NO_SFTP_PASS=<YOUR_SFTP_PASSWORD>
   set NO_SFTP_LOGPATH=logs/console.log
   ```
2. In `no_mapvote_bot.py`, set `RCMD_HOST = "<YOUR_SERVER_IP>"` and `RCMD_PORT = 5550` (the relay endpoint).
3. Verify connectivity: `run.bat --testconn` (should print mission time) and `run.bat --scanlog NOSTATS` (should show plugin output).
4. Start the bot: `run.bat`. It self-heals on crashes and begins writing `ranks.json`, `activity.log`, and `dashboard_state.json`.

### 5. Build the live map

Place in-game map screenshots at `map-build/heartland.png` and `map-build/ignus.png`, then run `python map-build\build_map_atlas.py`. It writes `map_atlas.py` + the terrain PNGs to the project root. Set the correct per-map `xmin`/`gcols` in the generator's `MAPS` table for your maps.

### 6. Configure & run the web command centre

1. Create `apiKey.txt` with your Pterodactyl **client** key (`ptlc_…` — copy the full token, not the key ID).
2. Create `panel.txt` with the panel URL on line 1 (a browser URL like `https://<panel>/server/<id>` works — the server ID is auto-discovered if omitted).
3. Launch `webcc.bat` (or run `python cc_web.py`) and open `http://127.0.0.1:8770`.

### 7. Enable deploy automation (optional)

1. Register a **Windows Scheduled Task** (daily 05:00) that runs `deploy.bat` (which calls `run.bat --deploy-plugin`).
2. Run `run.bat --disable-panel-restart` once so the bot owns the restart window.
3. To ship a plugin update thereafter: build the DLL, `run.bat --put-atomic … BepInEx/plugins/NukeStats.dll`, and let the 05:00 job restart and verify. Use `run.bat --deploy-plugin-dry` to preview safely.

### 8. Daily operation

After a reboot, run `START HERE\START EVERYTHING.bat` (exactly one bot + one CC). Monitor `activity.log` and confirm `dashboard_state.json.ts` is advancing. Trust the relay (not the panel state) for liveness.

---

## Data Files & Contracts Reference

| File | Owner → reader | Lifecycle | Purpose |
|---|---|---|---|
| `ranks.json` | bot | atomic write ~5s + on events; daily `ranks_backup_<date>.json` | Lifetime ladder, keyed by SteamID. |
| `dashboard_state.json` | bot → web CC / TUI | every 2s (stale after ~6s) | Live mission/roster/AI snapshot. |
| `activity.log` | bot (+ CC for admin says) | append-only | Human-readable `[TAG]` event feed. |
| `console_mirror.log` | bot → web CC | append, trimmed to 3000 lines >2 MB | Raw game console mirror. |
| `match_history.json` | bot | one record per completed match | Mission/result/per-player stats. |
| `points_ledger.jsonl` | bot | append-only | Per-award audit (`!why`, exploit audits). |
| `skill_ledger.jsonl` | bot | append-only | Per-life skill audit. |
| `plugin_ranks.txt` | bot → plugin (SFTP) | push ~120s / on rank-up | `sid|ABBR|#colour|index(1–11)|fullName`. |
| `plugin_skill.txt` | bot → plugin (SFTP) | push ~120s / on life-bank | `sid|rating` (≥5 lives only). |
| `plugin_cmd_<id>.txt` | bot → plugin (SFTP) | atomic drop, deleted on consume | `verb|sid|faction` team command. |
| `plugin_bans.txt` | plugin | persistent | Teamkill bans (kicked on sight). |
| `admin_commands.jsonl` | web CC → bot | append-only queue | Decoupled grant/team IPC. |
| `apiKey.txt` / `panel.txt` | operator → web CC | static (secret) | Pterodactyl client key + panel URL. |

**`ranks.json` schema (per SteamID):**

```json
{
  "76561198xxxxxxxxx": {
    "name": "Tomo", "points": 8052.3, "wins": 6, "losses": 6,
    "ms": 0.0, "skillPoints": 879.3, "lives": 11, "curLife": 0.0
  }
}
```

`points` = lifetime ladder points; `ms` = last-credited in-match score (baseline; reset to 0 at match end); `skillPoints`/`lives` → skill rating (`skillPoints/lives`, ≥5 lives); `curLife` = score accumulating in the current life. Rank tier is derived from `points` on read — no tier field is stored.

**`dashboard_state.json`** carries `ts`, `bot_pid`, `server_up`, `mission`, `state`, `online_count`, `time_current`/`time_max`/`time_at`, `plugin_live`, `vote`/`approval`, a `players[]` array (`sid, name, faction, aircraft, rank_*, points, ingame_rank, match_points, teamkills, wins, losses, skill, x, z, grounded, fresh`), and an `air` block (`s[]` per-side AI/player counts, totals, `teamcap`/`totcap`).

**`admin_commands.jsonl`** lines:

```json
{"action":"grant","query":"Tomo","points":100.0,"ts":...}
{"action":"team","verb":"move","sid":"7656...","faction":"boscali","ts":...}
{"action":"team","verb":"spec","sid":"7656...","faction":"","ts":...}
{"action":"team","verb":"balance","sid":"","faction":"","ts":...}
```

**`map_atlas.py`** exposes, per map: `bounds` (`x0/x1/z0/z1`, terrain PNG extent), the grid model (`xmin`, `cell`, `znorth`, `gcols`), `bases` (`(name,"w",x,z,faction)`), and `img` (PNG filename).

---

## `[NOSTATS]` Event Reference

The plugin emits one JSON object per line, prefixed with `[NOSTATS] `, to `console.log`. The bot parses them in `handle_stats_line()`.

| `t` | Cadence / trigger | Fields | Bot action |
|---|---|---|---|
| `snap` | every 10s, per player | `id, n, f, s, rk, tk, ac` | Cache metadata; accumulate score delta → `points`. |
| `score` | on each `RewardPlayer` (Recon/Jamming suppressed) | `id, n, f, s, rk, tk, ac` | Same as `snap` (incremental). |
| `win` | `DeclareEndGame` | `f` (winning faction) | Authoritative win/loss tally. |
| `award` | match end, per bonus | `id, n, pts, reason` (win/1st/2nd/3rd) | Apply bonus; announce rank-ups. |
| `end` | match end | — | Bank scores; finalize match record. |
| `life` | death or mid-air eject | `id, r` (`death`/`eject`) | Bank `curLife` → `skillPoints`; `lives++`. |
| `capbonus` | base capture | `id, pts` (default 250) | Add to `curLife`. |
| `pos` | ~2s | `p:[{id,x,z}, …]` (flying only) | Live-map blip positions. |
| `air` | ~5s | `s:[{n,ai,pl}, …], ai, pl, teamcap, totcap` | Web CC air panel. |
| `kill` | human-vs-human kill | `kid, kn, vid, vn` | Award +50 (underdog-scaled); "X splashed Y". |
| `chat` | only if `ReformatChat` & not `RankInName` | `id, n, msg, all` | (Chat already logged natively otherwise.) |

Field key: `id`=SteamID, `n`=name, `f`=faction, `s`=`Player.PlayerScore`, `rk`=in-game rank (0–11), `tk`=teamkills, `ac`=aircraft, `pts`=points, `reason`/`r`=cause.

---

## Glossary & Acronyms

| Term | Definition |
|---|---|
| **Bot** | `no_mapvote_bot.py` — the central orchestrator (commands, ranks, dashboard). |
| **Web CC** | `cc_web.py` + `webcc.html` — the browser command centre. |
| **Relay** | `no_relay.py`/`.pl` — bridges the game's localhost-only command port to the WAN. |
| **Plugin** | NukeStats BepInEx DLL — in-process sensor/enforcer emitting `[NOSTATS]`. |
| **Atlas** | Live-map metadata (`map_atlas.py` + terrain PNGs). |
| **Calibration** | OCR label-detected pixel↔world transform, pinned to the printed grid. |
| **Admin command** | Grant/team action queued via `admin_commands.jsonl`. |
| **Dashboard** | `dashboard_state.json` — ephemeral snapshot (ts, mission, players, air). |
| **Activity** | `activity.log` — timestamped `[TAG]` action feed. |
| **Console** | `console_mirror.log` — mirrored game engine output. |
| **Pterodactyl** | Game hosting panel; client API for power control. |
| **SteamID** | 64-bit player identifier (e.g., `76561198…`). |
| **NuclearSkill** | Per-player average score per life (points/life). |
| **mmap hazard** | Corruption from overwriting a live, memory-mapped DLL in place. |
| **`--put-atomic`** | Safe DLL upload: temp file + `posix_rename` over the target. |
| **HQTick** | The plugin's periodic driver, hung off `FactionHQ.Update()`. |

---

*This document covers the full Nuclear Option Community Server Toolkit. No game-client modification is required — all customization lives in the server-side plugin and the bot/web orchestration layer.*
