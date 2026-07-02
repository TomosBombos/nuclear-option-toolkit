# Unreleased — in development

Changes that are in the nightly builds now and will ship in the next stable release. The
[main changelog](CHANGELOG.md) lists released versions only.

> **Maintainers:** add each change under the right heading between the markers below as it lands.
> When you cut a stable release, move these into a new `## [<version>]` section in `CHANGELOG.md`
> and clear this list. Only what's between the markers shows on nightly release pages.

<!-- NIGHTLY-NOTES:START -->
### Gameplay (server plugin)
- PvE mission timeout now counts as a **defeat** (default on), not a stalemate.
- **Votemap overhauled**: independent co-op/PvP option counts; balanced, random, or weighted selection; guaranteed pinned missions; avoid recently played; auto-force PvP at high population; and a kill-switch.
- Start-of-match bonus points are no longer double-granted (per-match dedup).
- **Teamkill enforcement hardened**: no more false bans on non-teamkill deaths, and the teamkill method is tracked and logged.
- Anti-grief circuit-breaker tuning (multi-player detection window).
- Killfeed now reports the killing weapon and attacker.

### Command centre (web) & operator tools
- **Rank-ladder editor**: edit rank titles, point thresholds, colours, and the promotion message.
- **Full `!help` editor**: per-command text, grouping, and per-word colours. A command auto-hides when its plugin is disabled.
- **Message manager** with a per-word colour helper across all message features.
- **Votemap logic editor** and **mission-pool tabs** (co-op / PvP / built-in).
- **Configurable server tick rate (30–120 Hz)** — previously a fixed value that could silently regress.
- **Cross-server shared ranks** (opt-in, off by default): two instances on one host aggregate into a combined rank.
- **Killfeed panel overhaul**: clean columns, teamkills flagged, weapon and attacker tracking, shorthand unit names.
- **Activity log**: colour-coded, category filter chips (including a Bot filter), and persistent filter settings.
- Precise in-map grid coordinates (e.g. `Jg83`) in the players table.
- **Mission pool: the Built-in tab now lists every stock mission, split correctly** — versus operations (Escalation, Terminal Control, Altercation, Confrontation, Domination, Carrier Duel) and stock co-op missions (Escalation & Terminal Control Co-op as BDF/PALA, Breakout, 13. Reprisal). Breakout is co-op, not PvP. Custom weather/time variants stay under the User tab.
- New-to-the-pool stock missions use **self-verifying rotation-key resolution**: listed immediately, kept off auto-ballots until the server accepts their key once (a first admin map change or `--probe-missions`), and a failed map change reports loudly in chat instead of silently doing nothing. A pinned-but-unverified mission is skipped per ballot with an activity note.
- **Live map: correct terrain for every built-in mission** — Carrier Duel shows Ignus Archipelago; Altercation/Confrontation/Domination/Breakout/13. Reprisal (and the Escalation co-ops) show Heartland; Terminal Control (and its co-ops) stays Ignus. A transient map-fetch failure no longer blanks the map for the rest of the mission.
- **Web command centre responsiveness**: clicking any option shows the change immediately and it stays shown — the anti-flicker hold now covers the real 2–5s apply round-trip and every editable panel (custom/built-in messages, `!help` editor, rank ladder, server config, shared-ranks card); toggles no longer swallow their own re-render; Game Settings keeps your applied value on screen until the server confirms (or honestly resyncs after 20s); message add/edit updates the list immediately and the "saved" confirmation isn't wiped.
- Command palette **`nextmap` now works for built-in missions** (it always sent Group "User", so stock missions silently did nothing while reporting success).
- Change-map picker and autocomplete now include enabled custom/uploaded missions, not just the stock lists.
- **Configurable match length** (Votemap settings → "Match length"): set the mission timer independently for co-op/custom maps and for built-in ops/scenarios. Built-in missions otherwise run on the game's 2h default; setting this to ~180 min gives the bot room to end a timed-out match and open the next-map vote.
- **New installs now generate a COMPLETE DedicatedServerConfig.json** (VoteKick, PostMissionDelay, NoPlayerStopTime, DisableErrorKick included) — the old slim template was why those fields couldn't be edited on some servers. Re-running the installer on an existing server fills in any missing keys without touching values you've already set.
- **Server Config saves are now trustworthy end-to-end.** Fixed the silent save failure where fields missing from a template-created config (VoteKick, PostMissionDelay, …) could never be saved — they're now created in the file automatically. Every save is verified by re-reading the file (a save that didn't land fails loudly instead of looking successful); failures show a red "✗ NOT SAVED" on the exact field plus an activity-feed line. Each field now shows live save states — "saving…", "✓ saved", and a persistent "● restart to apply" that only clears after the server actually restarts; if a panel re-templating boot reverts a saved value, the bot detects it, says so, and re-applies it once. Config writes are atomic with timestamped backups (last 5 kept), and admin actions queued while the bot was restarting are no longer lost (the queue survives restarts, with a 15-minute staleness guard).
- **Reliability**: a rejected admin map change no longer cancels the running vote or suppresses the next; bot-side rejections of queued admin actions now log a visible REJECTED line in the activity feed; a truncated admin queue can no longer replay old commands (double point-grants/bans); votemap guaranteed pins survive a save right after a bot restart; settings changed while empty log an "applies when a player is next online" note.
- A bot restart no longer re-welcomes everyone already on the server — the welcome message is back to greeting genuinely new joiners only.
- **Performance**: `activity.log` is now trimmed (it grew forever) and the backend reads only the tail of large logs per poll; the players table no longer rebuilds every second while someone is flying (which swallowed clicks); a slow network response can no longer paint older data over newer.
- **Safety**: mission names with apostrophes can no longer break (or inject into) the toggle buttons; Escape no longer discards an in-progress message draft or unsaved rank-ladder edits; the stale-data banner warns that changes made while the bot is down may not apply.
- `run.bat --probe-missions` now probes all stock co-op names, caches accepted keys (arming them for ballots), and restores the pre-existing next-mission override instead of clobbering it.
- **Netcode-stress monitor**: a live graph of packet drops, order streaks, dead-netId RPCs, and send-buffer disconnects.
- Moderation: per-entry ban-log remove, and "Clear all" no longer wipes ban history.
- Public listing (List Server Publicly + Region) now persists across empty-server restarts.
- Dimmer light theme, plus a broad accessibility pass (ARIA, keyboard control, reduced motion, screen-reader live regions).

### Install & distribution
- Public server directory with live gamemonitoring.net status banners.
- Signed nightly build pipeline: automatic pre-releases, with the most recent three kept.
- README and docs rewritten for clearer, plainer language.
- **Updater overhaul**: `update` now installs by default (backups kept; `--stage-only` for the old behaviour), updates everything unless you pick a component, always ends with a plain summary of what changed, and records every run in `update.log`. Per-folder installs now resolve their own config even without the launcher (no more silent wrong-channel runs).
- The updater can now update **itself** (new signed `installer.zip` asset) — updater/installer fixes reach existing installs without a reinstall. The signing trust root can never be replaced by a download.
- **New one-click "Deploy Staged Update" launcher** in START HERE: installs everything the updater staged (asks before the match-restarting plugin deploy) and can optionally schedule itself daily at 05:00 — so a staged update never sits unapplied just because a server "only got restarted".
<!-- NIGHTLY-NOTES:END -->
