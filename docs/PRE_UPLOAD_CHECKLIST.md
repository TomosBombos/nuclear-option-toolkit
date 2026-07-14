# Pre‑Upload Secret Scrub & First Steps

> **Read this before the project ever touches GitHub.** The working folder contains live credentials and player PII. A single careless `git add` would leak the SFTP password, the Pterodactyl API key, the server address, and hundreds of players' SteamIDs and names. This checklist was produced and cross‑checked by an adversarial review pass; treat every BLOCKER as a hard gate.

---

## 0. Two non‑negotiable rules

1. **Fresh repo, no history.** Build a clean *staging tree* (a brand‑new empty folder), copy in only the files you intend to publish, and run `git init` **there**. Never `git init` inside the live working folder — its history and untracked files would carry every secret forever.
2. **Whitelist, not blacklist.** Copy in only what you mean to publish. Deny everything by default. A `.gitignore` is a *backstop*, not the primary defence.

---

## 1. Rotate credentials first (do this regardless of scrub success)

The SFTP password and the Pterodactyl API key have appeared in command/automation outputs during development. Before publishing anything, **rotate both**:

- [ ] Change the **SFTP password** on the host, and update your local `run.bat` / secrets file.
- [ ] Regenerate the **Pterodactyl API key**, and update `apiKey.txt` / secrets file.

This makes any accidental historical leak harmless.

---

## 2. Files that MUST NEVER be published (deny list)

These contain secrets or player PII. None of them goes into the staging tree — ship a `.example` template instead where one is needed.

| File / folder | Why it's dangerous |
|---|---|
| `run.bat` | Embeds the **SFTP password**, host, and user → ship `run.bat.example` with placeholders |
| `apiKey.txt` | **Pterodactyl API key** → `apiKey.txt.example` |
| `panel.txt` | Panel URL → `panel.txt.example` |
| `ranks.json`, `ranks*.json`, `ranks_backup_*.json`, `ranks_pre_revert_*.json`, `backups/` | Player **SteamIDs + names** |
| `points_ledger.jsonl`, `skill_ledger.jsonl`, `admin_commands.jsonl` | Player SteamIDs |
| `match_history.json`, `dashboard_state.json`, `schedule.json`, `command_centre_settings.json` | SteamIDs / names / state |
| `*.log` (`bot_output.log`, `console_mirror.log`, `activity.log`, `keepalive.log`, `deploy_plugin.log`, …) | Chat, SteamIDs, host |
| `plugin_commands.txt`, `*.done` | A SteamID / state markers |
| `deployed_plugin.json`, `deployed_plugin.sha256`, `pending_plugin.json`, `pending_plugin.dll*` | Server SHAs / binaries |
| `archive/` (incl. `Filestructureforclaude.gz` — a ~774 MB full server dump — and `_bepinex.log`) | Almost certainly full live config/logs/ranks |
| `_server_backup/`, `mapdata/`, `map_backup_*/` | Server config / bulk |
| `.claude/`, `__pycache__/`, `NukeStats/{libs,bin,obj}/`, `NukeStats/bepinex_pack/` | Dev‑harness paths / build artifacts / vendored DLLs |

> **Relay helpers exception:** `archive/no_relay.py` and `archive/no_relay.pl` are clean and *should* be published — copy them by exact path into the new repo (e.g. `admin/relay/`). Exclude everything else in `archive/`.

---

## 3. Source files that hard‑code identity — parameterize before publishing

These are files you *do* want to publish, but they currently embed real identity that the scanner will (correctly) block on:

- [ ] `no_mapvote_bot.py` — `ADMIN_SIDS = {"…"}` (admin SteamID), an embedded host literal, and example SteamIDs → read admin IDs from config/env; default `ADMIN_SIDS = set()`; replace example IDs with `7656119xxxxxxxxxx`.
- [ ] `NukeStatsPlugin.cs` — `Config.Bind("Admin","SteamIds","…")` → default to `""`.
- [ ] Ship `run.bat.example`, `apiKey.txt.example`, `panel.txt.example`, and a hand‑authored `anz.nukestats.cfg.example` with a **blank** `AdminSteamIds` (do **not** derive it from any live cfg).

---

## 4. `.gitignore` (author it before commit 1 — the backstop)

