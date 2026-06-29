# Nuclear Option Community Server — Installer Source Manifest & Auto-Fetcher Design

Lead-engineer spec. Verified against the toolkit on disk (`NukeStats\bepinex_pack` = Doorstop 4.5.0 / BepInEx v5.4.23.5 Linux x64; startup block matches `NukeStats\README.md:78`; `installer\updater.py` already implements github-release + SHA-256 + minisign). Confidence is stated per row; nothing is asserted beyond what the research verified.

---

## 1. Source manifest

Hosting options:
- **A** — own PC (Windows or Linux), self-hosted
- **B** — external Linux behind a Pterodactyl panel
- **C** — external Windows host

**Universal truths that shape every table below**
- The dedicated-server **binaries come from Steam via SteamCMD, app id `3930080`** ("Nuclear Option Dedicated Server"), anonymous login. **There is no GitHub source for the binaries.** This corrects the owner's belief.
- **Shockfront DOES have a GitHub** (`Shockfront-Studios/Nuclear-Option-Server-Tools`) but it ships docs/tooling only — never the binaries, and it has **no Releases**.
- The **Pterodactyl egg is real but community-authored** (`pterodactyl/game-eggs`, author redbananaofficial), **not** Shockfront, and **not** in `parkervcp/pelican-eggs`. The stock egg is **vanilla — no BepInEx/Doorstop**; the toolkit must layer that on.
- Nuclear Option is **Unity Mono** → **BepInEx 5.4.x** (currently v5.4.23.5, Doorstop 4.5.0). **Never** BepInEx 6 / IL2CPP.

### Option A — own PC (Windows or Linux)

