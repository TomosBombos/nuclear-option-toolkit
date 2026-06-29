# Nuke-Option — Community Server Toolkit

This feature set is for those wanting to easily manage their dedicated servers with some additional features that add a ranking system, improve performance and moderation tools. As well as some other things like a live map because why not?

It's three cooperating pieces — a server-side **plugin** (BepInEx/Harmony), a **bot** that
runs on your PC, and a **web command centre** in your browser. They talk only through the
game's log, a relay, and shared files, so any one can restart without taking the others down.

## ⬇️ Download — pick your server type

Each download is a ready-to-go folder with **everything inside** — BepInEx, the NukeStats
plugin, all 18 missions, the bot, and the web command centre. Grab the one that matches how
your server is hosted, unzip it, and run the installer inside it. The installer asks for your
details (each field explains **what it is** and **where to find it**) and wires everything up.

> **Which is tested?** ✅ **Pterodactyl is working and tested end-to-end.** ⚠️ The **Local**
> (own-PC) and **Manual** (drag-and-drop) options are built but only lightly tested so far —
> they should work, but expect rough edges and please report anything that doesn't.

| Your setup | Status | Download | What you do |
|---|---|---|---|
| **Pterodactyl panel** (hosted Linux) | ✅ Working | **[⬇ Pterodactyl bundle](https://github.com/TomosBombos/nuclear-option-toolkit/releases/latest/download/nuclear-option-toolkit-pterodactyl.zip)** | Unzip → run `install.bat` (or `./install.sh`) → enter your panel's SFTP + API details → it pushes the plugin, missions and config to your server over SFTP and makes it boot modded. Then launch the bot + dashboard. |
| **Your own PC** (Windows / Linux) | ⚠️ Beta (lightly tested) | **[⬇ Local bundle](https://github.com/TomosBombos/nuclear-option-toolkit/releases/latest/download/nuclear-option-toolkit-local.zip)** | Unzip → run `install.bat` → it installs the dedicated server (SteamCMD), copies the toolkit in, and launches server + bot + dashboard together. |
| **Hosting by hand / other** | ⚠️ Beta (lightly tested) | **[⬇ Manual bundle](https://github.com/TomosBombos/nuclear-option-toolkit/releases/latest/download/nuclear-option-toolkit-manual.zip)** | Unzip → follow `README.md` to drag the files into place (both BepInEx packs included); the installer writes your config. |

> All downloads live on the **[Releases page](https://github.com/TomosBombos/nuclear-option-toolkit/releases/latest)**.
> You only need **Python 3.8+** installed first. Prefer to clone the repo and run the
> installer from source? See **[Get started](#get-started)** below.

## What it does

- **Ranks & economy** — lifetime points from real in-game score + win/placement bonuses, 11 ranks, fully audited ledgers.
- **NuclearSkill** — a points-per-life skill rating (`!skill`), used to balance teams fairly.
- **Team balance (PvP)** — keeps sides even; protects new joiners and **`!squadup`** friend groups; moves the player who best evens the skill totals.
- **Anti-grief & moderation** — automated teamkill enforcement (warn → kick → ban), bans, votekick, and a network flood guard that stopped a recurring match-start mass-disconnect.
- **AI limiter** — caps AI aircraft and clears stuck ones for performance; never touches players.
- **Live map + web command centre** — pan/zoom battle map with player/AI/ship blips, power control, a map-change button, scheduling, and a **⚙ Settings menu to change any plugin setting live**.
- **More** — map voting, chat rank tags, profanity filter, forfeit votes, a server-message manager, PvE timeout rules, and an opt-in **global cross-server leaderboard**.

→ Full plain-English tour: **[docs/FEATURES.md](docs/FEATURES.md)**

## Global Leaderboard

The top-ranked pilots across every server running the toolkit.
*(Goes live here once the shared cross-server board is up — it's being stood up now.)*

| Points | Rank | Name | Server |
|--------|------|------|--------|
| *coming soon* | | | |
| | | | |
| | | | |
| | | | |
| | | | |

The full board is also available in game with `!global`. **Running a server?** Turn on the global
leaderboard in the command centre's settings to contribute — only player names, points, region, and
your server name are published (never IPs or SteamIDs).

## Documentation

| Doc | What it covers |
|---|---|
| **[docs/FEATURES.md](docs/FEATURES.md)** | What every feature does and why — in plain English |
| **[docs/COMMANDS.md](docs/COMMANDS.md)** | Every command & tool: players, admins, the web console, the CLI |
| **[docs/MODERATION.md](docs/MODERATION.md)** | Teamkill enforcement, anti-grief auto-kick, bans, votekick, reports |
| **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** | How the three processes fit together — overview up top, deep technical reference below |
| **[SECURITY.md](SECURITY.md)** | Update signing (minisign) + the credential/secrets stance |

## Get started

**1. Install the prerequisites** — you need **Python 3.8+**, the **paramiko** package, and (to clone) **Git**.

<details>
<summary><b>Windows</b></summary>

1. **Python** — open <https://www.python.org/downloads/>, click **Download Python 3.x**, run the
   installer, and on the first screen **tick "Add python.exe to PATH"**, then **Install Now**.
   Open a *new* PowerShell window and check: `python --version`.
2. **paramiko** (needed for the external / SFTP options): run `pip install paramiko`
   (if `pip` isn't found, use `python -m pip install paramiko`).
3. **Git** (for cloning — skip if you use *Download ZIP* below) — install from
   <https://git-scm.com/download/win>, accept the defaults. Check: `git --version`.
</details>

<details>
<summary><b>macOS</b></summary>

```bash
brew install python git      # or get Python from python.org and Git via: xcode-select --install
pip3 install paramiko
python3 --version && git --version
```
</details>

<details>
<summary><b>Linux (Debian/Ubuntu)</b></summary>

```bash
sudo apt update && sudo apt install -y python3 python3-pip git
pip3 install paramiko
python3 --version && git --version
```
</details>

> Use `python` / `pip` on Windows, and `python3` / `pip3` on macOS & Linux.

**2. Download the toolkit**

With git:
```bash
git clone https://github.com/TomosBombos/nuclear-option-toolkit.git
cd nuclear-option-toolkit
```
No git? On this page click the green **`< > Code ▾` → Download ZIP**, extract it, and open a
terminal in the extracted folder.

**3. Run the guided installer** (the toolkit source lives in [`src/`](src))
```bash
python src/installer/setup.py
```
(On Windows you may need `py src\installer\setup.py`.) A wizard opens in your browser: it checks
prerequisites, asks where your server runs, takes your connection details, sets a few options,
and writes a clean config. **Your credentials stay on your machine and never enter the repo.**
More detail: **[src/installer/README.md](src/installer/README.md)**.

> Prefer to wire it up by hand? In `src/`, copy `run.bat.example` → `run.bat`,
> `apiKey.txt.example` → `apiKey.txt`, `panel.txt.example` → `panel.txt`, fill in your values,
> then run `run.bat`.

> **Building the plugin from source** requires the game's managed assemblies
> (`src/NukeStats/libs/`), which you supply from your own game install — they are not
> distributed here.

> ⚠️ **Early / iterating.** The guided installer is under active development. If a step
> doesn't yet complete end-to-end on your setup, the manual path above works — please
> [open an issue](https://github.com/TomosBombos/nuclear-option-toolkit/issues) with what
> you hit so we can harden it.

## Updating (opt-in)

Pull fixes when *you* choose — the **plugin and the bot**, on a **stable** or **nightly** channel:

```bash
python src/installer/updater.py check                    # what's available on your channel?
python src/installer/updater.py update --component all    # download + verify (SHA-256 + minisign) + stage
```

Pick your channel in `~/.nuke-option-toolkit/config.json` (`update.channel`: `"stable"` or
`"nightly"`). **Verify-before-apply is mandatory** and nothing is applied until you choose to
deploy (plugin → `run.bat --deploy-plugin`; bot → `update --component bot --apply`).
Maintainers publish with `src/scripts/release.py` (`--with-bot`, `--channel`). See **[SECURITY.md](SECURITY.md)**.

## Community Servers

Servers running the toolkit that have opted into the public directory — find them by **name** in the
in-game server browser (Nuclear Option has no direct-connect). **Status** refreshes about every 4 hours
(a server shows online if it checked in on the latest update).

| Server | Region | Status |
|--------|--------|--------|
| *coming soon* | | |

**Running a server?** Enable listing in the command centre's settings to appear here (name + region
only — never your IP).

## License

See [`LICENSE`](LICENSE) — GPL-3.0-or-later.
