# Nuclear Option Community Server Toolkit — Docs

A three‑process toolkit that turns a vanilla *Nuclear Option* dedicated server into a managed community server: persistent ranks, a real‑score economy, skill ratings, PvP team balance, anti‑grief enforcement, a live battle map, and a browser admin console.

**New here?** Start with **[FEATURES.md](FEATURES.md)** for what it does, then **[COMMANDS.md](COMMANDS.md)** for how to drive it.

## Documents

| Doc | What it is |
|---|---|
| **[FEATURES.md](FEATURES.md)** | Every feature in plain English — what it does, how it works, why it matters. |
| **[COMMANDS.md](COMMANDS.md)** | The complete command & tool reference: player chat commands, admin commands, the web command centre, and the `run.bat` CLI. |
| **[MODERATION.md](MODERATION.md)** | The moderation toolkit in depth: teamkill enforcement, anti‑grief auto‑kick, bans/unbans, votekick, the flood guard, and the reports tab. |
| **[ARCHITECTURE.md](ARCHITECTURE.md)** | How the three processes fit together: a simple overview up top, then the full technical reference (hooks, data contracts, the `[NOSTATS]` schema, the CLI/API surface). |

> The live **Global Leaderboard** (global Top 5) and **Community Servers** listings are on the [main page](../README.md), not here — that's where the data is shown.

For installers & maintainers: **[INSTALL_SOURCES.md](INSTALL_SOURCES.md)** (where the installer fetches each file), **[GLOBAL_LEADERBOARD_CONTRACT.md](GLOBAL_LEADERBOARD_CONTRACT.md)** (the cross‑server data format), and **[../SECURITY.md](../SECURITY.md)** (update signing + the credential stance).

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

See **[ARCHITECTURE.md](ARCHITECTURE.md)** for the full diagram and every data contract.
