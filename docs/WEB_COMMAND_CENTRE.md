# Web Command Centre

The Web Command Centre is a single-page browser dashboard that gives you a live view of one Nuclear Option server and the controls to run it: power, players, moderation, mission pool, settings, messages, ranks, and updates.

It is a **control surface, not the server itself.** The dashboard (`cc_web.py`, a small Flask app) does not touch SFTP or your rank data directly. It shows you what the running bot has published to local files, and when you click something it queues that intent for the bot to carry out. The game server, the bot, and this dashboard are three separate processes.

---

## Opening the dashboard

1. Start the bot (`run.bat`) so there is live data to show.
2. Start the dashboard: run `python cc_web.py` (or `webcc.bat`, which pins the correct port for that server folder).
3. Open the dashboard in your browser: `http://localhost:8770`.

When the dashboard binds to all interfaces, its startup log also prints a LAN URL you can use from another machine on your network. Please read the security note below before you use that.

**Settings:**
- `web.port` - default `8770` - the TCP port the dashboard listens on. Set in the toolkit `config.json` (`web` section), or override with the `PORT` / `NOCC_PORT` environment variables. The setup wizard auto-picks the first free port starting at 8770 so a second install on the same PC doesn't clash, and the generated launchers pin it via `NOCC_PORT`.
- `web.host` - default `0.0.0.0` (all network interfaces / LAN-reachable) - the interface the dashboard binds to. Set in `config.json` (`web` section), or override with the `NOCC_HOST` environment variable. Set it to `127.0.0.1` to lock the dashboard to this machine only (see below).

---

## Security - read this first

The Web Command Centre has **no login and no authentication on any page or API route.** Anyone who can reach its port has full admin control of your server: power start/stop/restart, bans, config edits, granting points, and more. Access is controlled entirely by which network interface the dashboard is bound to.

By default it binds to `0.0.0.0`, meaning it is reachable from your whole local network, not just this PC. It is **not** loopback-only out of the box.

Guidance:
- **Do not expose the dashboard directly to the internet.** Do not port-forward its port.
- To use it only from the machine it runs on, set `web.host` to `127.0.0.1` in `config.json`, or set the `NOCC_HOST=127.0.0.1` environment variable. Then reach it at `http://localhost:8770`.
- To reach it safely from elsewhere, put it behind something that adds authentication - a VPN into your network, or an authenticated reverse proxy - rather than opening the raw port.

---

## Header controls

The header runs across the top of the page and is always visible.

### Status bar
Chips showing the server state and versions at a glance.

**How it works:** The page polls the bot's published state once a second. A coloured dot shows online / stale / offline based on whether the server is up and how fresh the last sample is (fresh means under about 8 seconds old). It also shows the current mission, time left (the clock counts down smoothly between samples), player count, the live plugin version, and the installed toolkit version. A red "Data is stale" banner appears across the top if the feed stops updating.

**How to use it:** Read-only. Watch the dot colour and the stale banner to know whether you are looking at live data.

**Settings:** None.

### CPU / MEM / FRAME resource graphs
Three small sparkline graphs with numbers: server CPU percentage, memory use, and server frametime.

**How it works:** CPU and MEM poll a resources endpoint every 5 seconds; the values come from the Pterodactyl panel, or from a local running/offline probe when power mode is set to local. FRAME shows the plugin's smoothed (about one second) server frametime in milliseconds, updated on the once-a-second state poll. It draws green under 22 ms, amber up to 50 ms, and red beyond that; an em dash means no frametime data yet.

**How to use it:** Read-only. CPU and MEM show "no panel" if Pterodactyl power control isn't configured.

**Settings:** None.

### Power controls (Start / Restart / Stop / Kill)
Buttons to start, restart, stop, or force-kill the game server.

