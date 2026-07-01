# Changelog

What changed in each release. Versioned by the plugin version (`0.9.x`). Every release on GitHub shows
its own section from this file.

> Looking for what's coming? In-development changes live in
> [CHANGELOG.unreleased.md](CHANGELOG.unreleased.md) and appear on the nightly builds.

> **Maintainers:** when you cut a stable release, add a new `## [<version>] — <date>` section at the
> top describing what's in it. Only list things that ship in that release — nothing unreleased.

## [0.9.14] — 2026-06-29

First public release. The full toolkit, in three parts.

**Plugin (runs in the game server)**
- Persistent ranks and economy: lifetime points on an 11-rank ladder, from real in-game score plus win, placement, and kill bonuses.
- NuclearSkill: a separate per-sortie skill rating, used to balance teams.
- PvP team balance, with new-joiner and squad protection.
- Teamkill punishment, anti-grief auto-kick, and a flood guard that fixed a match-start mass-disconnect.
- AI aircraft limiter to protect framerate.
- Map voting, a slur filter, forfeit votes, and PvE timeout rules.

**Bot (runs on your PC)**
- Owns all saved data (ranks, skill, backups), runs the map vote, and carries out admin actions.

**Web command centre (runs in your browser)**
- Live battle map, player table, activity feed, console, and kill feed.
- Change maps, grant points, move/kick/ban, schedule restarts, and edit settings live.
- Power control wired to your hosting panel.

**Install and updates**
- One-click bundles for Pterodactyl, local, and manual hosting, with a guided web installer.
- Signed releases (minisign) and an opt-in updater that verifies a download before applying it.
- Opt-in public server directory (name and region only, never your IP).
