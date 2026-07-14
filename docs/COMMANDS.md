# Commands

This page lists every command in the Nuclear Option toolkit: the in-game chat
commands players and admins type, and the admin/CLI commands the server owner
runs from the toolkit folder or the generated launchers.

---

## In-game chat commands

Players type these in the game's chat. Some are answered by the bot, and some are
handled by the NukeStats plugin (noted in the description). Where a reply is
private, only the player who asked sees it.

| Command | Who can run it | What it does |
| --- | --- | --- |
| `!help` | Any player | Shows the list of in-game server commands (private reply, delivered by the plugin). The list includes `!points`, `!squadup`, `!forfeit`, and `!notk`; owners can edit or hide each line — or turn `!help` off entirely — in the Web CC Messages modal. |
| `!rank` | Any player | Shows you your server rank, points, and progress to the next rank (private reply). Prestige-aware: after a prestige it shows your current cycle's points, at top rank it shows the points remaining to prestige, and it tells you when you can `!prestige`. |
| `!skill` | Any player | Shows your skill rating (average points earned per life) and who is ranked just above you (private reply). |
| `!points` | Any player | Shows how many points you earned this life versus your previous life (private reply). |
| `!leaderboard` | Any player | Shows the top 5 pilots by points and top 5 by skill, led with your own position (private reply). |
| `!why` | Any player | Lists your most recent points events and why each was awarded (private reply). Not shown in the `!help` list, but it works. |
| `!prestige` | Any player at top rank | Starts a prestige: once your current cycle's points reach the top rank's threshold (100,000 on the default ladder), your displayed rank resets to the bottom and your rank tag permanently shows a prestige star count, e.g. `[ACE - 2*]`. Points are never deleted; lifetime points, skill, and wins/losses carry over. Asks you to confirm with `!yes`. Not shown in the `!help` list, like `!why`. |
| `!yes` | Any player | Confirms a pending `!prestige` (the confirmation stays open for 60 seconds). |
| `!notk` | Any player | Explains the no-team-killing policy and the escalation steps (eject warning, then kick with rank reset, then ban). Text only; enforcement is done by the plugin. |
| `!balance` | Any player | Explains how PvP team balancing works (join the fuller side and you may be warned then moved to spectator; use `!swapteam`). |
| `!votemap` | Any player | Calls a mid-mission map change. With more than one player online it first opens a yes/no approval poll, then a numbered ballot to pick the replacement. |
| `!y` / `!n` | Any player | Votes yes or no during a `!votemap` approval poll. `!y` is also used to accept a `!squadup` invite. |
| `!vote N` or `!1` … `!6` | Any player | Casts your vote for map number N on an open ballot. Bare numbers without `!` are ignored. |
| `!spec` | Any player | Moves you to spectator. Handled by the NukeStats plugin. |
| `!swapteam` | Any player (bare) / Admin (with a name) | Bare `!swapteam` switches you to the other team, allowed only when the other side has **fewer** players; keeps your life, points, and kill-feed record. Admins (SteamID in `Admin.SteamIds`) can run `!swapteam <player>` to move a named player — the target is sent to spectator first, then swapped. Handled by the plugin. |
| `!squadup <player>` | Any player | Squads up with another player for PvP team-balance grouping; the invitee accepts with `!y`. Handled by the plugin. |
| `!forfeit` | Any player | Surrenders the current PvP match (majority vote needed). Handled by the plugin. |
| `!skyswap [player]` | Admin only | Moves the target (yourself, or a named player) to the **enemy** side — the other team in PvP, the AI faction in PvE, same-team only as a fallback — and drops them into a fully-armed aircraft high in the sky. Life-, points-, and kill-feed-neutral; the loadout cost is refunded. Handled by the plugin. |
| `!forceteamswap <player>` | Admin only | Swaps the named player to the other team **immediately** — no balance check and no spectate phase (unlike admin `!swapteam`). The player is ejected if flying, their team is flipped, and a brief high-altitude aircraft spawn + eject resets their game UI to the new team. Life-, points-, and kill-feed-neutral. Handled by the plugin. |
| `!setrank <player> <n>` | Admin only | Sets a player's in-game rank (the game's own in-mission rank, not the server ladder). Handled by the plugin. |
| `!setfunds <player> <amount>` | Admin only | Sets a player's in-game funds. Handled by the plugin. |
| `!addfunds <player> <amount>` | Admin only | Adds to a player's in-game funds. Handled by the plugin. |

**Note on admin actions from the dashboard:** Team moves, granting or deducting
rank points, kick, ban, and similar per-player actions are normally done from the
Web Command Centre (see the Panels documentation), not typed into game chat. The
`skyswap`, `swapteam`, `forceteamswap`, `setrank`, `setfunds`, and `addfunds`
verbs are also available there — in the command bar/palette and the player-actions
popup — and are relayed to the plugin (the dangerous force-swap asks to confirm).

---

## Admin / CLI commands

The server owner runs these from the toolkit `src` folder or through the
generated launchers. On Windows the wrapper is `run.bat`; on Linux the launchers
use shell scripts. Set `NOST_DATA_DIR` first (the generated launchers do this for
you) so every tool reads the same config and secrets the wizard wrote.

### Setup and launch

