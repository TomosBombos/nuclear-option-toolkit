# Nuke Option Server — Admin Settings, Webcc Settings Menu, and Productization v2

## 1. Admin Settings Catalogue

**Coverage:** 43 plugin `Config.Bind` entries + 7 bot module constants + 1 game config setting = **51 settings**. One known tunable is intentionally excluded: `WARN_THRESHOLDS` (the mission-time chat-warning list `[3600,1200,600,300,60]` in the bot) is a list, not a scalar — surface it later as an editable comma-separated field if Tomo wants it (see §4).

**Legend** — *Live*: applies on the next HQ tick, no restart. *Restart*: needs a bot / server / config-reload to take effect. *Owner*: who reads the value at runtime. *Common?*: appears in the curated SIMPLE view.

### Match
| Setting | Friendly name | Type | Default | Live/Restart | Owner | Common? |
|---|---|---|---|---|---|---|
| `MISSION_MAX_TIME` | Mission Time Limit | int (s) | 10800 | Restart (bot) | bot | ✅ |
| `Mission.PvpStartingRank` | PvP Starting Rank Floor | int | 3 | Live | plugin | ✅ |
| `Forfeit.Enabled` | Forfeit Voting Enabled | toggle | true | Live | plugin | ✅ |
| `Forfeit.CooldownSeconds` | Forfeit Vote Cooldown | int (s) | 90 | Live | plugin | ✅ |
| `Swap.Altitude` | Team-Swap Spawn Altitude | float (m) | 2500 | Live | plugin | ✅ |
| `VOTE_DURATION` | Map-Vote Ballot Length | int (s) | 60 | Restart (bot) | bot | ✅ |
| `APPROVAL_DURATION` | !votemap Poll Length | int (s) | 60 | Restart (bot) | bot | — |

### Team Balance
| Setting | Friendly name | Type | Default | Live/Restart | Owner | Common? |
|---|---|---|---|---|---|---|
| `Balance.Enforce` | Team Balance Enabled | toggle | true | Live | plugin | ✅ |
| `Balance.MinPlayers` | Min Players for Balancing | int | 6 | Live | plugin | ✅ |
| `Balance.MaxDifference` | Max Team Size Gap | int | 2 | Live | plugin | ✅ |
| `Balance.AutoMove` | Auto-Move Players | toggle | true | Live | plugin | ✅ |
| `Balance.WarnSeconds` | Balance Warning Hold | int (s) | 300 | Live | plugin | ✅ |
| `Balance.NewJoinerSeconds` | New-Joiner Protection | int (s) | 900 | Live | plugin | ✅ |
| `Balance.MoveExemptGames` | Move Cooldown (Games) | int | 2 | Live | plugin | ✅ |
| `Skill.BalanceBySkill` | Balance by Skill Rating | toggle | true | Live | plugin | ✅ |
| `Balance.MoveOnlyUnspawned` | Only Move Players in Spawn Menu | toggle | true | Live | plugin | — |
| `Balance.RecheckSeconds` | Balance Check Interval | int (s) | 6 | Live | plugin | — |
| `Balance.MoveDebounce` | Min Seconds Between Moves | int (s) | 20 | Live | plugin | — |
| `Balance.GraceSeconds` | Legacy Grace Period *(legacy/unused — hide)* | int (s) | 180 | Live | plugin | — |

### Scoring & Ranks
| Setting | Friendly name | Type | Default | Live/Restart | Owner | Common? |
|---|---|---|---|---|---|---|
| `Scoring.WinPoints` | Win Points | int | 200 | Live | plugin | ✅ |
| `Scoring.FirstPlace` | 1st Place Bonus | int | 500 | Live | plugin | ✅ |
| `Scoring.SecondPlace` | 2nd Place Bonus | int | 250 | Live | plugin | ✅ |
| `Scoring.ThirdPlace` | 3rd Place Bonus | int | 100 | Live | plugin | ✅ |
| `Skill.CaptureBonus` | Capture Skill Bonus | int | 250 | Live | plugin | — |
| `Skill.WinBonus` | Win Skill Bonus | int | 200 | Live | plugin | — |
| `Skill.LossBonus` | Loss Skill Bonus | int | 50 | Live | plugin | — |
| `START_BONUS_PTS` | Start-of-Match Bonus | int | 250 | Restart (bot) | bot | ✅ |
| `START_BONUS_WINDOW` | Start-Bonus Window | int (s) | 60 | Restart (bot) | bot | — |
| `KILL_BONUS` | PvP Kill Bonus | int | 50 | Restart (bot) | bot | ✅ |
| `UNDERDOG_PER_PLAYER` | Underdog Kill Bonus per Player | int | 10 | Restart (bot) | bot | ✅ |

