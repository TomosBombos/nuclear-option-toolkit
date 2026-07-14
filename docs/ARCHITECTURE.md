# Architecture

This page explains how the toolkit fits together at a high level: the three parts, what
each one is responsible for, and how they actually talk to each other. For what each feature
*does*, see **[FEATURES.md](FEATURES.md)**; for the command list, **[COMMANDS.md](COMMANDS.md)**.

> Plugin identity: `anz.nukestats`, version `1.0.2`.

---

## 1. Overview

The toolkit is **three separate programs** (processes) that run alongside your game server.
They never call each other directly. They only pass messages through the game console log,
the game's remote-command port, and a handful of shared files. That loose coupling is on
purpose: any one of them can crash, restart, or be redeployed without taking the others down.

| Part | Runs | Language / host | What it is responsible for |
|---|---|---|---|
| **NukeStats plugin** | Inside the game server | C# / BepInEx | Lives inside the game. Emits `[NOSTATS]` telemetry, enforces rules (team-kill, PvP balance, AI cap, flood/anti-grief guard), reformats chat and names, runs the custom kill feed, and produces the live-map position feeds. |
| **The bot** (`no_mapvote_bot.py`) | Your admin PC | Python | The brain and the only owner of the data. Tails the game console, runs map votes, ingests `[NOSTATS]` into points/ranks/skill/moderation, owns `ranks.json` and the ledgers, and drives the game over the remote-command port. |
| **Web Command Centre** (`cc_web.py` + `webcc.html`) | Your admin PC (browser at `http://localhost:8770`) | Flask + one HTML page | The dashboard. A live browser view and control surface: it reads the bot's feed files and queues admin intents back to the bot. |

**An honest note.** The Web Command Centre does **not** run the game server, and it does **not**
own the rank data. It is a control surface: it queues intents (grant points, ban, restart,
change a setting) to the bot, and the bot carries them out over SFTP and the remote-command
relay. Three separate processes, one dashboard over the top of them.

### 1.1 How they communicate

```
        GAME SERVER (Pterodactyl container, or your own PC)
   ┌──────────────────────────────────────────────────────────┐
   │  Nuclear Option dedicated server                          │
   │    + NukeStats plugin (BepInEx)                           │
   │                                                           │
   │   writes:  [NOSTATS] {json}  ─────►  console.log          │
   │   reads :  plugin_ranks.txt / plugin_skill.txt            │
   │            plugin_cmd_<id>.txt   (dropped by the bot)     │
   │   self  :  plugin_bans.txt / plugin_squads.txt            │
   │   remote-command port  (5504 in-container / own-PC)       │
   └───────▲───────────────────────────────────▲──────────────┘
           │ commands (length-framed JSON)      │ tail console.log
           │ via relay :5550 when hosted        │ + drop plugin files (SFTP)
           │                                    │
   ┌───────┴────────────────────────────────────┴──────────────┐
   │  ADMIN PC                                                  │
   │                                                           │
   │   the bot (no_mapvote_bot.py)                             │
   │     owns ranks.json + ledgers + match history            │
   │     publishes: dashboard_state.json, activity.log,       │
   │                console_mirror.log                        │
   │     consumes:  admin_commands.jsonl, schedule.json       │
   │        ▲ imports the bot module in-process               │
   │        │ + reads its feed files                          │
   │   Web Command Centre (Flask :8770) ──► browser (admin)   │
   └───────────────────────────────────────────────────────────┘
```

Four channels, one direction each:

- **Plugin → bot (telemetry).** The plugin writes one-line `[NOSTATS] {json}` events to the
  game console (via Unity's log file). The bot tails that log — over SFTP for a hosted server,
  or as a local file when everything runs on your own PC. This is one-way; there is no socket
  from the plugin back to the bot. Event types include per-player score snapshots, kills,
  deaths, captures, wins/placement, life-start/end (for skill), team-kills, anti-grief reports,
  and the live-map position feeds. The live-map **air-traffic** counts come from the plugin's
  `air` frame, emitted by the AI-aircraft limiter. The `net` connection-health line is also
  kept: it carries the server frame time (`frametime_ms`) that feeds the dashboard's FRAME
  readout.

- **Bot → game (control).** The bot sends commands (send a chat line, get the player list,
  set the mission time, set the next mission, kick/ban) to the game's remote-command TCP port
  as length-framed JSON. On a hosted (Pterodactyl) server that port is only reachable inside
  the container, so a small in-container relay re-exposes it on a public port (default `5550`).
  On your own PC the bot talks to `127.0.0.1:5504` directly, with nothing to forward.

- **Bot → plugin (state files + updates).** The bot drops files onto the game server over SFTP:
  `plugin_ranks.txt` (the chat rank tags and the balance weight), `plugin_skill.txt` (skill
  weights for skill-based balancing), and short-lived `plugin_cmd_<id>.txt` files (one per admin
  action it relays — balance, move, spectate, set rank, set/add funds, sky-drop, team swaps). The plugin's own settings live in
  `BepInEx/config/anz.nukestats.cfg`, which the bot also writes over SFTP. To change the plugin
  itself, the bot swaps the DLL and restarts the server (BepInEx has no hot-reload).

- **Web CC ⇄ bot (shared local files, same PC).** The Web Command Centre imports the bot's
  Python module in-process to reuse its command relay, rank tables, and the Pterodactyl API. It
  **reads** the bot's feed files (`dashboard_state.json` every ~1s, plus `activity.log` and
  `console_mirror.log`) and treats `ranks.json` as read-only. It **writes** its intents back to
  the bot through `admin_commands.jsonl` and `schedule.json`; the bot is the sole validator and
  the sole writer of `ranks.json` and every other data file. The two processes never race on
  persistent state because only the bot writes it.

---

## 2. Short reference

### 2.1 The three processes

| Process | File(s) | Owns | Does not |
|---|---|---|---|
| Plugin | `NukeStatsPlugin.cs` → `NukeStats.dll` | In-game enforcement + telemetry | Does not persist ranks/points |
| Bot | `no_mapvote_bot.py` | `ranks.json`, ledgers, match history, map votes, deploys | Does not serve a web page |
| Web CC | `cc_web.py`, `webcc.html` | The browser dashboard / control surface | Does not run the game or write `ranks.json` |

### 2.2 Data files

| File | Written by | Read by | Purpose |
|---|---|---|---|
| `console.log` | Game / plugin | Bot | The console the bot tails (holds `[NOSTATS]` lines). |
| `dashboard_state.json` | Bot | Web CC | ~1s snapshot: mission/vote header, players, map blips, kill feed. |
| `activity.log` | Bot | Web CC | Human-readable event feed. |
| `console_mirror.log` | Bot | Web CC | Raw console mirror for the console panel. |
| `ranks.json` | Bot (sole writer) | Bot, Web CC (read-only) | The lifetime points/rank source of truth. |
| `admin_commands.jsonl` | Web CC | Bot | Queued admin intents (grant, team move, change map, bans). |
| `schedule.json` | Web CC | Bot | Scheduled restarts / plugin updates. |
| `plugin_ranks.txt` | Bot (SFTP) | Plugin | Chat rank tags + rank balance weight. |
| `plugin_skill.txt` | Bot (SFTP) | Plugin | Skill weights for skill-based balancing. |
| `plugin_cmd_<id>.txt` | Bot (SFTP) | Plugin | One-per-command channel (balance/move/spec/setrank/setfunds/addfunds/skyswap/swapteam/forceteamswap). |
| `anz.nukestats.cfg` | Bot (SFTP) | Plugin | The plugin's live settings. |
| `plugin_bans.txt`, `plugin_squads.txt` | Plugin | Plugin | Plugin-managed bans and squads. |

### 2.3 Ports

| Port | Where | Purpose |
|---|---|---|
| `8770` | Admin PC (TCP) | Web Command Centre dashboard (`web.port`). |
| `5504` | Game server (TCP) | Game remote-command port (localhost in-container / own-PC `127.0.0.1`). |
| `5550` | Game server (TCP) | Relay that re-exposes the command port publicly on a hosted server (`server.rcmd_port`). |
| `2022` | Game host (TCP) | SFTP the bot uses to tail the log and drop plugin files (Pterodactyl). |
| `7777` / `7778` | Game host (UDP) | Game port / query port (forward both as UDP for internet play). |

### 2.4 Security

There is **no login or authentication** on the Web Command Centre or any of its API routes.
Access control is entirely the network bind. By default it binds to `0.0.0.0` (reachable on
your LAN), so anyone who can reach port `8770` has full admin control (start/stop, bans, config
edits, granting points). To lock it to the host only, set `web.host = "127.0.0.1"` in the config
(or `NOCC_HOST=127.0.0.1`). Never expose port `8770` to the internet.