**How it works:** Each button asks for confirmation, then tells the backend to change power state. On a Pterodactyl host it uses the panel's power API; with local power mode it starts/stops the dedicated server process on this PC. Only start / stop / restart / kill signals are allowed. **Restart self-heals:** it stops the server, waits up to about 90 seconds for it to go offline, force-kills it if the graceful stop hangs, then always starts it again - a stuck process can no longer block a restart. **Kill** hard-stops the server process immediately, skipping the graceful shutdown, and has its own sterner confirmation. The clicked button pulses until the server is confirmed in the requested state.

**How to use it:** Click the Start, Restart, or Stop button in the header and confirm. Use Kill only when a normal Stop hangs. The buttons are disabled if Pterodactyl isn't configured, unless you are using local power mode.

**Settings:**
- `server.power` - default `pterodactyl` (or `local`) - set to `local` to start/stop the dedicated server process on this PC instead of through Pterodactyl. Set in the toolkit `config.json` (`server` section).

### Staged-update badge
A pulsing "Update staged: <version>" badge that appears when a plugin update has been downloaded and is waiting to deploy.

**How it works:** The badge shows only when an update is staged and its version differs from what is currently live on the server.

**How to use it:** Click the badge to open the Schedule modal, where you can deploy the staged plugin.

**Settings:** None.

### Find-a-setting search
A search box in the header that finds a setting across Game Settings and Server Config and jumps straight to it.

**How it works:** It searches the cached settings catalogue plus the server-config fields; picking a result opens the right modal, selects the setting's category, and scrolls to and highlights the matching row.

**How to use it:** Type in the header "Find a setting…" box and click a result.

**Settings:** None.

### Settings menu (dropdown)
A Settings dropdown that opens the seven configuration modals.

**How it works:** The dropdown lists Server Config, Game Settings, Schedule, Messages, Killfeed (the ☠ Killfeed editor), Ranks, and Updates; each item opens its modal.

**How to use it:** Click "Settings" in the header and pick a section.

**Settings:** None.

### Panels menu (show / hide dashboard cards)
A ▦ Panels dropdown that shows or hides each dashboard card.

**How it works:** The menu lists every card - Killfeed, Live Map, Players, Activity, Console, and Command - with a tick per card. Each card also has its own ✕ button in its header. Hiding a card re-balances the layout around it. Your choices are saved in the browser; "↺ Show all panels" at the bottom of the menu brings everything back.

**How to use it:** Click "▦ Panels" in the header and toggle a card, or click the ✕ on a card's header. Click "↺ Show all panels" to reset.

**Settings:**
- `nocc_panels` - default `all panels shown` - remembers which panels you have hidden. Stored in the browser (localStorage), per browser.

### Theme toggle (light / dark)
Switches the whole dashboard between a dark theme and a soft light theme.

**How it works:** The toggle flips the page theme, saves your choice in the browser, and immediately recolours the map blips and feeds. The saved theme is applied before the first paint so there is no flash.

**How to use it:** Click the moon / sun button at the far right of the header.

**Settings:**
- `nocc_theme` - default `dark` - remembers your light/dark choice. Stored in the browser (localStorage), per browser.

---

## Main panels

### Live Map
A pan-and-zoom tactical map over a baked terrain image, showing players, AI aircraft, ships, and bases.

**How it works:** The map picks the right terrain image and geometry from the current mission name. It draws team-coloured, named player blips (with a plane or helicopter glyph), AI aircraft and ship blips, faction bases, and a coordinate grid. Blips glide smoothly between the roughly one-second data updates. Player positions and AI/ship positions come from the plugin's live position frames.

**How to use it:** Scroll or drag to zoom and pan; use the zoom buttons at the bottom right. Press `F` or the fullscreen button for fullscreen (which adds a flanking killfeed and player panels); press `Esc` to exit. On narrow layouts, hold `Ctrl` while scrolling to zoom.

**Settings:** None.

### Air-traffic panel
A strip under the map showing total aircraft flying and, per faction, how many are AI versus player.

**How it works:** It is fed by the AI aircraft limiter's live counts: the plugin publishes per-side AI and player aircraft numbers plus the two caps (`AILimit.PerTeamAICap` and `AILimit.TotalAircraftCap`), and the panel highlights a cap in red when it is reached. It is hidden when there is no air data - including when `AILimit.Enforce` is turned off, which stops this feed (and the AI aircraft/ship blips on the map).