### AI & Performance
| Setting | Friendly name | Type | Default | Live/Restart | Owner | Common? |
|---|---|---|---|---|---|---|
| `AILimit.Enforce` | AI Limiter Enabled | toggle | true | Live | plugin | ✅ |
| `AILimit.PerTeamAICap` | AI Per-Team Cap | int | 32 | Live | plugin | ✅ |
| `AILimit.TotalAircraftCap` | Total Aircraft Cap | int | 64 | Live | plugin | ✅ |
| `AILimit.StuckSeconds` | Stuck-AI Clear Time | int (s) | 45 | Live | plugin | ✅ |
| `AILimit.StuckRadiusMetres` | Stuck-AI Move Radius | int (m) | 25 | Live | plugin | — |
| `Stats.SnapshotSeconds` | Stats Snapshot Interval | float (s) | 10 | Live | plugin | — |
| `Cleanup.DismountedPilots` | Clean Up Ejected Pilots | toggle | true | Live | plugin | — |
| `Cleanup.PilotLifetimeSeconds` | Ejected Pilot Lifetime | int (s) | 300 | Live | plugin | — |

### Squads
| Setting | Friendly name | Type | Default | Live/Restart | Owner | Common? |
|---|---|---|---|---|---|---|
| `Squad.MaxSize` | Max Squad Size | int | 4 | Live | plugin | ✅ |
| `Squad.InviteSeconds` | Squad Invite Timeout | int (s) | 90 | Live | plugin | — |

### Moderation
| Setting | Friendly name | Type | Default | Live/Restart | Owner | Common? |
|---|---|---|---|---|---|---|
| `Teamkill.Enforce` | Teamkill Punishment | toggle | true | Live | plugin | ✅ |
| `DedicatedServerConfig.VoteKick` | In-Game Vote-Kick | toggle | false | Restart (game) | game | ✅ |
| `Admin.SteamIds` | Admin SteamIDs | string (CSV) | *(blank in product)* | Live | plugin | — |

### PvE
| Setting | Friendly name | Type | Default | Live/Restart | Owner | Common? |
|---|---|---|---|---|---|---|
| `PvE.TimeoutForceDefeat` | PvE Timeout = Defeat | toggle | false | Live | plugin | ✅ |

### Chat
| Setting | Friendly name | Type | Default | Live/Restart | Owner | Common? |
|---|---|---|---|---|---|---|
| `Chat.RankInName` | Embed Rank in Player Name | toggle | true | Live | plugin | ✅ |
| `Chat.Reformat` | Reformat Chat as [Name - Rank] | toggle | true | Live | plugin | ✅ |
| `Chat.ProfanityFilter` | Slur Filter | toggle | true | Live | plugin | ✅ |
| `KillFeed.Custom` | Custom Kill Feed | toggle | true | Live | plugin | — |

> Chat precedence: `RankInName` overrides `Reformat` when both on. In the UI this is best shown as a single 3-way **Rank-in-Name / Reformat / Off** radio (see §2 / §3-F).

### Flood Guard *(rarely touched; two items need a restart)*
| Setting | Friendly name | Type | Default | Live/Restart | Owner | Common? |
|---|---|---|---|---|---|---|
| `Flood.Enforce` | Flood Guard Enabled | toggle | true | **Restart (plugin patch)** | plugin | ✅ |
| `Flood.FleetOrdersPerSec` | Fleet Orders Per Second | int | 3 | Live | plugin | ✅ |
| `Flood.FleetOrderBurst` | Fleet Order Burst | int | 6 | Live | plugin | ✅ |
| `Flood.LogDrops` | Log Dropped Orders | toggle | true | Live | plugin | — |
| `Flood.DropDeadNetIdRpcs` | Drop Dead-NetId RPCs | toggle | true | **Restart (plugin patch)** | plugin | — |