```gitignore
# secrets / credentials
run.bat
apiKey.txt
panel.txt
# runtime state / PII (SteamIDs, names, chat)
*.log
*.jsonl
ranks*.json
match_history.json
dashboard_state.json
schedule.json
command_centre_settings.json
plugin_commands.txt
*.done
# deploy state / binaries
deployed_plugin.*
pending_plugin.*
*.dll
# machine-generated dev dumps / harness
docs/_*.json
.claude/
__pycache__/
# bulk / backups / build
archive/
backups/
_server_backup/
map_backup_*/
mapdata/
NukeStats/libs/
NukeStats/bin/
NukeStats/obj/
NukeStats/bepinex_pack/
# un-ignore the published templates
!run.bat.example
!apiKey.txt.example
!panel.txt.example
!anz.nukestats.cfg.example
```

---

## 5. Final verification gate (must return **0** before `git init`)

Run a secret scanner **and** a custom literal/regex sweep over the entire staging tree. Both must report zero:

- [ ] `gitleaks detect --no-git` (stock rules) — **and** a custom sweep, because stock rules miss a bare password:
  - SteamID64 regex: `7656119[0-9]{10}`
  - your real SFTP host
  - your real SFTP user
  - your real server IP
  - your real SFTP password literal
- [ ] Wire that sweep into **both** a pre‑commit hook **and** a CI GitHub Action (so it can never regress).
- [ ] Push to a **private** repo first, let CI scan the pushed tree, then flip to public.

> Note: the project's own `docs/` already redacts these; the machine‑generated `docs/_*.json` dumps are gitignored above because they can contain raw SteamIDs.

---

## 6. After it's clean — first steps (Phase 0 of the plan)

See [PRODUCTIZATION_PLAN.md](PRODUCTIZATION_PLAN.md) §3 (Phase 0) for detail. In order:

1. Create the clean staging tree + `.gitignore` + the secret scanner in CI.
2. Lay out the repo skeleton (separate the **prebuilt plugin**, the **admin tooling**, the **installer**, **docs**, **example configs**).
3. Stand up a **CI build that compiles `NukeStats.dll`** and publishes it as a release asset (so end users never need the .NET SDK), plus a 3‑OS smoke build of the launcher.
4. Add `LICENSE`, `README.md` (quickstart), and the `.example` config templates.

---

## 7. Plan gaps to fold in before building (from the review)

These don't block the *upload*, but address them before building the product on top of the plan:

- **Add a test strategy** (the codebase has none today): in‑memory fakes for the file/command/power providers, and a **golden‑file test** that asserts `config → anz.nukestats.cfg` renders byte‑for‑byte and that the schema defaults equal today's literal constants. This is the safety net for the I/O refactor.
- **Pull DLL signing forward** (e.g. minisign) — do **not** ship a turnkey "auto‑push a prebuilt DLL to every downstream server" updater with no signature; SHA‑in‑manifest‑over‑HTTPS doesn't defend against a compromised release. Until signing lands, require manual confirm + published checksum for DLL updates.
- **Split the data‑dir move from the constants refactor**: introduce a `paths.py` indirection seam first (moves nothing), verify, *then* relocate the data directory — and add a **first‑run migration** for the existing ranks/ledgers so the live economy isn't stranded.
- **Keep the relay a first‑class transport.** The SSH direct‑tcpip path (`--testtunnel`) is a one‑shot *diagnostic*, not a persistent reconnecting transport; the production `RemoteCommand` class has real reconnect/resync logic an SSH channel would have to re‑implement. Prove the tunnel against the live host before relying on it; otherwise the relay stays the external default.
- **Make the secrets‑file fallback fail loud** on Windows/macOS (where there's a keychain) instead of silently writing plaintext; only auto‑use a file fallback on genuinely headless Linux.
- **Own‑PC BepInEx install** deserves a real wizard step (detect the game folder, offer to download+unzip the Thunderstore BepInExPack, write the config + DLL locally, then run a `[NOSTATS]`‑output verify loop) — it's the most common new‑owner scenario and is fully automatable locally.
- **External‑Windows** is the least‑proven path; ship v1 as "SSH/SFTP files + manual power" with a capability probe that degrades gracefully, and defer a Windows‑service power backend until tested on a real Windows dedicated server.