**How to use it:** Read-only.

**Settings:** None (the caps it displays are the `AILimit.*` settings in Game Settings).

### Killfeed
A live list of recent kills.

**How it works:** Each line reads "<time> <victim> shot down by <killer> · <coordinate>." Team-kills get a red TK badge (the plugin flags these authoritatively, with a same-faction fallback), and environmental deaths show as "went down." Unit and weapon names are tidied but keep the specific model. It appears in the left column and in the fullscreen map overlay.

**How to use it:** Click a row to copy it. Click "⚙ edit" in the card header to open the Killfeed editor (see the modals section below), which changes the wording of the in-game killfeed lines.

**Settings:** None (the in-game killfeed lines are edited in the Killfeed editor).

### Players table
A sortable roster of everyone online.

**How it works:** Each row shows name, faction, saved rank and points, aircraft, in-game rank, match points, skill rating, and live grid coordinates. The table only rebuilds when something actually changes, so your hover and focus survive updates.

**How to use it:** Click any row to open the player-actions popup (below).

**Settings:** None.

### Player-actions popup
A per-player action sheet reached by clicking a row in the Players table.

**How it works:** It builds buttons for that specific player (safely, even if the name contains quotes) and routes each action through the command API: grant/deduct actions and team/spectator moves (including Sky drop and Swap team) are queued through the bot, which relays them to the plugin; kick and ban are sent as whitelisted server commands.

**How to use it:** Click a player row, then: type a points number and click Grant (or deduct), move them to Boscali or Primeva, send them to spectator, **Sky drop (enemy team)** them - drops the player into an armed jet high over the other side (asks to confirm) - **Swap team** them to the other side, copy their SteamID, kick, or ban.

**Settings:** None.

### Activity feed
A chronological, colour-coded feed of what is happening: chat, joins and leaves, wins and losses, rank-ups, votes, map changes, moderation, and admin/system lines.

**How it works:** It tails the bot's activity log. Kill lines are always left out (they live in the Killfeed instead). Category filter chips let you hide or show line types, and your choices are remembered in the browser.

**How to use it:** Use the category chips in the card header to hide or show line types. Click a line to copy it.

**Settings:**
- `nocc_actfilter` - default `all categories on` - remembers which activity categories you have hidden. Stored in the browser (localStorage), controlled by the category chips.

### Console panel
A mirror of the server console with noise filtering.

**How it works:** The backend tails the console mirror, classifies lines (remote-command, weapon-manager, AI-units, stats, blast, Steam networking, engine warnings), and collapses the noisy ones into a summary line so only real output and errors show. You can left-click a line to add a pattern (numbers become `#`) that hides similar lines.

**How to use it:** Click "filtered/raw" to switch views. Left-click a line and choose "Filter messages like this" to hide that kind of line. The "filters" button lists and clears your custom filters.

**Settings:**
- `raw vs filtered` - default `filtered` - show noise-filtered output or every raw line. Toggled with the console card's filtered/raw button.
- `console_filters.json` - default `none` - your custom hide patterns. Added from the console line menu.

### Command area
A command console for running server and bot commands, with type-ahead and a full command palette.

**How it works:** The catalogue combines server command aliases and local bot commands (raw operational verbs are hidden). Tab autocomplete suggests missions, players, factions, and numbers. Commands are validated on submit: server verbs are whitelisted and stateful ones are routed through the bot's admin queue. Dangerous commands ask for confirmation.

**How to use it:** Type a command and press Enter (Tab to autocomplete). Examples include say, nextmap, changemap, endmission, leaderboard, ranks, grant, move/join/spec, setrank/setfunds/addfunds, balance, skyswap/swapteam/forceteamswap, kick/ban. Click "all commands" for the full palette, or "Change map" to end the match and switch now.

