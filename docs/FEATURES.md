# Features

Everything the Nuclear Option toolkit does, feature by feature.

- For the exact chat, admin, and `run.bat` commands, see **[COMMANDS.md](COMMANDS.md)**.
- For every setting's default and where to change it, see **[CONFIGURATION.md](CONFIGURATION.md)**.
- For moderation in depth, see **[MODERATION.md](MODERATION.md)**.
- For how the parts are wired together, see **[ARCHITECTURE.md](ARCHITECTURE.md)**.

The toolkit is three programs working together:

1. **A C# plugin (NukeStats)** that runs inside the game server. It reads real in-game events and enforces rules the stock server can't.
2. **A Python bot** that runs on your PC. It owns all saved data (ranks, points, match history, moderation) and sends commands to the server.
3. **A Web Command Centre** — a browser dashboard for live oversight and control.

Most settings apply live from the Web Command Centre. A few need a server or bot restart to take effect; those are marked **(restart)**. Each feature below lists only its most important settings — the full tables live in **[CONFIGURATION.md](CONFIGURATION.md)**.

---

## Progression & stats

### Server rank & points system
A permanent per-player rank ladder and lifetime points, with in-chat rank-up announcements.

**How it works:** Lifetime points are stored per SteamID in `ranks.json` and mapped onto an 11-tier ladder (Officer Cadet up to Air Chief Marshal). Crossing a threshold announces a colour-coded promotion in chat, and the plugin bakes the current rank into each player's chat name tag. The bot owns this data and snapshots it daily.

**How to use it:** Automatic. Players check their standing with `!rank` and `!leaderboard`. Owners edit the ladder (titles, thresholds, colours, and the rank-up message) in the Web CC **Ranks** modal, and can grant or adjust points from the command centre.

**Settings:**
- `rank_ladder.json` - default `built-in 11-tier ladder` - overrides the ladder tiers and rank-up message template (edited in the Ranks modal).
- `Scoring.WinPoints` - default `200` - points to every player on the winning side at match end.
- `Scoring.FirstPlace` / `SecondPlace` / `ThirdPlace` - default `500` / `250` / `100` - bonus for the match's top three scorers.
- Full list: **[CONFIGURATION.md](CONFIGURATION.md) → Scoring & Ranks**.

### NuclearSkill rating (points-per-life)
Rates each pilot by the average points they earn per life.

**How it works:** A "life" runs from spawn until the pilot is shot down or ejects mid-air. Score earned during the life is banked at death, and the rating is banked points divided by lives once the pilot has flown at least `SKILL_MIN_LIVES` lives. It is separate from rank and is what team balance uses to decide who to move.

**How to use it:** Automatic. Players view it with `!skill` (shown as a 0-10 figure).

**Settings:**
- `SKILL_MIN_LIVES` - default `5` - lives a player must fly before a skill rating is shown or counted.
- `Skill.CaptureBonus` / `Skill.WinBonus` / `Skill.LossBonus` - default `250` / `200` / `50` - skill points added for a capture, a win, and a loss.
- Full list: **[CONFIGURATION.md](CONFIGURATION.md) → Scoring & Ranks**.

### Shared cross-server ranks (same host)
Optionally combines ranks and points across several of your own servers into one board.

**How it works:** When enabled, each server writes its ranks to a shared local folder; the bot sums points, wins, and losses across all servers for the in-game `!leaderboard` and baked name tags. Skill rating stays per-server.

**How to use it:** Enable it and set the shared folder in the Web CC **Leaderboard** modal's sharing card, pointing every server at the same folder.

**Settings:**
- Shared-ranks folder path - default `off` - the local directory every server reads and writes (set in the Leaderboard modal).

> This is a local shared-folder feature between your own servers only. Prestige carries across servers too: each server publishes its prestige counts alongside its rank file, so a prestige earned on one of your servers shows on all of them.

### Prestige
A player who tops out the 11-tier ladder can prestige: their displayed rank cycles back to the bottom and their rank tag permanently shows a star count, e.g. `[ACE - 2*]`. Points are never deleted.

**How it works:** The bot banks each prestige in `prestige.json` (next to `ranks.json`; `ranks.json` itself is never edited). The displayed rank is driven by "cycle points" — the player's total minus the banked base — so after prestiging the player drops to the bottom rank and re-announces each rank on the way back up. Prestiging needs cycle points at or above the top rank's threshold (100,000 on the default ladder) and a `!yes` confirmation within 60 seconds. Lifetime points, skill rating, and wins/losses all carry over, and `!rank` stays prestige-aware (it shows cycle points, points to prestige, and lifetime points). The prestiged tag renders from a configurable template and flows into chat names and the kill feed automatically; the welcome message can show the star via a `{star}` placeholder. With shared ranks on, a prestige done on any of your servers shows everywhere. Prestige never lowers a player's in-game rank, so it never re-triggers rank funds.

**How to use it:** A player types `!prestige`, then confirms with `!yes` within 60 seconds (both are hidden from the `!help` list). Owners edit the prestige tag template in the Web CC **Ranks** modal's "Prestige tag" field.

**Settings:**
- `prestige_template` (`rank_ladder.json`) - default `[{abbr} - {n}*]` - the rank-tag format for a prestiged player; placeholders `{abbr}` `{rank}` `{n}`; must contain `{n}` (edited in the Ranks modal).
- `prestige.json` - automatic - the per-player prestige bank; sits next to `ranks.json` and is never merged into it.

### Rank catch-up (rising in-game start-rank floor)
Optionally raises the in-game starting-rank floor as a match runs, so latecomers and low-rank players are not stuck at the bottom of a long match. This is the game's own in-mission rank, not the server ladder.

**How it works:** The base floor is the mission's own starting rank (raised to `Mission.PvpStartingRank` on PvP maps). Every `PvpRankCatchupMinutes` of match time the floor rises by one rank, capped at `PvpRankCatchupMaxRank`. New spawns get the risen floor immediately; already-connected players below it are raised within about 15 seconds. It is a floor only — nobody is ever lowered — and each new floor is announced in chat. Resets on mission change. Off by default.

**How to use it:** Web CC **Game Settings → Rank + Fund catch-up** group. Set "Minutes per +1" above 0 to enable; both settings apply live.

**Settings:**
- `Mission.PvpRankCatchupMinutes` - default `0` (off) - minutes of match time per +1 to the starting-rank floor.
- `Mission.PvpRankCatchupMaxRank` - default `6` - the rising floor stops at this in-game rank.

### Rank funds (in-game money for ranking up)
Grants in-game money when a player's in-game rank goes up, with three payout policies. Pairs with rank catch-up so lifted players can afford aircraft.