| Command | Who runs it | What it does |
| --- | --- | --- |
| `python installer/setup.py` | Owner | Launches the browser-based setup wizard on a localhost-only port to gather server details, pick features, write config, and install/deploy. |
| `python installer/setup.py --no-browser` | Owner | Same as above but prints the wizard URL instead of opening a browser. |
| `python installer/setup.py --data-dir <path>` | Owner | Runs setup writing `config.json` + `secrets.json` to the chosen folder instead of the default per-folder `.nost-data`. |
| `python installer/setup.py --force` | Owner | Runs setup overwriting a different server's existing config. |
| `install.bat` / `./install.sh` | Owner | Bundle entry point: changes to the bundle folder and runs the setup wizard (`installer/setup.py`), scoped to that bundle's hosting type by its `bundle_type.txt`. Windows tries `python` then `py`; Linux tries `python3` then `python`. Generated into each release bundle by the bundle builder, not shipped in `src/`. |
| `run.bat` | Owner | Starts the bot for this server folder (runs `python no_mapvote_bot.py`). |
| `webcc.bat` | Owner | Starts the Web Command Centre for this server folder and opens the local dashboard. |
| `python cc_web.py` | Owner | Runs the Web Command Centre server directly (dashboard at `http://127.0.0.1:8770` by default). |
| `START HERE\START THIS SERVER.bat` | Owner | Starts everything for this folder together (bot, Web Command Centre, and on an own-PC install the game server too). |
| `START HERE/start_this_server.sh` | Owner | Linux equivalent of the "START THIS SERVER" launcher. |
| `1. Start Bot.bat` / `2. Start Web Command Centre.bat` | Owner | Individual generated launchers to start just the bot or just the Web Command Centre. |

### Server / config subcommands (via `run.bat`)

| Command | Who runs it | What it does |
| --- | --- | --- |
| `run.bat --set-votekick on\|off` | Owner | Enables or disables the game's built-in player vote-to-kick (applies on config reload / next mission / restart). |
| `run.bat --rewrite-wrapper` | Owner | Rebuilds the launch wrapper after a `TICK_RATE` change; restart the game server afterward for it to take effect. |
| `run.bat --set-server-name` | Owner | Takes **no** arguments: rewrites `ServerName` in `DedicatedServerConfig.json` to the name constant baked into the bot (`NEW_SERVER_NAME` — edit that constant first to use a different name). Makes a local backup; takes effect on the next full server restart. To change the server name, use the Web CC Server Config modal instead — this is a legacy helper. |
| `run.bat --set-ai-limits [--dry-run]` | Owner | Takes no value arguments. Applies the bot's baked AI limits to every PvE co-op mission over SFTP: the AI side's aircraft limit → 8, its extra AI per enemy player → 0.75, and the player side's AI-ally limit → 6. PvP missions are skipped automatically. `--dry-run` previews without uploading. |
| `run.bat --set-balance-diff <n>` | Owner | Sets the team-balance threshold: `<n>` is a whole number 0–10. Writes `[Balance] MaxDifference` into the live plugin config (`BepInEx/config/anz.nukestats.cfg`) over SFTP; a running plugin picks the change up live. |
| `run.bat --apply-map-changes [--dry-run]` | Owner | Applies the toolkit's per-mission tuning to every PvE co-op mission (factory production times, wreck limits and decay) and pushes the derived `PostMissionDelay` (vote length + post-vote delay) into the server config. Mission enable/disable and rotation changes from the Web CC apply immediately and do **not** need this. Idempotent, with local backups; mission edits apply as each mission next loads. `--dry-run` previews. |
| `run.bat --add-rotation <Name> [Group] [MaxTime]` | Owner | Adds a mission to the rotation in `DedicatedServerConfig.json`. `Group` defaults to `User`, `MaxTime` to `10800.0` seconds. Skips if the entry already exists; makes a local backup; takes effect on the next full server restart. |
| `run.bat --deploy-plugin` | Owner (bot job) | The guarded deploy job (also run by the Web CC scheduler and the updater's `--deploy`): acts only when the server is confirmed **empty**, uploads the pending plugin DLL atomically, then stop → wait for offline (~90 s) → force-kill if the stop hangs → start → verify the game is actually serving. Any failure from the stop onward still forces a start, so the server is never left down. Replaces only the DLL — never the plugin config. |
| `run.bat --deploy-plugin-dry` | Owner | Full pre-flight of the deploy job with **no** power or upload actions — reports exactly what would happen. |
| `run.bat --put-atomic <local> <remote>` | Owner | Low-level command that atomically (mmap-safe) uploads a built DLL to the server over SFTP. This is a different operation from `--deploy-plugin`. |

### Updater (`installer/updater.py`)

| Command | Who runs it | What it does |
| --- | --- | --- |
| `python installer/updater.py check` | Owner | Reports what updates are available from the configured GitHub repo (full releases only). |
| `python installer/updater.py update` | Owner | Downloads, verifies (SHA-256 + signature), and stages an update. Defaults to the plugin component. |
| `python installer/updater.py update --component bot\|webcc\|all\|plugin` | Owner | Chooses which component to stage (bot, Web Command Centre, plugin, or all). |
| `python installer/updater.py update --apply` | Owner | Backs up and replaces the bot or Web Command Centre file with the staged version. |
| `python installer/updater.py update --deploy` | Owner | Runs the guarded plugin deploy (`run.bat --deploy-plugin`) after staging. |
| `python installer/updater.py update --i-understand-unsigned` | Owner | Overrides the refusal to stage a download whose signature could not be verified. |

**Related setting:** For a private update repo, set the `GITHUB_TOKEN`
environment variable before running the updater.
