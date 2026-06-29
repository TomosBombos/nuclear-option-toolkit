# Nuclear Option Toolkit — Installer Wizard Content & Setup Guide

Ready-to-drop content for the setup wizard (`installer/setup.py` + `installer/wizard.html`) and a
companion setup guide. Every value here is reconciled against the toolkit on disk:
`installer/sources.json`, `docs/INSTALL_SOURCES.md`, `docs/ARCHITECTURE.md`, and the BepInEx pack
(`NukeStats/bepinex_pack`, Doorstop 4.5.0 / BepInEx v5.4.23.5 Linux x64).

> **Port convention used in this document.** The wizard presents the **public Shockfront-guide
> defaults — game `7777` / query `7778`** — and enforces that the two must differ. The community
> egg ships query defaulted to `7777` (collision); the wizard corrects that to `7778`. (The live
> ANZ reference deployment happens to run game `5504` / relay+query `5550`; those are just the
> values that server was set to. The wizard always proposes 7777/7778 and lets you override.)
> Whatever game port you choose, the **in-container relay** re-exposes the localhost-only
> remote-command port on a WAN-reachable TCP port — by default the **query port** number
> (UDP query and TCP relay do not collide).

The full stack a working server needs (all four hosting options must end here):

1. **Game-server binaries** — `NuclearOptionServer.x86_64` (Linux) / `.exe` (Windows), Steam app `3930080`.
2. **BepInEx** (Unity-Mono 5.4.x) + **Doorstop** — the in-process mod loader.
3. **NukeStats plugin** — `BepInEx/plugins/NukeStats.dll` + `BepInEx/config/anz.nukestats.cfg`.
4. **The relay** — `no_relay.py`/`.pl` in the container, exposing `127.0.0.1:<game> → 0.0.0.0:<query>`.
5. **The bot** — `no_mapvote_bot.py` + `run.bat` (env-injected SFTP creds) on the admin PC.
6. **The web command centre** — `cc_web.py` + `webcc.html` + `webcc.bat` on the admin PC.
7. **Generated config** — `config.json` + `secrets.json` (in `~/.nuke-option-toolkit/`), `anz.nukestats.cfg`,
   and (for power control) `apiKey.txt` / `panel.txt`.

---

## Part 1 — Pre-selection transparency cards (one per hosting option)

These render on the **Hosting** step, *before* the user commits to an option. Each card has a
**Prerequisites** block (red/amber gate items) and a **What the installer will ask / will do** block
(the full-stack steps, in order). Wizard option keys match `sources.json` → `options`.

---

### CARD A — `own_pc_windows` · "Host on my own Windows PC"

> Runs the game server **as a tracked child process on this same Windows PC**, alongside the bot
> and web CC. No SFTP, no panel — everything talks over `127.0.0.1`.

**Prerequisites — check these before you pick this option**

| Need | Detail |
|---|---|
| OS / arch | Windows 10/11, **x64**. (This PC is also the admin PC.) |
| Free disk | **~6–8 GB** (game binaries ~3–4 GB via SteamCMD + BepInEx + logs + headroom). |
| Ports | Game **7777/UDP**, query **7778/UDP** — **must differ**. Allow both through Windows Firewall; forward them on your router for public play. |
| Internet | Required **once** to download binaries + BepInEx + plugin. Online play needs the game ports reachable. A LAN-only server can run offline after install. |
| Admin SteamID64 | Your 17-digit SteamID64 (`https://steamid.io` → "steamID64") — authorises in-game `!`-admin commands. |
| Pterodactyl panel | **Not needed.** Power is handled locally (start/stop the child process). |
| SFTP login | **Not needed.** Logs are read from the local game folder. |

**The installer will ASK for**

1. Where to install the game (or path to an existing local install — autodetected from Steam if present).
2. Game port (default **7777**) and query port (default **7778**); validates `game ≠ query`.
3. Your admin **SteamID64**.
4. Server name + max players (+ optional server password).
5. Which plugin features to enable (17 toggles; presets Full / PvP / PvE / Minimal).
6. Web-CC port (default **8770**).
7. (Optional) a GitHub repo slug for opt-in plugin updates later.