| # | File / dependency | Real upstream URL | Fetch method | Always-latest | Integrity | Offline manual download |
|---|---|---|---|---|---|---|
| A1 | **SteamCMD bootstrap** (only if no Steam/SteamCMD present) | Win: `http://media.steampowered.com/installer/steamcmd.zip` · Linux: `https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz` | `http-zip` | Evergreen self-updating URL; SteamCMD self-updates on first run | TOFU-pin SHA-256 in lockfile (Valve publishes none) | Download the zip/tarball from the Valve URL; point installer at the extracted folder. **Confidence: high** |
| A2 | **Server binaries** (`NuclearOptionServer.exe`/`.x86_64` + `RunServer.*`) | `steamcmd +force_install_dir <dir> +login anonymous +app_update 3930080 validate +quit` (ref: `https://steamdb.info/app/3930080/`) | `steamcmd` | Inherently always-latest; re-running `app_update … validate` IS the update path | SteamCMD `validate` checksums the depot itself | **No GitHub mirror exists.** Offline = run the same SteamCMD `app_update` once on any internet-connected trusted machine, then point installer at the produced folder. **Confidence: high** |
| A3 | **BepInEx mod loader** — pick by OS | Win: `https://github.com/BepInEx/BepInEx/releases/download/v5.4.23.5/BepInEx_win_x64_5.4.23.5.zip` · Linux: `…/BepInEx_linux_x64_5.4.23.5.zip` | `github-release` (asset regex `BepInEx_(win\|linux)_x64_.*\.zip`) | Enumerate `GET /repos/BepInEx/BepInEx/releases`, pick newest tag matching `^v5\.4\.`, take the OS-matched x64 asset. **Do NOT use `/releases/latest`** (can resolve a 6.x prerelease) | No upstream per-asset SHA → **vendored known-good SHA-256 in manifest + TOFU lockfile** | From `https://github.com/BepInEx/BepInEx/releases/tag/v5.4.23.5` download the OS-matched x64 zip. For Linux, the toolkit already vendors this pack at `NukeStats\bepinex_pack\`. **Confidence: high** |
| A4 | **NukeStats plugin** (`NukeStats.dll`) | Toolkit's own plugin GitHub release (`update.github_repo` in config — see Risk D4) | `github-release` (asset `NukeStats.dll`) | `installer/updater.py` `check()` already does `/releases/latest` + asset match | SHA-256 (`NukeStats.dll.sha256` asset) + **minisign** (`.minisig`, trusted.pub bundled) | Download `NukeStats.dll` (+ `.sha256`/`.minisig`) from the release; drop into offline folder. **Confidence: medium** (canonical repo slug unconfirmed) |
| A5 | **Doorstop activation** (NOT a download) | bundled inside A3 | `manual` / `post: merge_startup` | derived from placed BepInEx | n/a | Linux: set launch wrapper to the Doorstop block (§3). Windows: `winhttp.dll`+`doorstop_config.ini` sit next to the exe — auto-injects, no env vars. **Confidence: high** |

### Option B — external Linux / Pterodactyl

| # | File / dependency | Real upstream URL | Fetch method | Always-latest | Integrity | Offline manual download |
|---|---|---|---|---|---|---|
| B1 | **Pterodactyl egg JSON** | `https://raw.githubusercontent.com/pterodactyl/game-eggs/main/nuclear_option/egg-nuclear-option.json` (HTTP 200 verified) | `github-raw` | Raw URL on `main` always serves latest. Detect drift via `GET /repos/pterodactyl/game-eggs/commits?path=nuclear_option/egg-nuclear-option.json&per_page=1` and compare JSON `exported_at` (currently `2025-10-30`). `update_url` is null → no panel auto-update, installer owns refresh | Vendored SHA-256 + TOFU; warn if upstream commit is newer | Open the blob page, click Raw, save `egg-nuclear-option.json`; import via panel Nests → Import Egg, or point installer at the local path. Also save the commit SHA. **Confidence: high** |
| B2 | **Server binaries** | App `3930080` via SteamCMD — **provided by host**: the egg's own install script runs `+app_update ${SRCDS_APPID}` (=3930080, anonymous) on install and on each boot when `AUTO_UPDATE=1` | `steamcmd` (`provided_by_host`, installer skips fetch) | Egg auto-pulls latest server-side | SteamCMD `validate` | Truly offline panel install is impractical (container must reach Steam CDN). Offline-ish: run SteamCMD yourself, SFTP the folder into `/home/container`, set egg `AUTO_UPDATE=0`. **Confidence: high** |
| B3 | **BepInEx (Linux x64)** | `https://github.com/BepInEx/BepInEx/releases/download/v5.4.23.5/BepInEx_linux_x64_5.4.23.5.zip` — **OR** the bundled `NukeStats\bepinex_pack\` (already this exact pack) | `github-release` (online) / vendored (default) | Same `^v5\.4\.` filter as A3 | Vendored SHA + TOFU | Download `BepInEx_linux_x64_5.4.23.5.zip` from the tag page, SFTP `BepInEx/`, `libdoorstop.so`, `run_bepinex.sh`, `doorstop_config.ini` into container root. Or just use the bundled pack. **Confidence: high** |
| B4 | **Startup-command override (Doorstop)** | replaces egg's bare `export LD_LIBRARY_PATH=…; ./NuclearOptionServer.x86_64` | `manual` / `merge_startup` | n/a | post-install verify that Doorstop took effect (`BepInEx/LogOutput.log` / `NukeStats loaded`) | Paste the Doorstop block (§3) + toolkit relay flags into the panel's Startup field; set egg `MODDED_SERVER/ModdedServer=true`. **Confidence: high** |
| B5 | **NukeStats plugin** (`NukeStats.dll` → `BepInEx/plugins/`) | as A4 | `github-release` | as A4 | SHA-256 + minisign | as A4; SFTP into `BepInEx/plugins/`. **Confidence: medium** |
| B6 | **Toolkit relay + `-ServerRemoteCommands` wrapper** | ships inside the toolkit (`no_relay.*`, `run.bat`/launch wrapper) | bundled | toolkit self-update | bundled | Already in the toolkit download; installer injects into container + startup line. **Confidence: medium** (not part of egg/BepInEx — installer must own it; see Risk D7) |

### Option C — external Windows

| # | File / dependency | Real upstream URL | Fetch method | Always-latest | Integrity | Offline manual download |
|---|---|---|---|---|---|---|
| C1 | **SteamCMD bootstrap** | `http://media.steampowered.com/installer/steamcmd.zip` | `http-zip` | Evergreen, self-updating | TOFU-pin SHA-256 | Download from Valve URL; point installer at extracted folder. **Confidence: high** |
| C2 | **Server binaries** | App `3930080` via SteamCMD (as A2) — Windows depot → `NuclearOptionServer.exe` + `RunServer.bat` | `steamcmd` | Always-latest `app_update … validate` | SteamCMD `validate` | No GitHub mirror; run SteamCMD once on a connected machine, copy the game dir. **Confidence: high** |
| C3 | **BepInEx (Windows x64)** | `https://github.com/BepInEx/BepInEx/releases/download/v5.4.23.5/BepInEx_win_x64_5.4.23.5.zip` | `github-release` (asset `BepInEx_win_x64_*.zip`) | `^v5\.4\.` filter | Vendored SHA + TOFU. **Note: toolkit does NOT vendor the Windows pack — must fetch (online) or hand-download (offline)** | Download `BepInEx_win_x64_5.4.23.5.zip` from tag page; extract `BepInEx\`, `winhttp.dll`, `doorstop_config.ini` next to `NuclearOptionServer.exe`. **Confidence: high** |
| C4 | **Doorstop activation** | bundled in C3 | `manual` | n/a | post-install presence check | Windows = no env vars/wrapper; `winhttp.dll` auto-injects on normal launch. **Confidence: high** |
| C5 | **NukeStats plugin** | as A4 → `BepInEx\plugins\NukeStats.dll` | `github-release` | as A4 | SHA-256 + minisign | as A4. **Confidence: medium** |

**Alternative source for B3/C3/A3 (documented fallback, not primary):** Nuclear-Option Thunderstore **BepInExPack** — `https://thunderstore.io/c/nuclear-option/p/BepInEx/BepInExPack/`, API `GET https://thunderstore.io/api/experimental/package/BepInEx/BepInExPack/` → `latest.version_number` (5.4.2305) + `latest.download_url`. Advantage: only ever serves the vetted Mono 5.x build (can't accidentally grab IL2CPP). Keep GitHub as primary, Thunderstore as the GitHub-blocked fallback. **Confidence: high it exists; medium on implementing it as a live method.**

