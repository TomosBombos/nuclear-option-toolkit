# Nuclear Option Toolkit

A management toolkit for a **Nuclear Option** dedicated server. It has three parts that work together: a **game plugin** (adds stats, ranks, moderation and team balancing inside the game), a **chat/admin bot** (runs map votes, tracks points, welcomes players and controls the server), and a **Web Command Centre** (a browser dashboard for watching and running your server).

## What it does

- **Map voting** — an automatic end-of-mission map vote, plus a player-called `!votemap` vote, then rolls the server to the winning mission.
- **Ranks, points and skill** — a persistent rank ladder and points system, per-player skill rating, and an in-game `!leaderboard`, all built from real in-game scores.
- **Nuke-aware stats and moderation** — turns plugin events into kills, captures and match records, with a team-kill log that attributes blast collateral to the launcher, plus an anti-grief auto-kick for command floods.
- **PvP team balancing and squads** — keeps team sizes close, protects new joiners and squads formed with `!squadup`, and lets admins move players.
- **Web Command Centre** — a live tactical map, kill feed, player table, activity and console feeds, power controls, and settings editors, all in one browser page.
- **In-game messaging** — automatic and owner-defined chat messages, join/welcome announcements, and an editable `!help` list.
- **Server lifecycle** — scheduled restarts and plugin updates (players warned first), game-panel power control, and self-recovery from crashes.
- **Mission pool management** — enable or disable missions, add Steam Workshop missions, and upload your own.

## Install

Pick one of three hosting options and follow **[docs/INSTALL.md](docs/INSTALL.md)**:

- **Pterodactyl** — for a Pterodactyl-panel-hosted server. This is the primary, most-used path.
- **Local** — runs the whole server on your own Windows or Linux PC. Beta / lightly tested.
- **Manual** — for owners who run the dedicated server themselves and add the toolkit by hand. Beta / lightly tested.

You need Python 3.8 or newer. The setup wizard walks you through the rest.

## Documentation

- **[docs/INSTALL.md](docs/INSTALL.md)** — install and run the toolkit for each hosting option.
- **[docs/FEATURES.md](docs/FEATURES.md)** — every feature, what it does and how to use it.
- **[docs/COMMANDS.md](docs/COMMANDS.md)** — the in-game chat commands.
- **[docs/CONFIGURATION.md](docs/CONFIGURATION.md)** — every setting and where to change it.
- **[docs/WEB_COMMAND_CENTRE.md](docs/WEB_COMMAND_CENTRE.md)** — the browser dashboard, panel by panel.
- **[docs/MODERATION.md](docs/MODERATION.md)** — team-kill, anti-grief, bans and vote-kick.
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — how the plugin, bot and dashboard fit together.

## Security note

The Web Command Centre has **no login** — anyone who can reach its port has full admin control. It binds to all interfaces (`0.0.0.0`, port `8770`) by default, which is fine on a home or LAN network behind a router. **Do not run it on a publicly-discoverable, internet-facing IP address.** If your host has a public IP, bind the dashboard to `127.0.0.1` (set `web.host` in config or `NOCC_HOST=127.0.0.1`) and reach it over a VPN or an authenticated reverse proxy.
