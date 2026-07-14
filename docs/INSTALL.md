# Install, run, and update

This guide covers installing the Nuclear Option toolkit, starting it, keeping it
running, and updating it later. There are three ways to host it — pick the one that
matches where your game server lives:

| Bundle | For | Status |
| --- | --- | --- |
| **Pterodactyl** | A server hosted on a Pterodactyl panel | Working / most-used path |
| **Local** | Running everything on your own Windows/Linux PC | Beta — lightly tested |
| **Manual** | You run the dedicated server yourself and add the toolkit by hand | Beta — lightly tested |

All three use the same guided setup wizard and the same optional updater, described below.

---

## Before you start (prerequisites)

This toolkit is **not** dependency-free. You need:

- **Python 3.8 or newer.** On Windows, install from [python.org](https://www.python.org/downloads/)
  and tick **Add Python to PATH** during install.
- **Python packages:**
  - `paramiko` — required for any SFTP path (Pterodactyl and manual-external installs).
  - `flask` — required for the web command centre (the browser dashboard).
  - `requests` — required by the bot and the updater.

You can install the packages in either of two ways:

- **In the wizard:** the Welcome step has an **"Install Python packages"** button that runs
  pip to install `flask`, `paramiko`, and `requests` for you.
- **By hand:** open a terminal and run:

  ```
  pip install paramiko flask requests
  ```

---

## The setup wizard (used by every hosting type)

First-time setup is done through a small, offline, browser-based wizard.

**How it works:** `installer/setup.py` starts a **localhost-only** web server on a random
free port, protected by a random one-time token in the URL, and opens `installer/wizard.html`
in your browser. Nothing is uploaded except when you press a **Test** button or the actual
**Install** button. It reads the list of features/settings from `settings_catalogue.json`.

- In a downloaded **bundle**, a `bundle_type.txt` file scopes the wizard to that one hosting
  type, so you get a focused **5-step** flow: Welcome → Server → Connection → Options → Install.
- Running from a **full source checkout** shows the multi-option **7-step** flow:
  Welcome → Hosting → Server → Connection → Features → Files → Review.

**What the wizard writes:**

- `config.json` — connection details and settings. **Safe to share; contains no secrets.**
- `secrets.json` — SFTP password and/or Pterodactyl API key. Written with `0600` permissions,
  never placed in `config.json`.
- `anz.nukestats.cfg` — the plugin configuration.
- `DedicatedServerConfig.json` — the game server's config (name, ports, mission rotation, etc.).

**Where that config lives (important):** by default the wizard writes `config.json` and
`secrets.json` into a **per-folder** `.nost-data` directory inside the server folder, so two
installs in sibling folders never clash. You can point this elsewhere with the
`NOST_DATA_DIR` environment variable (or the `--data-dir` flag). The generated launchers set
`NOST_DATA_DIR` for you — but if you ever run the updater or the bot by hand, set it too so
they read the same config the wizard wrote. See "The data-dir trap" under **Updating**.

**Running the wizard from a source checkout** (the bundles wrap this in `install.bat` /
`install.sh` — see each option below). From the `src/` folder:

```
python installer/setup.py
```

Optional flags:

- `--no-browser` — print the wizard URL instead of opening a browser.
- `--data-dir <path>` — choose where `config.json` + `secrets.json` are written.
- `--force` — overwrite a config that belongs to a different server.

`install.bat` and `install.sh` are generated into every release bundle by the bundle build
(`src/scripts/build_bundles.py`), so you will not find them under `src/`. Each one changes
into the bundle folder and runs `installer/setup.py` — Windows tries `python`, then `py`;
Linux tries `python3`, then `python`. A `bundle_type.txt` file next to them scopes the wizard
to that bundle's hosting type.

---

## Option A — Pterodactyl (working / tested path)

This installs the toolkit onto a Pterodactyl-panel-hosted server: it pushes BepInEx, the
NukeStats plugin, all 18 bundled co-op missions (the server rotation also includes the
built-in Escalation PvP mode, for 19 rotation entries), and the in-container relay over
SFTP, writes
`DedicatedServerConfig.json`, and installs a self-injecting launch wrapper so the server boots
modded **with no panel edits**. The bot and web command centre then run on your PC.

### Install

1. Install **Python 3.8+** (tick *Add to PATH*).
2. Download and unzip the **Pterodactyl** bundle anywhere on your PC.
3. Run **`install.bat`** (Windows) or **`./install.sh`** (macOS/Linux). This launches the
   setup wizard scoped to Pterodactyl.
4. On the Welcome step, use **"Install Python packages"** if you haven't installed `paramiko` yet.
5. Enter your **panel SFTP details**: host, port (usually **2022**, not 22), SFTP username
   (the panel shows it as `account.SERVERID`), and the SFTP password (this is your panel
   account password). Use the **Test SFTP** button to confirm.
6. Optionally enter **power control** so the dashboard can start/stop/restart the server:
   the panel base URL, a **CLIENT** API key (it starts with `ptlc_`, created under
   **Account → API Credentials** — not an application key), and the server id.
   Use **Test panel** to confirm.
7. Set the **ports** (game **7777**, query **7778** — they must differ), the server name, and
   the **relay port** (add a second panel allocation; default **5550**).
8. Click **"Install to my server"** (uploads roughly 25 MB), then **Launch This Server**.
9. Watch the panel console for **`NukeStats loaded`** to confirm the plugin is running.

The wizard writes `config.json`, `secrets.json`, the power-control files `apiKey.txt` /
`panel.txt`, and a per-folder **START THIS SERVER** launcher. No IP address is uploaded anywhere.

### Run

- Open the dashboard at **http://localhost:8770**.
- To start everything again later, double-click **`START HERE\START THIS SERVER.bat`**.

See **Starting and keeping it running** below for the individual launchers.

---

## Option B — Local, your own PC (beta — lightly tested)

Runs the whole community server on your own Windows/Linux PC: the dedicated server, the plugin,
the missions, the bot, and the web command centre, launched together.

### Install

1. Install **Python 3.8+** (tick *Add to PATH*).
2. Unzip the **Local** bundle somewhere with a short path (avoid OneDrive and Program Files).
3. Run **`install.bat`** (Windows) or **`./install.sh`** (Linux). This launches the setup
   wizard scoped to Local.
4. In the wizard:
   - Install the dedicated server (the wizard runs SteamCMD to download Steam app **3930080**
     into a folder you choose) or point it at an existing install. Under the hood it runs:

     ```
     steamcmd +force_install_dir <dir> +login anonymous +app_update 3930080 validate +quit
     ```

   - Set the **ports**: game **7777/UDP** and query **7778/UDP**. For internet play, forward
     **both** as UDP on your router.
   - Set the **server name** and your **admin SteamID64**.
5. Click **Launch**.

The wizard copies the bundled BepInEx loader, `NukeStats.dll`, and the missions into the game
folder, writes `DedicatedServerConfig.json` (with `ModdedServer=true`), creates
`logs/console.log` and a StartServer launcher, and generates a per-folder **START THIS SERVER**
launcher that boots the game server + bot + web command centre together. Power mode is `local`,
so the bot talks to the game over `127.0.0.1` (RemoteCommand port default **5504**, no
port-forwarding needed for the command channel).

For reference, the local server is launched with:

```
NuclearOptionServer.exe -batchmode -nographics -logFile logs\console.log -ServerRemoteCommands <port>
```

(On Linux it is `./NuclearOptionServer.x86_64` with the BepInEx doorstop environment variables set.)

### Run

- Open the dashboard at **http://localhost:8770**.
- To start everything again later, double-click **`START HERE\START THIS SERVER.bat`** (Windows)
  or run **`START HERE/start_this_server.sh`** (Linux).

---

## Option C — Manual, host it by hand (beta — lightly tested)

For owners who already run the dedicated server themselves and want to add the toolkit by hand.
The bundle ships everything — both the Windows and Linux BepInEx packs, the plugin, the missions,
the bot, and the web command centre — plus drag-and-drop instructions.

### Install

1. Install **Python 3.8+**.
2. Unzip the **Manual** bundle.
3. Follow the **`README.md` inside the bundle** to drag the game-side files (BepInEx +
   `NukeStats.dll` + the missions) into place on your server, and paste in the startup line.
4. Run the installer to write your config: **`install.bat`** / **`./install.sh`**, or from a
   source checkout `python installer/setup.py`. Scoped to Manual, this **only** writes your
   admin `config.json` + `secrets.json` + `anz.nukestats.cfg` — it does **not** upload anything.
   Each field on screen explains what it is and where to find it.
5. Start the bot and web command centre (see below).

### Run

- Start the bot: **`run.bat`**.
- Start the web command centre: **`webcc.bat`** (opens **http://127.0.0.1:8770**).

---

## Starting and keeping it running

The wizard generates per-folder, folder-safe launchers so a second server in a sibling folder
is never touched (every process it stops is matched by that folder's exact path, never by name):

- **`START HERE\START THIS SERVER.bat`** — starts everything for this server (Windows).
  On Linux it is **`START HERE/start_this_server.sh`**. The `START HERE` folder also contains
  **`1. Start Bot.bat`** and **`2. Start Web Command Centre.bat`** if you want them separately.
- **`run.bat`** — starts the **bot** only. It sets the per-folder `NOST_DATA_DIR` and the SFTP
  environment variables, then runs `python no_mapvote_bot.py`.
- **`webcc.bat`** — starts the **web command centre** only, on the pinned port for this folder
  (via `NOCC_PORT`), and opens the dashboard.

**Keeping it running:** the bot's main loop is wrapped in an auto-restart loop, so it recovers
from most errors on its own. An external **`run_keepalive.bat`** covers hard process death
(the whole process being killed).

**Useful bot subcommands** (run from the toolkit folder):

- `run.bat --set-votekick on|off` — turn the game's built-in vote-to-kick on or off.
- `run.bat --rewrite-wrapper` — rebuild the launch wrapper after changing the server tick rate,
  then restart the game server.

> Note: `run.bat.example` ships as a template. The real `run.bat` is generated by the wizard and
> is gitignored because it holds your SFTP password — never commit it, and rotate the password if
> it has ever been pasted into a chat or tool.

---

## Security note before you expose the dashboard

- The web command centre has **no login or authentication** on any page or API route. Anyone who
  can reach the port has full admin control (start/stop the server, ban players, edit config,
  grant points). Access control is entirely the network bind.
- The dashboard **binds to `0.0.0.0` by default** (all interfaces — reachable from your LAN),
  even though the startup message prints a `127.0.0.1` URL.
- To restrict it to the host machine only, set `web.host` to `"127.0.0.1"` in `config.json`,
  or set the environment variable `NOCC_HOST=127.0.0.1`.
- Do not port-forward or otherwise expose the dashboard to the public internet.

---

## Updating (the opt-in updater)

The install itself is fully offline — **nothing auto-updates**. Pulling fixes is a separate,
by-choice step using `installer/updater.py`. It never applies anything without you asking: `check`
only reports, `update` downloads + verifies + **stages**, and you decide when to apply.

### How it verifies before staging

Every download is verified before it is staged (identical for plugin and bot):

- **SHA-256** of the download is checked against the release's published `<asset>.sha256`.
- If a **minisign** `<asset>.minisig` and the bundled `trusted.pub` key are present, the Ed25519
  **signature** is verified (via the `minisign` CLI, or `pynacl` / `cryptography`).
- If no verifier is available it **refuses to stage** unless you explicitly run it with
  `--i-understand-unsigned`.

Updates come from **full GitHub releases only** — drafts and pre-releases are ignored.

### Commands

Run these from the toolkit's `src` folder. Set `NOST_DATA_DIR` first so the updater reads the
same config the wizard wrote (see the trap below).

- `python installer/updater.py check` — report what's available.
- `python installer/updater.py update` — download + verify + **stage the plugin**
  (as `pending_plugin.dll`); no deploy yet.
- `python installer/updater.py update --component bot` — stage the bot (`pending_bot.py`).
- `python installer/updater.py update --component all` — stage plugin + bot + web command centre.
- `--apply` — replace the bot/web-CC file in place (backs up the old file first; no auto-restart).
- `--deploy` — run the guarded plugin deploy (`run.bat --deploy-plugin`).

So a typical plugin update is: **check → update (stage) → deploy**. A bot update is:
**check → update --component bot (stage) → apply**.

You can also do this from the browser: the dashboard's **⚙ Settings → Updates** modal can check
GitHub, then download-and-stage; you then deploy the staged plugin from the **Schedule** modal.

> Note on the two deploy commands: `run.bat --deploy-plugin` is the guarded stage-restart-verify
> job the updater triggers to roll out a plugin. `run.bat --put-atomic <local> <remote>` is a
> separate low-level command that atomically uploads a single built DLL over SFTP. They are not
> interchangeable.

### Private repositories

If you update from a **private** GitHub repo (for example your own fork), set the `GITHUB_TOKEN`
environment variable so the updater can read the releases API.

### The data-dir trap

The setup wizard writes config into a per-folder `.nost-data` directory, but the updater defaults
to `~/.nuke-option-toolkit` when `NOST_DATA_DIR` is not set. **Unless you set `NOST_DATA_DIR`,
the updater reads a different location than the wizard wrote.** The generated launchers set it for
you; when running the updater by hand, set `NOST_DATA_DIR` to your server's `.nost-data` folder
first.

### Update settings (in `config.json`)

- `update.github_repo` — default `TomosBombos/nuclear-option-toolkit` — the repo the updater reads
  releases from. Change it only if you run your own fork. Set in the setup wizard.
- `update.auto_check` — default `false` — check for updates on launch. It still asks before
  applying anything. Set in the setup wizard.