### Explicitly UNKNOWN / uncertain
- **A4/B5/C5 plugin repo slug** — `updater.py` reads `update.github_repo` from user config; the canonical public repo and whether its releases carry `NukeStats.dll`+`.sha256`+`.minisig` is **unconfirmed**. → *Default: keep config-driven; hardcode in manifest only once Tomo confirms the slug.*
- **B1 egg drift** — community-authored, `update_url` null. → *Default: pin a known-good vendored copy in the toolkit; poll upstream commit/`exported_at` and only warn, never auto-adopt HEAD (HEAD could break our startup override).*
- **Query-port divergence** — Shockfront guide says query UDP **7778**; community egg defaults `QUERY_PORT` to **7777** (=game port). → *Default: installer prompts; default query to 7778 and validate game≠query.*
- **macOS dedicated server** — no macOS depot believed to exist under 3930080 (SteamDB depots page was 403 to automated fetch). → *Default: tell Mac users to host via Linux/Windows/panel; do not offer a macOS server target.*

---

## 2. Auto-fetcher design

### 2.1 Manifest format — `installer/sources.json`

```jsonc
{
  "schema_version": 1,
  "manifest_version": "2026.06.28",          // self-updated via toolkit github-release
  "options": {
    "own_pc_windows":       ["steamcmd-bootstrap-win", "server-binaries", "bepinex-win", "nukestats-plugin"],
    "own_pc_linux":         ["steamcmd-bootstrap-linux", "server-binaries", "bepinex-linux", "nukestats-plugin"],
    "external_linux_ptero": ["ptero-egg", "server-binaries", "bepinex-linux", "nukestats-plugin", "toolkit-relay"],
    "external_windows":     ["steamcmd-bootstrap-win", "server-binaries", "bepinex-win", "nukestats-plugin"]
  },
  "dependencies": {
    "server-binaries": {
      "id": "server-binaries", "name": "Nuclear Option Dedicated Server",
      "role": "game", "required": true, "platform": "any", "arch": "x64",
      "fetch": { "method": "steamcmd", "appid": 3930080, "login": "anonymous", "validate": true },
      "provided_by_host": ["external_linux_ptero"],   // egg installs it server-side
      "dest": ".", "latest": { "how": "steamcmd_always_latest" },
      "integrity": { "type": "steamcmd_validate" },
      "offline": {
        "official_url": "https://steamdb.info/app/3930080/",
        "instructions": "Run `steamcmd +force_install_dir <dir> +login anonymous +app_update 3930080 validate +quit` on a connected machine; point installer at <dir>."
      }
    },
    "bepinex-linux": {
      "id": "bepinex-linux", "name": "BepInEx (Unity Mono, Linux x64)",
      "role": "modloader", "required": true, "platform": "linux", "arch": "x64",
      "fetch": {
        "method": "github-release", "repo": "BepInEx/BepInEx",
        "tag_filter": "^v5\\.4\\.", "asset_regex": "^BepInEx_linux_x64_.*\\.zip$"
      },
      "vendored": "NukeStats/bepinex_pack",          // offline-parity fallback
      "dest": ".",
      "latest": { "how": "github_releases_filtered" },
      "integrity": { "type": "sha256_vendored", "sha256": "<pin-on-bump>", "tofu": true },
      "post": ["extract", "chmod_exec:libdoorstop.so", "merge_startup:doorstop_linux"],
      "offline": {
        "filename": "BepInEx_linux_x64_*.zip",
        "official_url": "https://github.com/BepInEx/BepInEx/releases/tag/v5.4.23.5"
      }
    },
    "ptero-egg": {
      "id": "ptero-egg", "name": "Pterodactyl egg (Nuclear Option)",
      "role": "egg", "required": true, "platform": "any",
      "fetch": {
        "method": "github-raw", "repo": "pterodactyl/game-eggs",
        "branch": "main", "path": "nuclear_option/egg-nuclear-option.json"
      },
      "drift_check": { "commits_api": "...&path=nuclear_option/egg-nuclear-option.json&per_page=1", "compare_field": "exported_at" },
      "dest": "installer/cache/egg-nuclear-option.json",
      "integrity": { "type": "sha256_vendored", "tofu": true },
      "post": ["merge_startup:doorstop_linux", "set_var:MODDED_SERVER=true"],
      "offline": { "filename": "egg-nuclear-option.json",
        "official_url": "https://github.com/pterodactyl/game-eggs/blob/main/nuclear_option/egg-nuclear-option.json" }
    }
    // steamcmd-bootstrap-{win,linux} (http-zip), bepinex-win (github-release),
    // nukestats-plugin (github-release + minisig), toolkit-relay (bundled) — same shape
  }
}
```

