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
- **Netcode-stress monitor**: a live graph of packet drops, order streaks, dead-netId RPCs, and send-buffer disconnects.
- Moderation: per-entry ban-log remove, and "Clear all" no longer wipes ban history.
- Public listing (List Server Publicly + Region) now persists across empty-server restarts.
- Dimmer light theme, plus a broad accessibility pass (ARIA, keyboard control, reduced motion, screen-reader live regions).

### Install & distribution
- Public server directory with live gamemonitoring.net status banners.
- Signed nightly build pipeline: automatic pre-releases, with the most recent three kept.
- README and docs rewritten for clearer, plainer language.
<!-- NIGHTLY-NOTES:END -->