**The installer will DO (in order)**

1. Fetch **SteamCMD (Windows)** → fetch **server binaries** (`app_update 3930080 validate`).
2. Fetch + lay down **BepInEx (win_x64 5.4.x)**: `winhttp.dll` + `doorstop_config.ini` next to
   `NuclearOptionServer.exe` (Windows auto-injects — **no env vars / no wrapper**).
3. Fetch + verify (SHA-256 + minisign) **NukeStats.dll** → `BepInEx\plugins\`.
4. Write **`anz.nukestats.cfg`** → `BepInEx\config\` with your feature toggles + `[Admin] SteamIds`.
5. Write **`DedicatedServerConfig.json`** (ports, name, max players, mission rotation).
6. Drop the **relay** (`no_relay.py`) locally and wire local launch with `-ServerRemoteCommands <game>`.
7. Write **`config.json`** (no secrets) + **`secrets.json`** (0600) to `~/.nuke-option-toolkit/`.
8. Register **bot + web CC** launchers (`START HERE\`), open `http://127.0.0.1:8770`, and run a
   self-test (relay liveness via `get-mission-time`, `[NOSTATS]` scan).

---

### CARD B — `own_pc_linux` · "Host on my own Linux box"

> Same as Card A but on Linux: the game runs as a tracked child process locally; bot + web CC run
> on the same machine (Python). Doorstop is activated via a **launch wrapper**, not `winhttp.dll`.

**Prerequisites**

| Need | Detail |
|---|---|
| OS / arch | Linux **x64** with glibc; Python 3.8+. |
| Free disk | **~6–8 GB** (binaries + BepInEx + logs). |
| Ports | Game **7777/UDP**, query **7778/UDP** — **must differ**; open in your firewall (and forward for public play). |
| Internet | Required **once** for binaries/BepInEx/plugin; online play needs the ports reachable. |
| Admin SteamID64 | Your 17-digit SteamID64. |
| Pterodactyl panel | **Not needed** (local process control). |
| SFTP login | **Not needed** (local log path). |

**The installer will ASK for** — same as Card A (game dir, ports, SteamID64, name/maxplayers/password,
features, web port, optional update repo).

**The installer will DO (in order)**

1. Fetch **SteamCMD (Linux)** → **server binaries** (`app_update 3930080 validate`).
2. Fetch + extract **BepInEx (linux_x64 5.4.x)**; `chmod +x libdoorstop.so`.
3. Fetch + verify **NukeStats.dll** → `BepInEx/plugins/`.
4. Write **`anz.nukestats.cfg`** → `BepInEx/config/` (features + `[Admin] SteamIds`).
5. Write **`DedicatedServerConfig.json`** (ports, name, max players, rotation).
6. Install the **launch wrapper** (Doorstop block + relay + `-ServerRemoteCommands <game>` + `-logFile logs/console.log`); drop `no_relay.py`/`.pl`.
7. Write `config.json` + `secrets.json` (0600).
8. Wire bot + web CC, open the web CC, run the self-test.

---

### CARD C — `external_linux_ptero` · "External Linux server on a Pterodactyl panel" **(recommended)**

> The production-proven path. Your game server lives in a **Pterodactyl Linux container**; the bot +
> web CC run on your always-on admin PC and reach it over **SFTP** (logs) and the **relay** (commands).

**Prerequisites — gate items**