Companion **`installer/sources.lock.json`** (TOFU): records `{id, resolved_version, sha256, source_url, installed_at}` for every artifact actually placed — reproducibility + tamper-evidence on re-runs.

### 2.2 Fetch engine — `installer/fetcher.py`

Generalises the GitHub+verify logic already in `updater.py` (`_get`, `_vt`, `_verify_minisig`, the pending/atomic staging) into a manifest-driven, multi-dependency fetcher.

```python
# installer/fetcher.py
def resolve_version(entry) -> str       # latest per method; lazy, never hardcoded
def fetch(entry, dest, *, offline=False, offline_dir=None) -> FetchResult
def _m_github_release(entry) -> (url, version)   # /releases (not /latest); tag_filter + asset_regex
def _m_github_raw(entry)     -> (url, version)   # raw.githubusercontent.com/<repo>/<branch>/<path>; HEAD=latest
def _m_steamcmd(entry, dest)                     # bootstrap-if-absent → app_update <appid> validate
def _m_http_zip(entry)       -> url              # fixed/templated URL
def _m_thunderstore(entry)   -> (url, version)   # experimental API latest.download_url
def _m_manual(entry)                             # presence-check only, print official_url
def _verify(path, entry) -> bool                 # sha256 (vendored | .sha256 asset | TOFU) + optional minisign
def _extract(zip_or_tar, dest)                   # path-traversal guard (reject ../), size cap
def _post(entry, dest)                           # chmod_exec, merge_startup, set_var
```

