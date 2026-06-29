# Design History & Rationale — *how each part came to be*

This companion to [ARCHITECTURE.md](ARCHITECTURE.md) explains **why** the toolkit is shaped the way it is. Almost every feature here exists because of a concrete problem on a live community server — an exploit someone found, a crash that dropped the whole lobby, a metric the game refused to expose. Reading this first makes the architecture make sense.

---

## 0. The founding constraint: *Nuclear Option hides per‑player score*

The single fact that everything else is built on top of: **the dedicated server exposes no per‑player score or stats to admins.** There is no scoreboard API, no kill log you can query, no "who's winning" endpoint. A vanilla dedicated server is a black box that runs a mission and forgets everyone the moment it ends.

That ruled out the obvious "just read the API" approach and forced the three‑process design:

1. **A server‑side plugin** (C# BepInEx/Harmony) that runs *inside* the game process, where the score data actually lives, and shouts it out as structured log lines.
2. **A bot** on the admin's PC that tails those log lines over SFTP, turns them into persistent ranks/skill, and sends commands back.
3. **A web command centre** so the admin doesn't have to live in a terminal.

Everything below is a consequence of that split.

---

## 1. The plugin: reading score the only way that works

### Why a BepInEx/Harmony plugin at all
Since the data is only visible *inside* the game process, we inject into it. BepInEx loads the plugin; Harmony patches the game's own methods. The plugin reads `Player.PlayerScore` directly — **the only per‑player metric the game reliably maintains** — and patches `FactionHQ.RewardPlayer` (the single funnel every kill/capture/refuel/repair reward passes through) so we see every gain exactly once.

### The two scars in the bootstrap code
Two non‑obvious decisions both came from painful debugging and are deliberately preserved:

- **`Debug.Log`, not `Console.WriteLine`.** The bot reads the server's `-logFile` (console.log). `Console.WriteLine` only reaches process stdout, which the bot never sees. Early versions emitted stats that silently went nowhere until we traced it to this. Every `[NOSTATS]` line therefore goes through Unity's `Debug.Log`.
- **No `OnDestroy`/`UnpatchSelf`.** On a headless dedicated server the plugin's manager GameObject is destroyed shortly after `Awake`. We originally unpatched in `OnDestroy` to be tidy — and it was tearing out *every* Harmony hook seconds after we applied them (a debug trace showed the methods re‑patched with **0 prefixes**). Harmony patches are static and process‑lived, so the fix was to **never unpatch**. This is also why the snapshot loop is driven off a Harmony hook on `FactionHQ.Update` rather than the plugin's own `Update()` — the latter never ticks once the manager object is gone.

### Scoring model: real score + bonuses, then a series of exploit patches
The rank economy started simple — accumulate each player's real in‑game score across matches, plus win/placement bonuses — and then hardened reactively as players found holes:

- **The score‑explosion exploit (2026‑06‑24).** A *game‑side* score/money payout could be triggered repeatedly, and the bot was banking it 1:1 with no clamp, blowing several players' totals into the hundreds of thousands. This drove the points **ledger with category tracking** (so every point is attributable) and a **score‑spike tripwire**. (A hard clamp was discussed and deferred; the ledger is the audit trail that lets us undo abuse precisely instead.)
- **Radar/spotting + jamming suppression.** Some reward types (Recon, Jamming) could be farmed for trivial, repeatable score. `SuppressSpottingScorePatch` blocks those reward types from counting — anti‑exploit, not anti‑gameplay.
- **The restart‑bonus duplication (fixed 2026‑06‑27).** The "+250 for being present at match start" bonus was re‑granting on *every server restart*, not once per genuine match. During a morning of deploy churn one admin collected it five times. The fix gates the bonus to fire **once, at the 1‑minute mark** of a match's life, so a quick restart that never reaches a minute grants nothing. The over‑credited points were clawed back via the ledger.

The throughline: rather than locking the economy down pre‑emptively, we kept it permissive but **fully audited**, and patched specific exploits as they appeared.

### NuclearSkill: three iterations to get "skill" to feel right
Lifetime points reward *playing a lot*; we wanted a separate **points‑per‑life** number that rewards playing *well*. Getting the definition of "a life" right took three passes:

1. **v1 — death/air‑eject only, ground dismounts dropped.** Landing to rearm ended your life, which made skill look absurdly low for cautious pilots.
2. **v2 — persistent across sorties.** A life now *accumulates* score across multiple sorties; landing and hopping out to rearm does **not** end it. The life ends and is counted on **death** or **air‑eject**.
3. **The key architectural move:** the running per‑life score lives in the **bot**, not the plugin. The plugin is only an *event detector* ("this life just ended, reason=death"). That's what lets a life **survive a disconnect and a match‑end** — the bot keeps the tally; a mid‑match rage‑quit is simply never counted. Balance/admin moves are made life‑neutral so being shuffled between teams never costs you your streak.

### Teamkill enforcement: escalation, because warnings alone don't work
Friendly fire on a public server needs teeth. The system escalates **per match**: 1st teamkill → eject + private warning, 2nd → kick (+ rank reset on rejoin), 3rd → ban. Bans persist to `plugin_bans.txt` and are enforced on sight. Detection uses the game's `ReportKilled` + damage‑credit, so it catches the actual culprit rather than the last person to touch the victim.

### The AI aircraft limiter: a performance fix
Co‑op missions spawn AI aircraft without bound, and a runaway count tanks the server's frame rate and clogs runways. The limiter caps AI per‑team and total, and clears grounded AI that haven't moved in 45 s (runway‑clog). It is **double‑guarded to never touch human players** — the one thing worse than too many AI is despawning a person's plane. It also emits the per‑side AI/player counts that feed the web CC's traffic panel.

---

## 2. Team balance: the longest evolution in the codebase

Auto‑balance went through more iterations than anything else, because every "obvious" approach broke something:

1. **Force‑move to the other team** — left players with a stale spawn menu; it simply didn't work reliably client‑side.
2. **Move to spectate, let them rejoin the smaller side** — worked, but felt punitive (you lose your plane and have to re‑pick).
3. **The swap mechanic (current).** Move the player to the other faction, spawn them a brief CI‑22 Cricket, and auto‑eject — which forces the client UI to reset to the new team while **keeping their points and skill‑life**. This is also exposed as the admin test commands `!swapteam` / `!forceteamswap`.
4. **The Cricket spawn location problem.** First we spawned the Cricket *landed at a base* — and it materialised on top of parked planes. Then *airborne at a base* — too close to combat. The fix: spawn it **high over open ocean** in a quiet corner of the map (coordinates verified ≥27 km from any base against the terrain atlas), so the brief un‑piloted plane and its auto‑eject can never crash into anything before the player is safely in their parachute.
5. **The phantom‑death bug.** An airborne eject normally banks a death. `_adminEjectGuard` suppresses the death/kill‑feed accounting for the few seconds around an admin‑initiated eject, so a balance move is genuinely life‑, points‑, and feed‑neutral.

**Triggering policy** also matured: balance fires **only on a player *leave*** (a join over‑stacking a side is bounced straight to spectate instead), never under a minimum population (6), and only after a **broadcast warning + a hold** so the gap can self‑correct before anyone is moved. Within that, it picks the player whose **skill rating best evens the two teams' totals**.

The v0.9.5 layer adds **protection tiers** on top: a **new joiner** (connected < 15 min) is the most protected and is only moved if literally everyone else is unavailable; **squad** members (friends grouped via `!squadup`) are weaker‑protected; everyone else is fair game. This came directly from the owner wanting friends to stay together during PvP and newcomers not to be yanked the moment they join. It all sits *inside* the existing "don't move the same person twice in two matches" rule.

---

## 3. The mass‑disconnect saga → the network flood guard

Twice the **entire lobby dropped at once**. This was the most serious incident class and produced the most carefully‑built feature.

- **First occurrence (2026‑06‑26)** looked like a kick but wasn't — it was a **network send‑buffer overflow** (`BufferFullException`) from an RPC flood aimed at an already‑destroyed network object. The lobby recovered on its own; there was no timeout‑kick setting to blame.
- **It recurred at match start (2026‑06‑27)** and was finally root‑caused: a **fleet commander spam‑issuing `CmdSetDestination` orders** creates a re‑path broadcast storm, *amplified* by `ServerRpc` calls aimed at just‑destroyed netIds (each of which the game logs, pushes an error for, and builds a network reader for — exhausting the byte‑buffer pool). Together they overflow every client's reliable send buffer and the whole lobby drops.

The fix (**plugin v0.9.1**, the two‑layer flood guard) addresses both halves:
- **Layer A** — a per‑player token bucket on `CmdSetDestination` that silently drops excess orders (no kick).
- **Layer B** — a manual patch on Mirage's internal `RpcHandler.HandleRpc` that **silently drops a ServerRpc whose target netId no longer exists**, removing the amplifier before it can build the reader and log.

A lesson baked into our process from this: **plugin patch/load state is verified in `BepInEx/LogOutput.log`, not console.log** — the `[diag]` lines that confirm a patch bound never reach the bot's feed. The flood guard has since held at 0 markers through 13‑player PvP.

---

## 4. Talking to a locked‑down server: the relay & the deploy pipeline

### The localhost‑only remote‑command port → the relay
The game's `RemoteCommand` port only accepts connections from **localhost**. The admin PC is not localhost to a hosted server. So the bot uploads a tiny **relay helper** that runs *on* the server, listens for the bot, and forwards `[code][len][body]`‑framed commands to the local RemoteCommand port. Every map change, chat message, and time‑remaining tweak rides this relay.

### Why deploys are a whole pipeline, not a file copy
BepInEx has **no hot‑reload**, and the plugin DLL is **memory‑mapped while the server runs** — overwriting it in place corrupts it (`BadImageFormatException`). So deploying a new plugin is necessarily: upload to a temp name → **atomic `posix_rename`** over the live file (mmap‑safe) → **restart the server** so BepInEx loads it → verify the server is back via the relay. The daily 05:00 job wraps all of that with a guardrail (any failure from the stop onward forces a start, so the server is never left down) and records what's actually live in `deployed_plugin.json`. The Pterodactyl panel's own state is unreliable, so liveness is confirmed by the relay answering, not by the panel.

---

## 5. From a terminal TUI to the web command centre

The admin tooling started as a single‑window Textual **TUI**. It worked but tied the admin to the machine running it. The **web command centre** (Flask + a browser page) replaced it so the server can be run from a phone or any browser, and so the live map could be a real pan/zoom surface instead of ASCII.

A hard rule fell out of the architecture: **the web process must never touch SFTP or `ranks.json` directly.** Only the bot owns those. The web CC writes admin actions (grant, team move, change map) to an `admin_commands.jsonl` queue that the bot drains and executes. This keeps a single owner for the authoritative data and avoids two processes racing on the player ranks.

### The live map
The map is a **faithful green‑on‑black terrain render** baked to a PNG, calibrated by pinning to the *printed* grid labels the game itself shows (with a per‑map world‑origin: Heartland and Ignus have different `xmin`). The browser draws grid, coordinate gutters, faction base rings, and player/AI blips in **screen space** over the PNG, so everything stays crisp at any zoom. Blips are **interpolated client‑side** between the ~2 s position updates for a smooth 60 fps glide rather than a 2 s teleport.

---

## 6. Smaller decisions worth knowing

- **Rank‑in‑name vs. chat reformat.** Embedding `[RANK] Name` into the player's name (the default) lets the game's *native* chat and text‑to‑speech keep working. The alternative — rerouting chat through a server message — gives more formatting control but breaks TTS, so it's opt‑in.
- **Why "X joined the game" can't be hidden.** That message is rendered client‑side from local UI; the server can't suppress it. The kill feed *can* be replaced because it's a server→client RPC. Knowing which messages are server‑authored and which are client‑local shaped what we could and couldn't customise.
- **The double‑bot footgun.** A second stray bot process makes the dashboard's AI counts flap and chat double‑post. The launchers now self‑clean old copies before starting.
- **Start it all after a reboot.** A `START HERE` launcher folder exists purely so a non‑technical admin can get the bot + web CC back up after the PC restarts, without remembering any commands.

---

## 7. The meta‑lesson for anyone extending this

Almost nothing here was designed up front. The pattern was: **ship a feature, watch a live community break it, root‑cause, and harden.** That's why the codebase is heavy on defensive guards, audit trails (ledgers), and "verify via the relay / the BepInEx log, not the thing you'd assume." If you extend it, keep that instinct: assume the server is a hostile black box, log what you change where you can actually see it, and make every points mutation attributable.
