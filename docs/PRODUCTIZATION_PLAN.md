# Nuclear Option Server Toolkit — Productization Plan

## 1. Vision & Product Shape

**Vision.** Today the Nuclear Option community-server toolkit (a Python bot + Flask web command centre + a BepInEx stats/moderation plugin) runs exactly one deployment: the owner's Pterodactyl/Linux box, reached over SFTP + an in-container relay, with secrets hard-coded in `run.bat` and a single admin SteamID baked into source. The goal is to turn it into an **installable, UI-driven, auto-updating product** that any Nuclear Option dedicated-server admin — on their own PC, an external Linux host, or an external Windows host — can download from GitHub, set up through a guided UI, customise, and keep current.

**What the user downloads, opens, and experiences end-to-end:**

1. **Download one thing.** From the GitHub repo's README, the admin downloads a small installer/launcher binary (`Toolkit-Setup.exe` / `setup` / `setup` for their admin OS, ~30–60 MB, bundled Python — no prerequisites, no .NET SDK).
2. **Open it → a UI appears.** Double-clicking starts a localhost-only Flask app and auto-opens a browser tab to a clean single-page **setup wizard** (the same Flask + single-page-JS stack that already powers `webcc.html`, so it's proven code, not a new toolkit).
3. **One question that matters.** The wizard asks *"Where does your game server run?"* — **This PC / External Linux / External Windows** — and shows only the fields that answer needs.
4. **Connect + secure.** It collects connection details and secrets into gitignored files outside the repo (replacing `run.bat` creds, `apiKey.txt`, `panel.txt`), tests the connection live, and asks for the one universally-required field: the admin's **own SteamID**.
5. **Pick features simply.** The admin picks one of four **presets** (Full Community Server / PvP-Competitive / PvE Co-op / Minimal Stats) or clicks **Advanced** for the full ~40-toggle grid. Safety features (flood guard, teamkill, AI limiter, profanity) default ON.
6. **Validate + launch.** A live green/amber/red **health check** (RemoteCommand round-trip, console-log read, plugin-alive probe, power-control ping) confirms the setup works, then a single **"Start the server tools"** button spawns the bot + web CC and redirects the browser to the live command centre.
7. **Stay current.** Re-running the launcher later lands on a small dashboard (Start/Stop, Edit settings, **Check for updates**). Updates are **opt-in** with release notes shown — never a silent restart of a live match.

The default path is deliberately short: **scenario + connection + SteamID + preset = done.**

---

## 2. Resolved Architecture (where the lenses are consolidated)

The six lenses largely reinforce each other; the real decisions are at their seams. Here is the single recommended architecture, with conflicts resolved.

### 2.1 UI delivery — **LOCAL-WEB wizard, frozen launcher** (decided)
All lenses that touched UI converged on a localhost-Flask SPA packaged as a PyInstaller one-file launcher. **Adopted as-is.** Rejected: Electron/Tauri/MAUI (second toolchain, triples build/sign burden, MAUI reintroduces the .NET dependency we're removing), a TUI (alienates the non-technical half of the audience), and a bare batch wizard (Windows-only, can't validate live or show a feature grid). The launcher is **also the auto-updater entrypoint** — one verified-download core serves both "install" and "update."

### 2.2 Config model — **one TOML, typed schema, separate secrets, derived targets** (decided)
The config lens and installer lens agreed on a single source of truth; this resolves them:

- **One `config.toml`** in a per-user data dir (`platformdirs`: `%APPDATA%/NukeOptionToolkit/`, `~/.config/nukeoption/`), **never** in the git working tree. TOML over YAML (no whitespace footguns, native comments preserve inline help) and over JSON (no comments, hostile to hand edits).
- **Typed schema layer (Pydantic v2)** owns defaults, validation, ranges/enums, and — critically — **exports `model_json_schema()` that drives the installer's auto-generated forms and tooltips**. Help text is lifted verbatim from the existing C# `Config.Bind` description strings and the bot's constant comments, so docs and UI never drift.
- **Three runtime targets are DERIVED, not hand-edited:** `render_plugin_cfg()` emits `anz.nukestats.cfg`; the bot/web-CC read one loaded `CONFIG` object instead of module-level constants; the launcher injects secrets into env at process start (replacing `run.bat` literals).
- **`config_version` + ordered migrations** make updates safe; new keys get schema defaults automatically (most updates need no migration code).

### 2.3 Secrets — **keyring primary, 0600 file fallback** (decided)
Secrets live **outside** `config.toml` and the repo. Tier 1: OS keychain via `keyring` (Windows Credential Manager / libsecret / macOS Keychain). Tier 2 (headless Linux with no keyring backend): a `0600` `secrets.toml` in the per-user data dir. `config.toml` stores only a *reference* that a secret is set, never the value. A one-time `import-legacy-secrets` step migrates the current `apiKey.txt`/`panel.txt`/`run.bat` password into the store and tells the user to delete the plaintext files **and rotate both credentials** (they're already in plaintext on disk and in the design notes).

### 2.4 Server transport — **provider composition, not monolithic backends** (decided)
This is the deepest item and the hard dependency every other area is blocked on. Adopt the cross-platform lens's recommendation: a **`server_backend/` package with three independent Provider protocols** — `FileTransport`, `CommandTransport`, `PowerController` — plus a `Backend` facade and a provider-independent `liveness()` probe, all selected by a single config block.

**Why composition over one class per scenario:** real setups mix-and-match (SSH-files + Pterodactyl-power, SSH-files + systemd-power, SSH-files + *manual* power are all common). Composition covers the matrix without a combinatorial class explosion and makes **graceful degradation first-class** — `PowerController` can be a no-op `ManualPowerController` while files and command remain fully functional. The toolkit keeps full gameplay function with **zero** power control (it just warns the admin to restart by hand).

**One reconciliation between lenses:** the installer lens and config lens both proposed a transport config block; the cross-platform lens proposed `backend.json`. **Resolution: there is no separate `backend.json`.** The transport lives as the `[server]` discriminated-union section *inside* `config.toml` (`mode = local | ssh_pterodactyl | ssh_systemd | ssh_windows | manual`), so there is genuinely **one** config file. The `server_backend` factory consumes `CONFIG.server`; the schema lens already owns that section.

**Command-delivery default for external hosts:** prefer the **SSH-tunnel** transport (paramiko `direct-tcpip` to `127.0.0.1:5504` over the same SSH session used for files — already proven by the existing `--testtunnel` code). This **retires the relay + wrapper-install requirement** for any SSH-reachable host. The relay stays only as the **Pterodactyl/no-SSH fallback** (many eggs expose no SSH). Own-PC uses a **direct** TCP connect to localhost; no relay, no SSH.

### 2.5 Plugin modularization — **single DLL + manifest-driven cards + presets** (decided)
The plugin lens and config lens agreed: ship the **existing single DLL unchanged**, build feature selection as a **config generator** over the existing `Config.Bind` toggles, driven by a shared **`plugin_features.json` manifest** (id, label, blurb, section/key/default/type, range, safety flag, category, `requires`/`conflicts` edges). The **same manifest renders both the installer's Advanced grid and a future web-CC "Plugin features" tab.** Reject true per-feature DLLs for v1 (XL effort, multiplies build/version/deploy/patch-ordering complexity, near-zero gain — a disabled toggle already runs no code). One **required small plugin change**: add a `[Stats]` master enable + per-feed toggles so telemetry is genuinely selectable and the dependency edges are real (all flags default ON → existing servers byte-behaviour-identical). Compile-time `#if` variants stay an optional later path only if a concrete minimal-surface need is voiced.

### 2.6 Distribution & updates — **GitHub Releases, opt-in pull updater, reuse the deploy pipeline** (decided)
Both distribution lenses agreed: **versioned GitHub Releases** (not pipx, not a monolithic exe, not bare `git clone`) carrying three assets per release — `toolkit-vX.Y.Z.zip` (app bundle), `NukeStats.dll` (prebuilt), `manifest.json`/`latest.json` (version + per-file SHA-256). A **clean-room scrub-first** fresh repo (no history import — the folder has no `.git`, so commit 1 is born clean). **Updates are strictly opt-in** with release notes shown (this tool power-cycles a live match). The updater's two halves are cleanly split: **local app-file replacement** (new, simple, fully reversible from a backup) vs **server-side DLL redeploy** (delegate 100% to the existing `deploy_plugin_job` — atomic SFTP swap + relay-verify + force-START rollback). The prebuilt DLL is built by CI on a **self-hosted runner** the owner controls, because the build legally requires the non-redistributable `Assembly-CSharp.dll`/Unity/Mirage assemblies that can never enter the repo.

**Channels:** stable (default) + opt-in beta/prerelease. **Integrity for v1:** SHA-256-in-manifest over HTTPS+GitHub; detached signatures (minisign/GPG) deferred to a hardening pass (flagged below as a real risk, since the DLL runs arbitrary code in the game server).

---

## 3. Phased Roadmap

Effort key: **S** ≈ 1–2 days · **M** ≈ 3–5 days · **L** ≈ 1–2 weeks · **XL** ≈ 3–4 weeks. Sizes are for one focused developer and assume reuse of existing code wherever noted.

### Phase 0 — The Unblock (repo + secret scrub + prebuilt-DLL CI)
**Goal:** a clean public repo that can never leak secrets/PII, and a release pipeline that produces the prebuilt DLL so end users never need the .NET SDK. Nothing user-facing yet; this is what makes everything else legal and safe to ship.

| Deliverable | Components | Effort |
|---|---|---|
| Clean-room staging copy (whitelist only: plugin source, bot, cc_web, webcc.html, map_atlas, map-build, relay helpers, docs) — **never** `cp -r` the working folder | repo | S |
| `.gitignore` written **before** first `git add`; enumerate-then-un-ignore-examples pattern | repo | S |
| Secret/PII scanner gate: `gitleaks --no-git` + SteamID64 regex `7656119[0-9]{10}` + literal sweep (password/host/key strings) — **must report 0 before commit** | CI + pre-commit hook | S |
| Replace every secret/PII file with a `.example` template loaded at runtime | repo, bot, cc_web | M |
| Monorepo layout (`/plugin /admin /admin/relay /installer /map-build /config /docs /.github`), LICENSE (GPL-3.0-or-later), README skeleton, SECURITY.md, issue/PR templates with "don't paste creds/SteamIDs" warnings | repo | M |
| Self-hosted-runner CI: `build-plugin.yml` restores game DLLs from a runner-local path (never the repo) → `dotnet build` → upload `NukeStats.dll` artifact; `release.yml` hard-depends on it | CI | L |
| **Rotate** the exposed SFTP password + Pterodactyl key | ops | S |

**Phase 0 total ≈ L (1.5–2 weeks).** This is the gate; do not start Phase 1 until the scanner passes and the DLL builds in CI.

### Phase 1 — MVP (works for TODAY's setup, but config-driven + setup UI + auto-update)
**Goal:** the current Pterodactyl/Linux owner can install from a Release, set up through the wizard, and auto-update — with the codebase now cleanly config-driven so Phase 2 transports drop in.

| Deliverable | Components | Effort |
|---|---|---|
| Pydantic v2 schema (all ~75 fields, defaults = today's literals, help text lifted from `Config.Bind`/comments), `model_json_schema()` export | new `noconfig.py` + schema | L |
| CI check: schema defaults vs C# `Config.Bind` literals must match | CI | S |
| Refactor bot + cc_web off module-level constants onto `CONFIG` (thin aliases like `RCMD_HOST = CONFIG.server.rcmd_host` to preserve call sites + `bot.RCMD_HOST` import coupling) | bot, cc_web | L |
| `server_backend/` package: protocols + facade; reproduce **today's Pterodactyl/SFTP/relay path byte-for-byte** as the first provider combo (proves the abstraction) | new package, bot, cc_web | L |
| Secrets store (`keyring` + 0600 fallback) + `import-legacy-secrets` migration | new module | M |
| `render_plugin_cfg()` (whole-file, merge-preserving unknown keys) reusing the existing atomic SFTP push | bot | M |
| `plugin_features.json` manifest + the **`[Stats]` master-enable plugin change** (bump 0.9.6, fix stale Awake banner) | plugin, repo | M |
| Setup wizard SPA: preflight → scenario (Linux preset only this phase) → connection + secrets (masked, Test button) → SteamID → preset picker → Apply → **health check** → launch hand-off | installer | L |
| Frozen launcher (PyInstaller one-file, bundled Python) — install + update entrypoint | installer/packaging | M |
| `updater.py`: opt-in check against `latest.json`, release-notes modal, SHA-256-verified download, manifest-allowlist file swap with PRESERVE list + pre-update backup + rollback; DLL staged via existing `deploy_plugin_job` | updater, bot | L |

**Phase 1 total ≈ XL (3–4 weeks).** Biggest single line is the constant→`CONFIG` refactor across the 4750-line bot (mechanical but wide — move one section per commit, diff behaviour).

### Phase 2 — Cross-platform transports + module selection + presets
**Goal:** the two new scenarios actually work, and feature customisation is full.

| Deliverable | Components | Effort |
|---|---|---|
| **LocalBackend** (own PC): local-file console source, direct `127.0.0.1:5504` command, `LocalProcessPower` (spawn/track the Unity server) or attach-degrades-to-Manual | server_backend, bot | M |
| **SSHTunnelCommand** transport (retire relay for SSH hosts) + key-auth (`keyfile`/passphrase) for `SSHFileTransport` | server_backend | M |
| **External Windows**: SSH/SFTP files (OpenSSH-for-Windows), SSH-tunnel command, `WindowsServicePower` (`sc`/`schtasks`) or Manual | server_backend | M |
| `systemdPower` provider for service-style Linux hosts | server_backend | S |
| `ManualPowerController` + capability checks wired through deploy/schedule/changemap (warn-and-ask, hide power buttons in web CC) | bot, cc_web | M |
| Wizard: all three scenarios live, per-scenario fields + live capability probe, auto-detect `game_root` (with file-picker fallback) | installer | M |
| Advanced feature grid (full ~40 toggles, manifest-rendered, dependency/safety enforcement) + 4 presets as sparse overlays | installer, repo | M |
| Cross-platform process spawn (subprocess, not `.bat`) + OS-appropriate autostart units | installer | M |

**Phase 2 total ≈ L–XL (2.5–3 weeks).** The new power providers (systemd/Windows) are net-new code with no in-repo reference — keep them optional, default unknowns to Manual.

### Phase 3 — Polish
**Goal:** trustworthy distribution + the optional "real modularization" only if demand exists.

| Deliverable | Components | Effort |
|---|---|---|
| Code-signing / notarization (Windows SmartScreen, macOS Gatekeeper) for the launcher | packaging | M (+ cert cost) |
| Detached signatures (minisign/GPG) on release manifests | CI, updater | M |
| Beta channel hardening, scheduled-window apply via the existing Schedule modal | updater, cc_web | S |
| Web-CC "Plugin features" tab (same manifest renderer, SFTP cfg write + guarded restart) | cc_web | M |
| **Optional** compile-time `#if` DLL variants (Full / Stats-only / Safety-only) — *only if a concrete minimal-surface need is voiced* | plugin, CI | L |
| Stub/facade reference assemblies so a hosted runner can build the DLL (removes self-hosted-runner single point of failure) — optional | CI | L |

**Phase 3 total ≈ L, mostly optional.** Do not build the split-DLL path (XL, high risk, near-zero gain).

---

## 4. Area Sections

### 4.1 Installer / Setup UX
**Chosen approach.** A frozen single-file launcher per admin OS that starts a localhost-only Flask app (random per-run URL token, `127.0.0.1` bind), auto-opens the browser, and serves a single-page setup SPA built on the existing `webcc.html` stack. Linear stepper with a persistent **Advanced** toggle; progress saved per step so a half-finished setup resumes. Re-running detects a valid config and lands on a small Start/Stop/Edit/Update dashboard.

**Key design details.** `/api/preflight` reports bundled-Python OK, paramiko presence (SSH scenarios only), free ports, OS/arch, and own-PC game-folder/BepInEx detection. The **.NET SDK is never a prerequisite** (DLL ships prebuilt). The wizard writes the BepInEx cfg + `config.toml` + secrets, runs scenario-appropriate health probes (reusing the battle-tested `--testconn` / `--testchat` / `[NOSTATS]`-grep CLIs), and refuses to advertise success unless the critical probes (command round-trip + log read) pass.

**Risks.** PyInstaller one-file startup is slow (2–5 s) and unsigned binaries trip SmartScreen/Gatekeeper (mitigate: splash + documented "Run anyway"; sign in Phase 3). BepInEx install on the **game** server is the genuinely hard, host-specific step — fully automatable only for SFTP-reachable hosts (reuse `--setup-server`/`--upload-bepinex`); for own-PC/Windows it's **guided-manual** with a "verify plugin loaded" `[NOSTATS]` probe. Be honest about that.

### 4.2 Module / Feature Selection
**Chosen approach.** Single DLL + ~13 friendly **feature cards** (master toggle + collapsed "Customise" tunables) in 4 categories (Core telemetry, Economy & progression, Moderation & safety, Match management), driven by 4 presets, all rendered from `plugin_features.json`. The UI **enforces dependency edges** (grey out skill-balance unless skill + bot present; telemetry is the root — disabling it warns the live map + economy go dark; chat display is a **3-way radio** RankInName/Reformat/Off reflecting the code precedence, not two checkboxes). Safety cards default ON behind a friction confirm.

**Key design details.** The one required plugin change is the `[Stats]` master + per-feed flags (`EmitScore`/`EmitLiveMap`/`EmitKillFeed`/`EmitSkillEvents`), all defaulting ON, wrapping the centralised emit sites — making "Minimal Stats" and "no live map" real rather than cosmetic. A release-time validation parses the `Config.Bind` calls and asserts every key has a manifest entry (kills drift).

**Risks.** Manifest↔plugin drift (mitigated by the CI check). Features that span plugin+bot+webcc (live map) must fan a single toggle out to several effects — model each toggle as a set of derived effects, document the fan-out.

### 4.3 Config & Customisation
**Chosen approach.** One `config.toml` (per-user data dir) → Pydantic v2 schema → three generators (plugin cfg, bot/web `CONFIG`, launcher env). Secrets separate (keyring + 0600 fallback). Presets are **sparse overlays** (defaults < preset < user edits). `config_version` + ordered migrations.

**Key design details.** Sections mirror `anz.nukestats.cfg` 1:1 so `render_plugin_cfg()` is a mechanical render. The bot keeps thin module-level aliases sourced from `CONFIG` so the hundreds of existing references and `cc_web`'s in-process `import` keep working with zero call-site churn. `render_plugin_cfg()` is **merge-preserving** (keeps unknown keys a newer plugin added → forward-compat). Runtime data (ranks/ledgers/logs) and config both move to the data dir so updates never clobber them.

**Risks.** `tomlkit` round-trip can reorder keys on malformed input → always validate through the schema and re-emit canonical output, never blind-append. Schema-vs-C#-default drift silently changes behaviour → the CI diff check is mandatory. Some cfg keys apply only at plugin `Awake` (need a restart) vs the few that are file-watched live — the UI must label which.

### 4.4 Cross-Platform Transport
**Chosen approach.** `server_backend/` with `FileTransport` / `CommandTransport` / `PowerController` protocols + `Backend` facade + provider-independent `liveness()`, composed per scenario, selected by `CONFIG.server`. LocalBackend ships first as the reference impl. SSH-tunnel command delivery is the external default; relay is the Pterodactyl/no-SSH fallback. Power degrades gracefully to `ManualPowerController` with warn-and-ask.

**Key design details.** Method signatures map 1:1 onto today's helpers (`CommandTransport.send` = `RemoteCommand.send` drop-in). Atomic-write semantics (tmp+rename / posix_rename / os.replace) are encapsulated **inside** `write_bytes`/`put_file`, never left to callers. After migration, **delete** the old global constants so any missed call site is an immediate `NameError`, not a silent fallback to the old host. `deploy_plugin_job` becomes `backend.files.put_file(...)` + `backend.power.signal(...)` + `backend.liveness()` — orchestration unchanged, verbs provider-dispatched. The safety invariant (force-START on any failure) is re-expressed per provider (no-op warn for Manual; force-start unit/process for systemd/local).

**Risks.** This is **not purely UI** — a beautiful wizard over an SFTP-only bot silently fails own-PC and Windows. The SSH path that reproduces today's working setup needs the most careful regression testing. systemd/Windows-service power are net-new (default unknowns to Manual). Process lifecycle on own-PC: if the admin launched the game themselves, power degrades to Manual (attach by PID file / port-probe).

### 4.5 Auto-Update / Distribution
**Chosen approach.** GitHub Releases (3 assets: app zip, prebuilt DLL, manifest). Tiny self-updating launcher = install + update in one code path. Opt-in only: `updater.check()` notifies (header chip + optional once-daily background *notify-only* check), user views release notes, picks **Apply now** or **Schedule** (feeds the existing `schedule.json` so it runs in the 5 am window with player warnings).

**Key design details.** Apply runs **out of process** (detached `updater.py --apply <tag>`, like `deploy.bat` today). It downloads + SHA-256-verifies every file against the manifest, stops the local bot/web-CC, **backs up** the PRESERVE allowlist, replaces **only** files listed in `manifest.files[]` (opt-IN replacement — never a blind tree copy), runs an optional `migrate.py`, restarts. The DLL is staged as `pending_plugin.dll` + sidecar and handed to the **unchanged** `deploy_plugin_job`. App-only updates = no game downtime; plugin updates = a player-facing server bounce (the modal must say which, derivable from whether `dll_sha256` differs). Rollback: app-scope restores from `_update_backup/`; plugin-scope rides the existing force-START guardrail + archived `*.deployed-*` DLLs.

**Risks.** The **PRESERVE allowlist is the highest-stakes surface** — a bug that overwrites `ranks.json` wipes the player economy. Mitigations: replace strictly by manifest allowlist, mandatory pre-update backup, a `--dry-run` shown in the modal, refuse to apply if backup fails, atomic-write each replaced file. Integrity is HTTPS+SHA-256 for v1; **detached signatures deferred** is a real risk because the DLL runs arbitrary code in the game server — call it out and prioritise in Phase 3. GitHub API rate limits (60/hr unauth) are fine for opt-in + once-daily.

### 4.6 GitHub Rollout & Ops
**Chosen approach.** Scrub-first **fresh** repo (no history import — strictly safer than import-then-scrub, and there's no `.git` to preserve). One public monorepo `nuclear-option-server-toolkit`, **GPL-3.0-or-later** (BepInEx/Thunderstore ecosystem fit, keeps community forks open). SemVer for the whole package, decoupled from the plugin's internal `BepInPlugin` version (recorded in release notes + `latest.json`). Start **private**, run CI scanners, then flip public.

**Key design details.** LICENSE + README must **explicitly exclude** the proprietary game assemblies (`Assembly-CSharp.dll`, Unity, Mirage) — users obtain them from their own legally-owned install; CI builds against a runner-local copy, never the repo. Issue/PR templates carry bold "do NOT paste apiKey/panel/SFTP creds or player SteamIDs" warnings. BepInEx pack is **linked/downloaded from Thunderstore at setup, not vendored**. `latest.json` schema = `{package_version, plugin_version, dll_url, dll_sha256, bundle_url, bundle_sha256, min_supported_version, notes_url, breaking}`.

**Risks.** Missing one PII/secret file in commit 1 = permanent public leak → the deny-by-default staging whitelist + mandatory scanner gate are the guarantee (gitleaks alone may miss a bare SFTP password; the **custom SteamID64 + literal regexes are the real net**). Self-hosted runner = owner's PC must be online for releases (acceptable for hobby cadence; stub-assembly path in Phase 3 removes it). Rotate the exposed credentials regardless of scrub success.

---

## 5. Decisions for Tomo

Consolidated from all six lenses' open questions. Each has a recommendation — defaults chosen to keep the common path simple.

1. **Bundle Python in the launcher, or require system Python?** → **Bundle** (PyInstaller, ~30–60 MB). Honours "no prerequisites / keep it simple"; accept the larger artifact.
2. **Secret storage default?** → **OS keyring primary + 0600 file fallback** for headless Linux. Confirm you're OK with the `keyring` dependency (it's pure-Python, ships in the frozen artifact).
3. **Own-PC power: launch the game (full control) or attach (Manual)?** → **Offer both; default to "I'll start it myself" (attach/Manual)** for the simplest first run, with a one-click "let the toolkit launch it" option.
4. **External command delivery default?** → **SSH-tunnel** for any SSH-reachable host (retire the relay/wrapper); keep the **relay only as the Pterodactyl/no-SSH fallback**. Makes paramiko a hard dep for external setups (already is, for SFTP).
5. **SSH auth?** → **Support both password and private-key (+passphrase)**; keys are the norm for serious external Linux admins.
6. **Plugin cfg ownership: authoritative overwrite or surgical edits?** → **Authoritative but merge-preserving** (toolkit owns config, keeps unknown keys). Manual server-side cfg edits get reverted on next Apply — document this.
7. **Telemetry allowed fully OFF (plugin-standalone, no bot)?** → **Keep telemetry effectively mandatory** when the bot/web-CC are selected; offer a "Minimal Stats" preset rather than a true bot-less profile.
8. **Group the 4 PvP-movement features into one card?** → **Yes, one "PvP match management" card** with sub-toggles (they share the `EnforceBalance` gate + move machinery).
9. **Expose safety tunables (FleetOrdersPerSec, AI caps, StuckSeconds) on cards?** → **Lock behind Advanced** so non-technical owners can't accidentally weaken the flood guard.
10. **Ship `AdminSteamIds` blank?** → **Yes, blank + a "detect my SteamID" helper** in the wizard to avoid lockout. Never ship the current hard-coded ID.
11. **Mission pools / 11-tier RANKS ladder: user-editable config or fixed?** → **Expose both as config** (other owners want their own ladder/missions), defaulting to current values; gate the nested editors behind Advanced so the simple path never sees them.
12. **Relocate runtime data (ranks/ledgers/logs) to the data dir now, or defer?** → **Relocate now.** It's the clean answer for updates/secrets; do it during the constant→`CONFIG` refactor so paths change once.
13. **Licence?** → **GPL-3.0-or-later** (ecosystem fit). MIT only if you prioritise maximal closed-fork adoption.
14. **Update integrity for v1?** → **SHA-256-over-HTTPS now; add detached signatures (minisign/GPG) in Phase 3.** The DLL runs arbitrary code in your server, so don't skip Phase 3 signing.
15. **Auto-check cadence?** → **Notify-only once-daily background check + strictly opt-in apply.** Never silent.
16. **DLL build infra?** → **Self-hosted runner on your PC for v1** (or local build + `gh release upload`); stub/facade assemblies in Phase 3 to remove the single point of failure.
17. **Rotate the exposed SFTP password + Pterodactyl key?** → **Yes, immediately**, regardless of scrub success — they're already in plaintext locally and in the design notes.
18. **Repo start private then flip public?** → **Yes** — push, run CI scanners on the pushed tree, then make public.

---

## 6. First Steps This Week

A concrete, ordered checklist. Steps 1–7 are the irreversible-if-wrong security work — do them first, do them carefully.

1. **Rotate credentials now.** Change the SFTP password (`<YOUR_SFTP_HOST>` / user `<YOUR_SFTP_USER>...`) and regenerate the Pterodactyl client key. They're in plaintext on disk and in these notes; treat them as compromised.
2. **Create an empty staging directory** outside the working folder. Do **not** `git init` in the existing folder.
3. **Copy only whitelisted source/templates** into staging by name (deny-by-default): `NukeStatsPlugin.cs` + `.csproj` + `build.bat`, `no_mapvote_bot.py`, `cc_web.py`, `command_centre.py`, `webcc.html`, `map_atlas.py`, `map-build/`, the relay helpers, `docs/`. **Never** `cp -r` the working folder. Exclude `NukeStats/libs/`, `archive/`, `_server_backup/`, all logs/ledgers/ranks.
4. **Write `.gitignore` first** (enumerate secret/PII/data files; `plugin/libs/`, `*.dll`, `bin/`/`obj/`; add `!*.example` un-ignores).
5. **Replace each secret/PII file with a `.example` template** (`run.bat.example`, `apiKey.txt.example`, `panel.txt.example`, `anz.nukestats.cfg.example`, `ranks.example.json` with 1–2 fake entries).
6. **Run the scanner gate against staging** before any commit: `gitleaks detect --no-git --source <staging>`, plus `grep -rIlE '7656119[0-9]{10}'` and a literal sweep for the password/host/key/SteamID strings. **All must return zero.**
7. **`git init && git add -A && git commit`** only after step 6 is clean — history is born clean. Create the repo **private**, push, run a gitleaks GitHub Action + the SteamID regex on the pushed tree, add `.pre-commit-config.yaml`.
8. **Stand up the monorepo skeleton** (`/plugin /admin /admin/relay /installer /map-build /config /docs /.github`), add `LICENSE` (GPL-3.0-or-later) with the explicit game-DLL exclusion notice, `README.md`, `SECURITY.md`, issue/PR templates.
9. **Register the self-hosted runner** on your PC; put the game/Unity/Mirage DLLs in a runner-local path (e.g. `C:/no-toolkit-libs/`); wire `build-plugin.yml` to restore them → `dotnet build` → upload `NukeStats.dll`. Confirm the DLL builds in CI.
10. **Cut a `v0.1.0` private pre-release** with the prebuilt DLL + app zip + a hand-written `manifest.json` to validate the asset/release contract end-to-end.
11. **Only then start Phase 1 code** — begin the constant→`CONFIG` refactor one section per commit, and stub the `server_backend/` package reproducing today's Pterodactyl path first.

Once steps 1–10 pass and you've confirmed zero scanner hits on the pushed private repo, you can safely flip the repo public and begin building the wizard against the clean foundation.

---

# 7. Plan Deltas — Owner New Requirements (A–F)

> This section EXTENDS §1–6 with six new owner requirements. Where a requirement strengthens or overrides an earlier decision, that is called out as a **Δ (delta)**. The original §3 phases still apply; §7.7 gives the updated phase mapping and §7.8 the newly-resolved open questions.

The throughline of all six: the product must be **installable by a stranger with no game server, no secrets, and no internet at install time, on Windows OR macOS, with the plugin DLL trustworthy out of the box.** That reframes the audience from "an admin who already has a Pterodactyl server + creds" (the §1 default) to "anyone who owns the game and wants a community server." The §1 wizard grows a *zeroth* branch — "I don't have a server yet" — and the package contract tightens to ship zero secrets and run fully offline.

## 7.A FRESH-SERVER ONBOARDING — "I don't have a server yet"

**Problem.** §1's wizard opens with *"Where does your game server run? — This PC / External Linux / External Windows"*. That assumes a running dedicated server already exists and the admin already holds SFTP creds + a Pterodactyl key. A brand-new user has none of that. We add a **fourth top-level branch, "I don't have a server yet,"** that forks into two guided paths, then rejoins the existing flow once a reachable server + creds exist.

### 7.A.1 The branch and its two sub-paths

```
Where does your game server run?
  ┌─ This PC                    ┐
  ├─ External Linux  (existing) ┤→ collect creds → test → SteamID → preset → launch   (§1 path, unchanged)
  ├─ External Windows           ┘
  └─ I don't have a server yet  → 7.A.2 (Pterodactyl path)  OR  7.A.3 (own-PC path)
```

- **7.A.2 — Managed (Pterodactyl) path.** For users who want an always-on hosted server. The wizard does NOT create the Pterodactyl account or provision the node (that is the host's billing/infra domain — not automatable from a localhost app, and varies per provider). Instead it is a **guided checklist + deep-links + live validators**: it walks the user through choosing a host, creating the server, generating a CLIENT API key, and locating SFTP creds, validating each artifact the moment it's pasted in. This converts a 12-step scavenger hunt into a checklist where every box turns green only when the real thing works.
- **7.A.3 — Own-PC path.** For users who want to self-host on the admin PC itself (no third party). The wizard automates the most of anything because everything is local: detect/locate the Steam install of the *Nuclear Option* **Dedicated Server** tool, lay down BepInEx + the signed DLL + generated cfg, write a `DedicatedServerConfig.json`, and spawn the server as a child process. This is the LocalBackend from §2.4 / §4.4, surfaced as a first-run option rather than an "attach to my already-running server" option.

### 7.A.2 Pterodactyl guided path — automate vs instruct (the honest split)

| Step | Wizard does | User must do (instructed, with deep-link + paste-back validation) |
|---|---|---|
| 1. Pick a host | Shows a short curated list of known *Nuclear Option*/Pterodactyl game hosts + "my own Pterodactyl panel" + a "what is Pterodactyl?" explainer. Cannot rank/recommend by price (out of scope, changes constantly). | Choose + sign up + pay on the host's site. **Not automatable** (billing, captcha, ToS). |
| 2. Create the NO dedicated server | Explains the egg/"game" to pick (Nuclear Option dedicated, or a generic SteamCMD egg + the NO app-id as a documented fallback) and the minimum specs. | Click "Create server" in the panel. **Not automatable** (panel-account-scoped; no client-API endpoint to create servers — creation is an *application* API action the user's host controls, not the client key). |
| 3. Panel URL | — | Paste the panel base URL. Wizard normalizes it (the existing `panel.txt` two-line URL+id form) and probes it. |
| 4. **Generate a CLIENT API key** | **Deep-links** straight to `<panel>/account/api` (the Pterodactyl client-area API page), shows an annotated screenshot/GIF of the "Create API Key" dialog, names the key (`nuke-option-toolkit`), and explains the *allowed-IPs* field (leave blank or add the admin's IP). | Click "Create", copy the one-time `ptlc_…` token. |
| 5. Validate the key + resolve server id | **Automatable + automated.** On paste, the wizard calls `GET /api/client` (list servers) with the key (reusing the bot's Cloudflare-aware `_pt_api`), shows the returned server name(s), and lets the user pick — auto-filling the server **identifier** into `panel.txt` so the user never has to hunt for the id. Green check = key works AND has access to that server. | Pick the right server from the returned list. |
| 6. SFTP host/port/user/password | **Automatable + automated.** Pterodactyl exposes SFTP details on the server's **Settings** page in a fixed shape: host `sftp://<node-fqdn>:2022`, **user `<panel-username>.<server-shortid>`**, password = the **panel account password** (NOT a separate secret). The wizard explains this exact shape, deep-links to `<panel>/server/<id>/settings`, and the moment host+user+password are entered it opens a real paramiko SFTP session (existing `SFTPConsoleSource` connect) and lists `/` to confirm. | Copy host+port+user from Settings; type the panel password. |
| 7. Install BepInEx + plugin on the server | **Automatable + automated** for an SFTP-reachable Pterodactyl server: reuse the EXISTING `--setup-server` + `--upload-bepinex` machinery (wrapper install that adds `-ServerRemoteCommands 5504`, the console-log redirect, and the relay; then push the BepInEx pack + the signed `NukeStats.dll` + generated cfg). | Nothing (wizard does it), except a one-time **server restart** if the panel's start command can't be hot-applied — the wizard issues the power-cycle via the now-validated client key. |
| 8. Relay liveness | **Automated.** After the restart, run the existing `_relay_alive()` round-trip + a `[NOSTATS]`-line grep to confirm the plugin loaded and is emitting. | Nothing. |

**Net rule for §7.A.2: the wizard automates everything that a CLIENT API key + SFTP password can do (validate creds, resolve the server id, install BepInEx, push the DLL, power-cycle, verify). It can only INSTRUCT for the steps gated behind the host's billing/account/application-API surface (sign-up, server creation), and it makes those steps foolproof with deep-links, annotated screenshots, and paste-back validators so the user never wonders "did that work?".**

### 7.A.3 Own-PC guided path — maximal automation

1. **Detect the dedicated-server tool.** Probe the standard Steam library paths (`%ProgramFiles(x86)%/Steam/steamapps/common/…`, the registry `SteamPath`, and Steam's `libraryfolders.vdf` extra libraries on Windows; `~/Library/Application Support/Steam/steamapps/…` on macOS) for the *Nuclear Option Dedicated Server*. File-picker fallback if not found. If the tool isn't installed, link to the exact Steam tool page and explain the one-time `steamcmd`/Steam-client install.
2. **Lay down the plugin stack locally** (no SFTP needed — it's the same machine): unzip the **bundled** BepInEx pack into the server dir, drop the **signed** `NukeStats.dll` into `BepInEx/plugins/`, write `BepInEx/config/anz.nukestats.cfg` from the chosen preset (§7.F), and write/patch `DedicatedServerConfig.json` (server name, MaxTime, VoteKick).
3. **Wire the command channel.** Own-PC needs no relay/tunnel — the bot connects **directly** to `127.0.0.1:5504`. The wizard writes the launch arguments so the server binds `-ServerRemoteCommands 5504` and `-logFile` to a known path the bot tails *locally* (a plain file read, not SFTP — a new `LocalConsoleSource`).
4. **Launch.** `LocalProcessPower` spawns the Unity server as a tracked child process (PID file), and the wizard runs the same liveness probe (direct `127.0.0.1:5504` round-trip + local-log `[NOSTATS]` grep). Power "restart" = kill+respawn the child; if the user prefers to launch the game themselves, power degrades to `ManualPowerController` (attach by PID/port-probe).

### 7.A.4 Add-to-existing-server path (UNCHANGED, must keep working)

The existing three branches (This PC / External Linux / External Windows for a server that ALREADY runs) are preserved verbatim as §1's flow. **Δ to §1:** the only change is that these branches now also expose the §7.A.2 *credential helpers* (the "generate a client API key" deep-link + the "find your SFTP details" explainer) as optional in-line helpers, because even an existing-server admin often doesn't have a *client* key yet (they may have been using the panel UI by hand). So the credential-acquisition helpers are factored OUT of the fresh-server branch and made reusable by every branch that needs a Pterodactyl key or SFTP password.

**Effort:** the Pterodactyl guided path is **M** (mostly UI + reusing `_pt_api`/`--setup-server`/`--upload-bepinex`); the own-PC path's automation is **M** and overlaps almost entirely with the LocalBackend already scheduled in Phase 2 §3 (`LocalConsoleSource` + `LocalProcessPower`). Net new work beyond Phase 2 ≈ **M**.

## 7.B CREDENTIAL-FREE PACKAGE — the shipped install contains ZERO owner secrets

**Requirement.** The downloadable package contains **none** of the owner's secrets, keys, IDs, panel URL, host, or player PII. Every secret is **collected from the user at run time** in the wizard and stored **locally** (OS keyring, or a `0600` untracked secrets file on headless Linux). This is the §2.3 secrets model, now stated as a hard product invariant and tied back to the scrub.

### 7.B.1 What the package must NOT contain (re-confirmed scrub, tied to the catalogue)

The package = the app bundle (`toolkit-vX.Y.Z.zip`) + the signed `NukeStats.dll` + the manifest. None of the following may appear in ANY of those, nor in git history (per `PRE_UPLOAD_CHECKLIST.md` §2–3, which this section re-affirms):

- Owner Pterodactyl **client key** (`ptlc_…`), panel **URL**, server **identifier** → collected at run time → stored in keyring (key) + `config.toml`'s non-secret `[server]` block (URL/id are not secret but are owner-specific, so they too are user-entered, never bundled).
- Owner **SFTP** host / port / user / **password** → host/port/user into `config.toml` `[server]` (user-entered), password into keyring.
- Owner **`AdminSteamIds`** (the hard-coded `<ADMIN_STEAMID>`) → ships **blank**; wizard's "detect my SteamID" helper fills it. (This is the §3 Phase-0 `Config.Bind("Admin","SteamIds","")` change — re-confirmed.)
- Owner `ADMIN_SIDS` in the bot → ships `set()`, sourced from config (re-confirmed, `PRE_UPLOAD_CHECKLIST.md` §3).
- Any `ranks.json` / ledgers / logs / `match_history` / `dashboard_state` (player PII) → not in the package; created fresh at first run in the per-user data dir.
- The 774 MB `archive/` dump, `_server_backup/`, `*.example` are the ONLY config-shaped files shipped, and the `.example` templates carry **placeholders only** (`7656119xxxxxxxxxx`, `ptlc_REPLACE_ME`, `your-host.example:2022`).

### 7.B.2 Run-time collection + local storage (the §2.3 model, made concrete)

- **Collected in the wizard:** panel URL, client key, server id (auto-resolved from key), SFTP host/port/user/password, admin SteamID(s), and the feature preset. Secrets fields are **masked** with a "Test" button that validates live before the user can proceed.
- **Stored locally, two tiers (Δ to §2.3 — fail-loud added):** Tier 1 = OS keychain via `keyring` (Windows Credential Manager / macOS Keychain / libsecret). Tier 2 = a `0600` `secrets.toml` in the per-user data dir, **only on genuinely headless Linux with no keyring backend** — and on Windows/macOS a missing keyring backend must **fail loud**, never silently write plaintext (per `PRE_UPLOAD_CHECKLIST.md` §7). `config.toml` stores only a boolean "is set" reference, never the value.
- **Injection:** the launcher reads secrets from the store and injects them into the child bot/web-CC environment at spawn (`NO_SFTP_*`, `PT_*`), exactly replacing today's `run.bat` literals. The bot/web-CC keep reading `os.environ` so their call sites barely change.
- **First-run import (for the OWNER's own migration only — not shipped behaviour):** a local `import-legacy-secrets` step reads the owner's existing `apiKey.txt`/`panel.txt`/`run.bat`, moves them into the keyring, and prints "delete the plaintext files + ROTATE both credentials." This is a dev convenience, gated behind a flag; it ships disabled.

### 7.B.3 Re-confirm the scrub (carry forward, do NOT relax)

This requirement does NOT replace the scrub — it depends on it. The scrub (PRE_UPLOAD_CHECKLIST §1–5, plan §6 steps 1–7) is what guarantees the *repo/history* is clean; §7.B is what guarantees the *shipped artifact* is clean. Both gates apply:

- **Rotate** the exposed SFTP password + Pterodactyl key **now** (already a BLOCKER; re-stated because a live secret transited tool output during the docs session per MEMORY).
- The CI **secret-scanner gate** (gitleaks `--no-git` + SteamID64 regex `7656119[0-9]{10}` + literal sweep) runs on the staging tree **and** on the assembled release bundle before any asset upload — a new check: **scan the built `.zip` and `.dll`-adjacent files for SteamID64/host/key literals as a release-job step**, so a stray PII file can't ride into a Release even if it slipped past the repo scan.
- The `.example` templates are the ONLY identity-shaped files in the package and contain placeholders only.

**Effort:** the secrets store + masked wizard fields are already Phase-1 §3 (**M**). The added release-bundle scanner step is **S**. Net new ≈ **S**.

## 7.C OFFLINE-FIRST — install + configure + launch with NO internet; only auto-update needs the network

**Requirement.** A user must be able to **install, configure, and launch** the entire toolkit with **no internet connection at all**. Everything required for those three phases is **bundled** in the download. Only the *later* auto-UPDATE step reaches out to the network. (For the *own-PC* fresh path this is fully offline end-to-end; for the *external-server* paths the user obviously needs their own network to reach their own host, but the **toolkit itself** pulls nothing from us.)

### 7.C.1 What MUST be vendored into the download (the offline bill-of-materials)

| Bundled artifact | Why it must be in the package (not fetched) |
|---|---|
| **Frozen launcher** (PyInstaller one-file, per admin OS) with a **bundled CPython** | No system Python / pip at install time. (§1 decision, now a hard offline constraint.) |
| **All Python deps, vendored** — `paramiko` (+ its `cryptography`/`cffi`/`bcrypt`/`pynacl` native wheels for the target OS+arch), `flask`, `keyring` (+ platform backends), `platformdirs`, `tomlkit`/`pydantic`, `minisign`-verify lib (§7.E) | The frozen exe already embeds these; the point is the **build** must collect the *native* wheels for **each** OS+arch so nothing is pip-installed on the user's machine. macOS needs `cryptography`/`pynacl` arm64 + x86_64 (or a universal2 build). |
| **Prebuilt + SIGNED `NukeStats.dll`** + its `.minisig` (§7.E) | End users never get the .NET SDK or the non-redistributable game assemblies; they get a ready, verifiable binary. |
| **Bundled BepInEx pack** (the Thunderstore `BepInExPack` for the server's platform — **Linux x64** for Pterodactyl/Linux hosts; this is the one Δ to §2.6 / §4.6) | §4.6 currently says "BepInEx pack is linked/downloaded from Thunderstore at setup, not vendored." **Δ: for OFFLINE-FIRST, vendor the BepInEx pack** so own-PC + first-time external installs work with no network. Thunderstore's licence (BepInEx is LGPL/MIT) permits redistribution; include the upstream LICENSE + a `THIRD_PARTY_NOTICES`. Keep the "download latest from Thunderstore" path as an *optional online refresh*, not the default. |
| **The signed-update trust root** — the minisign **public key** baked into the launcher (§7.E) | Verification must not require fetching the key. |
| **The settings catalogue / `plugin_features.json` + presets** + all `.example` configs + the baked map atlas/PNGs | Config generation, the feature grid, and the live map must render with no network. |
| **Bundled docs / quickstart** (the README + a short offline "getting started" HTML the wizard can open) | The help the user needs during setup can't assume they can reach GitHub. |

### 7.C.2 The network boundary (exactly one online dependency)

- **Offline (always):** launcher start, wizard, preflight, config generation, secrets storage, BepInEx+DLL install onto the server (uses the user's own SFTP/local FS, not us), launch, health check, and running the bot/web-CC. The web-CC live map, command centre, and all gameplay features are offline-local.
- **Online (only when the user opts in):** `updater.check()` against `latest.json` on GitHub Releases, and the subsequent verified download of a new bundle/DLL. This is the **only** call to *our* infrastructure. It is opt-in, notify-only by default (§4.5), and if it fails the product keeps working on the bundled version.
- **First-run with no network must not warn or degrade** — the updater check is lazy/deferred and silently skipped when offline; nothing in install/configure/launch may block on a network call (no telemetry, no license check, no font/CDN fetch in `webcc.html` — audit `webcc.html` for any external `<script>`/`<link>`/font CDN and **vendor them** so the SPA renders offline).

### 7.C.3 Build implications

- The frozen-launcher build must run **per OS+arch** and bundle the matching native wheels (this is also the §7.D macOS matrix). A CI matrix of `{windows-x64, macos-arm64, macos-x64-or-universal2}` produces three launcher artifacts.
- Add an **offline smoke test** to CI: on a network-disabled runner (or with outbound firewalled), run launcher → wizard preflight → render the feature grid → generate a cfg → assert no socket attempts leave the box except to the (mock) game host. This is the regression guard that keeps the "only update needs network" invariant true.

**Effort:** vendoring deps is mostly packaging config inside the already-scheduled frozen-launcher work (Phase 1 §3, **M**). Vendoring the BepInEx pack + the `webcc.html` external-asset audit is **S**. The offline CI smoke test is **S**. Net new ≈ **S–M**.

## 7.D macOS AS A FIRST-CLASS ADMIN OS

**Requirement.** The **admin OS** (the machine running the launcher + bot + web-CC) must support **macOS** as a first-class citizen alongside Windows — full build matrix, with the gotchas spelled out. (This is about the ADMIN PC; the game-server OS is independent — Linux on Pterodactyl, Windows, or the admin's own Mac/PC for own-PC mode.)

### 7.D.1 Build matrix (Δ to §3 — adds macOS as a shipped target)

| Admin OS | Arch | Launcher artifact | Status |
|---|---|---|---|
| Windows | x64 | `Toolkit-Setup.exe` (PyInstaller one-file) | Primary (today's owner). |
| macOS | arm64 (Apple Silicon) | `Toolkit-Setup.app` / `.dmg` | **First-class, NEW.** |
| macOS | x86_64 (Intel) | universal2 in the same `.app`, or a second artifact | **First-class, NEW.** Prefer `universal2` to ship one Mac artifact. |
| Linux | x64 | (admin-on-Linux is the headless case) | Best-effort; the `0600` secrets fallback path. |

The plan's existing process model already says "cross-platform process spawn (subprocess, not `.bat`)" (§3 Phase 2) and the bot is pure Python — so the runtime is portable. The macOS work is concentrated in **packaging, signing, and a handful of OS-specific paths.**

### 7.D.2 macOS gotchas (the spelled-out list)

1. **Gatekeeper / notarization.** An unsigned `.app` is quarantined and shows "cannot be opened because the developer cannot be verified." Options: (a) **sign + notarize** with an Apple Developer ID ($99/yr) — the only friction-free path; (b) ship unsigned + document the `xattr -dr com.apple.quarantine` / right-click-Open dance. **Recommendation:** unsigned + documented for v1 (parallels the Windows SmartScreen stance in §4.1), Developer-ID notarization in Phase 3 alongside Windows code-signing.
2. **Keychain prompts.** `keyring` on macOS uses the login Keychain; the first read after a new build may prompt "X wants to use your confidential information." Expected — document it; an unsigned/ad-hoc-signed binary re-prompts after each rebuild because the code-signature identity changes (another reason to sign).
3. **`platformdirs` data dir.** macOS user data lands in `~/Library/Application Support/NukeOptionToolkit/` — **never** hard-code `%APPDATA%`. The §2.2 data-dir indirection (`paths.py` seam) must use `platformdirs` so all three OSes resolve correctly.
4. **Native wheels.** `cryptography`, `bcrypt`, `pynacl`, `cffi` (paramiko's chain) need **arm64** (and x86_64 if not universal2) macOS wheels collected at build time. A Windows-only freeze won't run on a Mac — this is why §7.C's build matrix is per-OS+arch.
5. **Browser auto-open.** `webbrowser.open()` works on macOS, but the localhost-token URL must be opened with the default browser, and Safari's "Do you want to allow…" localhost prompts should be anticipated in the quickstart.
6. **Steam path for own-PC mode (§7.A.3).** macOS Steam library = `~/Library/Application Support/Steam/steamapps/`. **Caveat:** if the *Nuclear Option dedicated server* tool is **Windows/Linux-only** on Steam, own-PC mode on a Mac may be unavailable — the wizard must **detect this and gracefully say "own-PC hosting isn't available on macOS for this game; use a Pterodactyl/Linux host instead,"** rather than failing. (Admin-on-Mac driving an *external* Linux server is always fine.)
7. **File perms / line endings.** The relay helpers + wrapper are pushed to a Linux host; ensure LF line endings and `chmod 0755` (the existing `--chmod-exec`) regardless of admin OS. No `.bat` on the admin side — the launcher spawns Python subprocesses directly.
8. **No `run.bat` / `deploy.bat`.** Those are Windows entrypoints; on macOS the launcher IS the entrypoint (it sets env + spawns the bot), and the daily-deploy Scheduled Task becomes a `launchd` plist or an in-launcher scheduler. (The §3 Phase-2 "OS-appropriate autostart units" line now explicitly includes `launchd` for macOS.)

**Effort:** runtime is portable already; macOS is **M** (packaging matrix, `platformdirs` seam, `launchd` autostart, the own-PC-on-Mac availability check) + the optional notarization in Phase 3 (**M** + $99/yr).

## 7.E DLL SIGNING NOW (minisign) — folded into Phase 1

**Requirement.** Pull DLL signing **forward into Phase 1** (it was deferred to Phase 3 in §2.6/§3). The DLL runs arbitrary code inside the game server, so a turnkey auto-updater that pushes a DLL to downstream servers **must** verify a signature, not just a SHA-in-manifest (SHA-over-HTTPS does not defend against a compromised release/account). Use **minisign** (small, modern, single Ed25519 keypair, trivial to vendor a pure-verify path).

### 7.E.1 The five pieces (all land in Phase 1)

1. **Keygen (one-time, offline).** Generate a minisign keypair on an offline/secure machine. The **secret key** never enters the repo or CI config as plaintext — it lives only in a CI **secret** (GitHub Actions encrypted secret) or, safer, signing happens on the owner's self-hosted runner where the key is local. Record the **public key** + a key-id in the repo's `SECURITY.md` and bake it into the launcher.
2. **Sign-on-release in CI.** The `release.yml` job, after building `NukeStats.dll` (and the bundle), runs `minisign -Sm NukeStats.dll` → produces `NukeStats.dll.minisig`. Also sign the `latest.json`/`manifest.json` (so the version pointer itself is tamper-evident, defending against a downgrade/redirect). Upload `.minisig` files as release assets alongside each signed artifact.
3. **Verify-before-apply in the updater (HARD GATE).** `updater.py` downloads the artifact + its `.minisig`, and **verifies the minisign signature against the BAKED-IN public key BEFORE** (a) writing the DLL anywhere, and (b) handing it to `deploy_plugin_job`. SHA-256 still checks integrity/corruption; **the signature checks authenticity**. A verification failure **aborts the update** and surfaces a clear "signature check failed — not applying" error; the running version stays live. (This is the §4.5 "PRESERVE allowlist / refuse-on-failure" discipline extended to authenticity.)
4. **Key distribution.** The trust root = the public key **baked into the launcher binary** (offline-verifiable, §7.C). Publish the same public key in `SECURITY.md` + the README so users can independently confirm it. Document a **key-rotation** procedure (ship a new launcher carrying the new public key; sign the transitional release with BOTH old+new keys) — needed because a baked-in key can't be revoked over the wire.
5. **Interim rule until the updater ships.** Per `PRE_UPLOAD_CHECKLIST.md` §7: until verify-before-apply is live, **do not** ship any "auto-push prebuilt DLL to downstream servers" capability — require a manual confirm + a published checksum. Phase 1 closes this gap by shipping signing and the verifier together.

### 7.E.2 Why minisign over GPG/Sigstore

minisign = one tiny Ed25519 keypair, a ~200-line verify that is trivial to vendor offline (no keyserver, no web-of-trust, no `gpg` runtime dependency, no Sigstore/Fulcio online dependency — which would violate §7.C's offline-verify requirement). GPG's keyring/agent complexity and Sigstore's online transparency-log are both poor fits for a frozen offline launcher.

**Δ to §2.6 / §3:** signing moves **Phase 3 → Phase 1**. The §4.5 integrity story becomes "SHA-256 for corruption **+ minisign signature for authenticity**, both required, signature verified against a baked-in key before any apply." **Effort:** keygen **S**, CI sign step **S**, updater verify gate **M** (it's the high-stakes part), key-rotation docs **S**. Net ≈ **M**, and it is now a Phase-1 gating deliverable.

## 7.F ONE-CLICK INSTALL + PLUGIN FEATURE TOGGLES at install time

**Requirement.** A genuine **one-click install** that, driven by the **settings catalogue**, lets the user pick which plugin features to enable at install time — those choices are written into the generated `anz.nukestats.cfg` (and the bot/web-CC `CONFIG`). This unifies §4.2 (module selection) + §4.3 (config) + §7.A (onboarding) into a single first-run gesture.

### 7.F.1 "One-click" defined honestly

True one-click (zero questions) is impossible for the external paths (they need creds + a SteamID — there's no secret-free default). So "one-click" means: **after the unavoidable connection step, a single "Install with recommended settings" button** that applies the **Full Community Server** preset, generates the cfg, installs BepInEx + the signed DLL, and launches — with an **"Advanced"** escape hatch beside it. For the **own-PC** path it gets closest to literal one-click: detect game → recommended preset → install → launch, with creds not required.

### 7.F.2 Feature toggles driven by the settings catalogue (§4.2 model, at install time)

- The **same `plugin_features.json` manifest** (id, label, blurb, section/key/default/type, range, `safety`, category, `requires`/`conflicts`) that §4.2 renders in the web-CC also renders the **installer's feature grid**. One catalogue → two surfaces, no drift.
- **Four presets** (Full Community / PvP-Competitive / PvE Co-op / Minimal Stats) are sparse overlays over the schema defaults (§4.3). The one-click button = the Full preset.
- **~13 friendly feature cards** in four categories (Core telemetry · Economy & progression · Moderation & safety · Match management), each a master toggle + a collapsed "Customise" for its tunables. **Safety cards** (flood guard, teamkill, AI limiter, profanity) default **ON** behind a friction-confirm if toggled off. Dependency edges enforced in-UI (skill-balance greyed unless telemetry+skill on; chat display is the 3-way radio RankInName/Reformat/Off per the code precedence in `NameInjectPatch`/`FormatAndBroadcast`).
- The choices write straight into the generated cfg via `render_plugin_cfg()` (merge-preserving), the bot/web `CONFIG`, and — for own-PC — are dropped onto disk; for external, pushed over SFTP with the DLL.
- **The one required plugin change** (already in §2.5/Phase 1 §3) makes the toggles *real*: add a `[Stats]` master-enable + per-feed flags (`EmitScore`/`EmitLiveMap`/`EmitKillFeed`/`EmitSkillEvents`), all defaulting ON, so "Minimal Stats" and "no live map" are genuine, not cosmetic. The toggles that gate a Harmony patch at `Awake` (vs. the live-read ConfigEntry ones) are **labelled "applies on next restart"** in the grid — which is fine at install time because the very next step IS the first server start.

### 7.F.3 Catalogue is the single source

The settings catalogue (`plugin_features.json` + the Pydantic schema, help text lifted verbatim from the C# `Config.Bind` descriptions and the bot constant comments) drives: the installer grid, the web-CC "Plugin features" tab, the generated cfg, and the docs — so adding a plugin feature later means one manifest entry, surfaced everywhere. A release-time CI check asserts every `Config.Bind` key has a manifest entry (kills drift).

**Effort:** the manifest + grid + presets are already Phase-1/Phase-2 §3 (**M**). "One-click" is mostly a button + the Full preset wiring (**S**). The `[Stats]` plugin change is the same **M** already in Phase 1. Net new ≈ **S**.

## 7.7 Updated Phase Mapping (with the deltas folded in)

| Requirement | Lands in | Δ vs original §3 |
|---|---|---|
| **A — Fresh-server onboarding** (Pterodactyl guided path) | **Phase 2** (after LocalBackend + SSH transports exist) — wizard gains the 4th branch + credential helpers | NEW. Pterodactyl path reuses `_pt_api`/`--setup-server`/`--upload-bepinex`. |
| **A — Fresh-server onboarding** (own-PC guided path) | **Phase 2** — overlaps LocalBackend (`LocalConsoleSource`+`LocalProcessPower`) | NEW surface over already-planned LocalBackend. |
| **B — Credential-free package** | **Phase 0** (scrub + blank `AdminSteamIds`/`ADMIN_SIDS`) + **Phase 1** (secrets store, masked wizard) + release-bundle scanner | Strengthens §2.3/§6; adds the **release-bundle scan** step. |
| **C — Offline-first** | **Phase 1** (vendored deps in the freeze) + **Phase 0/1** (vendor BepInEx pack — **Δ to §4.6**, was "download at setup") + offline CI smoke test | Δ: BepInEx now **vendored**, not fetched. |
| **D — macOS first-class** | **Phase 1** (build matrix + `platformdirs` seam + `launchd`) ; notarization **Phase 3** | NEW shipped target; runtime already portable. |
| **E — DLL signing (minisign)** | **Phase 1** (keygen + sign-in-CI + verify-before-apply) — **Δ: moved from Phase 3** | Δ: pulled forward; now a Phase-1 GATE for any DLL auto-push. |
| **F — One-click + install-time toggles** | **Phase 1** (`[Stats]` change + schema + manifest) + **Phase 2** (full grid + presets + one-click button) | Unifies §4.2/§4.3; one-click = Full preset. |

**Phase 0** gains: blank-identity defaults are now a *credential-free-package* invariant (B); vendor the Linux BepInEx pack into the repo/release (C); generate the minisign keypair (E). **Phase 1** gains the most: secrets store + masked wizard (B), vendored-deps freeze + offline smoke test (C), macOS build matrix + `platformdirs` + `launchd` (D), **minisign sign+verify (E, pulled forward)**, schema + `[Stats]` plugin change + manifest + presets foundation (F). **Phase 2** gains the two fresh-server wizard branches (A) and the full feature grid + one-click (F). **Phase 3** keeps only notarization/code-signing of the *launcher* (Windows + macOS Developer-ID) and the optional split-DLL path.

## 7.8 Open Questions now RESOLVED by these requirements

Several §5 "Decisions for Tomo" are no longer open — the owner's requirements decide them:

| §5 question | Was | NOW RESOLVED to |
|---|---|---|
| #1 Bundle Python or require system Python? | recommended bundle | **RESOLVED: bundle** — mandated by C (offline-first). |
| #2 Secret storage default? | recommended keyring + file fallback | **RESOLVED: keyring primary, 0600 fallback headless-Linux-only, FAIL-LOUD on Win/macOS** — mandated by B. |
| #14 Update integrity for v1? | SHA-256 now, signatures Phase 3 | **RESOLVED: minisign signature REQUIRED in Phase 1** (verify-before-apply) — mandated by E. |
| #16 DLL build infra? | self-hosted runner | **RESOLVED: self-hosted runner ALSO holds/uses the minisign secret key** (signing co-located with the build) — per E. |
| #17 Rotate exposed creds? | yes, immediately | **RESOLVED: hard BLOCKER, re-confirmed** — per B + MEMORY's live-secret note. |
| (new) BepInEx pack vendored or fetched? | §4.6 said fetch from Thunderstore | **RESOLVED: VENDOR the Linux x64 pack** (with upstream LICENSE) — mandated by C; keep online refresh optional. |
| (new) macOS a shipped admin target? | not addressed | **RESOLVED: YES, first-class** (arm64 + Intel/universal2) — per D. |
| (new) Does the wizard provision the Pterodactyl server? | not addressed | **RESOLVED: NO** — guided checklist + deep-links + live validators; automate only what a CLIENT key + SFTP password allow (validate, resolve id, install BepInEx, push DLL, power-cycle, verify); INSTRUCT sign-up + server creation — per A. |

### Still genuinely open (carry forward to Tomo)
- macOS **own-PC hosting**: is the *Nuclear Option dedicated server* tool available on macOS via Steam at all? If not, own-PC mode is Windows/Linux-only and the Mac wizard must say so (§7.D.6) — needs a one-line confirmation from the owner.
- minisign **key custody**: secret key on the self-hosted runner (simplest) vs. an offline-only signing machine (safer, manual `gh release upload`)? (§7.E.1)
- Curated **host list** for §7.A.2: ship a short list of known NO/Pterodactyl hosts, or stay host-agnostic with only the "my own panel" + generic-SteamCMD-egg path? (avoids implied endorsement) — owner call.