**How it works:** The amount is ranks gained × `RankFundsPerRank`, in millions (30 = 30,000,000), paid the same way as admin `!addfunds`. The rank a player is first seen at is the baseline and grants nothing, and no rank is ever paid twice in a match (this survives reconnects; it resets on mission change). The mode decides when funds pay: `catchup_raised` (the default) pays only players the catch-up floor lifts, for the lift; `any_rankup` pays any player who reaches a new rank however they got there; `catchup_all` pays every connected player one rank of funds each time the floor steps up. An unknown mode falls back to the default. The bot can announce each grant in chat — the "Rank-funds grant announce" message in the Messages tab — but that message ships **off**, so out of the box funds are granted silently.

**How to use it:** Web CC **Game Settings → Rank + Fund catch-up** group. Both settings apply live. Set `RankFundsPerRank` to `0` to turn rank funds off entirely.

**Settings:**
- `Scoring.RankFundsPerRank` - default `30` - in-game funds per rank gained, in millions; `0` = off.
- `Scoring.RankFundsMode` - default `catchup_raised` - when funds pay: `catchup_raised` / `any_rankup` / `catchup_all`.
- `rankfunds` message (Messages tab) - default `off` - the optional chat announce, "+{funds} funds for reaching rank {rank}!".

### Vanilla award toggles
Five master switches turn individual bonus-point sources off, so a server can run scoring as close to vanilla as you like while ranks still display and carry.

**How it works:** Each bonus source has its own on/off switch stored in `award_config.json`: the PvP kill bonus, the underdog bonus (extra kill points when ranked below the victim), the start-of-match bonus, the per-capture bonus, and the match-end win/placement points. Turning a source off stops both the points and its announce line, without affecting rank display, `!rank`/`!leaderboard`, or cross-server carry of points already earned. All awards off + kill feed vanilla + all messages off = a vanilla server that still shows and carries ranks.

**How to use it:** Web CC **Game Settings → Scoring & Ranks** → flip the `Award.*` toggles. All default on. The panel shows a "needs restart" badge to be safe, but the switch is saved immediately and guaranteed after a bot restart.

**Settings:**
- `Award.KILL_BONUS_ON` / `UNDERDOG_ON` / `START_BONUS_ON` / `CAPTURE_BONUS_ON` / `WIN_POINTS_ON` - default `1` (all on) - per-award master switches (stored in `award_config.json`).

---

## Match flow & map voting

### Log-driven map-vote engine
Runs the automatic end-of-mission map vote and the player-called `!votemap` vote, then rolls the server to the winner.

**How it works:** The bot watches the console for a "Mission complete" line, which triggers a ballot. Each ballot is built from two pools — enabled PvE co-op maps and enabled PvP built-in modes — with a dark-map cap so at least one bright option is always offered. The winning mission is queued next and the current mission is cut short to roll over.

**How to use it:** Automatic on mission end. Players can also start it with `!votemap` (a majority yes/no approval poll opens first, then the numbered ballot). Owners tune it in the Web CC **Map Pool** modal.

**Settings:**
- `enabled` - default `true` - master kill-switch for the map vote.
- `coop_count` / `pvp_count` - default `4` / `2` - how many co-op and PvP options appear on the ballot.
- `MAP_VOTE_DURATION` - default `30` seconds - the one ballot length used for the end-of-match vote, `!votemap`, and the approval poll (live; see the vote-timing feature below).
- `POST_VOTE_MAP_CHANGE_DELAY` - default `15` seconds - gap after the ballot closes before the winning map loads (live).
- `ROLLOVER_SECONDS` / `POST_VOTE_COOLDOWN` - default `10` / `90` seconds - mission cut-over time and per-player anti-spam cooldown (bot constants; **restart**).
- Full list: **[CONFIGURATION.md](CONFIGURATION.md) → Match & Map Rotation**.

### Consolidated vote timing
Two owner knobs — ballot length and delay after the vote — replace the old three separate timing settings, and the server's `PostMissionDelay` is kept in sync automatically so a map change can never land before the ballot closes.

**How it works:** `MAP_VOTE_DURATION` (default 30 s) is the single ballot length for the end-of-match vote, `!votemap`, and the yes/no approval poll. `POST_VOTE_MAP_CHANGE_DELAY` (default 15 s) is how long after the ballot closes the winning map loads. The server's real `PostMissionDelay` is derived (vote + delay, so 45 s by default) and pushed to the server config automatically — at startup and again after every detected server restart. Both values persist in the deploy-protected `.nost-data/votemap_timing.json`, so code deploys can't reset them. Out-of-range edits are rejected with an explanation (vote 10–300 s, delay 5–300 s), not silently clamped.

**How to use it:** Web CC **Map Pool** modal (the vote-timing card shows a live "map loads X s after the mission ends" total) or **Game Settings → End of Match & Votes**. Both apply live; no bot restart. `PostMissionDelay` is hidden from the Server Config modal — never set it by hand.

**Settings:**
- `MAP_VOTE_DURATION` - default `30` - ballot length in seconds for every vote type (min 10).
- `POST_VOTE_MAP_CHANGE_DELAY` - default `15` - seconds after the ballot closes before the winning map loads (min 5).
- `PostMissionDelay` - derived (`vote + delay` = 45) - bot-owned; pushed automatically, not operator-settable.

### Default / boot map
An owner-chosen mission the server converges to after a (re)start and whenever a vote produces no eligible pick.