Rules baked in (per research):
- **github-release**: enumerate `/releases`, skip draft/prerelease, **filter by `tag_filter` then `asset_regex`** — this is what keeps BepInEx on the 5.4.x Mono line and rejects 6.x/IL2CPP.
- **steamcmd**: always-latest by definition; bootstrap SteamCMD via the `http-zip` sub-dependency if absent; `provided_by_host` options skip it.
- **Atomic install**: download+verify to temp, then `os.replace`/dir-swap into place — a partial fetch never corrupts a working install.
- **Extraction**: reject `../` entries; set exec bit on Linux server binary + `libdoorstop.so`; enforce a Content-Length/time cap; guard cross-host redirects.

### 2.3 Online/offline toggle — `installer/offline.py`

- **ONLINE**: `fetcher` resolves latest + downloads from upstream.
- **OFFLINE**: user points at a folder of pre-downloaded files. `validate_offline_folder(option)` iterates that option's required entries, matches each by `offline.filename` glob, checks SHA-256 where known, emits a **green/amber/red** report. For each missing/mismatched item it surfaces the exact `offline.official_url` + expected filename. The **same** extract/verify/place path runs afterward — only the byte-source differs.
- Offline trades auto-latest for trust: it shows upstream "latest" (resolved if any connectivity, else from the last manifest pin) **next to** the user's file version and warns on mismatch.
- UI affordances: per-dependency line `[name] → download from <url> → save as <filename> → drop in <folder>`, a **Re-validate** button, and a **Copy all URLs** button (for a phone/second machine). Validate-only never mutates the user's folder.

### 2.4 Autodetect + connectivity — `installer/detect.py` (extends `setup.py._preflight()`)

- **Scenario suggestion**: Win → read `HKCU/HKLM` Steam `SteamPath` + parse `steamapps/libraryfolders.vdf` (cross-drive locator) for app `3930080` / a `NuclearOptionServer.*`; Linux → scan `~/.steam`, `~/.local/share/Steam/steamapps`. Found locally → suggest `own_pc` + pre-fill `local_game_dir`. Pterodactyl URL/key already in config → suggest `external_linux_ptero`.
- **Arch/OS**: `platform` module → choose `linux_x64` vs `win_x64` assets.
- **Connectivity preflight**: HEAD `api.github.com` + Steam CDN + (if configured) the panel → decide whether ONLINE is possible and default the toggle. No internet → route to the offline checklist, **don't error**.
- Detection runs fully offline (registry/FS scans need no network). **Never auto-proceed** — present detection as overridable suggestions.

### Files to add to `installer/`
`sources.json`, `sources.lock.json` (generated), `fetcher.py`, `offline.py`, `detect.py`; extend `setup.py` to call `fetcher.fetch()` per chosen scenario and `_preflight()`→`detect`. **Reuse, don't duplicate, `updater.py`.**

---

## 3. Per-option end-to-end flow

### Option A — own PC (Windows or Linux)

**Online**
1. `detect.py`: find Steam/game, OS/arch, connectivity → suggest `own_pc_{os}`, online.
2. Wizard confirms option + install dir.
3. If no SteamCMD → `http-zip` bootstrap (A1), self-update on first run.
4. `steamcmd … +app_update 3930080 validate +quit` into the game dir (A2).
5. First run auto-generates `DedicatedServerConfig.json`; installer templates ServerName/Password/MaxPlayers/Port + query port (default 7778, ensure ≠ game port).
6. `github-release` BepInEx, OS-matched x64 asset, `^v5\.4\.` filter → extract contents into game root (A3).
7. `github-release` `NukeStats.dll` → `BepInEx/plugins/`; verify SHA-256 + minisign (A4).
8. **Doorstop activation** (A5): Linux → write launch wrapper with the Doorstop block (§3 block); Windows → `winhttp.dll`+`doorstop_config.ini` already in place, no wrapper.
9. Write `sources.lock.json`; start server; verify `NukeStats loaded` in log.