Three team-action verbs are wired through to the plugin: `skyswap <player>` drops the player into an armed KR-67 Ifrit high in the sky on the enemy team; `swapteam <player>` moves the player to the other team (a brief high-altitude aircraft, then an eject that resets their view to the new team); `forceteamswap <player>` does the same immediately, ignoring the balance check - it is flagged dangerous and asks you to confirm.

**Settings:** None.

---

## Settings and tools (modals)

### Map Pool / Mission Pool
Configures the end-of-mission map vote and which missions are eligible.

**How it works:** The Votemap card controls the ballot: how many PvE co-op and PvP options appear, the selection mix, weights, guaranteed pins, high-population PvP forcing, and vote length. A 🚀 Default / boot map dropdown picks the mission that gets queued after a server (re)start and when a vote produces no pick; leave it empty and the server rotation decides. A per-type match length (in minutes) is set for co-op/custom maps and for built-in ops - the bot applies it when it queues a map. An "Advanced: map appearance chance" editor sets a weight per map for the random ballot slots, shown as a percentage within its pool; 0 means the map is never offered unless it is pinned. The mission tabs toggle which built-in, custom (User), and Workshop missions can appear. The bot is the sole validator of vote and mission changes. Uploaded mission folders are staged, uploaded by the bot over SFTP, and added switched off.

**How to use it:** Click "Map Pool." Tune the Votemap card, set the default/boot map and match lengths, open the Advanced editor to tune per-map chances, toggle missions in the Built-in / User / Workshop tabs, or use "Add Workshop ID" or "Upload mission folder."

**Settings:**
- `votemap_config.json: enabled` - default `true` - master on/off for the automatic map vote.
- `coop_count / pvp_count` - default `4 / 2` - how many co-op and PvP options appear on the ballot.
- `coop_mode / pvp_mode` - default `balanced / fixed` - how options are picked from each pool.
- `include_pvp / include_custom` - default `true / true` - allow PvP slots, and allow enabled custom missions into the co-op pool.
- `guaranteed / avoid_recent` - default `[] / 0` - pin named missions onto every ballot; suppress the last N winners.
- `force_pvp_enabled / force_pvp_players / force_pvp_coop / force_pvp_pvp` - default `true / 24 / 0 / 6` - switch to a PvP-heavy ballot once at least that many players are online.
- `boot_map` - default `empty (server rotation decides)` - the mission queued as the next map after a server (re)start and when a vote produces no pick.
- `coop_minutes / builtin_minutes` - default `180 / 180` - match length in minutes the bot sets when it queues a co-op/custom or built-in map (10-600).
- `mission_weights` - default `1 per map` - per-map appearance chance for random ballot slots (0 = never offered unless pinned). Edited in the Advanced editor, shown as a percentage of the map's pool.

Vote timing is set with exactly two live values: `MAP_VOTE_DURATION` (default 30 s - the ballot length for both the end-of-match vote and `!votemap`) and `POST_VOTE_MAP_CHANGE_DELAY` (default 15 s - the gap after the ballot closes before the winning map loads). Both are edited here in the pool's timing card or in Game Settings -> End of Match & Votes, apply live with no bot restart, and persist in `.nost-data/votemap_timing.json`. The server's `PostMissionDelay` is derived from them (vote + delay) and pushed automatically, so the map can never change before the vote finishes - it cannot be set by hand and is hidden from the Server Config modal. (`ROLLOVER_SECONDS` and `POST_VOTE_COOLDOWN` remain bot constants that need a bot restart.)

### Moderation (Reports + Banned)
Two tabs: an anti-grief / team-kill report log, and a banned-players list.

**How it works:** Reports show auto-kick and team-kill events with the method, unit count, rate, and action taken. Clicking a report row expands it into a plain-English detail card: the player, the teammate killed, the damage dealt, how the kill was delivered, the weapon, and a "Killed in this blast" list naming every enemy and friendly unit destroyed in the same blast. Flagged-only entries explain why the kill was not counted (damage below the lethal floor, no weapon recorded, an automated defence firing on its own, collateral that killed more enemies than friendlies, or a strike that destroyed a major enemy ship). The Banned tab shows the plugin and game ban lists plus a repeat-offender log. Every action (ban, unban, log, clear) is routed through the bot, which owns the reports and ban files.