**How it works:** `votemap_config.json` gains a `boot_map` key (a mission name; empty = off, the server's own rotation decides). When the bot detects the server came back after a restart, or a ballot yields no eligible pick, it queues the boot map as the next mission. It is skipped (with an activity note) if the mission isn't enabled in the pool. Note it is a set-next-mission override: the literal first mission of a cold boot still comes from the game's own rotation; the server converges to the boot map at the next rotation.

**How to use it:** Web CC **Map Pool** modal → the "🚀 Default / boot map" dropdown; pick a mission, or "server rotation decides" to clear it.

**Settings:**
- `boot_map` (`votemap_config.json`) - default `""` (off) - mission queued after a server restart or a no-pick vote; must be enabled in the pool.

### Ballot weights & per-type match length
Weighted map selection and owner-set match lengths for the maps the bot queues.

**How it works:** Three optional weight tables shape the ballot: `coop_weights` (per co-op category), `pvp_weights` (per PvP mode), and `mission_weights` (per individual mission: `1` = normal, `2` = twice as likely, `0` = never offered unless pinned; the Web CC shows these as percentages). Guaranteed pins bypass weights. Two timers set the match length the bot assigns when it queues a map — `coop_minutes` for co-op/custom maps and `builtin_minutes` for built-in ops, scenarios, and PvP modes — so a built-in mission isn't stuck on the server's default; `MISSION_MAX_TIME` is now only a fallback. The PvP ballot pool also gains Altercation, Confrontation, Domination, and Carrier Duel alongside Escalation and Terminal Control.

**How to use it:** Web CC **Map Pool** modal — pin missions, open "Advanced: map appearance chance" to weight maps, and set the Co-op / Built-in match minutes.

**Settings:**
- `coop_weights` / `pvp_weights` / `mission_weights` - default `{}` (all equal) - relative appearance likelihood per co-op category, per PvP mode, and per individual mission.
- `coop_minutes` / `builtin_minutes` - default `180` / `180` - match length in minutes (10–600) the bot sets when queueing a co-op/custom vs built-in map.

### Mission & map pool management
Audits and edits the live mission rotation: enable/disable missions, add Workshop missions, upload custom missions, and set server-wide match rules.

**How it works:** Web CC actions and `run.bat` subcommands edit the server config and mission files over SFTP with deep-diff guards and a config reload. A curated list of official missions flags any unofficial or edited mission in the audit. New uploads and Workshop adds are staged and added **off** so you enable them deliberately.

**How to use it:** Use the Web CC **Map Pool** modal (Built-in / User / Workshop tabs, plus "Add Workshop ID" and "Upload mission folder"), or the `run.bat` mission subcommands (see COMMANDS.md).

**Settings:**
- `MISSION_MAX_TIME` - default `10800` - per-mission time limit in seconds; must match the server's `MaxTime` (bot constant; **restart**).
- `TICK_RATE` - default `60` - server engine tick rate in Hz; applied on the next server restart after `run.bat --rewrite-wrapper`.
- Full list: **[CONFIGURATION.md](CONFIGURATION.md) → Match & Map Rotation** and **AI & Performance**.

### Match milestones & mission-time warnings
Grants a kickoff participation bonus, posts "stay for the next match" reminders, and warns players as mission time runs out.

**How it works:** Everyone present within the start-bonus window of kickoff gets the start bonus once per match (de-duplicated across bot restarts). Set elapsed times trigger "stay for next match" reminders, and set remaining times trigger "time remaining" chat warnings, each fired once per mission.

**How to use it:** Automatic.

**Settings:**
- `START_BONUS_PTS` / `START_BONUS_WINDOW` - default `250` / `60` - kickoff bonus and the join window that qualifies for it (bot constants; **restart**).
- `STAY_MARKS` - default `6300/7500/8700s` - elapsed times that fire "stay for next match" reminders.
- `WARN_THRESHOLDS` - default `3600/1200/600/300/60` - remaining-time thresholds that fire mission-time warnings.

### PvP timeout result (real faction score)
When a PvP mission times out, the winner is the team with the higher faction score — the number players actually see on the scoreboard and join screen.

**How it works:** Shortly before the mission's time limit (120 seconds early by default, so the map vote can still run) the plugin compares each faction's own scoreboard total and announces the result; an exact tie is a draw. The announced totals match the in-game scoreboard.

**How to use it:** Automatic under `PvP.TimeoutResult`.

**Settings:**
- `PvP.TimeoutResult` - default `true` - decide and announce a winner when a PvP mission times out.

---

## Teams & squads

### PvP team balance
Keeps PvP team sizes close and moves players to even the sides, using skill rating rather than rank. PvE is never balanced.

**How it works:** The plugin runs the balancer on a timer and only acts once one side leads by more than the allowed gap, with enough humans present. Joining the fuller side is blocked; if a player *leaving* unbalances the teams, the lobby is warned, then one best-fit player is moved from the bigger side (keeping their points and skill-life via a brief over-ocean swap). New joiners and squads are protected, and a recently-moved player sits out the next couple of games. The bot supplies the explainer text and relays admin team actions to the plugin.

**How to use it:** Automatic. Players read `!balance`; admins run team moves and a one-off balance pass from the Web CC or in-game (see COMMANDS.md).

**Settings:**
- `Balance.Enforce` - default `true` - master on/off for PvP auto-balancing.
- `Balance.MaxDifference` - default `2` - how far one side may lead before balancing acts.
- `Balance.MinPlayers` - default `6` - never balance below this many humans.
- `Balance.WarnSeconds` / `Balance.NewJoinerSeconds` - default `300` / `900` - warn-and-wait before a move, and how long a fresh joiner is protected.
- `Skill.BalanceBySkill` - default `true` - pick who to move by skill rating instead of rank.
- Full list: **[CONFIGURATION.md](CONFIGURATION.md) → Team Balance**.

### Squads
Lets friends group up so team balancing tries not to split them; squads persist across restarts.

**How it works:** Players form a squad with `!squadup`; the group is saved to `plugin_squads.txt` and reloaded on start, and squadmates get a weak balance immunity so the balancer keeps them together.

**How to use it:** A player types `!squadup <player>`; the invitee accepts with `!y`.

**Settings:**
- `Squad.MaxSize` - default `4` - most players in a squad.
- `Squad.InviteSeconds` - default `90` - how long an invite stays open to accept.

---

## Moderation

Full detail, escalation ladders, and the ban workflow are in **[MODERATION.md](MODERATION.md)**.

### Team-kill (TK) moderation log
Records friendly-fire enforcement events into the moderation log and Web CC Reports tab, including how the kill happened.

**How it works:** The plugin enforces the friendly-fire ladder and reports each event to the bot, which logs the offender, the teammate killed, the offence count, the action taken, and the method (weapon name, SAM, CRAM, and so on). The log is nuke-aware: shockwave collateral is attributed to the launcher.

**How to use it:** Automatic. Owners view and clear entries in the Web CC **Moderation → Reports** tab.

**Settings:**
- `Teamkill.Enforce` - default `true` - turns the plugin's friendly-fire punishment ladder on or off.
- Full list: **[CONFIGURATION.md](CONFIGURATION.md) → Moderation**.

### Collateral-verdict friendly-fire judgement
Friendly-fire enforcement judges each friendly kill by what the same blast also killed, so a legitimate strike is never punished while deliberate team-kills still climb the warn/kick/ban ladder.

**How it works:** A friendly kill below the damage floor, with no resolvable weapon, or caused by an auto-engaging deployed defence or strategic launcher is reported but never counted. Otherwise the verdict waits for a munition-sized window (much longer for nuke-scale blasts, which are recorded at launch): only friendlies died = deliberate → the ladder; at least as many enemies as friendlies = collateral → reported to Moderation with the full per-blast unit list, not punished; overwhelming collateral (many enemies, few friendlies) = silent, log line only. Killing a big enemy objective (a carrier or other ship) in the same window exempts the friendly kill. Exonerating verdicts are capped per match so the system can't be farmed. With `Teamkill.CollateralEnforce` off, verdicts are still computed and logged but the classic ladder enforces regardless.

**How to use it:** Automatic; all settings apply live from Web CC Game Settings. The defaults are tuned — read **[MODERATION.md](MODERATION.md)** before changing them.

**Settings:**
- `Teamkill.CollateralEnforce` - default `true` - judge blast collateral before punishing: a friendly kill that came with equal-or-more enemy kills is reported, not punished. Off = log-only verdicts; the classic ladder enforces regardless.
- `Teamkill.MinDamage` - default `100` - minimum credited damage for a friendly kill to count at all (`0` = off).
- Full list: **[CONFIGURATION.md](CONFIGURATION.md) → Moderation**.

### Anti-grief command-flood auto-kick
Detects a single connection flooding unit-move commands to brick the server, and kicks, bans, or reports them.

**How it works:** The bot reads the game's own RPC rate-limit console lines; when one SteamID exceeds the flood threshold within the window (only for movement RPCs) it files a Reports entry and takes the configured action. A circuit breaker suppresses mass-kicks during server-wide congestion so a network storm never mass-kicks the lobby. The plugin's own anti-grief auto-kicks are also ingested.

**How to use it:** Automatic. Owners tune it and view entries in the Web CC **Reports** tab. Validate the threshold first with report-only mode.

**Settings:**
- `Flood.FleetOrdersPerSec` - default `1` - max accepted unit commands/sec; excess = drop + immediate kick.
- `Flood.FleetOrderBurst` - default `1` - token-bucket capacity (1 = no burst).
- `Grief.AutoKick` - default `true` - master on/off for detect + report + immediate kick.
- `Grief.RequireActiveFlooding` - default `true` - kick on order-rate excess alone (recommended), not on unit count.
- `Grief.ReportOnly` - default `false` - report but do not kick, to validate the threshold.
- Full list: **[CONFIGURATION.md](CONFIGURATION.md) → Anti-grief / flood protection**.

### In-game vote-kick
The game's own built-in player vote-to-kick.

**How it works:** A value in `DedicatedServerConfig.json` that the game reads; it applies on config reload, next mission, or restart.

**How to use it:** Run `run.bat --set-votekick on` (or `off`) from the toolkit folder.

**Settings:**
- `DedicatedServerConfig.VoteKick` - default `false` - enable/disable the game's built-in vote-to-kick.

---

## Nuke weapon tracking

### Nuke launch & collateral tracking
Tracks nuclear-weapon launches, attributes them to the launcher, and records blast collateral — the data behind kill attribution, the TK log, and stats.

**How it works:** Inside the NukeStats plugin, two spawn hooks record each missile's weapon and yield and feed launch attribution; a forward shockwave window records collateral kills with a per-blast victim list. The bot consumes these downstream events (kills, score, team-kills, wins) and builds the stats, leaderboard, and moderation on top.

**How to use it:** Automatic whenever the plugin is loaded.

**Settings:** None directly. The TK log's collateral cap applies only when collateral enforcement is enabled, which keeps the log accurate without changing enforcement policy.

---

## Chat & messages

### Chat formatting & kill feed
Rewrites player chat to show rank, blocks slurs, and replaces the spammy native kill feed.

**How it works:** These are live plugin settings read each server tick. Chat can carry the player's rank (either as a `[Name - Rank]` reformat or baked inside the name), a slur is replaced with a canned line while ordinary swearing is untouched, and the native kill-feed spam is suppressed in favour of kill streaks and ship-sink announcements for every ship class.

**How to use it:** Toggle in the Web CC **⚙ Settings → Game Settings** (applies live), or preset the on/off in the setup wizard.

**Settings:**
- `Chat.RankInName` - default `true` - puts the rank tag inside the player name so native chat and text-to-speech still work. Independent of the kill-feed mode: it works with both the custom and the vanilla kill feed.
- `Chat.Reformat` - default `true` - rewrites chat to name + rank in the rank colour (overridden by Rank-in-Name).
- `Chat.ProfanityFilter` - default `true` - replaces a whole message that contains a racist slur.
- `KillFeed.Custom` - default `true` - suppresses the native kill feed and shows the customizable feed instead. Does not affect rank tags. See the two kill-feed features below.

### Custom kill feed (per-line editor)
Every kill-feed line can be kept vanilla, rewritten with your own template, or turned off entirely — per line, live, with a dedicated editor in the dashboard.

**How it works:** Eight lines are configurable: `splash`, `splash_underdog`, `teamkill`, `ai_kill`, `went_down`, `streak`, `ship_sink`, and `kill_bonus`. Each has a Mode (`vanilla` / `custom` / `off`) and a Text template with placeholders `{killer}` `{killer_plane}` `{victim}` `{victim_plane}` `{weapon}` `{streak}` `{ship}` `{points}`, plus per-word colour via the game's `<color=#hex>` tags. An empty custom template falls back to the vanilla wording; `off` suppresses just that line (a kill still scores). The plugin renders its own lines and the bot renders the splash lines it composes, both from the same settings. The master `KillFeed.Custom` toggle gates everything: off = the native game feed only.

**How to use it:** Web CC **⚙ Settings → ☠ Killfeed** (or the kill-feed card's "⚙ edit" button). Pick vanilla/custom/off per line and write the template with the placeholder chips and live preview. Changes apply live and persist.

**Settings:**
- `KillFeed.<line>.Mode` (×8 lines) - default `vanilla` - vanilla wording, your custom Text, or off.
- `KillFeed.<line>.Text` (×8 lines) - default empty - the custom template; empty falls back to the vanilla wording.

### Kill-feed overhaul (1.0)
With the custom kill feed on, the feed announces every player shot-down, celebrates every ship class sink, and hides the native pilot-capture spam.

**How it works:** A player downed by a teammate gets a "shot down by" line; a kill by an AI unit gets its own line (strategic-launcher kills are coalesced rather than spamming); a crash or self-inflicted death shows as "went down"; enemy player kills keep the "splashed" line. Ship sinks announce for **every** ship class — carrier, destroyer, frigate, corvette, Argus, Dynamo, Shard, Cursor, and a generic fallback — each in its own colour. The native "pilot rescued/captured" spam is suppressed while the custom feed is on, and kill-streak callouts are unchanged (5/10/25/50, colour escalating). With the custom feed on, an aircraft's radar/map label shows the plane only, so a pilot's name appears once via their chat name. Rank-in-name is independent of the kill-feed mode: turning the custom feed off does not disable rank tags.

**How to use it:** All under `KillFeed.Custom` (default on) plus the per-line settings above.

**Settings:**
- `KillFeed.Custom` - default `true` - master switch: suppress the native feed and the pilot-capture line; show streaks, all-ship sinks, and the custom lines. Rank embedding is independent (`Chat.RankInName`).

### Automated & custom server messages
Posts built-in periodic messages, your own custom messages, and lets you edit the in-game `!help` list.

**How it works:** Built-in messages can be re-worded, re-timed, or disabled — the welcome, the join "server is testing" notice, thanks/commands, the auto leaderboard, the spectate tip, rank-up announcements, the end-of-match "stay" reminder and match summary, the rank-funds announce, `!help` itself, the mission-time-remaining warnings, the victory announcement, and the start-of-match bonus announce. Hiding an announce never stops the underlying points or enforcement. Custom messages fire on an interval, daily at a set time, or on match start/end, with optional per-word colour. The `!help` list is built from a registry where each line's text is editable and each command can be shown or hidden.

**How to use it:** Edit everything in the Web CC **Messages** modal.

**Settings:**
- `system_messages.json` - default `all enabled except rankfunds` - per-message enable / text / interval / delay overrides for the 13 built-in messages.
- `THANKS_INTERVAL` / `LEADERBOARD_INTERVAL` / `SPECTIP_INTERVAL` - default `900` / `1800` / `1020` seconds - default cadence of the periodic messages.
- `help_config.json` - default `all shown` - show/hide individual `!help` commands.

### Join / welcome announcements
Welcomes players shortly after they join and logs joins, leaves, and online counts.

**How it works:** A roster poll updates the name cache; a new SteamID is queued for a delayed welcome (with their rank and points) once their name is known, de-duplicated per session.

**How to use it:** Automatic. The welcome line is a customizable message with `{name}`, `{rank}`, `{pts}`, and `{star}` (the prestige star) placeholders (Messages modal).

**Settings:**
- `JOIN_POLL_INTERVAL` / `WELCOME_DELAY` - default `5` / `5.0` seconds - how often the roster refreshes, and the delay before welcoming a new joiner.
- `LOG_CONVERSATION` - default `True` - log player chat and bot replies to the activity log (`False` = curated events only).

### Plugin version notice on join
Each player gets a one-time private chat line shortly after joining: "Nuke-Option Plugin Version &lt;version&gt; is active on this server."

**How it works:** The plugin whispers the line about 6 seconds after a player is first seen (so their chat UI is ready), once per session; a rejoin shows it again.

**How to use it:** Automatic; there is no setting. Documented so you know where the line comes from.

**Settings:** None.

---

## Admin tools (in-game)

### Sky-drop (`!skyswap`)
Admin-only command that moves a target to the **enemy** side and drops them into a fully-armed aircraft high in the sky.

**How it works:** The plugin first moves the target to the enemy side — the other joinable team in PvP, the AI faction in PvE — falling back to a same-team drop only when no enemy destination exists. It then spawns the configured armed jet at altitude with forward speed, facing the map centre, over the destination faction's own drop point, and refunds the loadout cost so the move is life- and points-neutral. Per-map, per-faction drop points decide where swapped and sky-dropped players appear; the same points position the team-swap aircraft (below), so a swapped player always lands over their own side.

**How to use it:** In-game admin command `!skyswap [player]` (targets yourself or a named player), or `skyswap <player>` from the dashboard command palette / the player popup's "🌤 Sky drop (enemy team)" button.

**Settings:**
- `Admin.SkyAircraft` - default `Ifrit` - which aircraft to spawn (KR-67 Ifrit).
- `Admin.SkyAltitude` - default `12000` - spawn altitude in metres.
- `Admin.SkySpeed` - default `180` - launch speed (m/s) so the jet doesn't stall on spawn; `0` = drop stationary.
- `Admin.SkyPrimaryWeapon` / `SkySecondaryWeapon` / `SkySecondaryStations` - default `Scimitar` / `Scythe` / `1` - the weapons loaded (empty = default loadout) and how many stations get the secondary.
- `Admin.SkyDropHeartlandPala` / `SkyDropHeartlandBdf` - default `-5000,-15000` / `-5000,45000` - drop point (x,z metres) per team on Heartland (PALA north / BDF south).
- `Admin.SkyDropIgnusPala` / `SkyDropIgnusBdf` - default `-75000,0` / `75000,0` - drop point (x,z metres) per team on Ignus (Carrier Duel counts as Ignus). Malformed values fall back to a safe open-ocean point.

### Team swap (`!swapteam`, `!forceteamswap`)
Moves a player to the other team without losing their life, points, or kill-feed record.

**How it works:** The swap flips the player's faction server-side, then spawns a brief CI-22 Cricket at `Swap.Altitude` over the destination team's own drop point and ejects — which resets the client UI to the new team. Bare `!swapteam` is public self-service: any player can switch, but only to the team with **fewer** players. Admin `!swapteam <player>` sends the target to spectator first, then swaps; `!forceteamswap <player>` swaps immediately with no balance check. Auto-balance uses this same swap to move its pick straight to the smaller team instead of dumping them to spectate. All three verbs are also wired through the Web CC (player popup and command palette; force-swap asks to confirm).

**How to use it:** In-game: `!swapteam` (self), or admin `!swapteam <player>` / `!forceteamswap <player>`. Dashboard: the player popup's "⇄ Swap team" button or the command bar.

**Settings:**
- `Swap.Altitude` - default `2500` - altitude (m) of the brief Cricket during a swap; raise to 3000 if any embed/crash is seen.

### Admin rank & funds commands (`!setrank`, `!setfunds`, `!addfunds`)
Admins can set a player's in-game rank and set or add their in-game funds.

**How it works:** Plugin-handled admin chat commands (your SteamID must be in `Admin.SteamIds`), also available from the Web CC command palette. They act on the game's own in-mission rank and money, not the server rank ladder.

**How to use it:** `!setrank <player> <n>`, `!setfunds <player> <amount>`, `!addfunds <player> <amount>` — or the same verbs in the dashboard command bar.

**Settings:** None.

---

## Web Command Centre

### The dashboard server & how it is secured
A local Flask app that serves the single-page dashboard plus a JSON API, giving you a live browser view and full control of one server.

**How it works:** The web process reuses the running bot's command relay and data (the bot stays the single owner of ranks and moderation). The page polls the server state every second and resource stats every five seconds.

**How to use it:** Run `python cc_web.py` (or the generated `webcc.bat`), then open `http://127.0.0.1:8770`.

**Settings:**
- `web.port` (env `PORT` / `NOCC_PORT`) - default `8770` - the port the dashboard listens on.
- `web.host` (env `NOCC_HOST`) - default `0.0.0.0` (all interfaces / LAN-reachable) - set `127.0.0.1` to lock it to this PC only.

> **Security:** there is **no login or authentication** on any route. Access control is entirely the network bind, and the default bind is `0.0.0.0` (LAN-reachable, not loopback). Anyone who can reach the port has full admin control — power, bans, config edits, granting points. To restrict access, set `web.host` to `127.0.0.1` (or `NOCC_HOST=127.0.0.1`).

### Live status header
Top-of-page chips showing server online/stale/offline, current mission, time left, player count, and the live plugin and toolkit versions.

**How it works:** Reads the state feed each second and colours the status dot from server freshness; the time-left clock ticks down smoothly between samples. A red "Data is stale" banner appears if the feed stops updating.

**How to use it:** Read-only; always visible.

**Settings:** None.

### CPU / memory / FRAME graphs
Three header sparklines with numeric readouts: server CPU %, memory, and FRAME — the server's smoothed frame time in milliseconds.

**How it works:** CPU and memory poll resource stats every five seconds from the Pterodactyl client API, or a local running/offline probe when power mode is local. FRAME comes from the plugin's connection-health telemetry: it samples real per-frame deltas and publishes a ~1-second smoothed average, updated once a second with the state poll. It shows green under 22 ms (about 45 Hz or better), amber to 50 ms, red beyond, and an em-dash when there is no reading yet.

**How to use it:** Read-only. CPU/MEM show "no panel" if Pterodactyl power control isn't configured; watch the FRAME number and colour for server load.

**Settings:** None.

### Live tactical map
A pan/zoom battle map over a calibrated terrain image, drawing player, AI-aircraft, and ship blips, faction bases, and a coordinate grid.

**How it works:** The bot publishes live positions from the plugin (flying players, AI aircraft, ships). Blips glide smoothly between the ~1-second poll anchors; players are named and team-coloured, AI aircraft are unnamed so you can tell bot from human.

**How to use it:** Scroll or drag to zoom and pan; press F or the fullscreen button for a fullscreen view with flanking kill feed and player panels (Esc to exit).

**Settings:** None.

### Air-traffic panel
A strip under the map showing total aircraft flying and per-faction AI-vs-player counts, with caps highlighted when reached.

**How it works:** Consumes the plugin's per-side aircraft-count feed and only redraws when the numbers change.

**How to use it:** Read-only; hidden when there is no air data.

**Settings:** None.

> This panel is fed by the AI aircraft limiter's live counts (the `AILimit` performance precaution): per-side AI and player aircraft numbers plus the per-team and total caps. If `AILimit.Enforce` is turned off, the feed stops and the panel hides.

### Kill feed
A live list of recent kills with time, victim, killer, weapon, and grid coordinate, with team-kills flagged.

**How it works:** Assembled from kill/down/life events with last-known positions; the killer can be a player, an AI aircraft, or a ground unit such as a SAM, named where known. Team-kill detection is plugin-authoritative; environmental deaths show as "went down". Unit and weapon names are tidied but keep the specific model.

**How to use it:** Read-only (its own left-column card, with a "⚙ edit" button that opens the kill-feed editor, and flanking the fullscreen map). Click a row to copy it.

**Settings:** None.

### Players table
A sortable roster of online pilots.

**How it works:** Shows name, faction, saved rank and points, aircraft, in-game rank, match points, skill rating, and live grid coordinates, redrawing only when something changed so focus and hover survive.

**How to use it:** Click any row to open the player-actions popup.

**Settings:** None.

### Player-actions popup
A per-player action sheet reached from the players table.

**How it works:** Each button routes its action through the bot: grant or deduct rank points, move to Boscali or Primeva, send to spectator, copy SteamID, kick, or ban.

**How to use it:** Click a player row, then type points and Grant, or use the team/spectate/kick/ban buttons (kick and ban confirm first).

**Settings:** None.

### Activity feed
A colour-coded, chronological event log of chat, joins/leaves, wins, rank-ups, votes, map changes, moderation, and system lines.

**How it works:** Tails the bot's activity log; kill lines are dropped (they live in the kill feed). Category filter chips are remembered in the browser.

**How to use it:** Toggle the category chips in the card header to hide or show line types; click a line to copy it.

**Settings:**
- Category filter chips - default `all on` - client-side show/hide of activity line types (remembered per browser).

### Console panel
A mirror of the server console with noise filtering.

**How it works:** The backend tails the console mirror, classifies lines, and collapses benign spam into a summary so only real output and errors surface. You can left-click a line to hide future lines like it.

**How to use it:** Use the filtered/raw toggle to switch views; left-click a line to add a filter; the filters button lists and clears your custom filters.

**Settings:**
- Raw vs filtered view - default `filtered` - show every raw line, or the noise-filtered view.
- `console_filters.json` - default `none` - patterns added from the console context menu to hide matching lines.

### Command bar & palette
A command console to run server and bot commands with type-ahead autocomplete.

**How it works:** Offers server wire commands and local bot commands (raw operational verbs hidden), with Tab autocomplete for missions, players, factions, and numbers. Stateful commands route through the bot's admin queue; dangerous ones confirm first.

**How to use it:** Type a command (Tab to complete, Enter to run), click "≡ all commands" for the full palette, or "🔁 Change map" to end the match and switch now.

**Settings:** None.

### Map Pool modal
Configures the map vote and which missions are eligible, and adds new missions.

**How it works:** Tune the ballot (co-op/PvP counts, mix, guaranteed pins, high-population PvP forcing, vote length), toggle built-in / custom / Workshop missions, add a Workshop ID, or upload a mission folder (staged and added off). The bot is the sole validator of these changes.

**How to use it:** Click **🗺 Map Pool** in the header.

**Settings:** See the map-vote engine and mission-pool features above, and **[CONFIGURATION.md](CONFIGURATION.md) → Match & Map Rotation**.

### Moderation modal
Reports and banned-players management.

**How it works:** A Reports tab (anti-grief and team-kill events with method, unit count, rate, and action) with ban/unban/log/clear, plus a Banned tab (plugin and game ban lists) with a manual-SteamID unban box and a repeat-offender log. All actions route through the bot, which owns the ban files.

**How to use it:** Click **🛡 Moderation** (the badge shows the report count). See **[MODERATION.md](MODERATION.md)**.

**Settings:** None.

### Leaderboard modal
This server's rankings, plus an optional combined cross-server board.

**How it works:** Shows this server's all-time ranks and, when cross-server sharing is on, a combined board aggregated from every server's rank file in the shared folder. A card enables and configures the sharing folder.

**How to use it:** Click **🏆 Leaderboard**. To combine ranks, tick Enable sharing, set a shared folder each server points at, Validate, then Save.

**Settings:** See the shared cross-server ranks feature above.

### Game Settings modal
Browse and change live plugin, bot, and server settings.

**How it works:** Merges a static settings catalogue with live values, with Common/Advanced views, category chips, search, per-setting detail (owner, live-vs-restart, "affects gameplay" badges), and confirm-to-apply that validates type and range. A "needs restart" badge means it is saved but applies after a restart.

**How to use it:** **⚙ Settings → Game Settings**; pick a setting, change the control, click Apply.

**Settings:** This is the front door to most settings — see **[CONFIGURATION.md](CONFIGURATION.md)**.

### Server Config modal
Edits `DedicatedServerConfig.json` fields.

**How it works:** Loads the config over SFTP; edits (server name, ports, max players, password, and other fields) are written back by the bot and, on Pterodactyl, mirrored to the matching panel startup variable. Password inputs are masked; name/port/password/max-player changes need a restart.

**How to use it:** **⚙ Settings → Server Config**; edit fields, then "⟳ Restart server to apply".

**Settings:** See **[CONFIGURATION.md](CONFIGURATION.md) → Server / connection config**.

### Schedule modal
Schedules future restarts and staged-plugin update deploys.

**How it works:** Items are written to a schedule the bot polls; at the target time the bot warns players 5 and 1 minute before, then runs the guarded restart or deploy. It also shows the status of any plugin update currently staged.

**How to use it:** **⚙ Settings → Schedule** (or click the header "Update staged" badge). Pick Restart or Update, a date/time, and a note.

**Settings:** None.

### Messages modal
Edits the automated chat and the `!help` list.

**How it works:** Three sections — toggle/re-word/re-time the built-in messages, edit and show/hide each `!help` command, and create custom messages with triggers (interval, daily clock, match start/end) and per-word colour rendered live.

**How to use it:** **⚙ Settings → Messages**.

**Settings:** See the automated-messages feature above.

### Ranks modal
Edits the whole rank ladder and the rank-up announcement.

**How it works:** Edit each rank's points threshold, title, abbreviation, and chat colour, plus the rank-up message template, with a live preview. The lowest rank is pinned to the 0-point floor. The bot is the sole validator.

**How to use it:** **⚙ Settings → Ranks**; edit ranks and the template, then Save ladder.

**Settings:** See the rank & points feature above.

### Updates modal
Shows installed versions, checks GitHub for a newer release, and downloads and installs updates.

**How it works:** Reports the installed toolkit and plugin versions, checks the configured GitHub repo for the latest full release with a per-component current-vs-update readout, and downloads + verifies + installs it. Bot, web Command Centre, and installer updates apply immediately (backups kept; restart the bot and web CC to load them). The plugin is only staged and deploys later via Schedule, so a match is never surprise-restarted.

**How to use it:** **⚙ Settings → Updates**; "Check for updates", then "⬇ Download & install". Restart the bot and web CC to load their updates; deploy the staged plugin via Schedule.

**Settings:** See the GitHub updater feature below.

### Power controls (with Kill and self-healing Restart)
Header buttons to start, restart, stop, or force-kill the game server.

**How it works:** Each button confirms, then drives the Pterodactyl client power API, or a local process path when power mode is local. Restart is self-healing: it stops the server, waits up to ~90 seconds for it to go offline, force-kills it if the graceful stop hangs, then always starts it — so a zombie process can never block a restart. Kill hard-stops the process immediately (it has its own stronger warning); use it only when a normal Stop hangs. The clicked button pulses amber until the server is confirmed in the requested state, then flashes green.

**How to use it:** Click ▶ Start / ⟳ Restart / ■ Stop / ✖ Kill. Buttons disable when Pterodactyl isn't configured (unless local power).

**Settings:**
- `server.power` - default `pterodactyl` (or `local`) - control the server via the panel API, or as a local process on this PC.

### Dashboard chrome (settings menu, search, badge, theme)
Small convenience controls in the header.

**How it works:** The **⚙ Settings** dropdown opens the seven configuration modals (including the ☠ Killfeed editor); the **Find a setting** search jumps straight to any Game Settings or Server Config field; a pulsing **Update staged** badge appears when a plugin update differs from what's live; the **🌙 / ☀** button flips the whole dashboard between dark and light.

**How to use it:** Use the header controls at the top of the page.

**Settings:**
- `nocc_theme` - default `dark` - the chosen light/dark theme (remembered per browser).

---

## Server management & lifecycle

### Scheduled restarts, updates, power & keepalive
Runs owner-scheduled restarts and plugin updates, controls server power, and self-recovers from crashes.

**How it works:** The bot polls the schedule, warns players 5 and 1 minutes before a due restart or update, then runs the guarded deploy job at the target time. The guarded job only acts when the server is confirmed empty, uploads any pending plugin DLL atomically, then stops the server, force-kills it if the graceful stop hangs (waiting up to ~90 s, then up to ~45 s more after the kill), starts it, and verifies the game is actually serving — any failure from the stop onward still forces a start, so the server is never left down. It replaces only the DLL, never the plugin config. Server power runs through the Pterodactyl client API (or local process). The bot's main loop auto-restarts itself, and an external keepalive script covers hard process death.

**How to use it:** Schedule restarts and updates in the Web CC Schedule modal; use the header power buttons for immediate control.

**Settings:** Scheduling and power creds live in the toolkit config — see **[CONFIGURATION.md](CONFIGURATION.md)**.

### Dashboard data feed
Publishes everything the Web CC needs to local files, so the dashboard needs no server credentials of its own.

**How it works:** The bot writes a console mirror and a state file (about once a second: mission and vote header, player table, ranks, planes, match points) that the Web CC and legacy terminal viewer read. Live-map positions and the kill feed come from the plugin's position and event frames.

**How to use it:** Automatic while the bot runs.

**Settings:** None.

---

## Installation & running

Step-by-step install lives in each download bundle's own README; this is what each path is.

### Setup wizard
A guided, offline, browser-based wizard that gathers your server details, picks features, writes config and secrets, and installs.

**How it works:** It starts a localhost-only server guarded by a random per-run token and opens the wizard page. A pre-assembled bundle scopes the wizard to its one hosting type (5 steps); a full-repo run shows the multi-option wizard (7 steps). It writes `config.json` (no secrets), a separate `secrets.json` (locked-down permissions), the plugin config, and the server config. Nothing phones home except the Test buttons and the actual upload.

**How to use it:** From the toolkit folder, `python installer/setup.py` (add `--no-browser` to print the URL, `--data-dir <path>` to choose where config lives). In a bundle it is wrapped by `install.bat` / `install.sh`.

**Settings:**
- `--data-dir <path>` (env `NOST_DATA_DIR`) - default `<server folder>/.nost-data` - where `config.json` and `secrets.json` are written (per-folder, so sibling installs don't clash).

> **Real dependency:** Python 3.8+, plus `paramiko` (for any SFTP path), Flask (dashboard), and `requests` (bot/updater). The wizard's Welcome step has a button to pip-install these.

### Pterodactyl install & run (primary path)
Installs the toolkit onto a Pterodactyl-panel-hosted server over SFTP, then runs the bot and Web CC on your PC. The most-used, primary path.

**How it works:** You enter panel SFTP details and optional power-control credentials. The installer pushes the BepInEx loader, the NukeStats plugin, the bundled missions, and the in-container relay, writes the server and plugin configs, installs a self-injecting launch wrapper so the server boots modded with no panel edits, and (if a power key was given) restarts the server. A second panel allocation is used for the relay.

**How to use it:** Unzip the Pterodactyl bundle, run `install.bat` / `install.sh`, enter SFTP + API details, set ports and relay port, click "Install to my server", then Launch.

**Settings:**
- `relay port` (`server.rcmd_port`) - default `5550` - the public TCP port the in-container relay listens on (needs a second panel allocation).

> Uses a Pterodactyl **client** API key (starts `ptlc_`), not an application key. No IP is uploaded anywhere.

### Local install & run (own PC) — beta
Runs the whole community server on your own Windows or Linux PC: the dedicated server, the plugin, the missions, the bot, and the Web CC, launched together. Marked beta / lightly tested.

**How it works:** The wizard installs the dedicated server via SteamCMD, copies the plugin and missions into the game folder, writes the server config, and generates a launcher that boots the game server, bot, and Web CC together. Power mode is local; the bot reaches the game over `127.0.0.1` (no port-forwarding needed for the relay).

**How to use it:** Unzip the Local bundle, run `install.bat` / `install.sh`, follow the wizard (install/point to the server, set ports, server name, admin SteamID64), then Launch — or later double-click the generated START THIS SERVER launcher.

**Settings:**
- `RemoteCommand port` - default `5504` - the port the game server opens for admin commands (reached over `127.0.0.1`).

> For internet play, forward the game and query ports (default 7777 and 7778) as UDP.

### Manual install (host it by hand) — beta
For owners who run the dedicated server themselves and want to add the toolkit by hand. Marked beta / lightly tested.

**How it works:** The bundle ships both Windows and Linux BepInEx packs, the plugin, the missions, and the bot/Web CC, plus drag-and-drop instructions. The wizard scoped to "manual" only writes the admin config and plugin config — it does not upload; you place the game-side files and startup line yourself from the bundle README.

**How to use it:** Unzip the Manual bundle, follow its README to place the game-side files and startup line, run the installer to write your config, then start the bot and Web CC.

**Settings:** None beyond the shared config (see **[CONFIGURATION.md](CONFIGURATION.md)**).

### Launchers, run & keepalive
Per-server, folder-safe launchers that start the bot and Web CC (and, on your own PC, the game server) and can be restarted safely without touching a sibling install.

**How it works:** The wizard generates `run.bat` (bot), `webcc.bat` (Web CC), and a START THIS SERVER launcher. Every process kill is scoped to this folder's path (never by name), so a second server in a sibling folder is never touched. Each launcher sets a per-folder data dir and pins the Web CC port.

**How to use it:** Double-click the START THIS SERVER launcher to start everything, or `run.bat` / `webcc.bat` for one piece. The bot also forwards admin and deploy subcommands (see COMMANDS.md).

**Settings:**
- `NOST_DATA_DIR` - default `<folder>/.nost-data` - the per-folder config/secrets dir every launcher sets.
- `NOCC_PORT` - default `= web.port (8770)` - pins the Web CC port for this folder.

> `run.bat` is generated by the wizard and holds the SFTP password, so it is not shared. Set `NOST_DATA_DIR` for any manual `run.bat`/updater call so it reads the same data the wizard wrote.

### Opt-in GitHub updater
A separate, by-choice tool to pull plugin, bot, and Web CC fixes from GitHub with verify-before-apply. The install itself is fully offline; nothing auto-updates.

**How it works:** It fetches the latest full GitHub release only (drafts and pre-releases are ignored), verifies the download against a published checksum and signature, and stages it. It refuses to stage an unverified download unless explicitly overridden.

**How to use it:** `python installer/updater.py check` to see what's available, `update` to stage the plugin, `--component bot|webcc|all`, `--apply` to replace the bot/Web CC file. Deploy a staged plugin through the Web CC Schedule modal. Set `NOST_DATA_DIR` so it reads the config the wizard wrote. For a private repo set `GITHUB_TOKEN`.

**Settings:**
- `update.github_repo` - default `the official repo` - which repo the updater reads releases from (change only for your own fork).
- `update.auto_check` - default `false` - check for updates on launch (still asks before applying).

> The staged plugin is applied by the bot's guarded deploy job: run `run.bat --deploy-plugin` (preview first with `run.bat --deploy-plugin-dry`), or schedule it in the Web CC Schedule modal — `python installer/updater.py update --deploy` runs the same job. The separate `run.bat --put-atomic <local> <remote>` is a lower-level atomic (mmap-safe) DLL upload — a different operation. See COMMANDS.md.

### Settings preservation on re-deploy
What is kept versus overwritten when you re-run the installer or deploy a new plugin.

**How it works:** `DedicatedServerConfig.json` is backed up to a timestamped copy and merged, so your existing values survive. The bot owns ranks and skill and snapshots them daily. The plugin config file, however, is currently rewritten from the wizard-rendered text on a re-deploy that supplies new text, without a key-level merge in that path.

**How to use it:** Re-run the installer or updater. Note down any hand-tuned plugin settings before a re-deploy until the merge behaviour is finalized.

**Settings:** None.

> A plugin **update or deploy** (`run.bat --deploy-plugin`, scheduled or manual) replaces only the DLL and never touches `anz.nukestats.cfg`, and every Web CC settings edit writes single keys — so hand-tuned plugin settings survive plugin updates. A full **re-install through the wizard**, however, rewrites the plugin config from your wizard choices, with no backup or key-level merge in that path. `DedicatedServerConfig.json` is merged; the bot owns ranks and skill. So: note down hand-tuned plugin settings before re-running the installer — but not before a plugin update.