**Offline**
1. `detect.py` (offline OK) → suggest own_pc, offline.
2. Wizard renders the offline checklist (A1/A3/A4 official URLs + filenames; A2 = "run SteamCMD once on a connected machine, then point here").
3. User drops files (or a pre-built game dir) into the offline folder; `offline.py` validates filenames+SHA → green/amber/red.
4. On green, identical extract/verify/place (steps 6–8 above) run from local bytes.
5. Lockfile, start, verify.

### Option B — external Linux / Pterodactyl

**Online**
1. `detect.py`: panel URL/key in config → suggest `external_linux_ptero`; test SFTP + panel API (existing `setup.py` buttons).
2. `github-raw` fetch egg JSON (B1); compare `exported_at`/commit, warn on drift.
3. Import egg into panel (Nests → Import) or reference local; set vars: `SERVER_NAME`, `SERVER_PASSWORD`, `MAX_PLAYERS`, ports, `AUTO_UPDATE=1`, **`MODDED_SERVER=true`**.
4. Panel install runs SteamCMD `app_update 3930080` server-side (B2) — installer does **not** fetch binaries.
5. Provide BepInEx Linux x64 (B3): default = SFTP bundled `NukeStats\bepinex_pack\`; online alt = `github-release` then SFTP `BepInEx/`, `libdoorstop.so`, `run_bepinex.sh`, `doorstop_config.ini` into `/home/container`.
6. **Override Startup command** (B4) to the Doorstop block + toolkit relay flags — via panel API if the client key allows, else print the exact line for the user to paste (see Risk D6).
7. SFTP `NukeStats.dll` → `BepInEx/plugins/` (B5); SHA-256 + minisign.
8. Inject toolkit relay + `-ServerRemoteCommands` wrapper (B6) into container + startup line.
9. (Re)start via panel; verify `NukeStats loaded` / `BepInEx/LogOutput.log`; lockfile.

**Offline**
1. Detect (offline) → suggest ptero, offline.
2. Checklist: egg JSON (B1 URL+filename) + BepInEx Linux zip (B3). Binaries (B2) = host pulls via SteamCMD when the container boots (needs container internet — note honestly).
3. User hand-downloads egg + BepInEx; `offline.py` validates.
4. Import egg, SFTP BepInEx + plugin + relay, paste the Doorstop startup line, set `MODDED_SERVER=true`.
5. Start, verify, lockfile. (Fully air-gapped panel = not practical; documented as online-required for the binary step only.)

### Option C — external Windows

**Online**
1. `detect.py` on the Windows host → suggest `external_windows`, online; confirm install dir.
2. SteamCMD bootstrap if absent (C1).
3. `steamcmd … +app_update 3930080 validate` → Windows depot (`NuclearOptionServer.exe`+`RunServer.bat`) (C2).
4. Template `DedicatedServerConfig.json` (ports, name, password, query≠game).
5. `github-release` **`BepInEx_win_x64_*.zip`** (C3) → extract `BepInEx\`+`winhttp.dll`+`doorstop_config.ini` next to the exe. (Not vendored → must fetch.)
6. `NukeStats.dll` → `BepInEx\plugins\` (C5); verify SHA-256+minisign.
7. No wrapper needed — `winhttp.dll` auto-injects (C4). Start via `RunServer.bat`; verify; lockfile.

**Offline**
1. Detect (offline) → external_windows, offline.
2. Checklist: SteamCMD zip (C1), BepInEx_win_x64 zip (C3), `NukeStats.dll` (C5). C2 = run SteamCMD once on a connected machine / copy a game dir.
3. User drops files; `offline.py` validates SHA/filenames.
4. Identical extract/place (steps 5–7); start; verify; lockfile.

---

## 4. Decisions / risks for Tomo

Confidence is honest; each has a recommended default so the installer can ship without blocking on Tomo.

| ID | Question / uncertainty | Confidence | Recommended default |
|---|---|---|---|
| D1 | **Does Shockfront have a GitHub, and is it the binary source?** | **High — resolved.** `Shockfront-Studios/Nuclear-Option-Server-Tools` exists but ships **docs/tooling only, no Releases, no binaries.** | Encode "binaries = SteamCMD 3930080; Shockfront GitHub = reference docs only." Do **not** point any fetch at it. |
| D2 | **Is there a Pterodactyl egg, and where?** | **High — resolved.** Yes, in `pterodactyl/game-eggs` (community, redbananaofficial), **not** Shockfront, **not** parkervcp. Raw URL HTTP 200 verified. | Pull egg via `github-raw` from `main`; **vendor a known-good copy**, poll upstream commit/`exported_at` and only **warn** on drift (don't auto-adopt HEAD — it could break our startup override). |
| D3 | **BepInEx integrity** — no upstream per-asset SHA-256; egg has no checksum. | **High on the gap.** | **Vendor a known-good SHA-256 per pinned BepInEx version in `sources.json` + TOFU-pin into the lockfile.** Maintenance cost = update the SHA on each version bump. |
| D4 | **Canonical NukeStats plugin repo slug** + does the release carry `NukeStats.dll`/`.sha256`/`.minisig`? | **Medium — uncertain.** `updater.py` currently reads it from user config. | Keep config-driven for now; **Tomo confirms the public slug**, then hardcode in the manifest. Block plugin auto-fetch on a missing slug rather than guessing. |
| D5 | **Air-gapped SERVER install** — Steam content isn't a single downloadable zip. | **High — genuine limitation.** | Treat the binary step as **online-required**: "run `app_update 3930080 validate` once on a connected machine, then copy/point the game dir." Optionally ship a helper that snapshots a portable game dir. Don't try to mirror Steam (ToS). |
| D6 | **Can we rewrite the Pterodactyl Startup command via the CLIENT API** the toolkit already uses, or is it admin/egg-only? | **Medium — unconfirmed.** | Attempt API rewrite; on failure, **print the exact Doorstop startup line for the user to paste** into the panel. Don't hard-fail. |
| D7 | **Toolkit relay + `-ServerRemoteCommands` wrapper** on option B aren't in the egg or BepInEx. | **Medium.** | Installer **owns** injecting these into the container + startup line for option B. Fold into manifest entry `toolkit-relay` (bundled). |
| D8 | **Query-port divergence** — guide says 7778, egg defaults 7777 (=game). | **High on the divergence.** | Installer **prompts**; default query=7778; validate game≠query to avoid collision. |
| D9 | **macOS dedicated server** — depots page was 403 to automated fetch. | **Medium — likely none.** | Tell Mac users to host via Linux/Windows/panel; don't offer a macOS server target. Eyeball `steamdb.info/app/3930080/depots/` in a browser to confirm. |
| D10 | **Thunderstore as a live fallback vs documentation-only** for BepInEx. | **High it exists; medium on implementing.** | Ship **GitHub as primary**; implement Thunderstore as a real `thunderstore` method only as a GitHub-blocked fallback. Until then, list its manual URL in the offline checklist. |
| D11 | **Steam beta branch?** Guide shows optional `-beta/-betapassword`. | **Medium — default public is fine.** | Default to the public branch; expose `SRCDS_BETAID`/`SRCDS_BETAPASS` as optional advanced fields (egg already supports them). |

**Bottom line:** the four research outputs are mutually consistent and match the toolkit on disk. The only items that genuinely block a "flawless" install are **D4** (plugin repo slug) and **D6** (panel startup-rewrite capability) — both have safe printed-instruction fallbacks, so the installer can ship now and tighten later.