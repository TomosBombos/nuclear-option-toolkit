# Nuclear Option Community Server Toolkit — Docs

A three‑process toolkit that turns a vanilla *Nuclear Option* dedicated server into a managed community server: persistent ranks, a real‑score economy, skill ratings, PvP team balance, anti‑grief enforcement, a live battle map, and a browser admin console.

## Documents

| Doc | What it is |
|---|---|
| **[ARCHITECTURE.md](ARCHITECTURE.md)** | The authoritative technical reference — every plugin, bot, and web‑command‑centre feature: what it does, how it works (hooks/algorithms/files), the `[NOSTATS]` wire schema, all data contracts, the `run.bat` CLI and `/api` surface, and the cross‑process data flow. Start here to understand the system. |
| **[DESIGN_HISTORY.md](DESIGN_HISTORY.md)** | *Why* it's shaped this way — the founding constraint (the game hides per‑player score) and the incidents (exploits, mass‑disconnects, balance ping‑pong) that drove each feature into existence. Read this to understand the reasoning behind the architecture. |
| **[PRODUCTIZATION_PLAN.md](PRODUCTIZATION_PLAN.md)** | The plan to turn this from one admin's setup into an installable product for other server owners: a setup UI, selectable plugin features, full customisation, support for the game server on your own PC / external Linux / external Windows, and GitHub‑based auto‑updates. Includes a phased roadmap and the decisions the owner must make. |
| **[PRE_UPLOAD_CHECKLIST.md](PRE_UPLOAD_CHECKLIST.md)** | **Read before publishing.** The exhaustive secret‑scrub + first‑steps checklist (the working folder contains live credentials and player PII). |

## The three processes at a glance

```
 Remote game server          Admin PC                       Browser
 ┌─────────────────┐  SFTP   ┌──────────────────┐  files   ┌──────────────┐
 │ NukeStats plugin │ ──────► │ no_mapvote_bot.py │ ──────► │ web command  │
 │ (BepInEx/Harmony)│ ◄────── │  (ranks, votes,   │ ◄────── │ centre (SPA) │
 │  [NOSTATS] feed  │  relay  │   deploy, brain)  │  queue  │ live map etc │
 └─────────────────┘         └──────────────────┘          └──────────────┘
```

- **Plugin → bot:** one‑way `[NOSTATS]` telemetry through the server's console log, tailed over SFTP.
- **Bot → plugin:** SFTP file drops + a relay to the game's localhost‑only remote‑command port.
- **Web CC ⇄ bot:** shared local files (the web process never touches SFTP or the rank data directly; it queues intents the bot executes).

See **ARCHITECTURE.md §1** for the full diagram and **§6** for every data contract.
