# Commands & Tools

Every command and tool the toolkit gives you, grouped by who uses it. There are **players** (in-game
chat), **admins** (in-game chat), the **web command centre** (browser), and the **server owner's CLI**
(`run.bat`). For the moderation tools in depth see **[MODERATION.md](MODERATION.md)**.

---

## 1. Player chat commands

Typed in in-game chat. Replies are private to the asker unless noted. Commands start with `!`. A bare
word or number is treated as ordinary chat, so votes need the `!`.

### Answered by the bot

| Command | What it does |
|---|---|
| `!help` | Lists the available commands. |
| `!rank` | Your server rank, current points, and points to the next rank. |
| `!skill` | Your skill rating (points per life, scaled 0–10) and the pilot just above you. Shows "unranked" until you've flown **5 lives**. |
| `!points` | Points earned **this life** against **last life**. |
| `!leaderboard` | Your position, who's above you, and the Top-5 by points and Top-5 by skill. Also auto-posted to chat every ~30 min. |
| `!why` | Your last 4 points-ledger entries. An audit of where your points came from. |
| `!balance` | An explainer of how PvP team balancing works. |
| `!notk` | The no-team-killing policy and its warn → kick → ban escalation. |
| `!votemap` | Start a vote to change the map mid-mission. Alone it opens the vote immediately. With others it first runs a 30s yes/no approval poll, then the vote. A post-vote cooldown applies. |
| `!y` / `!n` | Vote yes/no in a `!votemap` approval poll. `!y` also accepts a pending squad invite. |
| `!1` … `!6` | Cast your map vote during the 60-second ballot. Last vote wins. Ties break to the first-voted option. No votes means random. |

### Answered by the plugin (public)

| Command | What it does |
|---|---|
| `!spec` / `!spectate` | Send **yourself** to spectator. No admin needed when used with no argument. |
| `!autobalance` / `!ab` | Print the auto-balance explainer: status, max team gap, grace window, and new-joiner/squad protections. |
| `!squadup` / `!squad` / `!su` | Team up with friends (up to 4) so PvP auto-balance keeps you on the same side. It persists across matches. Bare shows status, `!squadup <player>` invites, `!squadup leave` exits. The invitee accepts with `!y`. |
| `!forfeit` / `!ff` / `!surrender` | Call or second a vote for **your own team** to surrender the match. PvP only, and it needs a team majority. |

---

## 2. Admin in-game chat commands

Handled by the plugin. Authorisation is the plugin config **`[Admin] SteamIds`** in
`anz.nukestats.cfg`. Non-admins get "You're not authorised to use that command." Admins can also use
every player command above.

| Command | Syntax | Effect |
|---|---|---|
| `!move` / `!team` | `!move <player> <boscali\|primeva>` | Move a player to the named faction. An airborne target gets a 10-second warning first. |
| `!join` | `!join <player> <boscali\|primeva>` | Same as `!move`. |
| `!spec` / `!unteam` | `!spec [player]` | Move the named player, or yourself, to spectator. |
| `!swapteam` | `!swapteam <player>` | Move a player to the other team while **keeping their points and skill-life**. It spectates, swaps, spawns them briefly over the ocean, then ejects to reset their UI. |
| `!forceteamswap` | `!forceteamswap <player>` | Like `!swapteam` but immediate, with no initial spectate step. |
| `!balance` | `!balance` | Run one immediate PvP balance pass. It moves at most one player. |
| `!setrank` | `!setrank <player> <n>` | Set the player's **in-game** rank. This is the game's rank, not the server-points rank. |
| `!setfunds` | `!setfunds <player> <amount>` | Set the player's in-game funds. |
| `!addfunds` | `!addfunds <player> <amount>` | Add in-game funds, or remove them with a negative amount. |

> There is **no in-game `!kick`/`!ban`/`!unban` chat command**. Kicking and banning are done from the
> web command centre, and the plugin auto-enforces teamkill and anti-grief bans on its own. See
> [MODERATION.md](MODERATION.md). Faction keys are `boscali`/`primeva`, and `bdf`/`pala` also work.

---

## 3. Web command centre (browser, `http://127.0.0.1:8770`)

Most player and rank actions are **queued to the bot**, which owns the rank data. Server actions relay
live to the game. The dashboard is local-only, so anyone with access to it can issue these.

### Header bar
- **Status / mission / timer** and **CPU/Mem sparklines** are live readouts.
- **▶ Start / ⟳ Restart / ■ Stop** are real power control of the hosting panel, confirm-gated.
- **🗺 Map Pool** chooses which missions appear in the vote, edits the rotation, adds Workshop missions by ID, uploads custom missions, and sets the ballot options.
- **🛡 Moderation** opens the Reports and Banned-players modal. The badge shows the report count. See [MODERATION.md](MODERATION.md).
- **🏆 Leaderboard** shows this server's board: Top players by points and by skill.
- **⚙ Settings ▾** holds 🖥 Server Config · ⚙ Game Settings · ⏱ Schedule · 📢 Messages.
- **🌙 / ☀** switches light/dark theme. The **Update badge** appears when a plugin update is staged.

