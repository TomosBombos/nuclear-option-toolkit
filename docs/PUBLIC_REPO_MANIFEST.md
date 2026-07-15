# Public Repo Manifest

The **exact** contents of the public `nuclear-option-toolkit` repository, and how it is
produced. The repo is built **clean-room** by `scripts/build_public_repo.py`, which copies
only the whitelist below into a fresh folder, scrubs the copies, hardens `.gitignore`, and
runs a secret/PII gate. **The live working files are never modified.** Last verified build:
**59 files + a hardened `.gitignore`, 8 scrubs, secret-scan CLEAN** (plugin docs auto-synced
to the live `NukeStatsPlugin.cs` version).

## How to build

```bash
cp scripts/scrub_targets.example.json scripts/scrub_targets.json   # fill in your real values (gitignored)
python scripts/build_public_repo.py --dest ../nuclear-option-toolkit
# then, only after the build prints CLEAN:
cd ../nuclear-option-toolkit && git init && git add -A && git commit -m "Initial public release"
```

`scrub_targets.json` holds the real admin SteamID / server IP / SFTP host the scrubber must
find-and-replace. It is **gitignored and never published**; only `scrub_targets.example.json`
(placeholders) ships, so a forker can scrub their own deployment the same way.

## INCLUDED (whitelist — deny-by-default; anything not listed is omitted)

**Root files (23):**
- Docs: `README.md`, `SECURITY.md`, `SERVER_DOCUMENTATION.md`
- Source: `no_mapvote_bot.py`, `cc_web.py`, `command_centre.py`, `map_atlas.py`, `webcc.html`, `settings_catalogue.json`
- Map assets: `heartland_map.png`, `ignus_map.png`
- Launchers: `webcc.bat`, `commandcentre.bat`, `deploy.bat`, `endmission.bat`, `say.bat`, `status.bat`, `run_keepalive.bat`
- Templates: `run.bat.example`, `apiKey.txt.example`, `panel.txt.example`, `anz.nukestats.cfg.example`, `config.example.json`

**Subtrees (whole tree minus the noted excludes):**
| Dir | Excludes |
|---|---|
| `docs/` | `_*.json` (machine dumps `_component_inventory.json`, `_settings_design.json`) |
| `installer/` | `__pycache__/`, `sources.lock.json` (per-install state) |
| `scripts/` | `__pycache__/`, `scrub_targets.json` (real PII) |
| `NukeStats/` | `libs/`, `bin/`, `obj/`, `bepinex_pack/` → ships only `NukeStatsPlugin.cs`, `NukeStats.csproj`, `build.bat`, `README.md` |
| `map-build/` | `__pycache__/` |
| `START HERE/` | `*.lnk` (machine-specific shortcut) |

Plus a generated, hardened `.gitignore`.

## EXCLUDED (and why)

- **Secrets:** `run.bat` (`NO_SFTP_PASS`), `apiKey.txt` (`ptlc_`), `panel.txt`, `secrets.json`, `*.key/*.pem/*.minisig`, `scripts/scrub_targets.json`.
- **PII / runtime state:** `ranks*.json*` (incl. the `ranks.json.bak` that escaped the old rule — 544 SteamIDs), `backups/`, `*.jsonl` (ledgers, `admin_commands`), `match_history.json`, `*.log`, `dashboard_state.json`, `schedule.json`, `command_centre_settings.json`, `console_filters.json`, `plugin_*.txt`, `bot_overrides.json`, `*.done`.
- **Build / binaries:** `*.dll`, `pending_plugin.*`, `deployed_plugin.*`, `NukeStats/{libs,bin,obj,bepinex_pack}`, the proprietary game assemblies (license).
- **Bulk / dumps:** `archive/` (774 MB), `_server_backup/`, `map_backup_*/`, `mapdata/`, `docs/_*.json`, `__pycache__/`.
- **Local AI / agent clutter:** `AGENTS.md` (and any `.cursor/` rules) — local-dev only; **not** on the whitelist (`REPO_ROOT_FILES` / `ROOT_FILES`). Keep it out of public trees.

## SCRUB transforms (applied to the copies)

1. `no_mapvote_bot.py` — real server IP → `os.environ.get("NO_RCMD_HOST","127.0.0.1")`; SFTP host comment → placeholder; `ADMIN_SIDS` → `set(os.environ["NO_ADMIN_SIDS"].split())`; remaining example SteamIDs → `7656119xxxxxxxxxx`.
2. `NukeStats/NukeStatsPlugin.cs` — `Config.Bind("Admin","SteamIds","<real>")` → default `""`.
3. `docs/INSTALL_SOURCES.md`, `docs/SETTINGS_AND_INSTALL_V2.md`, `docs/PRODUCTIZATION_PLAN.md` — strip the leaked agent-preamble line before the first `# ` heading.
4. `docs/ARCHITECTURE.md` — plugin version `0.9.5` → `0.9.6` (3 refs).
5. `docs/DESIGN_HISTORY.md` — "newest layer (v0.9.5)" → "v0.9.5 layer".
6. `SECURITY.md` — reword the `trusted.pub` claim to "ships from the first signed release onward" (it isn't generated yet).

## Secret-scan gate

After building, the script scans every text file. **HARD** hits (fail the build, exit 2):
`ptlc_`/`ptla_` keys, real `7656119##########` SteamIDs, the known real IP/host, private-key
blocks, non-placeholder `NO_SFTP_PASS`. **WARN** hits (reported, exit 0): generic IPv4
(minus localhost), emails. Run with `--strict` to fail on warnings too.

## Residual pre-publish TODOs (not blockers for building, but for going public)

- [x] **Rotate** the live SFTP password + Pterodactyl key — done 2026-06-28 (new values gitignored; restart the bot ops-side to apply).
- [x] Add a **`LICENSE`** — done (GPL-3.0-or-later, verbatim from gnu.org; README points to it).
- [ ] Generate + commit **`installer/trusted.pub`** (minisign public key) at the first signed release.
- [ ] Set the repo **owner/slug** (`update.github_repo`) — name = `nuclear-option-toolkit`, owner handle TBD.
- [ ] Apply the installer code-review fixes (`fetcher.py` TOFU-hash enforcement + tar symlink guard).
- [ ] **(User) a couple more things to add before shipping** — TBD, planned for tomorrow.
