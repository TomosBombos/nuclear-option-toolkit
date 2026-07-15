# Changelog

What changed in each toolkit release. Every GitHub release shows its own section from this file.
Versions follow the toolkit release number.

## [1.0.10] — 2026-07-15

Anti-grief and flood-protection rework, plus a team-colour cleanup.

### Changed

- **Flood protection and anti-grief are now one system.** The separate "Flood Guard" and "Anti-Grief" dashboard tabs are merged into a single **Anti-Grief** tab, with one rate limit (`Flood.FleetOrdersPerSec`, default 1/sec), one kick path, and one storm breaker — the previous overlapping, duplicated rate-limit-and-kick logic is gone. The redundant `Grief.FloodOrdersPerSec` setting is retired (kept only as a hidden legacy alias).
- **Team colours are consistent everywhere** — join messages, the kill feed, the player list, the live map, and the panels all use PALA yellow and BDF lavender. Live-map bases are now neutral grey for a cleaner read.

### Added

- Inbound-RPC rate guarding and send-buffer-overflow protection, hardening the server against command-flood mass-disconnects.
- The two storm-breaker controls (`Grief.BreakerDistinctPlayers`, `Grief.BreakerWindowSeconds`) are now editable from the dashboard.

## [1.0.2] — 2026-07-14

The first stable release of the full toolkit: the NukeStats plugin, the bot, the Web Command
Centre, and the guided installer, all at version 1.0.2.

### Added

- **Prestige.** A player who tops out the rank ladder can `!prestige`: their rank cycles back to the bottom and their tag permanently shows a star count (default `[ACE - 2*]`). Points are never deleted, and a prestige earned on one of your servers shows on all of them. The tag template is editable in the Web CC Ranks modal.
- **Rank catch-up.** An optional rising starting-rank floor (`Mission.PvpRankCatchupMinutes` / `PvpRankCatchupMaxRank`) so latecomers are not stuck at the bottom of a long match. Nobody is ever lowered. Off by default.
- **Rank funds.** In-game money per rank gained (`Scoring.RankFundsPerRank`, in millions), with three payout modes (`Scoring.RankFundsMode`: `catchup_raised` default, `any_rankup`, `catchup_all`). Grouped with the catch-up knobs under a new "Rank + Fund catch-up" heading in Game Settings.
- **Sky drop and team swaps, fully wired.** `!skyswap` drops a player into an armed jet high over the enemy side, `!swapteam` lets anyone self-swap to the smaller team (admins can swap others), and `!forceteamswap` swaps immediately with no balance check. Admins also get `!setrank`, `!setfunds`, and `!addfunds`. All of these work from the Web CC player popup and command palette too, with per-map faction-safe drop points.
- **Killfeed editor.** Every killfeed line can be kept vanilla, rewritten with your own template, or turned off, per line, live — from a new Killfeed editor in the dashboard.
- **All-ships killfeed.** Ship sinks are announced for every ship class, not just carriers and destroyers. The feed also announces teammate, AI, and crash shot-downs, and hides the native pilot-capture spam.
- **Teamkill collateral verdicts.** A friendly kill that came with equal or more enemy kills in the same blast is reported, not punished; only deliberate team-kills climb the warn/kick/ban ladder.
- **Anti-grief circuit breaker.** A lag spike that makes many players look like flooders at once no longer mass-kicks the lobby.
- **Consolidated vote timing.** Two live knobs — ballot length (`MAP_VOTE_DURATION`, 30s) and post-vote delay (`POST_VOTE_MAP_CHANGE_DELAY`, 15s) — replace the old three. The server's `PostMissionDelay` is derived and pushed automatically, so a map change can never land before the ballot closes.
- **Default / boot map.** Pick one mission the server converges to after a restart or when a vote produces no pick.
- **Kill button.** A fourth header power control that force-kills a hung server.
- **Self-healing restart.** The dashboard Restart and the guarded plugin deploy escalate a hung graceful stop to a hard kill, then always start — a zombie process can no longer block a restart.
- **Award toggles.** Five switches turn individual bonus-point sources off for vanilla-style scoring; ranks still display and carry.
- **Mission Pool upgrades.** Guaranteed (pinned) missions, per-map appearance weights, and per-type match length.
- **FRAME readout.** A header sparkline shows the server's smoothed frame time next to CPU and MEM.
- **Per-session welcome notice.** Each joiner gets a one-time private line naming the active plugin version.

### Changed

- The updater is stable-only: it installs full GitHub releases and ignores pre-releases.
- Rank tags in names no longer depend on the killfeed mode — turning the custom killfeed off keeps rank embedding on.
- Auto-balance moves its pick straight to the smaller team via the new force-swap instead of dumping them to spectate.
- Vote timing and other owner knobs persist in the deploy-protected `.nost-data` folder, so code deploys cannot reset them.
- Docs rewritten against the shipped 1.0.2 source; stale and unverified claims removed.

### Fixed

- PvP timeout results now use the real faction score — the number on the scoreboard — instead of a sum of personal player scores that could call the wrong winner.
- A hung graceful stop during a restart or plugin deploy can no longer leave the server down; the job force-kills and always starts.
- The map change can no longer fire before the vote finishes (the post-mission delay is derived from the vote timing).

### Removed

- The public server directory and its settings (server listing, region, and the gamemonitoring.net banner). Sharing ranks between your own servers via a shared folder stays.
- The global cross-server leaderboard.
- Stable/nightly update channels — there is only stable now.
- The Tracking Update Limiter and Radar Warning Limiter.
- The Performance Sampler.
- The NET stress-monitor panel. Its frametime readout stays, as the FRAME header graph.