### Main grid
- **Live map** pans and zooms over the baked terrain PNG. Zoom **+ / −**, recenter (fit terrain), fit-full-grid, wheel-zoom toward the cursor, drag-pan, and **⛶ fullscreen** (F / Esc). It draws base rings, player/AI/ship blips, the grid, and a cursor grid-ref.
- **Players table** shows Pilot · Faction · Rank · Points · Aircraft · in-game rank · match points · Skill · grid coords. Click a pilot for the **action popup**: Grant points, → Boscali / → Primeva, Spectate, Copy SteamID, Kick, and Ban. The last two are confirm-gated.
- **Activity feed** is a colour-coded event log. **Console** is the filtered server console with benign spam collapsed, a filters button, and a filtered/raw toggle.
- **Command bar** is a palette of every command with Tab-autocomplete and danger markers. It has a **🔁 Change map** button, and **≡ all commands** opens the full list.

### Settings & tabs
| Tab | What it does |
|---|---|
| **🖥 Server Config** | Edit `DedicatedServerConfig.json` (server name, ports, max players, password, votekick, …) over SFTP and mirror it to the hosting panel. Changes need a restart. |
| **⚙ Game Settings** | Live plugin and game settings (moderation, scoring, balance, killfeed, profanity, anti-grief, …) with Apply/Cancel staging. Most apply live. Some are flagged restart-only. |
| **⏱ Schedule** | Schedule a one-off **restart** or **update** at a date and time. The bot warns players and runs the guarded restart. |
| **📢 Messages** | Manage the automated chat: toggle, edit, or retime the built-in messages, and add your own (trigger = interval / daily clock / match start / match end, with an optional colour). |
| **🛡 Moderation** | **Reports** (anti-grief events, one-click ban/clear) and **Banned players** (unban from the plugin and game lists). Detailed in [MODERATION.md](MODERATION.md). |

---

## 4. Server-owner CLI & launchers

### `run.bat` flags

`run.bat` sets the SFTP environment and forwards all arguments to the bot. With no flag it starts the
bot on its self-healing main loop. Run anything that touches the server through `run.bat` so the
credentials are present. Credentials live in `run.bat` and are never printed.

**Live server control**
- `--say <message>` — broadcast a chat message.
- `--endmission` — force the current mission to end.
- `--cmd <name> [args…]` — send a raw remote command to the game.

**Diagnostics / testing**
- `--testconn` — verify the remote-command channel. `--testchat` — tail chat ~20s. `--testtunnel` — probe the localhost command port over SSH.
- `--selftest` / `--matchtest` — offline self-tests (chat parsing, ballots, rank thresholds, result→winner).
- `--scanlog <tokens>` / `--ctxlog <args>` / `--findchat` — inspect the console log.
- `--check-server` — server health check. `--players` — print the player list. `--colortest` — send a colour test line.
- `--ranks` / `--rankpreview` / `--audit` / `--check-ranks` / `--fix-ranks` — rank and ledger inspection and repair.

**SFTP file tooling**
- `--ls <path>` / `--cat <path>` / `--get <remote> [local]` / `--put <local> <remote>`.
- `--put-atomic <local> <remote>` — temp-upload then rename (mmap-safe; **use for DLLs**). `--chmod-exec <path>`.

**Mission / server config**
- `--add-rotation "<name>" [Group] [MaxTime]` — append a mission (idempotent, backed up).
- `--set-server-name`, `--set-ai-limits [--dry-run]`, `--set-balance-diff <n>`, `--set-votekick on|off`, `--apply-map-changes [--dry-run]`, `--probe-missions`.

**Deploy / install**
- `--deploy-plugin` — the guarded deploy: stage the new DLL if it changed, then stop → wait offline → start → verify. It never leaves the server offline. `--deploy-plugin-dry` previews it.
- `--disable-panel-restart` — hand the daily restart slot to the bot.
- `--setup-server` / `--revert-server` — install or undo the launch wrapper (relay plus stable console log) at the panel entry point. `--upload-bepinex` — push the BepInEx pack and DLL (server stopped).

### Launchers

| File | Purpose |
|---|---|
| `START HERE/START EVERYTHING.bat` | One click: start exactly one bot window plus one web command centre, with a browser tab. |
| `START HERE/1. Start Bot.bat` | Start just the bot. |
| `START HERE/2. Start Web Command Centre.bat` | Start just the web command centre. |
| `webcc.bat` | Open the web command centre and browser. |
| `commandcentre.bat` | Open the interactive terminal command centre, a backup viewer and controller. |
| `deploy.bat` | The scheduled daily deploy entry point (`run.bat --deploy-plugin` plus a bot and web restart). |
| `say.bat` / `endmission.bat` / `status.bat` | Quick helpers: broadcast a message, end the round, show bot status. |
| `NukeStats/build.bat` | Build `NukeStats.dll` with the .NET SDK. |

---

## Notes

- **`!squadup`, `!forfeit`, `!spec`, `!autobalance`** appear in the bot's `!help` text but actually run
  **in the plugin**. If the plugin is offline they do nothing, while `!rank`/`!skill`/`!votemap` still
  work.
- **`changemap` vs `nextmap`:** `nextmap` only queues the next mission. **`changemap`** (the 🔁 button)
  ends the current match and cuts over immediately. It is routed through the bot so the auto map-vote
  doesn't override it.
- **Defaults worth knowing:** map-vote window 60s; skill qualifies at 5 lives; squad max 4; new-joiner
  protection ~15 min; balance acts only when a side is more than the allowed gap ahead.
- **Plugin-side actions need a player online to apply.** While the server is empty, commands relay but
  don't take effect until someone joins. The game-native ban list is the exception. See
  [MODERATION.md](MODERATION.md).