**How to use it:** Click "Moderation" (a badge shows the report count). Click a report row for the detail card; use Ban / Unban / Log / Clear on a report. On the Banned tab, unban players (including by typing a SteamID) and view repeat offenders.

**Settings:** None.

### Leaderboard
Two-column rankings plus the cross-server rank-sharing control.

**How it works:** One column is this server's all-time ranks; the other is a combined board across your own servers. The combined board sums the rank files every server writes to a shared folder. The sharing card enables sharing and sets that folder, with a Validate step to check the path. This is purely a local shared-folder feature between your own servers (a local or network path they can all reach) - nothing is published anywhere public. Prestige carries across: a prestige earned on one of your servers shows on all of them. Skill ratings stay per-server.

**How to use it:** Click "Leaderboard." To combine ranks across your own servers, tick Enable sharing, enter a shared folder that each server points at, Validate, then Save.

**Settings:** None (the shared folder path is set in this card).

### Game Settings
Browse and change live plugin, bot, and server settings.

**How it works:** It merges the static settings catalogue with live values (plugin config from the dashboard, bot overrides from a local file). A category rail on the left lists ★ Common - the most-changed settings - plus every settings group with a count of what it holds; the search box filters across all of them. Changing a control stages the change and opens an inline confirm box under the setting; some changes add a contextual warning first - editing the admin SteamID list warns that a wrong value can lock admins out, and relaxing an anti-grief threshold past its recommended value warns that it is more lenient. Clicking Apply queues the change to the bot, which validates type, range, and enum against the catalogue. Each setting shows who owns it and whether it applies live or needs a restart. The "Rank + Fund catch-up" group is added by the dashboard at runtime, so it appears even when the shipped catalogue predates those settings. When the server is empty, the panel shows defaults with a note (the plugin pauses its reporting) and nudges it to dump fresh values; changes you make are still queued and apply.

**How to use it:** Settings menu → Game Settings. Pick a category on the left (or search), change the control, and click Apply in the confirm box. A "needs restart" badge means the change is saved but applies after a restart.

**Settings:** The individual game/plugin/bot settings are documented in the Settings reference. This panel is where you edit most of them.

### Server Config
Edits the dedicated server's config file - name, ports, max players, password, and other fields.

**How it works:** Fields are read from the server config over SFTP by the bot. Edits are written back by the bot; on Pterodactyl it also mirrors the matching panel startup variable. Passwords are masked. Each field shows a save badge as you edit: "saving…" while the bot confirms, "✓ saved" once the bot's copy matches, "✗ NOT SAVED" with the error if the bot rejects the value, and a "not confirmed" warning if no confirmation arrives in time - a failed save can never look like a success. An amber "● restart to apply" dot marks saved fields that only take effect after the next server restart.

**How to use it:** Settings menu → Server Config. Edit fields, then click "Restart server to apply" for name, ports, password, and max-players changes.

**Settings:** Uses the server config fields (see the config / install reference for `server_name`, `game_port`, `query_port`, `max_players`, `password`).

### Schedule (restarts and updates)
Schedules future server restarts or staged-plugin deploys, and shows the status of any update currently staged.

**How it works:** It shows whether a plugin update is staged for deploy. Items you add (a future date/time and a note) are validated and saved; the bot polls the schedule and runs each item at its time through the guarded deploy pipeline, warning players about 5 minutes and 1 minute before.

**How to use it:** Settings menu → Schedule (or click the header "Update staged" badge). Choose Restart or Update, pick a date and time and a note, then "Schedule it." Delete an item from the same list.

**Settings:** None.

### Messages
Edit the automated chat messages and the in-game `!help` list.