> `Flood.Enforce` and `Flood.DropDeadNetIdRpcs` gate Harmony patch bindings at **plugin load** (the `CmdSetDestination` throttle and the dead-netId RPC drop), so toggling them needs a **server restart**. Their numeric children (`FleetOrdersPerSec` / `FleetOrderBurst` / `LogDrops`) are live.

### SIMPLE vs ADVANCED split
- **Default = SIMPLE.** A curated ~14-card view, ordered for the way the server is actually run: Mission length, AI caps (per-team 32 / total 64), the core balance dials (on, min-players 6, warn-hold 300s, new-joiner 900s), start/kill bonuses, win points, PvE-timeout, in-game vote-kick, PvP rank floor, and teamkill punishment. (Stretch +1 with `Squad.MaxSize` or `Chat.ProfanityFilter` if there's room.)
- **ADVANCED** = all 51 grouped exactly as the tables above, in the order **Match → Team Balance → Scoring & Ranks → AI & Performance → Squads → Moderation → PvE → Chat → Flood Guard** (most-touched first; rarely-touched and restart-gated last).
- **UI must flag the non-live items:** all 7 bot constants, the game `VoteKick`, and the two Flood patch toggles render with a "needs restart" badge — a cc_web write to them won't take effect until the bot/server restarts. Plugin live settings apply on the next HQ tick and persist to `BepInEx/config/anz.nukestats.cfg`.
- **Hide `Balance.GraceSeconds`** (legacy/unused). `Admin.SteamIds` is a comma-separated string; in the product it ships blank and is populated at install (see §3-B).

---

## 2. Webcc Settings Menu — Build Spec

End-to-end pipeline: **webcc.html UI ⇄ cc_web HTTP ⇄ bot relay ⇄ plugin `setcfg`/`dumpcfg`**, persisted to `anz.nukestats.cfg`. Bot- and game-owned settings are handled by the bot directly (they never reach the plugin).

### 2.1 Wire format

A compact line protocol reusing the existing relay framing (`[code][len][body]`, same channel as the live-map/`ent` feeds).

- **Plugin → bot (dump):** plugin emits one diagnostic line per key on demand and at startup:
  `[cfg] <Key>=<value>\t<type>\t<default>\t<live|restart>` — e.g. `[cfg] Balance.MinPlayers=6\tint\t6\tlive`. Bot parses these into an in-memory `cfg_state` dict.
- **Bot → plugin (set):** a single relay command line `setcfg <Key> <value>` (value verbatim; strings quoted). Plugin validates type + min/max, clamps, applies to the live `ConfigEntry`, calls `Config.Save()`, and echoes `[cfg-ack] <Key>=<applied>\tok` or `[cfg-err] <Key>\t<reason>`.
- **Bot → plugin (dump request):** `dumpcfg` → plugin replies with the full `[cfg]` block (used on relay (re)connect to seed `cfg_state`).

### 2.2 Plugin — `NukeStats/NukeStatsPlugin.cs` *(requires a plugin redeploy)*

- Add a static `Dictionary<string, ConfigEntryBase>` populated alongside the existing `Config.Bind` block (lines ~98–212) — one entry per key, capturing type/default/min-max and a `live|restart` flag (restart for `Flood.Enforce`, `Flood.DropDeadNetIdRpcs`).
- `DumpCfg()` — iterate the dict, emit the `[cfg]` lines above. Call once in `Awake()` after binds, and on receipt of `dumpcfg`.
- `SetCfg(string key, string raw)` — lookup, type-parse, clamp to min/max, assign `entry.BoxedValue`, `Config.Save()`, emit ack/err. For the two restart-gated keys, persist + ack with a `restart` note (the Harmony patch only re-reads at load).
- Wire `setcfg` / `dumpcfg` into the existing remote-command dispatcher (same place `!`-commands and relay verbs are handled). **No new game API needed** — pure config mutation.

### 2.3 Bot — `no_mapvote_bot.py` *(pure bot/web — no redeploy)*

- **Ingest:** in the existing relay/stdout line handler, parse `[cfg]`, `[cfg-ack]`, `[cfg-err]` into a module dict `CFG_STATE[key] = {value,type,default,live,owner:"plugin"}`. Send `dumpcfg` on relay connect.
- **Bot-owned settings:** seed `CFG_STATE` from the module constants (`MISSION_MAX_TIME` L67, `VOTE_DURATION` L120, `APPROVAL_DURATION` L121, `KILL_BONUS` L159, `UNDERDOG_PER_PLAYER` L160, `START_BONUS_PTS` L225, `START_BONUS_WINDOW` L226), each `owner:"bot", live:"restart"`. Persist overrides to a new `bot_overrides.json` loaded at startup *before* the constants are used, so a restart picks them up. A write marks the key dirty → UI shows "restart bot to apply."
- **Game-owned `VoteKick`:** reuse `set_votekick()` (L3626) / `run.bat --set-votekick on|off`; `owner:"game", live:"restart"`.
- **Apply API:** add a `set_cfg(key, value)` dispatcher — plugin keys → relay `setcfg`; bot keys → write `bot_overrides.json` + mark dirty; game key → `set_votekick()`. Returns `{ok, applied, needs_restart}`.

### 2.4 cc_web — `cc_web.py` (Flask :8770) *(pure web — no redeploy)*

- `GET /api/settings` → JSON `{groups:[...], settings:[{key,friendlyName,group,type,default,value,min,max,live,owner,commonlyChanged,adminDescription}], simpleKeys:[...]}`. Built by merging the static catalogue (the §1 metadata, shipped as `settings_catalogue.json`) with live `CFG_STATE` values from the bot.
- `POST /api/settings` body `{key, value}` → calls the bot's `set_cfg`, returns `{ok, applied, needs_restart, error?}`. Validate against catalogue min/max server-side before dispatch.
- `POST /api/settings/bulk` (optional) for preset application — array of `{key,value}`, applied in order, aggregated result.
- Auth: same session/localhost guard the existing command bar uses; treat writes as admin actions and log to the activity feed.

### 2.5 webcc — `webcc.html` *(pure web — no redeploy)*

- New **⚙ Settings** modal/tab. Toggle **Simple / Advanced** (default Simple = `simpleKeys`). Advanced renders the 9 groups in the §1 order.
- Controls by type: `toggle`→switch, `int`/`float`→number input with min/max + step, `string`→text. Each card shows friendly name, `adminDescription` as help text, and a **"needs restart"** badge when `live=="restart"`.
- Chat block renders as the single 3-way **Rank-in-Name / Reformat / Off** radio (maps to `Chat.RankInName` + `Chat.Reformat`).
- On change: debounce → `POST /api/settings`; show ✓ applied / ⚠ needs-restart / ✗ error inline. Refresh values from `GET /api/settings` on open and after writes.

### 2.6 What needs a redeploy vs not

| Layer | Files / functions | Redeploy? |
|---|---|---|
| Plugin `setcfg`/`dumpcfg`, cfg-registry, ack/err | `NukeStatsPlugin.cs` (binds ~98–212, new `DumpCfg`/`SetCfg`, dispatcher) | **Plugin redeploy** (`run.bat --deploy-plugin`) |
| Relay ingest, `CFG_STATE`, `set_cfg`, `bot_overrides.json`, `set_votekick` reuse | `no_mapvote_bot.py` | Bot restart only |
| HTTP endpoints | `cc_web.py` `/api/settings` GET/POST(+bulk) | Web restart only |
| Settings UI | `webcc.html`, `settings_catalogue.json` | Reload only |

> Single plugin redeploy carries `setcfg`/`dumpcfg`. Everything downstream is bot/web/HTML and ships without touching the DLL. The two Flood patch toggles remain restart-gated *to take effect* even though `setcfg` persists them live.

---

## 3. Productization Plan v2 — Deltas

Adopt as **§7 Plan Deltas** of `docs/PRODUCTIZATION_PLAN.md`. Throughline: reframe the audience from "admin who already has a Pterodactyl server + creds" to **"anyone who owns the game and wants a community server."** Ship **zero secrets** and run **fully offline** except one opt-in update check.

### A. Fresh-server onboarding (no server yet)
Add a **4th wizard branch — "I don't have a server yet"** forking into two paths; preserve the existing add-to-existing-server branches and factor the credential helpers out for reuse:
- **Pterodactyl guided checklist.** Deep-link to `<panel>/account/api` (client key) and `<panel>/server/<id>/settings` (SFTP), with live paste-back validators: auto-resolve server id via `GET /api/client`, confirm SFTP via a real `paramiko` session. Then automate BepInEx + signed DLL install via existing `--setup-server` / `--upload-bepinex`, power-cycle, and verify via the relay / `[NOSTATS]` marker. **Instruct** (don't automate) sign-up + server creation — those are billing/application-API gated. SFTP shape must be exact: user = `<panel-user>.<short-id>`, password = panel password; the client API cannot *create* servers.
- **Own-PC path** (depends on the Phase-2 LocalBackend): detect the Steam dedicated-server tool, lay down bundled BepInEx + signed DLL + generated cfg + `DedicatedServerConfig`, spawn a tracked child process, tail the local log, connect direct to `127.0.0.1:5504`.

### B. Credential-free package
Hard invariant: **zero owner secrets in the artifact.** Blank `Config.Bind` Admin SteamIds + bot `ADMIN_SIDS=set()`; no ranks/ledgers/logs; `.example` templates with placeholders only. Collect panel URL/key/server-id + SFTP host/port/user/password + admin SteamID **at run time** in masked wizard fields with live **Test** buttons. Store secrets in the **OS keyring** (a `0600 secrets.toml` fallback **only** on headless Linux; **fail loud** on Windows/macOS if no keyring — never silently write plaintext). `config.toml` holds only an "is set" boolean. Launcher injects secrets into the child bot/web-CC env (`NO_SFTP_*` / `PT_*`), replacing the `run.bat` literals. **New CI step:** a release-bundle scanner that greps the assembled `.zip` (and DLL-adjacent files) for SteamID64 / host / key literals. **Rotating the live SFTP password + Pterodactyl key remains a hard BLOCKER** before any publish.

### C. Offline-first install/configure/launch
Vendor everything needed pre-update into the download: frozen PyInstaller launcher with bundled CPython; all Python deps incl. paramiko's native crypto wheels **per OS+arch**; the prebuilt+signed `NukeStats.dll` + `.minisig`; the baked-in minisign public key; `plugin_features.json` + presets + `.example` configs + baked map atlas/PNGs; and **(Δ to §4.6) vendor the Linux x64 BepInEx pack** (with upstream LICENSE) instead of fetching it. **Exactly one online dependency:** the opt-in updater check against `latest.json`. Audit `webcc.html` for external script/font/CDN refs and vendor them. Add an **offline CI smoke test** (network-disabled runner) asserting no outbound sockets during install/configure.

### D. macOS as a first-class admin OS
Add macOS (arm64 + Intel, prefer **universal2**) to the launcher build matrix alongside win-x64. Runtime is already portable (pure-Python bot/web-CC, subprocess spawn not `.bat`). Eight spelled-out gotchas:
1. **Gatekeeper/notarization** — unsigned + documented for v1; Developer-ID notarize in Phase 3 (parallels the Windows SmartScreen stance).
2. **Login-keychain prompts** re-prompt on rebuild ⇒ another reason to sign.
3. **`platformdirs` `~/Library/Application Support`** path — never hard-code `%APPDATA%`.
4. arm64 / x86_64 **native wheels**.
5. Browser auto-open + Safari localhost prompts.
6. **Steam macOS library path** + a graceful "own-PC hosting unavailable on macOS if the dedicated tool is Win/Linux-only" check.
7. **LF + `chmod 0755`** for pushed relay/wrapper files.
8. **launchd plist** instead of `run.bat` / Scheduled-Task for autostart + daily deploy.

Admin-on-Mac driving an *external* Linux server always works; only own-PC-on-Mac is gated by gotcha #6.

### E. DLL signing now — minisign, folded into Phase 1
Move signing from Phase 3 → **Phase 1**. Five pieces:
1. One-time **offline minisign keygen**; secret key only on the self-hosted runner / CI secret; public key in `SECURITY.md` + **baked into the launcher**.
2. `release.yml` signs `NukeStats.dll` **and** `latest.json`/`manifest.json` after build, uploading `.minisig` assets.
3. `updater.py` **verify-before-apply HARD GATE** — verify the minisig against the baked-in pubkey **before** writing the DLL or calling `deploy_plugin_job` (SHA stays for corruption; signature for authenticity); failure aborts and keeps the live version.
4. Baked-in-key trust root + a documented **key-rotation procedure** (ship a new launcher; sign a transitional release with old+new keys).
5. **Interim rule:** no auto-push DLL capability ships until verify-before-apply is live.

Choose **minisign** (Ed25519) over GPG/Sigstore for offline-vendorable verify with no keyserver/transparency-log online dependency.

### F. One-click install + plugin feature toggles
Define **one-click honestly**: after the unavoidable connection step, a single **"Install with recommended settings"** button applies the **Full Community** preset, generates the cfg, installs BepInEx + signed DLL, and launches — with an **Advanced** escape hatch. Own-PC gets closest to literal one-click (no creds). Drive install-time feature selection from the **same `plugin_features.json` manifest the web-CC uses** (one catalogue, two surfaces, no drift): ~13 friendly cards in 4 categories + 4 sparse-overlay presets; safety cards default **ON** behind a friction-confirm; dependency edges enforced in-UI (skill-balance greyed unless telemetry+skill; chat display = the 3-way RankInName/Reformat/Off radio from §2.5). Choices write into the generated `anz.nukestats.cfg` via merge-preserving `render_plugin_cfg` + the bot/web `CONFIG`. **One required plugin change:** a `[Stats]` master-enable + `EmitScore`/`EmitLiveMap`/`EmitKillFeed`/`EmitSkillEvents`, **all default ON** (so existing servers stay byte-behaviour-identical), making "Minimal Stats" / "no live map" real. Awake-gated toggles are labelled "applies on next restart." **CI check:** every `Config.Bind` key has a manifest entry (and vice-versa).

### Resolved open-questions (from §5)
| Earlier question | Resolution |
|---|---|
| #1 UI surface | **Local web wizard confirmed.** |
| #2 Offline support | **Offline required** — single opt-in online dependency (updater). |
| #14 macOS support | **Yes** — first-class admin OS via per-OS+arch frozen-launcher matrix. |
| #16 DLL signing timing | **Now (Phase 1)**, via minisign verify-before-apply gate. |
| #17 Fresh-server provisioning | Wizard **guides** Pterodactyl setup (checklist + validators) and supports own-PC; does **not** auto-provision the panel server. |

### Phase mapping
- **Phase 0 (before anything public):** scrub repo + **rotate creds** (BLOCKER), blank identities, minisign keygen, vendor the Linux BepInEx pack.
- **Phase 1:** credential-free + offline-first + macOS matrix + minisign signing/verify-before-apply.
- **Phase 2:** the two fresh-server wizard branches (Pterodactyl guided + own-PC LocalBackend) + the full feature grid + one-click.

---

## 4. Decisions / open questions for Tomo
1. **macOS own-PC hosting** — is the Nuclear Option dedicated-server tool actually available on macOS via Steam? If not, own-PC mode is Windows/Linux-only and the Mac wizard must detect-and-say-so (one-line confirmation needed; gates own-PC-on-Mac).
2. **minisign key custody** — secret signing key on the self-hosted CI runner (simplest, but key sits on an internet-connected build box) vs an offline-only signing machine with manual `gh release upload` (safer, more manual)?
3. **Pterodactyl host list for the fresh-server path** — ship a short curated list of known NO/Pterodactyl hosts (implies endorsement, goes stale) vs stay host-agnostic with only "my own panel" + a documented generic SteamCMD-egg fallback?
4. **`WARN_THRESHOLDS` editability** — expose the mission-time chat-warning list (`[3600,1200,600,300,60]`) as an editable comma-separated field in the settings menu, or leave it code-only? (Excluded from the catalogue because it's a list, not a scalar.)