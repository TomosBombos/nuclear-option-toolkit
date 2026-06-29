# Nuclear Option — Community Server Toolkit

Turn a vanilla *Nuclear Option* dedicated server into a managed community server:
persistent ranks, a real-score economy, skill ratings, PvP team balance (with new-joiner
& squad protection), anti-grief enforcement, a flood-disconnect guard, a live battle map,
and a browser admin console where you can change every setting **live**.

It's three cooperating pieces: a server-side **plugin** (BepInEx/Harmony), a **bot** that
runs on your PC, and a **web command centre** in your browser. They talk only through the
game's log + a relay + shared files, so any one can restart without taking the others down.

## Get started

### Prerequisites — install these first

You need **Python 3.8+**, the **paramiko** package, and (to clone) **Git**. Step by step:

<details open>
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

**1. Download the toolkit**

With git:
```bash
git clone https://github.com/TomosBombos/nuclear-option-toolkit.git
cd nuclear-option-toolkit
```
No git? On this page click the green **`< > Code ▾` → Download ZIP**, extract it, and open a
terminal in the extracted folder.

**2. Run the guided installer**
```bash
python installer/setup.py
```
(On Windows you may need `py installer\setup.py`.) A wizard opens in your browser: it checks
prerequisites, asks where your server runs (your own PC / external Linux-Pterodactyl /
external Windows), takes your connection details, lets you pick which plugin features you
want, fetches the right files, and writes a clean config. **Your credentials stay on your
machine and never enter the repo.** More detail: **[installer/README.md](installer/README.md)**.

> Prefer to wire it up by hand? Copy `run.bat.example` → `run.bat`, `apiKey.txt.example` →
> `apiKey.txt`, `panel.txt.example` → `panel.txt`, fill in your values, then run `run.bat`.

> ⚠️ **Early / iterating.** The guided installer is under active development. If a step
> doesn't yet complete end-to-end on your setup, the manual path above works — please
> [open an issue](https://github.com/TomosBombos/nuclear-option-toolkit/issues) with what
> you hit so we can harden it.

## Documentation

| Doc | What it covers |
|---|---|
| **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** | Every feature, how it works, the data contracts, the CLI + API surface |
| **[docs/DESIGN_HISTORY.md](docs/DESIGN_HISTORY.md)** | *Why* it's built this way — the incidents that drove each feature |
| **[docs/PRODUCTIZATION_PLAN.md](docs/PRODUCTIZATION_PLAN.md)** | The roadmap to a one-click, cross-platform, auto-updating product |
| **[docs/PRE_UPLOAD_CHECKLIST.md](docs/PRE_UPLOAD_CHECKLIST.md)** | **Read before publishing** — the secret-scrub gate |
| **[SECURITY.md](SECURITY.md)** | Update signing (minisign) + the credential/secrets stance |

## Features at a glance

- **Ranks & economy** — lifetime points from real in-game score + win/placement bonuses, 11 ranks, fully audited ledgers.
- **NuclearSkill** — a points-per-life skill rating (`!skill`), used to balance teams fairly.
- **Team balance (PvP)** — keeps sides even; protects new joiners (15 min) and **`!squadup`** friend groups; moves the player who best evens the skill totals.
- **Anti-grief** — automated teamkill enforcement (warn → kick → ban) and a network flood guard that stopped a recurring match-start mass-disconnect.
- **AI limiter** — caps AI aircraft and clears stuck ones (performance), never touches players.
- **Live map + web CC** — pan/zoom battle map, player/AI/ship blips, power control, a map-change button, and a **⚙ Settings menu to change any plugin setting live**.
- **Map voting**, chat rank tags, profanity filter, forfeit votes, PvE timeout rules, and more.

## Updating (opt-in)

Pull fixes when *you* choose — the **plugin and the bot**, on a **stable** or **nightly** channel:

```bash
python installer/updater.py check                    # what's available on your channel?
python installer/updater.py update --component all    # download + verify (SHA-256 + minisign) + stage
```

Pick your channel in `~/.nuke-option-toolkit/config.json` (`update.channel`: `"stable"` or
`"nightly"`). **Verify-before-apply is mandatory** and nothing is applied until you choose to
deploy (plugin → `run.bat --deploy-plugin`; bot → `update --component bot --apply`).
Maintainers publish with `scripts/release.py` (`--with-bot`, `--channel`). See **[SECURITY.md](SECURITY.md)**.

## Status

Actively developed against a live ANZ community server. The toolkit is feature-complete and
battle-tested for the hosted-Linux/Pterodactyl setup; the cross-platform installer + frozen
launcher are in progress (see the productization plan). **Building the plugin requires the
game's managed assemblies** (`NukeStats/libs/`), which you supply from your own game install —
they are not distributed here.

## License

See [`LICENSE`](LICENSE) — GPL-3.0-or-later.