| Need | Detail |
|---|---|
| Admin PC | An **always-on Windows/Linux PC** with Python 3.8+ and `paramiko` (`pip install paramiko flask`). |
| Pterodactyl panel | A panel + a server (yours or a host's) running the Nuclear Option egg. |
| Panel **client** API key (`ptlc_…`) | From panel → *Account → API Credentials*. Read-only power + resources. Used by bot + web CC. |
| Panel **Application** API key (`ptla_…`) *(optional, upgrade path)* | From the **admin area** → *Application API*. Only needed if you want the installer to **auto-rewrite the Startup line**; otherwise you paste it once (see Part 3). |
| SFTP login | The panel's SFTP host/port/user/password (panel → *Settings → SFTP Details*). The bot tails `console.log` and pushes plugin files over it. |
| Ports | Game **7777/UDP** + query **7778/UDP** (must differ) allocated to the server in the panel; the **relay** uses the query-port number on TCP. |
| Free disk (container) | **~5–6 GB** allocation (binaries + BepInEx + logs). |
| Internet | The container needs outbound internet so its SteamCMD install step can pull the binaries. |
| Admin SteamID64 | Your 17-digit SteamID64 for in-game admin. |

**The installer will ASK for**

1. SFTP host / port / user / **password** (with a **Test SFTP** button).
2. Panel URL + **client** API key (`ptlc_…`) (with a **Test panel** button → lists your servers → pick one).
3. *(Optional)* an **Application** API key (`ptla_…`) to enable auto-Startup-rewrite.
4. Relay endpoint host (your server's public host) + relay/query port (default **7778**).
5. Game port (default **7777**), validated `≠` query.
6. Admin **SteamID64**, server name, max players, optional password.
7. Plugin features (toggles + presets) and web-CC port (default 8770).
8. *(Optional)* GitHub repo for opt-in updates.

**The installer will DO (in order)**

1. Fetch the **community egg** (`pterodactyl/game-eggs → nuclear_option`) and compare its
   `exported_at` to the vendored copy (**warn on drift, never auto-adopt HEAD**).
2. **Two-path Startup handling** (see Part 3): DEFAULT = guide you to import the egg + paste the
   **modded Doorstop Startup line** + set `MODDED_SERVER=true`; UPGRADE = with a `ptla_` key,
   auto-rewrite the Startup field and the egg var for you.
3. Push **BepInEx (linux_x64 5.4.x)** over SFTP into `/home/container` (`BepInEx/`, `libdoorstop.so`,
   `doorstop_config.ini`); the container's own SteamCMD step installs the binaries on boot.
4. Push + verify **NukeStats.dll** → `BepInEx/plugins/` (atomic `posix_rename`).
5. Push **`anz.nukestats.cfg`** → `BepInEx/config/` (features + `[Admin] SteamIds`).
6. Push the **relay** (`no_relay.py`/`.pl`) into the container (the Startup line launches it and
   forwards `0.0.0.0:<query> → 127.0.0.1:<game>`).
7. Edit **`DedicatedServerConfig.json`** (ports w/ `IsOverride:true`, name, max players, rotation).
8. Write `config.json` + `secrets.json` (0600) + `apiKey.txt` (`ptlc_…`) + `panel.txt` (url[+id]).
9. Start the server via the panel; **verify through the relay** (`get-player-list` succeeds — panel
   state is unreliable for this egg) and confirm `NukeStats loaded` in `BepInEx/LogOutput.log`.
10. Wire bot + web CC, open the web CC.

---

### CARD D — `external_windows` · "External Windows server (VPS / rented box)"

> Your game server runs on a **remote Windows machine** you reach over **SFTP/SSH**; bot + web CC run
> on your admin PC (which can be the same box). Doorstop auto-injects via `winhttp.dll` — no wrapper.

**Prerequisites**

| Need | Detail |
|---|---|
| Remote box | Windows **x64** VPS/host with an **SFTP/SSH** login and permission to run a dedicated server. |
| Admin PC | Python 3.8+ + `paramiko` (can be the same remote box or a separate always-on PC). |
| SFTP login | Host/port/user/password for the Windows box (to push files + tail `console.log`). |
| Ports | Game **7777/UDP** + query **7778/UDP** (must differ), opened in the box's firewall; relay on the query-port number (TCP). |
| Free disk | **~6–8 GB** on the remote box. |
| Internet | Required for the one-time SteamCMD fetch; online play needs the ports reachable. |
| Admin SteamID64 | Your 17-digit SteamID64. |
| Pterodactyl panel | **Not needed** (no panel on a raw Windows box; power = start/stop the process / scheduled task). |

**The installer will ASK for**

1. SFTP host / port / user / **password** (+ **Test SFTP**).
2. Remote game directory.
3. Relay endpoint host + relay/query port (default **7778**); game port (default **7777**, `≠` query).
4. Admin **SteamID64**, server name, max players, optional password.
5. Plugin features + web-CC port (default 8770).
6. *(Optional)* GitHub update repo.

**The installer will DO (in order)**

1. Fetch **SteamCMD (Windows)** + **server binaries** (run once on a connected machine, or in place
   if the remote box has internet) → `NuclearOptionServer.exe`.
2. Push **BepInEx (win_x64 5.4.x)**: `winhttp.dll` + `doorstop_config.ini` next to the `.exe`
   (auto-inject — **no env vars / no wrapper**).
3. Push + verify **NukeStats.dll** → `BepInEx\plugins\`.
4. Push **`anz.nukestats.cfg`** → `BepInEx\config\` (features + `[Admin] SteamIds`).
5. Edit **`DedicatedServerConfig.json`** (ports, name, max players, rotation).
6. Push the **relay** (`no_relay.py`) and configure the box's launch (a `RunServer.bat`/scheduled
   task) to start the relay + `-ServerRemoteCommands <game>` + `-logFile logs\console.log`.
7. Write `config.json` + `secrets.json` (0600).
8. Wire bot + web CC, open the web CC, run the relay/`[NOSTATS]` self-test.

---

## Part 2 — "Add to an already-running server" flow

For users who **already have a working Nuclear Option dedicated server** (vanilla or modded) and just
want to bolt on the toolkit. This is a non-destructive overlay: it never reinstalls binaries.

### What to ASK (3 grouped questions)

1. **Game root** — the directory containing `NuclearOptionServer.x86_64` / `.exe` (local path, or
   the SFTP path on the remote box). *Validation:* the server executable must be present here.
   - For SFTP targets, also collect host/port/user/password (with **Test SFTP**).
2. **BepInEx / plugins dir** — auto-derived as `<game root>/BepInEx/`. Ask only:
   - *"Is BepInEx already installed here?"* If **no**, the flow adds the matching BepInEx pack
     (linux_x64 / win_x64 5.4.x) + Doorstop activation. If **yes**, it reuses it and only drops the
     plugin + cfg. *Validation:* if present, confirm it is **Unity-Mono 5.4.x** (refuse 6.x/IL2CPP).
3. **Bot + web-CC target dir** — where the Python toolkit lives on your admin PC (default: the repo
   root you ran the installer from). Used to drop/refresh `run.bat`/`webcc.bat` env + `config.json`.

Plus the small always-needed extras: **admin SteamID64**, the **relay/query port** + **game port**
(read from the existing `DedicatedServerConfig.json` if found, else asked), and the **feature toggles**.

### Which files go where

| Source | Destination | Notes |
|---|---|---|
| `NukeStats.dll` (verified) | `<game root>/BepInEx/plugins/NukeStats.dll` | atomic put (`.deploytmp` + `posix_rename`) on a live server. |
| `anz.nukestats.cfg` (generated) | `<game root>/BepInEx/config/anz.nukestats.cfg` | written from feature toggles + `[Admin] SteamIds`. |
| `no_relay.py` / `no_relay.pl` | `<game root>/` | only if a relay isn't already there. |
| BepInEx pack *(only if missing)* | `<game root>/` | `BepInEx/`, plus `libdoorstop.so`+wrapper (Linux) or `winhttp.dll`+`doorstop_config.ini` (Windows). |
| Startup / wrapper edit | panel Startup line **or** launch wrapper | adds Doorstop env + relay + `-ServerRemoteCommands <game>` + `-logFile`. Skipped on Windows (auto-inject). |
| `config.json` / `secrets.json` | `~/.nuke-option-toolkit/` | admin-PC side; never in the game root. |
| `apiKey.txt` / `panel.txt` *(ptero only)* | bot/web-CC target dir | power control. |

### Dry-run preview format

Before writing anything, show a **plan** the user must confirm. Each row is one action with an
idempotency verdict:

```
ADD-TO-EXISTING — DRY RUN  (nothing has been changed)

Target game root : /home/container        (SFTP sftp.example.net:2022)
BepInEx          : present  (v5.4.23.5, Unity-Mono x64)  ✓ reuse
Relay            : present  (no_relay.pl)                ✓ reuse

PLAN
  [SKIP ]  BepInEx pack            already present, compatible
  [SKIP ]  no_relay.pl             already present
  [NEW  ]  BepInEx/plugins/NukeStats.dll        push (sha 3eddd6cb…, minisign OK)
  [BACKUP+WRITE]  BepInEx/config/anz.nukestats.cfg
                  → backup BepInEx/config/anz.nukestats.cfg.bak-20260629-1407
  [EDIT ]  Startup line           + Doorstop env, + relay, + -ServerRemoteCommands 7777
                  (panel: NO ptla_ key → will PRINT the line for you to paste)
  [WRITE]  DedicatedServerConfig.json   Port 7777 / QueryPort 7778  (IsOverride:true)
                  → backup DedicatedServerConfig.json.bak-20260629-1407
  [LOCAL]  ~/.nuke-option-toolkit/config.json, secrets.json (0600)
  [LOCAL]  apiKey.txt, panel.txt   (Pterodactyl power)

Legend: NEW=create · SKIP=already there · EDIT=modify-in-place · BACKUP+WRITE=overwrite (backup first)
Restart required after apply (BepInEx has no hot-reload).
Apply this plan?  [ Apply ]  [ Cancel ]
```

### Idempotency rules

- **Skip-if-present.** A dependency that is already there and compatible is `[SKIP]`, never re-pushed.
  Presence keys mirror `sources.json` (`steamcmd.exe`, `steamcmd.sh`, BepInEx core dir, `no_relay.*`).
  BepInEx presence additionally **version-gates** (must be 5.4.x Unity-Mono, else refuse and warn).
- **Backup-on-overwrite.** Any file that already exists and would be *changed* (`anz.nukestats.cfg`,
  `DedicatedServerConfig.json`, the Startup line/wrapper) is copied to
  `<name>.bak-<YYYYMMDD-HHMM>` **before** the write. The DLL is the exception: it is swapped
  atomically (`posix_rename`) so the live, memory-mapped inode is never corrupted — no `.bak` needed.
- **No blind merges.** `DedicatedServerConfig.json` is edited with a deep-diff (only the keys we own:
  ports, name, max players, rotation); unknown keys are preserved untouched.
- **Re-runnable.** Running the flow twice produces an all-`[SKIP]`/`[EDIT no-op]` plan — a second run
  changes nothing unless a value actually differs.
- **Restart, don't hot-swap.** BepInEx loads only at startup, so the flow always ends by prompting a
  restart; on a populated live server it warns and defers to the guarded deploy
  (`run.bat --deploy-plugin`) rather than bouncing immediately.

---

## Part 3 — Pterodactyl two-path Startup logic

The single hard question on the Pterodactyl option is **who edits the container Startup command** so
Doorstop loads BepInEx. The Pterodactyl **client** API key (`ptlc_…`) the toolkit already uses
**cannot** change a server's Startup field — that is an admin/Application-key operation. So the wizard
forks:

### DEFAULT path — import the egg + paste the Startup line (no admin key needed)

Use when the user has only a `ptlc_…` client key (the common case).

1. **Import the community egg** into the panel: *Admin → Nests → Import Egg* → upload
   `egg-nuclear-option.json` (fetched/vendored from `pterodactyl/game-eggs → nuclear_option`).
   Create the server from it (or reuse an existing Nuclear Option server).
2. **Set the egg variable** `MODDED_SERVER` (a.k.a. `ModdedServer`) **= `true`**.
3. **Paste the modded Doorstop Startup line** (below) into the panel's **Startup** field, replacing
   the egg's bare `export LD_LIBRARY_PATH=…; ./NuclearOptionServer.x86_64` line.
4. **Push files over SFTP** (BepInEx, `libdoorstop.so`, `doorstop_config.ini`, `NukeStats.dll`,
   `anz.nukestats.cfg`, `no_relay.*`).
5. Start + verify via the relay and `BepInEx/LogOutput.log`.

The wizard **prints the exact line and shows a "Copy" button**; it does not require any admin access.

### UPGRADE path — auto-rewrite with an Application key (`ptla_…`)

Use when the user supplies a Pterodactyl **Application** API key (admin area → *Application API*).

- The installer calls the **Application API** to rewrite the server's Startup string and set the
  `MODDED_SERVER` variable for them — no copy-paste. (Endpoint shape:
  `PATCH /api/application/servers/{id}/startup` with `{ "startup": "<line>", "environment": { "MODDED_SERVER": "true", ... }, "egg": <id>, "image": "<docker_image>" }`.)
- Everything else (SFTP file push, config edits, verify) is identical to the DEFAULT path.
- On any Application-API failure the installer **falls back to the DEFAULT path** and prints the line
  to paste — it never hard-fails (Risk D6).

### The exact modded Doorstop Startup line (Linux Pterodactyl)

This is the `doorstop_linux` startup block the manifest merges (`sources.json` → `startup_blocks`),
extended with the relay launch and the remote-command flag. Paste it verbatim into the panel's
**Startup** field (single line; substitute your game/query ports — defaults shown):

```sh
mkdir -p ./logs; python3 no_relay.py 0.0.0.0:7778 127.0.0.1:7777 & export LD_LIBRARY_PATH="$(pwd):$(pwd)/linux64:$LD_LIBRARY_PATH"; export DOORSTOP_ENABLED=1; export DOORSTOP_TARGET_ASSEMBLY="$(pwd)/BepInEx/core/BepInEx.Preloader.dll"; export LD_PRELOAD="$(pwd)/libdoorstop.so:$LD_PRELOAD"; ./NuclearOptionServer.x86_64 -batchmode -nographics -logFile ./logs/console.log -limitframerate 60 -ServerRemoteCommands 7777
```

What each piece does:

| Fragment | Why |
|---|---|
| `mkdir -p ./logs; python3 no_relay.py 0.0.0.0:7778 127.0.0.1:7777 &` | Starts the in-container relay so the off-box bot can reach the localhost-only command port (`0.0.0.0:<query> → 127.0.0.1:<game>`). |
| `export LD_LIBRARY_PATH="$(pwd):$(pwd)/linux64:…"` | Lets the loader find `libdoorstop.so` + the game's bundled libs. |
| `export DOORSTOP_ENABLED=1` | Turns Doorstop on. |
| `export DOORSTOP_TARGET_ASSEMBLY="$(pwd)/BepInEx/core/BepInEx.Preloader.dll"` | Tells Doorstop which managed DLL to inject before `main()`. |
| `export LD_PRELOAD="$(pwd)/libdoorstop.so:$LD_PRELOAD"` | Pre-loads the Doorstop hook so BepInEx initialises before the Unity main thread. |
| `./NuclearOptionServer.x86_64 -batchmode -nographics` | Headless server launch. |
| `-logFile ./logs/console.log` | Stable log path the bot tails over SFTP (carries `[NOSTATS]` + chat). |
| `-limitframerate 60` | Caps the server tick at 60 Hz (CPU + physics timestep). |
| `-ServerRemoteCommands 7777` | Opens the TCP command port on `127.0.0.1:<game>` for the relay to expose. |

> If your host's egg pins a different working dir or already passes some of these, keep its existing
> flags and only **add** the `no_relay.py …`, the four `export` lines, and `-ServerRemoteCommands`
> + `-logFile`. The `&` backgrounds the relay so the game binary stays PID 1 in the container.
> Windows Pterodactyl/standalone needs **none** of this — `winhttp.dll` + `doorstop_config.ini`
> next to the `.exe` auto-inject Doorstop, and the relay is launched from the `RunServer.bat`.

---

## Quick reference — option → dependency chain

(From `sources.json` → `options`; what each path installs.)

| Option | Dependency chain |
|---|---|
| `own_pc_windows` | SteamCMD(win) → binaries → BepInEx(win) → NukeStats |
| `own_pc_linux` | SteamCMD(linux) → binaries → BepInEx(linux) → NukeStats |
| `external_linux_ptero` | egg → binaries (host SteamCMD) → BepInEx(linux) → NukeStats → relay |
| `external_windows` | SteamCMD(win) → binaries → BepInEx(win) → NukeStats |

Binaries are **always Steam app `3930080`** (no GitHub mirror). BepInEx is **5.4.x Unity-Mono only**
(never 6.x/IL2CPP). The plugin DLL is **SHA-256 + minisign verified** before it is staged or pushed.
