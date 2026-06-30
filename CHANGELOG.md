# Changelog

All notable changes to the **Nuclear Option community toolkit** — the server-side
**NukeStats plugin**, the **mapvote bot**, the **web command centre**, and the **installer**.

The format is based on [Keep a Changelog](https://keepachangelog.com/). The toolkit is versioned by
its plugin version (`0.9.x`). Every nightly and stable release pulls its notes from the matching
section below.

> **Maintainers:** add changes under **[Unreleased]** as you make them. Nightly builds publish the
> **[Unreleased]** notes; when you cut a stable release, rename **[Unreleased]** to the new version
> with today's date and start a fresh empty **[Unreleased]**.

## [Unreleased]

### Added
- **Community directory banners** — opted-in servers can show their live
  [gamemonitoring.net](https://gamemonitoring.net/nuclear-option/servers/) 560×95 status banner in
  the README server list (auto-matched by address, or via a pasted URL).
- **Signed auto-updates** — every release asset is minisign-signed; the opt-in updater verifies the
  signature and SHA-256 before applying. Public key ships as `installer/trusted.pub`.
- **Nightly builds** — automatic nightly pre-releases (the most recent 3 are kept), alongside
  deliberate stable releases.
- **One-click installers** — per-host bundles (Pterodactyl / Local / Manual), each a full
  self-contained install driven by a guided web setup wizard.
- **Public server directory** — opt-in list of community servers (name, region, plugin version only;
  never IPs or player data).

### Changed
- README community list is now a centered table with the live banner embedded in the Server column.

### Fixed
- _Nothing yet._

## [0.9.14] — 2026-06-29

First public **stable** release — the complete toolkit:

- **Plugin (NukeStats):** persistent ranks + economy, NuclearSkill per-life rating, PvP team
  balancing with squad and new-joiner protection, automated teamkill enforcement, anti-grief
  auto-kick, AI aircraft limiter, and a network flood guard (fixes a match-start mass-disconnect).
- **Bot:** map voting and `!votemap`, chat rank tags, profanity filter, forfeit votes, admin
  commands, and a live activity feed.
- **Web command centre:** pan/zoom live battle map, players/ranks/skill, chat + console, a settings
  menu to change any plugin setting live, scheduling, a server-message manager, and power control.

---

_Earlier 0.x development predates this changelog; see the release history on GitHub._