**How it works:** Three sections. First, toggle, retext, and retime the 13 built-in automated messages: the join/welcome message, the join "server is testing" notice, the "thanks for playing" reminder, the auto leaderboard post, the spectate/team-switch tip, rank-up announcements, the end-of-match "stay" reminder, the end-of-match summary, the rank-funds grant announce, the `!help` command itself, mission-time-remaining warnings, the victory announcement, and the start-of-match bonus announce. All ship enabled except the rank-funds announce (the plugin still grants the funds; only the chat line is off). Second, edit each command line in the `!help` list and show or hide it. Third, create custom automated messages with a trigger (every N minutes, daily at a time, or on match start/end) and per-word colour. Rich text uses the game's colour tags, rendered live. The bot re-validates and owns all of it.

**How to use it:** Settings menu → Messages. Toggle or edit the built-ins, edit `!help` lines, or write a new message, pick a trigger, colour words, and Add.

**Settings:** None (message text, timing, and triggers are edited in this modal).

### Killfeed editor
Controls the wording and visibility of every in-game killfeed line.

**How it works:** Eight feed lines are editable: the air-kill "splash" line, the underdog splash, teamkill, AI kill, went down, kill streak, ship sunk, and kill bonus. Each line has three modes - vanilla (the normal wording), custom (your own template), or off (hidden entirely). Custom text takes placeholder chips - `{killer}` `{killer_plane}` `{victim}` `{victim_plane}` `{weapon}` `{streak}` `{ship}` `{points}` - which you click to insert, plus the colour palette for colouring words, and a live preview renders the line with sample names as you type. Mode and text apply live and persist.

**How to use it:** Settings menu → Killfeed, or click "⚙ edit" on the Killfeed panel. Pick vanilla / custom / off per line; for a custom line, type the template, click placeholders to insert them, and colour words with the palette.

**Settings:** None (the per-line modes and templates are the settings; they are stored as `KillFeed.*` keys in the plugin config, edited only here).

### Ranks (Rank Ladder editor)
Edits the whole rank ladder and the rank-up announcement.

**How it works:** It loads the current ladder and lets you edit each rank's points threshold, title, abbreviation, and chat colour, plus the rank-up message template (with placeholders and a live preview). A "Prestige tag" field sets the rank tag shown for players who have prestiged: the template (`prestige_template`, default `[{abbr} - {n}*]`, up to 48 characters) takes the placeholders `{abbr}` `{rank}` `{n}` and must include `{n}`, the prestige count - so a rank-2-prestige Ace shows as `[ACE - 2*]`. Edits are shape-checked and queued to the bot, which is the sole validator. The lowest rank is pinned to the 0-point floor.

**How to use it:** Settings menu → Ranks. Edit, add, reorder, or remove ranks, edit the rank-up template and the Prestige tag, then click "Save ladder."

**Settings:** None (the ladder itself is the setting; it is persisted by the bot to `rank_ladder.json`).

### Updates
Shows installed versions and lets you check for, download, verify, and install an update.

**How it works:** It shows the installed toolkit and plugin versions and checks GitHub for a newer release; after a check, a per-component readout shows whether each part (bot, web Command Centre, plugin, installer) is current or has an update. "Download & install" downloads and verifies the release, then applies bot, web Command Centre, and installer updates immediately - backups are kept, and you restart the bot and web Command Centre to load them. The plugin is never installed directly: it is only staged, and deploys later via the Schedule modal, so an update can never surprise-restart a running match.

**How to use it:** Settings menu → Updates. Click "Check for updates," then "Download & install." Restart the bot and web Command Centre to load their updates; deploy the staged plugin via the Schedule modal.

**Settings:** None.

---

## How the data reaches the page

While the bot is running it publishes everything the dashboard needs to local files, so the dashboard needs no server credentials of its own. It writes a console mirror (every raw console line) and a dashboard state file (about once a second: mission and vote header, player table, ranks, planes, and match points). The page polls the state once a second and the resource graphs every 5 seconds. The live map, killfeed, and player positions are built from the plugin's live frames.
