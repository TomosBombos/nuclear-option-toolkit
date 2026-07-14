/*
 * NukeStats - server-side BepInEx plugin for Nuclear Option.
 *
 *  1) Stats sensor: emits each player's real PlayerScore/PlayerRank/Teamkills as
 *     "[NOSTATS] {json}" lines on stdout (-> console.log, which the external bot tails).
 *  2) End-of-game awards: on FactionHQ.DeclareEndGame("Victory") it determines the
 *     winning faction authoritatively (no faction-0 guessing) and emits award events:
 *     +WinPoints to every player on the winning side, and placement bonuses
 *     (1st/2nd/3rd by PlayerScore). The bot applies these to ranks.json.
 *  3) Chat reformat: rewrites player chat as "[Name - Rank] message" in the rank's
 *     colour, by rerouting it through a server message (the normal path renders
 *     "Name:" + faction colour CLIENT-side and strips rich text, so it can't be
 *     restyled in place). Rank label+colour come from plugin_ranks.txt, which the
 *     bot writes to the container.
 *  4) Profanity gate: the in-game filter doesn't work, so before chat broadcasts we
 *     scan it; if any token is a racist slur (leet/spacing/repeat-normalised), the
 *     WHOLE message is replaced with a canned line. Ordinary swearing is left alone.
 *  5) Team control: PvP auto-balance (move the rank-optimal unspawned player when a side
 *     is >MaxDifference ahead) + admin in-game chat commands (!move/!spec/!join/!balance,
 *     authorised by plugin_admins.txt) and a public !autobalance explainer.
 *
 * Member names confirmed by decompiling Assembly-CSharp.dll (ilspycmd). Tunables live
 * in BepInEx/config/anz.nukestats.cfg. Items marked VERIFY are runtime-confirmed at deploy.
 */
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using BepInEx;
using BepInEx.Configuration;
using BepInEx.Logging;
using HarmonyLib;
using Mirage;
using NuclearOption.Chat;
using NuclearOption.Networking;
using NuclearOption.SavedMission;
using UnityEngine;

namespace NukeStats
{
    [BepInPlugin(Guid, "NukeStats", Version)]
    public class NukeStatsPlugin : BaseUnityPlugin
    {
        public const string Guid = "anz.nukestats";
        public const string Version = "1.0.2";
        internal static ManualLogSource Log;
        internal static NukeStatsPlugin Instance;

        // Tunable without rebuilding (BepInEx/config/anz.nukestats.cfg)
        internal static ConfigEntry<bool> ReformatChat;
        internal static ConfigEntry<int> WinPoints, FirstPlace, SecondPlace, ThirdPlace;
        internal static ConfigEntry<float> SnapshotSeconds;
        internal static ConfigEntry<bool> EnforceBalance;        // PvP team-balance block-join
        internal static ConfigEntry<int> BalanceMaxDiff;
        internal static ConfigEntry<bool> RankInName;            // embed [RANK] into the player's chat name (restores native TTS)
        internal static ConfigEntry<bool> ProfanityFilter;       // replace whole messages that contain a racist slur
        internal static ConfigEntry<bool> CustomKillFeed;        // suppress the native flood; announce streaks + ship sinks instead
        internal static ConfigEntry<bool> CleanupPilots;         // periodically despawn old dismounted pilots
        internal static ConfigEntry<int> PilotLifetime;          // seconds a dismounted pilot may linger before cleanup
        internal static ConfigEntry<bool> AiLimit;               // AI aircraft limiter (perf precaution)
        internal static ConfigEntry<bool> TimeoutForceDefeat;    // PvE: force human defeat on mission-timer expiry
        internal static ConfigEntry<bool> PvpTimeoutResult;      // PvP: on timeout the higher total in-game score wins (tie = draw)
        internal static ConfigEntry<int>  TimeoutLeadSeconds;    // fire the timeout resolution this many seconds BEFORE MaxTime (so the map vote runs before rotation)
        internal static ConfigEntry<int> AiPerTeamCap, AiTotalCap, AiStuckSeconds, AiStuckRadius;
        internal static ConfigEntry<int> PvpStartingRank;        // PvP (both factions joinable): floor every player's start to this in-game rank (0 = off)
        internal static ConfigEntry<bool> ForfeitEnabled;        // PvP: allow a team to vote to surrender via !forfeit
        internal static ConfigEntry<int>  ForfeitCooldownSeconds; // seconds before a team can START another forfeit vote
        internal static ConfigEntry<bool> FloodEnforce;          // per-player rate limit on fleet move-orders (anti mass-DC)
        internal static ConfigEntry<int>  FloodPerSec, FloodBurst;
        internal static ConfigEntry<bool> FloodLogDrops, FloodDropDeadNet;
        internal static ConfigEntry<bool> MirageRaiseSendBuffer;  // anti mass-DC Layer C: raise the reliable-send-buffer cap
        internal static ConfigEntry<int>  MirageSendBufferLimit;  // target for MaxReliablePacketsInSendBufferPerConnection
        internal static ConfigEntry<bool> DiagNetProbe;          // ONE-OFF diagnostic: dump the connection object's fields to LogOutput.log to settle whether per-player RTT is reachable on this Mirage build (OFF by default)
        internal static ConfigEntry<string> CommandPolicy, CommandAllowedJsonKeys;  // restrict which units can be CmdSetDestination'd
        internal static ConfigEntry<bool>   CommandDiagLog;
        internal static ConfigEntry<float> SwapAltitude;         // !swapteam/!forceteamswap: Cricket spawn altitude (world-Y m)
        internal static ConfigEntry<string> SkyAircraft, SkyPrimaryWeapon, SkySecondaryWeapon;   // !skyswap loadout
        internal static ConfigEntry<string> SkyDropHeartlandPala, SkyDropHeartlandBdf,           // faction-safe drop points "x,z"
                                            SkyDropIgnusPala, SkyDropIgnusBdf;                   // (skyswap + swap Cricket spawns)
        internal static ConfigEntry<float> SkyAltitude, SkySpeed;
        internal static ConfigEntry<int> SkySecondaryStations;
        internal static ConfigEntry<int> PvpRankCatchupMinutes, PvpRankCatchupMaxRank;   // rank catch-up floor over match time
        internal static ConfigEntry<int> RankFundsPerRank;       // accumulative rank funds (0 = off)
        internal static ConfigEntry<string> RankFundsMode;       // WHEN funds pay: catchup_raised | any_rankup | catchup_all
        internal static ConfigEntry<bool> DamageCalibration;     // [dmgcal] diagnostic log (Teamkill section)
        // KILLFEED customization (per-line Mode + Text). Bound in Awake; applied to plugin-emitted feed lines.
        static readonly string[] KfLines = { "streak", "ship_sink", "ai_kill", "went_down", "teamkill", "splash", "splash_underdog", "kill_bonus" };
        static readonly Dictionary<string, ConfigEntry<string>> _kfMode = new Dictionary<string, ConfigEntry<string>>(StringComparer.Ordinal);
        static readonly Dictionary<string, ConfigEntry<string>> _kfText = new Dictionary<string, ConfigEntry<string>>(StringComparer.Ordinal);
        ChatManager Cm;                                          // cached from the chat hook, for messaging a player

        // sid -> (short label, hex colour, full rank name), pushed by the bot as plugin_ranks.txt.
        // label (ABBR) is used for the kill-feed/radar tag; full is used for the CHAT name tag.
        static readonly Dictionary<string, (string label, string color, string full)> RankMap =
            new Dictionary<string, (string, string, string)>();
        static long _rankFileTicks = -1;
        static string RankFilePath => Path.Combine(Paths.GameRootPath, "plugin_ranks.txt");

        // RankInName bookkeeping: the player's REAL name (sans our "[RANK] " prefix), and a
        // set of SteamIDs whose first "joined the game" message has already been shown (so the
        // extra JoinMessage our rename triggers is suppressed). Both pruned when a player leaves.
        static readonly Dictionary<string, string> RawNames = new Dictionary<string, string>();
        // dismounted-pilot cleanup: pilot -> first time we saw it (Time.time)
        static readonly Dictionary<PilotDismounted, float> PilotSeen = new Dictionary<PilotDismounted, float>();
        static float _nextPilotSweep;

        Harmony _harmony;
        float _lastEnd = -999f;
        readonly Dictionary<string, float> _chatThrottle = new Dictionary<string, float>();

        void Awake()
        {
            Instance = this; Log = Logger;
            _cfgFile = Config;   // cache the ConfigFile NOW — it survives the GameObject being destroyed, whereas Instance.Config later reads as Unity-null
            try { DontDestroyOnLoad(gameObject); } catch { }   // try to survive scene loads on the dedicated server
            ReformatChat    = Config.Bind("Chat", "Reformat", true,
                "Rewrite player chat as [Name - Rank] in the player's rank colour.");
            WinPoints       = Config.Bind("Scoring", "WinPoints", 200, "Points to each player on the winning side.");
            FirstPlace      = Config.Bind("Scoring", "FirstPlace", 500, "Bonus to the top scorer of the match.");
            SecondPlace     = Config.Bind("Scoring", "SecondPlace", 250, "Bonus to 2nd place.");
            ThirdPlace      = Config.Bind("Scoring", "ThirdPlace", 100, "Bonus to 3rd place.");
            RankFundsPerRank = Config.Bind("Scoring", "RankFundsPerRank", 30,
                "In-game funds granted PER RANK on a rank-up, in MILLIONS (30 = 30,000,000). 0 = off. Only an actual "
                + "in-game rank INCREASE during play grants funds; the rank a player is FIRST seen at is recorded as the "
                + "baseline and grants NOTHING (joining at rank 2 grants nothing, ranking 2 to 3 later grants one rank). "
                + "CUMULATIVE and MONOTONIC per match: each later +1 tops up one more rank and the same rank is never "
                + "granted twice (survives reconnect). Reset on mission change; prestige never re-grants. Uses the same "
                + "funds path as admin addfunds.");
            RankFundsMode = Config.Bind("Scoring", "RankFundsMode", "catchup_raised",
                "WHEN rank funds pay out (needs RankFundsPerRank above 0). "
                + "catchup_raised = only players the rank catch-up floor lifts, for the ranks they gain (a player already at that rank, or who earns it in play, gets nothing). "
                + "any_rankup = any player who reaches a new rank, however they got there. "
                + "catchup_all = every connected player each time the catch-up floor steps up a rank, one rank of funds each. "
                + "Unknown values fall back to catchup_raised.");
            SnapshotSeconds = Config.Bind("Stats", "SnapshotSeconds", 10f, "Seconds between full per-player snapshots.");
            EnforceBalance  = Config.Bind("Balance", "Enforce", true,
                "PvP only: keeps the two teams' sizes close so one side doesn't badly outnumber the other. If a " +
                "team is more than 'Max Team Size Gap' players ahead, extra players are stopped from joining it " +
                "(and, with Auto-Move on, the rank/skill-optimal player is moved to the smaller side).");
            BalanceMaxDiff  = Config.Bind("Balance", "MaxDifference", 2,
                "Max allowed team-size difference; balancing only triggers when a side is MORE than this many ahead (2 => a 2-player gap is allowed, only a 3+ gap acts). Higher = fewer/less-twitchy moves.");
            AutoMove        = Config.Bind("Balance", "AutoMove", true,
                "PvP only: when a side is more than MaxDifference ahead, MOVE the rank-optimal player to the smaller side (false = block-join only).");
            MoveOnlyUnspawned = Config.Bind("Balance", "MoveOnlyUnspawned", true,
                "Auto-balance only moves players who are NOT currently flying (i.e. in the spawn menu).");
            RecheckSeconds  = Config.Bind("Balance", "RecheckSeconds", 6,
                "Seconds between auto-balance checks.");
            MoveDebounce    = Config.Bind("Balance", "MoveDebounce", 20,
                "Minimum seconds between auto-balance moves (anti-churn).");
            BalanceGraceSeconds = Config.Bind("Balance", "GraceSeconds", 180,
                "LEGACY (superseded by WarnSeconds; no longer used). Was the silent hold before a balance move.");
            BalanceMinPlayers = Config.Bind("Balance", "MinPlayers", 6,
                "Auto-balance NEVER triggers unless at least this many HUMANS are on the server. Small lobbies are "
                + "left completely alone (no move, no warning).");
            BalanceWarnSeconds = Config.Bind("Balance", "WarnSeconds", 300,
                "When teams become unbalanced (and >= MinPlayers are on), broadcast a warning and WAIT this many "
                + "seconds before moving anyone, giving the gap time to self-correct. Default 300 = a 5-minute warning. "
                + "The timer resets if teams even out (so each fresh imbalance gets its own 5-min warning).");
            BalanceMoveExemptGames = Config.Bind("Balance", "MoveExemptGames", 2,
                "Once auto-balance moves a player, don't move them again for this many GAMES (2 = at most once per 2 games). "
                + "Spreads the burden so the same person isn't repeatedly the one moved.");
            BalanceNewJoinerSeconds = Config.Bind("Balance", "NewJoinerSeconds", 900,
                "STRONGEST auto-balance protection: never move a player who connected less than this many seconds ago "
                + "(default 900 = 15 min). A new joiner is moved ONLY if every other non-exempt player on the bigger side "
                + "is also a new joiner. Resets if they leave and rejoin; after a server restart everyone counts as new "
                + "until the window elapses. 0 = off.");
            SquadMaxSize = Config.Bind("Squad", "MaxSize", 4,
                "Maximum number of players in a !squadup group. Squadmates get a WEAK auto-balance immunity (moved only "
                + "if no unprotected non-exempt player is available - weaker than new-joiner protection).");
            SquadInviteSeconds = Config.Bind("Squad", "InviteSeconds", 90,
                "Seconds a !squadup invite stays open for the invited player to accept with !y.");
            PvpStartingRank = Config.Bind("Mission", "PvpStartingRank", 3,
                "PvP matches only (both factions joinable - Escalation & Terminal Control): every player starts at "
                + "AT LEAST this in-game rank (applied on top of the mission's own playerStartingRank, incl. the built-in "
                + "PvP maps we can't edit). 0 = off. Co-op/PvE is unaffected (uses the mission file's playerStartingRank).");
            PvpRankCatchupMinutes = Config.Bind("Mission", "PvpRankCatchupMinutes", 0,
                "Rank catch-up: every this many MINUTES of match time, the starting-rank FLOOR rises by +1 - latecomers "
                + "spawn at the risen floor and connected players below it are raised too (a FLOOR: nobody is ever lowered). "
                + "0 = off. Base = the mission own starting rank; on PvP matches PvpStartingRank also floors the base.");
            PvpRankCatchupMaxRank = Config.Bind("Mission", "PvpRankCatchupMaxRank", 6,
                "Rank catch-up: the rising floor stops at this in-game rank. Ignored while catch-up is off.");
            ForfeitEnabled  = Config.Bind("Forfeit", "Enabled", true,
                "PvP only: a team can vote to SURRENDER the match via !forfeit (loss for them, win for the other team). "
                + "Needs a majority of the team to agree.");
            ForfeitCooldownSeconds = Config.Bind("Forfeit", "CooldownSeconds", 90,
                "Seconds before a team can START another forfeit vote (anti-spam). The vote window is min(60, this).");
            SwapAltitude    = Config.Bind("Swap", "Altitude", 2500f,
                "!swapteam / !forceteamswap: world-Y altitude (metres) at which the brief CI-22 Cricket is spawned "
                + "before ejecting. ~2500 m clears all terrain at the chosen out-of-the-way coords; raise to 3000 if any embed/crash is seen.");
            // ---- Admin !skyswap: drop the admin (or a named player) into a fully-armed jet high in the sky,
            //      works in ANY mode incl. PvE. ----
            SkyAircraft     = Config.Bind("Admin", "SkyAircraft", "Ifrit",
                "!skyswap: which aircraft to spawn (matched by unitName/code substring, e.g. Ifrit -> KR-67 Ifrit).");
            SkyAltitude     = Config.Bind("Admin", "SkyAltitude", 12000f,
                "!skyswap: world-Y altitude (metres) to spawn the jet at. 12000 = high cruise.");
            SkySpeed        = Config.Bind("Admin", "SkySpeed", 180f,
                "!skyswap: forward launch speed (m/s) so the jet does not stall on an air spawn. 0 = drop stationary.");
            SkyPrimaryWeapon = Config.Bind("Admin", "SkyPrimaryWeapon", "Scimitar",
                "!skyswap: weapon (weaponName substring) to load on every compatible missile station EXCEPT the ones "
                + "reserved for the secondary. Scimitar -> AAM-36 Scimitar. Empty = leave the default loadout.");
            SkySecondaryWeapon = Config.Bind("Admin", "SkySecondaryWeapon", "Scythe",
                "!skyswap: weapon loaded on SkySecondaryStations of the compatible stations (the last ones - typically "
                + "an internal bay). Scythe -> AAM-29 Scythe. Empty = primary on all stations.");
            SkySecondaryStations = Config.Bind("Admin", "SkySecondaryStations", 1,
                "!skyswap: how many missile stations get the secondary weapon (rest get the primary). Default 1 = one "
                + "bay of Scythes, the rest Scimitars. Guns/IRMs/other stations keep the aircraft default weapons.");
            // ---- faction-safe drop points: where a swapped player is spawned, per map + destination team,
            //      so a drop lands over their OWN side instead of mid-map. Format: x,z world metres. ----
            SkyDropHeartlandPala = Config.Bind("Admin", "SkyDropHeartlandPala", "-5000,-15000",
                "Drop point x,z for a player swapped to PALA (Primeva) on Heartland - over the PALA bases in the "
                + "NORTH (in-game north = negative z; webcc grid cell J7). Used by skyswap and the "
                + "swapteam/forceteamswap Cricket so the spawn is over their own side of the map.");
            SkyDropHeartlandBdf = Config.Bind("Admin", "SkyDropHeartlandBdf", "-5000,45000",
                "Drop point x,z for a player swapped to BDF (Boscali) on Heartland - over the BDF bases in the "
                + "SOUTH (in-game south = positive z; webcc grid cell D7).");
            SkyDropIgnusPala = Config.Bind("Admin", "SkyDropIgnusPala", "-75000,0",
                "Drop point x,z for a player swapped to PALA (Primeva) on Ignus - far west, over the Primeva side.");
            SkyDropIgnusBdf = Config.Bind("Admin", "SkyDropIgnusBdf", "75000,0",
                "Drop point x,z for a player swapped to BDF (Boscali) on Ignus - far east, over the Boscali side.");
            AdminSteamIds   = Config.Bind("Admin", "SteamIds", "",
                "Comma-separated SteamIDs allowed to use the IN-GAME team commands (!move/!spec/!join/!balance). " +
                "The public !autobalance explainer works for everyone; command-centre moves don't need this.");
            RankInName      = Config.Bind("Chat", "RankInName", true,
                "Embed the player's rank into their NAME (e.g. '[ACM] Brick') instead of rerouting " +
                "chat. Lets native chat + the game's text-to-speech work. Overrides Reformat when true.");
            ProfanityFilter = Config.Bind("Chat", "ProfanityFilter", true,
                "If a chat message contains a racist slur (leet/spacing/repeats normalised away), " +
                "replace the WHOLE message with a canned line. Ordinary swearing is NOT filtered.");
            CustomKillFeed  = Config.Bind("KillFeed", "Custom", true,
                "Suppress the native global kill feed (it floods with AI units; personal 'you killed X' " +
                "is unaffected) and instead announce kill STREAKS (N confirmed kills, colour escalates at " +
                "5/10/25/50) and CARRIER/DESTROYER sinks. Also drops the player name from the unit label " +
                "(radar/map) so a pilot's name shows once, via their chat name.");
            // ---- KILLFEED customization: per-line Mode (vanilla|custom|off) + custom Text template.
            //      Applies to the PLUGIN-emitted feed lines. Placeholders in Text: {killer} {killer_plane}
            //      {victim} {victim_plane} {weapon} {streak} {ship} {points}. Live via setcfg. ----
            foreach (var line in KfLines)
            {
                _kfMode[line] = Config.Bind("KillFeed", line + ".Mode", "vanilla",
                    "Killfeed line " + line + " mode: vanilla = current default wording, custom = use the Text template, off = suppress the line.");
                _kfText[line] = Config.Bind("KillFeed", line + ".Text", "",
                    "Killfeed line " + line + " custom template (used when Mode=custom). Placeholders: {killer} {killer_plane} {victim} {victim_plane} {weapon} {streak} {ship} {points}. Empty falls back to vanilla wording.");
            }
            CaptureSkillBonus = Config.Bind("Skill", "CaptureBonus", 250,
                "NuclearSkill: extra skill points for capturing a base (added to that life's score).");
            WinSkillBonus   = Config.Bind("Skill", "WinBonus", 200,
                "NuclearSkill: skill points added to a WINNER's final life at match end (before the 5s auto-eject).");
            LossSkillBonus  = Config.Bind("Skill", "LossBonus", 50,
                "NuclearSkill: skill points added to a LOSER's final life at match end (before the 5s auto-eject).");
            BalanceBySkill  = Config.Bind("Skill", "BalanceBySkill", true,
                "NuclearSkill: auto-balance by skill rating (plugin_skill.txt) instead of server rank.");
            TimeoutForceDefeat = Config.Bind("PvE", "TimeoutForceDefeat", true,
                "PvE co-op: when the mission timer expires and humans haven't won, declare the human team " +
                "DEFEATED (the AI faction 'wins') instead of silently rotating. No effect in PvP. " +
                "Default OFF until observed on a live timeout - flip to true in the config once verified.");
            PvpTimeoutResult = Config.Bind("PvP", "TimeoutResult", true,
                "PvP: when the mission timer expires with no winner, decide the match by TOTAL in-game score - the " +
                "higher-scoring team wins (an exact tie is a draw) instead of rotating with no result. " +
                "Off = the match just rotates. No effect in PvE/co-op.");
            TimeoutLeadSeconds = Config.Bind("Match", "TimeoutLeadSeconds", 120,
                "Fire the timeout resolution (PvE defeat or PvP score result) this many seconds BEFORE the mission's " +
                "MaxTime, so the match ends with time to spare and the map vote can run before the game auto-rotates. " +
                "120 = 2 min early. 0 = exactly at MaxTime (the map may rotate before the vote).");
            // (Global.* public-listing / global-leaderboard binds removed 1.0.1 - the public server
            //  directory feature was retired; stale Global.* keys in an existing cfg are inert.)
            CleanupPilots   = Config.Bind("Cleanup", "DismountedPilots", true,
                "Periodically despawn dismounted (ejected) pilots that have lingered on the map, to cut clutter and load.");
            PilotLifetime   = Config.Bind("Cleanup", "PilotLifetimeSeconds", 300,
                "Seconds a dismounted pilot may linger before it is cleaned up (captures/rescues usually happen well within this).");
            TeamkillEnforce = Config.Bind("Teamkill", "Enforce", true,
                "Auto-punish friendly fire (destroying a friendly player's aircraft/vehicle/building). Per match: " +
                "1st = eject + private warning, 2nd = kick (+ in-game rank reset on rejoin), 3rd = ban. Bans persist (plugin_bans.txt).");
            TeamkillMinDamage = Config.Bind("Teamkill", "MinDamage", 100f,
                "Minimum credited damage for a friendly kill to COUNT as a punishable teamkill; 0 = off. Default 100 = "
                + "one destroyed part: a deliberate gun kill credits ~100-140 while a mere graze credits under 100 - so 100 "
                + "rejects the grazed-a-teammate-who-later-died-to-terrain wrongful attribution without missing real kills. "
                + "A friendly kill below the floor is shown in Moderation as a flagged not-counted report, never a punishment.");
            TeamkillCollateralEnforce = Config.Bind("Teamkill", "CollateralEnforce", true,
                "COLLATERAL CHECK. Judge each friendly kill by what the same player blast/window ALSO killed: only-friendlies "
                + "= deliberate -> the punish ladder; a few of EACH (enemies >= friendlies) = collateral -> NOT punished, "
                + "Moderation entry listing every unit that died; overwhelming = collateral, no Moderation entry. FALSE = "
                + "LOG-ONLY: verdicts computed and logged but enforcement runs the old ladder regardless.");
            TeamkillCollateralWindow = Config.Bind("Teamkill", "CollateralWindow", 2.5f,
                "Seconds BEFORE a friendly kill in which the same player other kills count toward the collateral verdict "
                + "(one conventional bomb kills land within a couple of seconds).");
            TeamkillCollateralWindowNuclear = Config.Bind("Teamkill", "CollateralWindowNuclear", 20f,
                "Seconds counted EACH WAY around a friendly kill for NUKE-scale blasts (the shockwave expands at 340 m/s and "
                + "kills in ANY order over ~7-35s; nuclear is detected from the munition blastYield at launch). Also delays a "
                + "nuke event verdict/warn by this long.");
            TeamkillSilentMinEnemies = Config.Bind("Teamkill", "SilentMinEnemies", 10,
                "When a blast killed at least this many enemies AND at least SilentRatio x the friendly count, the collateral "
                + "verdict is SILENT - no Moderation entry, just a log line. Still counts toward CollateralMaxPerMatch so "
                + "silence cannot be farmed. 0 = tier off (every collateral verdict is logged in Moderation).");
            TeamkillSilentRatio = Config.Bind("Teamkill", "SilentRatio", 5f,
                "Companion to SilentMinEnemies: enemies must also be >= this many times the friendly count for the silent verdict.");
            TeamkillCollateralMaxPerMatch = Config.Bind("Teamkill", "CollateralMaxPerMatch", 3,
                "Anti-abuse cap: how many EXONERATING collateral/big-unit verdicts one player can receive per match before "
                + "further friendly kills are judged on the normal ladder regardless. 0 = uncapped.");
            TeamkillBigUnitExempt = Config.Bind("Teamkill", "BigUnitExempt", true,
                "If the same blast/window also killed a BIG enemy objective (carrier/destroyer/other ship classes), treat the "
                + "friendly kill as collateral of that strike - flag it in Moderation, never punish.");
            // Damage calibration diagnostic. Moved out of the Stats section (0.9.47) into Teamkill - it feeds the
            // teamkill min-damage floor calibration, so it belongs with the other moderation/teamkill diagnostics.
            DamageCalibration = Config.Bind("Teamkill", "DamageCalibration", true,
                "Log a [dmgcal] line (victim unit type, total credited damage at death, top attacker share, attacker unit) "
                + "for every player-caused unit death. The game exposes no max-HP, so total-credited-damage-at-death is the "
                + "best proxy for a unit effective HP pool - collected over time it builds an empirical per-unit kill-threshold "
                + "table used to calibrate the teamkill MinDamage floor. Log-only; no gameplay effect.");
            AiLimit         = Config.Bind("AILimit", "Enforce", true,
                "Performance precaution: cap AI aircraft and clear stuck ones. ONLY ever removes AI aircraft, never players.");
            AiPerTeamCap    = Config.Bind("AILimit", "PerTeamAICap", 32,
                "Max AI aircraft flying per faction. The excess (grounded/lowest first) is destroyed.");
            AiTotalCap      = Config.Bind("AILimit", "TotalAircraftCap", 64,
                "Max TOTAL aircraft (AI + players, all sides). When exceeded, AI is removed from the side with the " +
                "MOST aircraft until at/under the cap -- a player is never force-ejected, only AI.");
            AiStuckSeconds  = Config.Bind("AILimit", "StuckSeconds", 45,
                "A GROUNDED AI aircraft that has not moved for this many seconds is cleared (frees a clogged runway). 0 = off.");
            AiStuckRadius   = Config.Bind("AILimit", "StuckRadiusMetres", 25,
                "Movement radius (metres) under which a grounded AI counts as 'not moving' for the stuck check.");
            FloodEnforce    = Config.Bind("Flood", "Enforce", true,
                "Per-player rate limit on fleet move-orders (UnitCommand.CmdSetDestination). Stops a runaway/held-key/macro "
                + "order spam from flooding the reliable send buffer and mass-disconnecting the whole lobby at match start. "
                + "ONLY drops the offending connection's EXCESS orders server-side; never kicks, never touches other players.");
            FloodPerSec     = Config.Bind("Flood", "FleetOrdersPerSec", 3,
                "Sustained fleet move-orders accepted per second per player (token refill). A human commander issues well "
                + "under 1/s; the observed flood was ~19/s. 3/s leaves a large safety margin.");
            FloodBurst      = Config.Bind("Flood", "FleetOrderBurst", 6,
                "Max burst of fleet orders before excess is dropped (token-bucket capacity). The bucket starts FULL, so a "
                + "player's first orders are never dropped.");
            FloodLogDrops   = Config.Bind("Flood", "LogDrops", true,
                "Log (throttled, at most once per 5s per player) the name/SteamID of a player whose orders are being dropped.");
            FloodDropDeadNet = Config.Bind("Flood", "DropDeadNetIdRpcs", true,
                "Defence-in-depth: silently drop ServerRpcs aimed at a netId with no live object (already-destroyed/unknown). "
                + "The game drops these anyway, but first LOGS each one + pushes an error to the sender + builds a network "
                + "reader -- under a flood (a client re-firing at a just-destroyed unit) that storm exhausts the ByteBuffer "
                + "pool and overflows send buffers. Dropping silently removes the amplifier. Patches a private Mirage method; "
                + "fail-open (auto-disables if it can't bind, leaving the CmdSetDestination throttle as the primary guard).");
            CommandPolicy = Config.Bind("Command", "Policy", "HeliDroppedOnly",
                "Which units players may order via CmdSetDestination (unit move-commands). One of: "
                + "All (any commandable unit) | RateLimitOnly (alias of All) | "
                + "HeliDroppedOnly (ONLY player-deployed ground vehicles -- the Hexhound SAM/GMG, AA, APC, LAC "
                + "SAM/AT, AT trucks dropped or sling-loaded from a UH-190/Tarantula; blocks mission/AI ground "
                + "units, ships, missiles) | AllowlistTypes (all ground vehicles, or only those whose jsonKey is "
                + "in Command.AllowedJsonKeys) | Disabled (no unit can be commanded by anyone). The per-player "
                + "rate limit (Flood.*) ALWAYS applies on top. LIVE-tunable; an unknown/unresolved value fails "
                + "OPEN (treated as All) so a typo never breaks commanding.");
            CommandAllowedJsonKeys = Config.Bind("Command", "AllowedJsonKeys", "",
                "Only used when Policy=AllowlistTypes. Comma-separated UnitDefinition.jsonKey values to allow "
                + "(case-insensitive). EMPTY = allow ALL ground vehicles. Discover the exact jsonKeys with "
                + "Command.DiagLog=true, then paste them here, e.g. \"hexhound_sam,lac_at,apc\".");
            CommandDiagLog = Config.Bind("Command", "DiagLog", false,
                "Log the resolved unit type (Class/jsonKey), player-deployed owner state, and the ALLOW/DROP "
                + "decision for each command order (drops throttled ~once/5s per player). Turn ON briefly to "
                + "discover unit jsonKeys / confirm what's being blocked, then OFF (verbose).");
            MirageRaiseSendBuffer = Config.Bind("Mirage", "RaiseReliableSendBuffer", true,
                "Anti mass-DC (Layer C): raise Mirage's per-connection reliable-send-buffer cap "
                + "(MaxReliablePacketsInSendBufferPerConnection, game default 3000) so a transient fleet-order / dead-netId "
                + "RPC burst on a busy server is ABSORBED and drained instead of overflowing into a BufferFullException that "
                + "cascades a lobby-wide disconnect. Mutates the one Config at host start (BEFORE the Peer is built), so it "
                + "takes effect on the NEXT match host. ONLY raises a buffer ceiling -- never kicks, never touches gameplay. "
                + "Fail-open (auto-disables if the host/config site can't be resolved; Layers A/B still apply).");
            MirageSendBufferLimit = Config.Bind("Mirage", "ReliableSendBufferLimit", 12000,
                "Layer C target for MaxReliablePacketsInSendBufferPerConnection (default 12000 = 4x the game's 3000). Higher "
                + "absorbs bigger bursts but costs more memory per connection and a dead-slow client buffers longer before "
                + "it's finally dropped. Clamped to never go BELOW the game default 3000 (and never LOWERS an already-higher "
                + "value). Try 24000 (8x) if a burst still overflows. Live-tunable via the webcc settings menu, but only "
                + "applies at the NEXT match host.");
            DiagNetProbe = Config.Bind("Diag", "NetProbe", false,
                "ONE-OFF DIAGNOSTIC (default OFF). When on AND at least one player is connected, dump the first online "
                + "player's network-connection object to BepInEx/LogOutput.log (concrete type + every numeric/string field & "
                + "property, recursing one level into any AckSystem member) plus NetworkTime.Rtt, ONCE per process (a few "
                + "snapshots, then it stops). Pure read-only reflection, never touches the netcode, emits NOTHING to players. "
                + "Purpose: settle empirically whether per-player server-side RTT is even reachable on this Mirage build before "
                + "any ping feature is built (research says NetworkTime.Rtt is client-fed ~0 on a headless server). Turn ON, "
                + "capture one LogOutput.log dump, turn OFF.");
            GriefAutoKick = Config.Bind("Grief", "AutoKick", true,
                "Anti-grief: automatically KICK a single player who is mass-commanding units to brick the server (the "
                + "reliable-buffer flood that mass-disconnects everyone). Kicks the OFFENDER, not the lobby, and emits a "
                + "report to the webcc Reports tab. Two-factor by default (see RequireActiveFlooding) so a legit "
                + "base-builder is not kicked. Set false to disable detection+report+kick entirely. LIVE-tunable.");
            GriefOwnedThreshold = Config.Bind("Grief", "OwnedUnitThreshold", 12,
                "Auto-kick when a player owns MORE than this many live ground vehicles (their heli-dropped SAMs/AA/APC/"
                + "etc.) AND is actively flooding move-orders. The owner's '>10 units' rule; 12 leaves headroom for a "
                + "legit multi-SAM setup.");
            GriefRequireFlooding = Config.Bind("Grief", "RequireActiveFlooding", true,
                "Two-factor safety (recommended ON): only auto-kick if the player is ALSO sustained-flooding move-orders "
                + "(a macro/held-key/loop), not merely owning many units sitting idle. A normal 'select all + move once' "
                + "burst never trips it. Set false for the literal 'owns > threshold units -> kick' rule (more aggressive).");
            GriefFloodPerSec = Config.Bind("Grief", "FloodOrdersPerSec", 3,
                "The sustained move-order rate (orders/second per player) that counts as 'flooding'; must be held ~4s "
                + "(2 scan windows). A legit commander issues well under 1/s, and the game caps a connection's ACCEPTED "
                + "orders at ~5/s, so 3/s sustained = deliberate macro/click-spam. Lower = more aggressive.");
            GriefHardBan = Config.Bind("Grief", "HardBan", false,
                "If true, a tripped offender is also BANNED (plugin_bans.txt, kicked on rejoin), not just kicked once. "
                + "Default false = kick only (recoverable). You can always ban from the Reports tab.");
            GriefReportOnly = Config.Bind("Grief", "ReportOnly", false,
                "If true, DETECT + REPORT to the Reports tab but do NOT kick. Use for a night to validate the threshold "
                + "against real play before enabling the kick. Default false = actually kick.");
            GriefExemptAdmins = Config.Bind("Grief", "ExemptAdmins", true,
                "Never auto-kick a player whose SteamID is in [Admin] SteamIds (an admin may legitimately mass-command "
                + "units for a scenario). Set false to include admins (e.g. to self-test the detector).");
            GriefBreakerDistinct = Config.Bind("Grief", "BreakerDistinctPlayers", 3,
                "Server-wide CIRCUIT BREAKER (mirrors the bot's flood-breaker). If THIS many DISTINCT players trip the "
                + "grief detector within BreakerWindowSeconds, it is treated as a synchronized order/lag SPIKE (server "
                + "congestion), NOT grief -- ALL kicks/bans for that window are SUPPRESSED (reports still emit). Stops a "
                + "shared lag hitch from mass-kicking the lobby. 0 = disabled (legacy: kick each tripper).");
            GriefBreakerWindow = Config.Bind("Grief", "BreakerWindowSeconds", 6,
                "The rolling window (seconds) over which BreakerDistinctPlayers is counted for the grief circuit breaker.");

            LoadBans();
            LoadSquads();

            _harmony = new Harmony(Guid);
            try
            {
                _harmony.PatchAll();
                var mine = _harmony.GetPatchedMethods().ToList();
                Log.LogInfo($"[diag] patched {mine.Count} method(s): " +
                    string.Join(", ", mine.Select(m => (m.DeclaringType != null ? m.DeclaringType.Name : "?") + "." + m.Name)));
            }
            catch (Exception e) { Log.LogError("PatchAll failed: " + e); }

            // Flood guard Layer B: silently drop ServerRpcs aimed at a dead/unknown netId (kills the
            // "Spawned object not found" log + sender SetError + ByteBuffer-pool storm that overflows
            // reliable send buffers and mass-disconnects the lobby). Manual patch because
            // Mirage.RemoteCalls.RpcHandler is internal and HandleRpc is private ([AggressiveInlining]).
            // Fail-open: if it can't bind, Layer A (the CmdSetDestination throttle) still applies.
            try
            {
                var rpcHandlerT = AccessTools.TypeByName("Mirage.RemoteCalls.RpcHandler");
                var handleRpc = rpcHandlerT != null ? AccessTools.Method(rpcHandlerT, "HandleRpc") : null;
                if (handleRpc != null)
                {
                    _harmony.Patch(handleRpc, prefix: new HarmonyMethod(
                        typeof(DeadNetIdDropPatch).GetMethod("Prefix", BindingFlags.Static | BindingFlags.NonPublic)));
                    Log.LogInfo("[diag] HandleRpc patched (flood guard B: dead-netId drop)");
                }
                else Log.LogWarning("[flood] RpcHandler.HandleRpc not found; dead-netId drop disabled (Layer A still active)");
            }
            catch (Exception e) { Log.LogError("[flood] HandleRpc patch failed (Layer A still active): " + e); }

            // Flood guard Layer C: raise Mirage's per-connection reliable-send-buffer cap so a transient
            // fleet-order / dead-netId RPC burst is ABSORBED and drained instead of overflowing into a
            // BufferFullException -> lobby-wide mass-DC. Mutates the single Mirage.SocketLayer.Config at its
            // one creation site (NetworkManagerNuclearOption.ConfigureNetwork), after it's assigned to
            // Server.PeerConfig and BEFORE NetworkServer.StartServer builds the Peer. Reflective: no hard
            // SocketLayer reference. Fail-open: unresolved type/method -> warn + skip (Layers A/B still apply).
            try
            {
                if (MirageRaiseSendBuffer != null && MirageRaiseSendBuffer.Value)
                {
                    var nmnoT = AccessTools.TypeByName("NuclearOption.Networking.NetworkManagerNuclearOption");
                    var configure = nmnoT != null ? AccessTools.Method(nmnoT, "ConfigureNetwork") : null;
                    if (configure != null)
                    {
                        _harmony.Patch(configure, postfix: new HarmonyMethod(
                            typeof(MirageBufferRaisePatch).GetMethod("Postfix", BindingFlags.Static | BindingFlags.NonPublic)));
                        Log.LogInfo("[diag] ConfigureNetwork patched (flood guard C: raise reliable send buffer)");
                    }
                    else Log.LogWarning("[flood] NetworkManagerNuclearOption.ConfigureNetwork not found; send-buffer raise disabled (Layers A/B still active)");
                }
            }
            catch (Exception e) { Log.LogError("[flood] ConfigureNetwork patch failed (Layers A/B still active): " + e); }

            // CONNECTION-HEALTH telemetry: record the DisconnectReason on each forced drop. The PUBLIC
            // NetworkServer.Disconnected event hands us the player but NOT the reason; the reason only exists
            // on the PRIVATE Peer_OnDisconnected(IConnection, DisconnectReason) callback. Manual patch (private
            // target). Read-only postfix: it just tallies per-SteamID forced-DC count + last reason for the
            // {"t":"net"} telemetry line. Fail-open: if the method can't be resolved, no-op (never blocks load).
            try
            {
                var nsT = AccessTools.TypeByName("Mirage.NetworkServer");
                var onDisc = nsT != null ? AccessTools.Method(nsT, "Peer_OnDisconnected") : null;
                if (onDisc != null)
                {
                    _harmony.Patch(onDisc, postfix: new HarmonyMethod(
                        typeof(DcReasonPatch).GetMethod("Postfix", BindingFlags.Static | BindingFlags.NonPublic)));
                    Log.LogInfo("[diag] Peer_OnDisconnected patched (net-health: capture DisconnectReason)");
                }
                else Log.LogWarning("[net] NetworkServer.Peer_OnDisconnected not found; per-DC reason capture disabled (net telemetry still emits, lastDc stays empty)");
            }
            catch (Exception e) { Log.LogError("[net] Peer_OnDisconnected patch failed (net telemetry still emits): " + e); }

            Log.LogInfo($"NukeStats {Version} loaded (+ team balance: autobalance fires ONLY on a LEAVE, then WARNS and waits before moving; protection tiers = new joiners (<{(BalanceNewJoinerSeconds!=null?BalanceNewJoinerSeconds.Value:900)}s, strongest) > squads > then the best skill-evening pick; join-the-fuller-side = INSTANT spectate, no warning; + PvP !forfeit team-surrender vote (cd {ForfeitCooldownSeconds.Value}s); + PvP start-rank floor={PvpStartingRank.Value}; + admin !setrank/!setfunds/!addfunds; + live-map entity feed: AI aircraft + ships, heli/plane; + AI aircraft limiter: per-team {AiPerTeamCap.Value}/total {AiTotalCap.Value} caps + {AiStuckSeconds.Value}s stuck-runway clear @5s scan; AI-only, never players; SKILL = PERSISTENT points-per-death: life ends ONLY on death/air-eject, survives disconnect + match-end (no match-end eject), balance/admin moves are life-NEUTRAL, captures emit capbonus; strategic-strike announce removed; PvP balance: joinable-only team detect [spectate-move]; radar/spotting + jamming score SUPPRESSED [anti-exploit]; FLOOD GUARD: A=per-player CmdSetDestination rate-limit {(FloodPerSec!=null?FloodPerSec.Value:3)}/s burst {(FloodBurst!=null?FloodBurst.Value:6)} [drop excess, no kick], B=silent-drop ServerRpc to dead netId [{(FloodDropDeadNet!=null&&FloodDropDeadNet.Value?"on":"off")}] -> stops match-start mass-DC; autobalance: never under MinPlayers={(BalanceMinPlayers!=null?BalanceMinPlayers.Value:6)} + {(BalanceWarnSeconds!=null?BalanceWarnSeconds.Value:300)}s WARNED hold, then MOVES the picked player via the swap mechanic (landed Cricket); admin !swapteam/!forceteamswap [team swap + Cricket spawn HIGH over open ocean + eject -> UI reset, life/points-neutral]; + !squadup friend groups (max {SquadMax}, persist across matches, protected from auto-balance below new joiners); + LIVE CONFIG (webcc settings menu via setcfg/dumpcfg -> live ConfigEntry edit + Config.Save)). RankFile={RankFilePath}");
            DumpCfg();   // emit an initial [NOSTATS] cfg snapshot so the webcc settings menu has live values on load
            try { var tgo = new GameObject("NukeStatsTicker"); DontDestroyOnLoad(tgo); tgo.AddComponent<Ticker>(); Log.LogInfo("[diag] NukeStatsTicker up (fallback periodic driver; survives mission/scene changes)"); }
            catch (Exception e) { Log?.LogError("ticker create: " + e); }
        }

        // Deliberately NO OnDestroy/UnpatchSelf: on this dedicated server the manager
        // GameObject is destroyed shortly after load, and unpatching there was REMOVING
        // every hook (the debug trace showed the methods re-patched with 0 prefixes right
        // after we applied them). Harmony patches are static and live for the process, so
        // we never unpatch — the hooks then survive even if this object is destroyed.

        // Periodic full-player snapshot. On this dedicated server the manager
        // GameObject is destroyed shortly after Awake, so our own Update() never ticks
        // (verified: 0 "snap" lines reach console.log). We therefore drive the periodic
        // work from both a Harmony hook on FactionHQ.Update and a persistent fallback
        // ticker. The ticker is important during mission/scene transitions and built-in
        // PvP map states where HQ.Update can stop being a reliable heartbeat.
        static float _nextSnapShared;
        static int _snapDiag;
        static int _lastPeriodicFrame = -1;

        internal static void PeriodicTick()
        {
            try
            {
                int frame = UnityEngine.Time.frameCount;
                if (frame == _lastPeriodicFrame) return;
                _lastPeriodicFrame = frame;
                PerfTick();                           // server frametime sampler (smoothed ms; emitted on the net line)
                PvETimeoutTick();                     // PvE: force human defeat when the mission timer expires
                MaybeSnapshot();
                MaybeCleanupPilots();
                MaybeBalance();
                PumpBounces();                        // bounce wrong-team joiners to spectate (cheap when idle)
                PumpKillStreaks();                    // announce settled kill streaks (cheap when idle)
                PumpStrategic();                      // coalesce strategic-launcher shot-downs into one line
                SkillTick();                          // NuclearSkill: per-life tracking + end-match eject
                PosTick();                            // live map: ~2s plane position broadcast
                TkTick();                             // teamkill enforcement (warn/eject/kick/ban)
                GriefTick();                          // anti-grief: detect + auto-kick a single connection mass-commanding units
                AiLimitTick();                        // AI aircraft limiter (cap + stuck-runway clear)
                CatchupTick();                        // rank catch-up: raise already-connected players below the risen floor
                RankFundsTick();                      // accumulative rank funds: grant on any in-game rank increase
                PollCommands();
            }
            catch (Exception e) { Log?.LogError("PeriodicTick: " + e); }
        }

        internal static void MaybeSnapshot()
        {
            try
            {
                float now = Time.time;
                if (now < _nextSnapShared) return;
                float iv = (SnapshotSeconds != null) ? Mathf.Max(2f, SnapshotSeconds.Value) : 10f;
                _nextSnapShared = now + iv;
                if (_snapDiag < 5)   // first few snapshots: confirm it runs + player count
                {
                    try { Log?.LogInfo($"[diag] snapshot #{_snapDiag}: {Humans().Count} player(s)"); } catch { }
                    _snapDiag++;
                }
                EmitAll("snap");
                NetHealthTick();                     // connection-health telemetry ({"t":"net"}); always-works, no RTT needed
                NetProbe();                          // one-off diagnostic dump (no-op unless Diag.NetProbe is on); settles RTT reachability
                PruneLeavers();                      // forget RankInName bookkeeping for players who left
            }
            catch (Exception e) { Log?.LogError("MaybeSnapshot: " + e); }
        }

        // Drop RankInName/JoinMessage state for SteamIDs no longer present, so a genuine
        // rejoin gets a fresh "joined" message and the dictionaries don't grow unbounded.
        static void PruneLeavers()
        {
            try
            {
                if (RawNames.Count == 0) return;
                var present = new HashSet<string>();
                foreach (var p in Humans()) present.Add(Sid(p));
                foreach (var sid in new List<string>(RawNames.Keys))
                    if (!present.Contains(sid)) RawNames.Remove(sid);
            }
            catch (Exception e) { Log?.LogError("PruneLeavers: " + e); }
        }

        // ---------------- flood guard: per-player fleet-order rate limit (Layer A) ----------------
        // A single client spamming UnitCommand.CmdSetDestination (held key / macro / a UI loop
        // re-firing at a destroyed unit) overflows every client's reliable send buffer at match
        // start and mass-disconnects the lobby. We cap accepted orders per SENDER with a leaky
        // token bucket; excess orders are dropped server-side (the client is unharmed, and the next
        // accepted waypoint supersedes any dropped one). Called from FleetOrderFloodPatch.
        static readonly Dictionary<string, (float tokens, float last)> _orderBucket =
            new Dictionary<string, (float, float)>();
        static readonly Dictionary<string, float> _orderDropLog = new Dictionary<string, float>();

        // true = ALLOW this fleet order, false = DROP it. Keyed on SteamID (one bucket per player).
        internal static bool AllowFleetOrder(Player player)
        {
            try
            {
                if (FloodEnforce == null || !FloodEnforce.Value || player == null) return true;
                string id = Sid(player);
                if (string.IsNullOrEmpty(id)) return true;                 // can't key -> never punish
                float now = Time.time;
                float cap  = Mathf.Max(1f, FloodBurst  != null ? FloodBurst.Value  : 6);
                float rate = Mathf.Max(0.5f, FloodPerSec != null ? FloodPerSec.Value : 3);
                if (!_orderBucket.TryGetValue(id, out var b)) b = (cap, now);   // new player: bucket starts FULL
                float tokens = Mathf.Min(cap, b.tokens + (now - b.last) * rate);
                if (tokens >= 1f) { _orderBucket[id] = (tokens - 1f, now); return true; }
                _orderBucket[id] = (tokens, now);                          // empty: keep the clock moving
                try { _netDrops[id] = (_netDrops.TryGetValue(id, out var nd) ? nd : 0) + 1; } catch { }   // net-health: per-player rate-dropped order count (reset each emit)
                if (FloodLogDrops != null && FloodLogDrops.Value)
                {
                    if (!_orderDropLog.TryGetValue(id, out var t) || now - t > 5f)
                    {
                        _orderDropLog[id] = now;
                        Log?.LogWarning($"[flood] rate-dropping fleet orders from {player.PlayerName} ({id}) -> exceeded {rate}/s (burst {cap})");
                    }
                }
                return false;
            }
            catch (Exception e) { Log?.LogError("AllowFleetOrder: " + e); return true; }
        }

        // COMMAND POLICY: which units may be ordered via CmdSetDestination, ON TOP of the per-sender rate limit.
        // true = ALLOW (still subject to the rate limit), false = DROP this order outright. `cmd` is the
        // UnitCommand component, which lives on the SAME GameObject as the commanded unit (only GroundVehicle/
        // Ship/Missile are ICommandable). GroundVehicle.Networkowner = the deploying Player on a heli drop/sling,
        // null for mission/AI spawns -> the clean "player-deployed" discriminator. Default "All" = no filtering
        // (current behaviour). LIVE-tunable; fail-OPEN on any ambiguity (the rate limit is the real flood guard).
        static readonly Dictionary<string, float> _cmdPolicyDropLog = new Dictionary<string, float>();
        static HashSet<string> _allowedKeysCache; static string _allowedKeysRaw;

        internal static bool AllowCommandTarget(UnitCommand cmd, Player player)
        {
            try
            {
                string mode = CommandPolicy != null ? (CommandPolicy.Value ?? "All").Trim() : "All";
                if (mode.Length == 0
                    || string.Equals(mode, "All", StringComparison.OrdinalIgnoreCase)
                    || string.Equals(mode, "RateLimitOnly", StringComparison.OrdinalIgnoreCase))
                    return true;
                if (string.Equals(mode, "Disabled", StringComparison.OrdinalIgnoreCase))
                    return DropCmd(player, cmd, "policy=Disabled");

                Unit unit = cmd != null ? cmd.GetComponent<Unit>() : null;
                if (unit == null)   // resolve failure -> ALLOW (never break legit commanding); the rate limit still guards
                {
                    if (CommandDiagLog != null && CommandDiagLog.Value)
                        Log?.LogInfo("[cmdpolicy] unresolved target (no Unit on UnitCommand) -> ALLOW (fail-open)");
                    return true;
                }
                GroundVehicle gv = unit as GroundVehicle;

                if (string.Equals(mode, "HeliDroppedOnly", StringComparison.OrdinalIgnoreCase))
                {
                    bool ok = false; try { ok = gv != null && gv.Networkowner != null; } catch { ok = false; }
                    if (CommandDiagLog != null && CommandDiagLog.Value)
                        Log?.LogInfo($"[cmdpolicy] HeliDroppedOnly target={Describe(unit)} gv={(gv != null)} owned={(gv != null && SafeOwner(gv) != null)} -> {(ok ? "ALLOW" : "DROP")}");
                    return ok ? true : DropCmd(player, cmd, "not a player-deployed ground unit");
                }
                if (string.Equals(mode, "AllowlistTypes", StringComparison.OrdinalIgnoreCase))
                {
                    if (gv == null) return DropCmd(player, cmd, "not a GroundVehicle");
                    string keys = CommandAllowedJsonKeys != null ? (CommandAllowedJsonKeys.Value ?? "") : "";
                    if (string.IsNullOrWhiteSpace(keys)) return true;   // empty list => all ground vehicles allowed
                    if (!ReferenceEquals(keys, _allowedKeysRaw))        // rebuild cache only when the config string changes
                    {
                        _allowedKeysRaw = keys;
                        _allowedKeysCache = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                        foreach (var k in keys.Split(',')) { var t = k.Trim(); if (t.Length > 0) _allowedKeysCache.Add(t); }
                    }
                    string jk = gv.definition != null ? gv.definition.jsonKey : null;
                    bool ok = jk != null && _allowedKeysCache.Contains(jk);
                    if (CommandDiagLog != null && CommandDiagLog.Value)
                        Log?.LogInfo($"[cmdpolicy] AllowlistTypes jsonKey={jk} -> {(ok ? "ALLOW" : "DROP")}");
                    return ok ? true : DropCmd(player, cmd, $"jsonKey '{jk}' not in allowlist");
                }
                if (CommandDiagLog != null && CommandDiagLog.Value)
                    Log?.LogWarning($"[cmdpolicy] unknown Command.Policy '{mode}' -> ALLOW (fail-open)");
                return true;
            }
            catch (Exception e) { Log?.LogError("AllowCommandTarget: " + e); return true; }   // fail-open on any error
        }

        static bool DropCmd(Player player, UnitCommand cmd, string why)
        {
            try
            {
                string id = player != null ? Sid(player) : null;
                float now = Time.time;
                if (!string.IsNullOrEmpty(id) && (!_cmdPolicyDropLog.TryGetValue(id, out var t) || now - t > 5f))
                {
                    _cmdPolicyDropLog[id] = now;
                    string what = "?"; try { var u = cmd != null ? cmd.GetComponent<Unit>() : null; what = Describe(u); } catch { }
                    Log?.LogInfo($"[cmdpolicy] dropped order from {(player != null ? player.PlayerName : "?")} on {what} ({why})");
                }
            }
            catch { }
            return false;
        }

        static Player SafeOwner(GroundVehicle gv) { try { return gv.Networkowner; } catch { return null; } }

        static string Describe(Unit u)
        {
            if (u == null) return "null";
            try
            {
                string jk = u.definition != null ? u.definition.jsonKey : null;
                string nm = u.definition != null ? u.definition.unitName : u.unitName;
                return $"{u.GetType().Name}/{jk ?? nm ?? "?"}";
            }
            catch { return u.GetType().Name; }
        }

        // ======================= CONNECTION-HEALTH telemetry + RTT probe =======================
        // Two read-only, fail-open, never-throw additions for the connection-stress webcc panel.
        //  (A) NetHealthTick(): emit a {"t":"net"} line every snapshot when humans>0 -- per-player order/drop
        //      rate, anti-grief streak, forced-DC count + last reason -- using ONLY existing counters plus the
        //      two tiny tallies below. NEEDS NO RTT, so it ships regardless of whether ping is reachable.
        //  (B) NetProbe(): a ONE-OFF diagnostic (Diag.NetProbe, default false) that dumps the first online
        //      player's connection object's fields to LogOutput.log to settle whether per-player RTT is reachable.
        // Everything is try/catch-swallowed and read-only; nothing here can disturb the netcode.

        // per-player tallies for the {"t":"net"} line, reset on each emit (lightweight, no allocation churn)
        static readonly Dictionary<string, int> _netOrders = new Dictionary<string, int>();   // CmdSetDestination attempts since last emit
        static readonly Dictionary<string, int> _netDrops  = new Dictionary<string, int>();    // rate-dropped orders since last emit
        // forced-DC bookkeeping, populated by DcReasonPatch (keyed on SteamID)
        internal static readonly Dictionary<string, int>    _dcCount  = new Dictionary<string, int>();
        internal static readonly Dictionary<string, string> _dcReason = new Dictionary<string, string>();
        static float _netEmitElapsedAnchor = -1f;

        // Reflectively read NetworkTime.Rtt (proves it reads ~0 on a headless server). Empty string if unresolved.
        static string ProbeRttString()
        {
            try
            {
                var ntT = AccessTools.TypeByName("Mirage.NetworkTime");
                if (ntT == null) return "";
                var rttP = AccessTools.Property(ntT, "Rtt");
                if (rttP != null && rttP.GetMethod != null && rttP.GetMethod.IsStatic)
                    return System.Convert.ToString(rttP.GetValue(null), CultureInfo.InvariantCulture);
                return "";
            }
            catch { return ""; }
        }

        // Reflectively read the per-connection reliable-send-buffer cap (Layer C target) for the bufCap field.
        static int ProbeSendBufferCap()
        {
            try
            {
                var nmno = NetworkManagerNuclearOption.i;
                if (nmno == null) return 0;
                var server = ReflectGet(nmno, "Server");
                var peerCfg = server != null ? ReflectGet(server, "PeerConfig") : null;
                var v = peerCfg != null ? ReflectGet(peerCfg, "MaxReliablePacketsInSendBufferPerConnection") : null;
                return v != null ? System.Convert.ToInt32(v) : 0;
            }
            catch { return 0; }
        }

        // field-or-property reflective getter (read-only, swallow)
        static object ReflectGet(object o, string name)
        {
            try
            {
                if (o == null) return null;
                var t = o.GetType();
                var p = AccessTools.Property(t, name);
                if (p != null && p.GetMethod != null) return p.GetValue(o);
                var f = AccessTools.Field(t, name);
                return f != null ? f.GetValue(o) : null;
            }
            catch { return null; }
        }

        // ---- SERVER FRAMETIME sampler (contract [FRAMETIME]). Sample real per-frame delta on the per-frame
        // PeriodicTick pump; publish a ~1s smoothed EMA in ms on the {"t":"net"} telemetry line. A tick GAP
        // (>5000ms mission transition) is NOT a frame and restarts the accumulator so it can't fake a spike.
        internal static float SrvFrameMs;   // smoothed frametime (ms); 0 = no data yet
        static float _pfLast, _pfEma;
        internal static void PerfTick()
        {
            try
            {
                float now = UnityEngine.Time.realtimeSinceStartup;
                if (_pfLast > 0f)
                {
                    float dt = (now - _pfLast) * 1000f;
                    if (dt > 5000f) { _pfLast = now; _pfEma = 0f; SrvFrameMs = 0f; return; }   // tick gap -> restart
                    _pfEma = _pfEma <= 0f ? dt : _pfEma + 0.1f * (dt - _pfEma);                 // EMA (~0.1 alpha)
                    SrvFrameMs = _pfEma;
                }
                _pfLast = now;
            }
            catch { }
        }

        // Connection-health line. NO RTT. Emits only existing counters; omits any field we can't compute.
        internal static void NetHealthTick()
        {
            try
            {
                var humans = Humans();
                if (humans.Count == 0) return;
                float now = Time.time;
                float elapsed = _netEmitElapsedAnchor < 0f ? 1f : Mathf.Max(0.5f, now - _netEmitElapsedAnchor);
                _netEmitElapsedAnchor = now;

                var sb = new StringBuilder(256);
                sb.Append("{\"t\":\"net\",\"p\":[");
                bool first = true;
                foreach (var p in humans)
                {
                    string id = Sid(p);
                    if (string.IsNullOrEmpty(id) || id == "0") continue;
                    int orders = _netOrders.TryGetValue(id, out var o2) ? o2 : 0;
                    int drops  = _netDrops.TryGetValue(id, out var d2) ? d2 : 0;
                    int ordPerSec = (int)(orders / elapsed);
                    int streak = _griefStreak.TryGetValue(id, out var st) ? st : 0;
                    int sbDc   = _dcCount.TryGetValue(id, out var dc) ? dc : 0;
                    string lastDc = _dcReason.TryGetValue(id, out var dr) ? dr : "";
                    if (!first) sb.Append(',');
                    first = false;
                    sb.Append("{\"id\":\"").Append(id).Append("\",\"ord\":").Append(ordPerSec)
                      .Append(",\"drop\":").Append(drops)
                      .Append(",\"streak\":").Append(streak)
                      .Append(",\"sbDc\":").Append(sbDc)
                      .Append(",\"lastDc\":\"").Append(Esc(lastDc)).Append("\"}");
                }
                sb.Append("],\"deadNet\":").Append(_deadNetDrops)
                  .Append(",\"bufCap\":").Append(ProbeSendBufferCap())
                  .Append(",\"frametime_ms\":").Append(SrvFrameMs.ToString("0.0", CultureInfo.InvariantCulture)).Append('}');
                Out(sb.ToString());
                _netOrders.Clear(); _netDrops.Clear();   // reset per-emit tallies (forced-DC tallies persist for the panel)
            }
            catch (Exception e) { Log?.LogError("NetHealthTick: " + e); }
        }

        // ONE-OFF RTT-reachability probe. Pure read-only reflection; emits to LogOutput.log only, never to players.
        static int _netProbeRuns; static bool _netProbeDone;
        internal static void NetProbe()
        {
            try
            {
                if (DiagNetProbe == null || !DiagNetProbe.Value || _netProbeDone) return;
                var humans = Humans();
                if (humans.Count == 0) return;
                if (_netProbeRuns++ >= 3) { _netProbeDone = true; return; }   // a few snapshots then stop (throttle)

                Player p = humans[0];
                object owner = ReflectGet(p, "Owner");                        // INetworkPlayer
                Log?.LogInfo($"[netprobe] run #{_netProbeRuns}: NetworkTime.Rtt={ProbeRttString()} (expect ~0 on a headless server) bufCap={ProbeSendBufferCap()}");
                if (owner == null) { Log?.LogInfo("[netprobe] Owner is null (no INetworkPlayer); cannot reach a connection object"); return; }
                Log?.LogInfo($"[netprobe] Owner concrete type: {owner.GetType().FullName}");
                object conn = ReflectGet(owner, "Connection");               // IConnection (Mirror/Mirage fork-specific)
                if (conn == null) { DumpMembers("Owner", owner, 0); return; } // no Connection member -> dump the player object itself
                DumpMembers("Connection", conn, 0);
                _netProbeDone = (_netProbeRuns >= 3);
            }
            catch (Exception e) { Log?.LogError("NetProbe: " + e); }
        }

        // Log every numeric/string field & property of `o`; recurse ONE level into any AckSystem-ish member.
        static void DumpMembers(string label, object o, int depth)
        {
            try
            {
                if (o == null) { Log?.LogInfo($"[netprobe] {label}: <null>"); return; }
                var t = o.GetType();
                Log?.LogInfo($"[netprobe] {label} type={t.FullName} (depth {depth})");
                const BindingFlags BF = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
                foreach (var f in t.GetFields(BF))
                {
                    try { object v = f.GetValue(o); ProbeMember(label, f.Name, f.FieldType, v, depth); }
                    catch { }
                }
                foreach (var pr in t.GetProperties(BF))
                {
                    try
                    {
                        if (pr.GetMethod == null || pr.GetIndexParameters().Length > 0) continue;
                        object v = pr.GetValue(o);
                        ProbeMember(label, pr.Name, pr.PropertyType, v, depth);
                    }
                    catch { }
                }
            }
            catch (Exception e) { Log?.LogError("DumpMembers: " + e); }
        }

        static void ProbeMember(string label, string name, Type type, object v, int depth)
        {
            try
            {
                string tn = type != null ? type.Name : "?";
                bool ackish = (tn.IndexOf("AckSystem", StringComparison.OrdinalIgnoreCase) >= 0)
                              || name.StartsWith("ack", StringComparison.OrdinalIgnoreCase)
                              || name.IndexOf("AckSystem", StringComparison.OrdinalIgnoreCase) >= 0;
                if (v == null) { Log?.LogInfo($"[netprobe]   {label}.{name} ({tn}) = null"); return; }
                if (type != null && (type.IsPrimitive || type.IsEnum || v is string || v is decimal))
                    Log?.LogInfo($"[netprobe]   {label}.{name} ({tn}) = {System.Convert.ToString(v, CultureInfo.InvariantCulture)}");
                else
                    Log?.LogInfo($"[netprobe]   {label}.{name} ({tn}) = <object>");
                if (ackish && depth < 1)
                    DumpMembers(label + "." + name, v, depth + 1);
            }
            catch { }
        }

        // Map a Mirage IConnection (the arg to Peer_OnDisconnected) to the SteamID of a CURRENT player, by
        // reflectively comparing each online player's Owner.Connection. Read-only; "" if no live match.
        internal static string SidForConnection(object conn)
        {
            try
            {
                if (conn == null) return "";
                foreach (var p in Humans())
                {
                    try
                    {
                        object owner = ReflectGet(p, "Owner");
                        object pConn = owner != null ? ReflectGet(owner, "Connection") : null;
                        if (pConn != null && ReferenceEquals(pConn, conn)) return Sid(p);
                    }
                    catch { }
                }
            }
            catch { }
            return "";   // player already removed from the lookup by the time we run -> unmapped (telemetry just omits it)
        }

        // Record a forced disconnect (count + last reason) for the {"t":"net"} telemetry line. Called from DcReasonPatch.
        internal static void NoteForcedDc(string sid, string reason)
        {
            try
            {
                if (string.IsNullOrEmpty(sid) || sid == "0") return;
                _dcCount[sid] = (_dcCount.TryGetValue(sid, out var c) ? c : 0) + 1;
                _dcReason[sid] = reason ?? "";
            }
            catch { }
        }

        // Layer B bookkeeping: count silently-dropped dead-netId ServerRpcs (the log/alloc amplifier),
        // surfaced occasionally so admins can see the guard working without re-introducing the spam.
        static int _deadNetDrops; static float _deadNetLog = -999f;
        internal static void NoteDeadNetIdDrop()
        {
            try
            {
                _deadNetDrops++;
                float now = Time.time;
                if (now - _deadNetLog > 30f)
                {
                    _deadNetLog = now;
                    Log?.LogInfo($"[flood] dead-netId ServerRpc drops so far: {_deadNetDrops} (silently absorbed; no log/alloc storm)");
                }
            }
            catch { }
        }

        void Update() { PeriodicTick(); }   // a no-op if this object never ticks

        // -------- player enumeration (humans only; SteamID filters out AI/unjoined) --------
        static string Sid(Player p) { try { return p.SteamID.ToString(); } catch { return ""; } }

        // PERF: FindObjectsOfType<Player> is expensive and Humans() is called many times
        // per HQ tick (SkillTick/TkTick/PollCommands/FindPlayerBySid/snapshot/balance...).
        // Cache it for the current frame (Time.time is constant within a frame) so the
        // scene scan runs once per frame instead of a dozen+ times.
        static List<Player> _humansCache;
        static float _humansCacheTime = -1f;
        static List<Player> Humans()
        {
            float now = Time.time;
            if (_humansCache != null && _humansCacheTime == now) return _humansCache;
            var ok = new List<Player>();
            // Use the GAME's own player registry (UnitRegistry.playerLookup) - the same source
            // ChatManager uses for chat delivery, so these Player objects have a valid .Owner
            // (FindObjectsOfType returned copies whose .Owner was null in the poll context, so
            // whispers/TellPlayer silently no-op'd). Fall back to a scene scan if it's empty.
            try
            {
                foreach (var p in UnitRegistry.playerLookup.Values)
                {
                    if (p == null) continue;
                    string id = Sid(p);
                    if (!string.IsNullOrEmpty(id) && id != "0") ok.Add(p);
                }
            }
            catch (Exception e) { Log?.LogError("Humans/playerLookup: " + e); }
            if (ok.Count == 0)
                foreach (var p in UnityEngine.Object.FindObjectsOfType<Player>())
                {
                    if (p == null) continue;
                    string id = Sid(p);
                    if (!string.IsNullOrEmpty(id) && id != "0") ok.Add(p);
                }
            _humansCache = ok; _humansCacheTime = now;
            return ok;
        }

        static string Fac(Player p)
        {
            try { return p.HQ != null && p.HQ.faction != null ? p.HQ.faction.factionName : ""; }
            catch { return ""; }
        }

        // The plane the player is currently in (their live Aircraft), or the airframe
        // they have selected if not spawned. Empty string => in menu / between spawns.
        static string Plane(Player p)
        {
            try
            {
                var ac = p.Aircraft;
                if (ac != null)
                {
                    var d = ac.definition;
                    if (d != null && !string.IsNullOrEmpty(d.unitName)) return d.unitName;
                }
            }
            catch { }
            try
            {
                var af = p.AirframeInUse;            // OwnedAirframe? - selected airframe
                if (af.HasValue && af.Value.Definition != null
                    && !string.IsNullOrEmpty(af.Value.Definition.unitName))
                    return af.Value.Definition.unitName;
            }
            catch { }
            return "";
        }

        // -------- emit [NOSTATS] lines --------
        // Use UnityEngine.Debug.Log so the line lands in Unity's -logFile
        // (/logs/console.log) which the external bot tails. (Console.WriteLine only
        // reaches process stdout, NOT the -logFile, so the bot wouldn't see it.)
        static void Out(string json) => Debug.Log("[NOSTATS] " + json);

        internal static void EmitAll(string type)
        {
            try
            {
                foreach (var p in Humans()) EmitOne(p, type);
                if (type == "end") Out("{\"t\":\"end\"}");
            }
            catch (Exception e) { Log?.LogError("EmitAll: " + e); }
        }

        internal static void EmitOne(Player p, string type)
        {
            if (p == null) return;
            try
            {
                string id = Sid(p);
                if (string.IsNullOrEmpty(id) || id == "0") return;
                var sb = new StringBuilder(160);
                sb.Append("{\"t\":\"").Append(type).Append("\",\"id\":\"").Append(id).Append("\"");
                sb.Append(",\"n\":\"").Append(Esc(RawNameOf(p))).Append("\"");
                sb.Append(",\"f\":\"").Append(Esc(Fac(p))).Append("\"");
                sb.Append(",\"s\":").Append(Num(p.PlayerScore));
                sb.Append(",\"rk\":").Append(Num(p.PlayerRank));
                sb.Append(",\"tk\":").Append(Num(p.Teamkills));
                sb.Append(",\"ac\":\"").Append(Esc(Plane(p))).Append("\"");
                sb.Append('}');
                Out(sb.ToString());
            }
            catch (Exception e) { Log?.LogError("EmitOne: " + e); }
        }

        // -------- live map: fast (~2s) position tick. One compact line of every FLYING player's
        // world x/z. Cheap enough to run far more often than the full 10s snapshot. A player with no
        // fresh pos = not flying -> the command centre shows them as dead/ejected until they respawn. --------
        static float _nextPos;
        internal static void PosTick()
        {
            float now = Time.time;
            if (now < _nextPos) return;
            _nextPos = now + 2f;
            try
            {
                var sb = new StringBuilder(256);
                sb.Append("{\"t\":\"pos\",\"p\":[");
                bool first = true;
                foreach (var p in Humans())
                {
                    Aircraft ac = null; try { ac = p.Aircraft; } catch { }
                    if (ac == null) continue;                              // only flying players
                    string id = Sid(p);
                    if (string.IsNullOrEmpty(id) || id == "0") continue;
                    var gp = ac.GlobalPosition();
                    if (!first) sb.Append(',');
                    first = false;
                    sb.Append("{\"id\":\"").Append(id).Append("\",\"x\":").Append((int)gp.x).Append(",\"z\":").Append((int)gp.z)
                      .Append(",\"k\":\"").Append(AcKind(ac)).Append("\"}");
                }
                sb.Append("]}");
                Out(sb.ToString());
            }
            catch (Exception e) { Log?.LogError("PosTick: " + e); }
        }

        // ======================= AI AIRCRAFT LIMITER =======================
        // Performance precaution against AI over-spawning / clogging runways. Checked ~every 3s.
        // It ONLY ever removes AI aircraft (ac.Player == null) -- a player is never touched.
        //   A) per-side AI cap: each faction may have at most AiPerTeamCap (32) AI flying.
        //   B) total cap: total aircraft (AI + players, all sides) must not exceed AiTotalCap (64);
        //      when over, AI is removed from the side with the MOST aircraft (never a player).
        //   C) stuck: a GROUNDED AI that hasn't moved > AiStuckRadius for AiStuckSeconds (45s) is
        //      cleared, to free a clogged runway. Independent of the caps.
        // Removal = Aircraft.DisableUnit() (the game's own destroy path -> explode + despawn, synced
        // to clients), falling back to ejection if that ever throws. A per-tick budget smooths the ramp.
        const int AiMaxRemovalsPerTick = 12;
        static float _nextAiTick;
        sealed class AiTrack { public Vector3 anchor; public float since; }
        static readonly Dictionary<int, AiTrack> _aiStuck = new Dictionary<int, AiTrack>();

        static Vector3 AcPos(Aircraft ac) { try { var g = ac.GlobalPosition(); return new Vector3(g.x, g.y, g.z); } catch { return Vector3.zero; } }
        static float AcAlt(Aircraft ac) { try { return ac.GlobalPosition().y; } catch { return 99999f; } }
        static bool IsGrounded(Aircraft ac) { try { return ac.IsLanded(); } catch { return false; } }

        internal static void AiLimitTick()
        {
            if (AiLimit == null || !AiLimit.Value) return;
            float now = Time.time;
            if (now < _nextAiTick) return;
            _nextAiTick = now + 5f;   // 5s (was 3s): with mission AI caps now below the 32 limiter cap the
                                      // limiter rarely acts, so this full-scene FindObjectsOfType<Aircraft>
                                      // scan can run less often - fewer frame hitches + less GC. The 45s
                                      // stuck-runway timer means 5s reaction granularity is still fine.
            try
            {
                var sides   = new Dictionary<FactionHQ, List<Aircraft>>();   // every aircraft, per side
                var aiSides = new Dictionary<FactionHQ, List<Aircraft>>();   // AI only, per side
                var live = new HashSet<int>();
                foreach (var ac in UnityEngine.Object.FindObjectsOfType<Aircraft>())
                {
                    if (ac == null) continue;
                    FactionHQ hq = null; try { hq = ac.NetworkHQ; } catch { }
                    if (hq == null) continue;
                    Player pl = null; try { pl = ac.Player; } catch { }
                    if (!sides.TryGetValue(hq, out var L)) { sides[hq] = L = new List<Aircraft>(); aiSides[hq] = new List<Aircraft>(); }
                    L.Add(ac);
                    if (pl == null)                                          // AI aircraft (no human pilot)
                    {
                        aiSides[hq].Add(ac);
                        int id = ac.GetInstanceID(); live.Add(id);
                        Vector3 pos = AcPos(ac);
                        float r = AiStuckRadius.Value;
                        if (!_aiStuck.TryGetValue(id, out var t)) _aiStuck[id] = new AiTrack { anchor = pos, since = now };
                        else if ((pos - t.anchor).sqrMagnitude > r * r) { t.anchor = pos; t.since = now; }
                    }
                }
                if (_aiStuck.Count > 0)                                      // forget aircraft that no longer exist
                {
                    var goneIds = new List<int>();
                    foreach (var k in _aiStuck.Keys) if (!live.Contains(k)) goneIds.Add(k);
                    foreach (var k in goneIds) _aiStuck.Remove(k);
                }

                var removed = new HashSet<Aircraft>();
                int budget = AiMaxRemovalsPerTick;
                void Remove(Aircraft ac, string why)
                {
                    if (ac == null || budget <= 0 || removed.Contains(ac)) return;
                    if (ac.Player != null) return;                          // SAFETY: never remove a player's aircraft
                    removed.Add(ac); budget--;
                    try { ac.DisableUnit(); }
                    catch (Exception e) { try { ac.StartEjectionSequence(); } catch { } Log?.LogWarning("[ailimit] DisableUnit fell back to eject: " + e.Message); }
                    Log?.LogInfo("[ailimit] cleared AI aircraft (" + why + ")");
                }
                IEnumerable<Aircraft> Removable(List<Aircraft> ai, int n) =>
                    ai.Where(a => a != null && a.Player == null && !removed.Contains(a)).OrderBy(AcAlt).Take(n);

                // RULE C: stuck grounded AI (independent of the caps)
                int stuckSec = AiStuckSeconds.Value;
                if (stuckSec > 0)
                    foreach (var ai in aiSides.Values)
                        foreach (var ac in ai)
                        {
                            if (ac == null || removed.Contains(ac)) continue;
                            int id = ac.GetInstanceID();
                            if (_aiStuck.TryGetValue(id, out var t) && now - t.since >= stuckSec && IsGrounded(ac))
                            { Remove(ac, "stuck " + stuckSec + "s on the ground"); _aiStuck.Remove(id); }
                        }

                // RULE A: per-side AI cap
                int perCap = AiPerTeamCap.Value;
                if (perCap > 0)
                    foreach (var kv in aiSides)
                    {
                        int n = kv.Value.Count(a => a != null && !removed.Contains(a)) - perCap;
                        if (n > 0) foreach (var ac in Removable(kv.Value, n)) Remove(ac, "team AI cap " + perCap);
                    }

                // RULE B: total aircraft cap -> trim AI from the busiest side (never a player)
                int totalCap = AiTotalCap.Value;
                if (totalCap > 0)
                {
                    int Eff(FactionHQ h) => sides[h].Count(a => a != null && !removed.Contains(a));
                    int total = 0; foreach (var h in sides.Keys) total += Eff(h);
                    while (total > totalCap && budget > 0)
                    {
                        FactionHQ busiest = null; int best = -1;
                        foreach (var h in sides.Keys)
                        {
                            if (!aiSides[h].Any(a => a != null && a.Player == null && !removed.Contains(a))) continue;
                            int e = Eff(h);
                            if (e > best) { best = e; busiest = h; }
                        }
                        if (busiest == null) break;                         // no removable AI anywhere
                        var victim = Removable(aiSides[busiest], 1).FirstOrDefault();
                        if (victim == null) break;
                        Remove(victim, "total cap " + totalCap);
                        total--;
                    }
                }

                EmitAir(sides, aiSides, removed);
                EmitEntities(sides, removed);
            }
            catch (Exception e) { Log?.LogError("AiLimitTick: " + e); }
        }

        // -------- live map entity feed: per-entity world positions for the command-centre map.
        // Runs once per AiLimitTick (~5s), right after EmitAir. Two arrays:
        //   "a" = AI aircraft only (ac.Player==null, not removed this tick) -> {i,x,z,f,k,g}
        //         i=GetInstanceID (client interpolation key), f=faction, k=plane/heli, g=grounded
        //   "s" = all ships (one FindObjectsOfType<Ship> scan) -> {i,x,z,f,c} where c=class
        // Everything is guarded per-unit: a throw skips that one unit, never the whole feed. --------
        static void EmitEntities(Dictionary<FactionHQ, List<Aircraft>> sides, HashSet<Aircraft> removed)
        {
            try
            {
                var sb = new StringBuilder(512);
                sb.Append("{\"t\":\"ent\",\"a\":[");
                bool first = true;
                foreach (var kv in sides)
                {
                    string fn = ""; try { fn = kv.Key.faction != null ? kv.Key.faction.factionName : ""; } catch { }
                    foreach (var ac in kv.Value)
                    {
                        try
                        {
                            if (ac == null || removed.Contains(ac)) continue;
                            if (ac.Player != null) continue;                  // AI aircraft only
                            try { if (ac.disabled) continue; } catch { }      // skip mid-despawn ghosts
                            var gp = ac.GlobalPosition();
                            if (!first) sb.Append(',');
                            first = false;
                            sb.Append("{\"i\":").Append(ac.GetInstanceID())
                              .Append(",\"x\":").Append((int)gp.x).Append(",\"z\":").Append((int)gp.z)
                              .Append(",\"f\":\"").Append(Esc(fn)).Append("\"")
                              .Append(",\"k\":\"").Append(AcKind(ac)).Append("\"")
                              .Append(",\"g\":").Append(IsGrounded(ac) ? 1 : 0).Append('}');
                        }
                        catch { }                                             // fail-safe: skip this aircraft
                    }
                }
                sb.Append("],\"s\":[");
                first = true;
                foreach (var sh in UnityEngine.Object.FindObjectsOfType<Ship>())
                {
                    try
                    {
                        if (sh == null) continue;
                        try { if (sh.disabled) continue; } catch { }          // skip mid-despawn ghosts
                        FactionHQ hq = null; try { hq = sh.NetworkHQ; } catch { }
                        if (hq == null) continue;                             // skip ships with no side
                        string fn = ""; try { fn = hq.faction != null ? hq.faction.factionName : ""; } catch { }
                        var gp = sh.GlobalPosition();
                        if (!first) sb.Append(',');
                        first = false;
                        sb.Append("{\"i\":").Append(sh.GetInstanceID())
                          .Append(",\"x\":").Append((int)gp.x).Append(",\"z\":").Append((int)gp.z)
                          .Append(",\"f\":\"").Append(Esc(fn)).Append("\"")
                          .Append(",\"c\":\"").Append(ShipClass(sh)).Append("\"}");
                    }
                    catch { }                                                 // fail-safe: skip this ship
                }
                sb.Append("]}");
                Out(sb.ToString());
            }
            catch (Exception e) { Log?.LogError("EmitEntities: " + e); }
        }

        // plane vs heli, cached per AircraftDefinition. A heli has a CompoundHeloController in
        // its hierarchy; failing that we fall back to a known heli jsonKey set.
        static readonly Dictionary<AircraftDefinition, string> _acKindCache = new Dictionary<AircraftDefinition, string>();
        static readonly HashSet<string> _heliKeys = new HashSet<string> { "AttackHelo1", "QuadVTOL1" };
        static string AcKind(Aircraft ac)
        {
            try
            {
                AircraftDefinition def = null; try { def = ac.definition; } catch { }
                if (def != null && _acKindCache.TryGetValue(def, out var cached)) return cached;
                string kind = "p";
                try { if (ac.GetComponentInChildren<CompoundHeloController>() != null) kind = "h"; } catch { }
                if (kind == "p" && def != null) { try { if (_heliKeys.Contains(def.jsonKey)) kind = "h"; } catch { } }
                if (def != null) _acKindCache[def] = kind;
                return kind;
            }
            catch { return "p"; }
        }

        // ship class string for the map, cached per ShipDefinition.
        static readonly Dictionary<ShipDefinition, string> _shipClassCache = new Dictionary<ShipDefinition, string>();
        static string ShipClass(Ship sh)
        {
            try
            {
                var def = sh.definition as ShipDefinition;
                if (def == null) return "corvette";
                if (_shipClassCache.TryGetValue(def, out var cached)) return cached;
                string cls;
                switch (def.shipType)
                {
                    case ShipType.CV:  case ShipType.LHA: cls = "carrier";   break;
                    case ShipType.DDG:                    cls = "destroyer"; break;
                    case ShipType.FFG:                    cls = "argus";     break;
                    case ShipType.FFL:                    cls = "corvette";  break;
                    case ShipType.LFD: case ShipType.LC:  cls = "cursor";    break;
                    default:                              cls = "corvette";  break;
                }
                _shipClassCache[def] = cls;
                return cls;
            }
            catch { return "corvette"; }
        }

        // live AI/player aircraft counts for the web command centre (per side + totals + caps)
        static void EmitAir(Dictionary<FactionHQ, List<Aircraft>> sides,
                            Dictionary<FactionHQ, List<Aircraft>> aiSides, HashSet<Aircraft> removed)
        {
            try
            {
                var sb = new StringBuilder(192);
                sb.Append("{\"t\":\"air\",\"s\":[");
                bool first = true; int totAi = 0, totPl = 0;
                foreach (var kv in sides)
                {
                    int ai = aiSides[kv.Key].Count(a => a != null && !removed.Contains(a));
                    int pl = kv.Value.Count(a => a != null && a.Player != null && !removed.Contains(a));
                    totAi += ai; totPl += pl;
                    string fn = ""; try { fn = kv.Key.faction != null ? kv.Key.faction.factionName : ""; } catch { }
                    if (!first) sb.Append(',');
                    first = false;
                    sb.Append("{\"n\":\"").Append(Esc(fn)).Append("\",\"ai\":").Append(ai).Append(",\"pl\":").Append(pl).Append('}');
                }
                sb.Append("],\"ai\":").Append(totAi).Append(",\"pl\":").Append(totPl)
                  .Append(",\"teamcap\":").Append(AiPerTeamCap.Value).Append(",\"totcap\":").Append(AiTotalCap.Value).Append('}');
                Out(sb.ToString());
            }
            catch (Exception e) { Log?.LogError("EmitAir: " + e); }
        }
        // ===================== end AI AIRCRAFT LIMITER =====================

        // -------- PvP team-balance: the other faction + a per-player message --------
        internal static FactionHQ OtherHQ(FactionHQ target)
        {
            try
            {
                foreach (var hq in UnityEngine.Object.FindObjectsOfType<FactionHQ>())
                    if (hq != null && hq != target) return hq;
            }
            catch { }
            return null;
        }

        internal void TellPlayer(Player p, string msg)
        {
            try
            {
                var cm = Cm;
                if (cm == null) cm = (Cm = UnityEngine.Object.FindObjectOfType<ChatManager>());   // Unity-null-safe: `??` would keep a DESTROYED ref (fake-null)
                if (cm != null && p != null && p.Owner != null) cm.RpcTargetServerMessage(p.Owner, msg, false);
                else Log?.LogWarning($"[tell] SKIP send: cm={(cm != null)} p={(p != null)} owner={(p != null && p.Owner != null)} len={(msg != null ? msg.Length : 0)}");
            }
            catch (Exception e) { Log?.LogError("TellPlayer: " + e); }
        }

        // Private command list (the !help reply). Sent natively from the plugin via TellPlayer -- the SAME
        // path as !spec's confirmation, which renders reliably -- instead of the bot's relayed 'tell' verb
        // (which logged "delivering" but never rendered). Built here so no text is relayed. ONE message with
        // \n line breaks; the diagnostic log records the size + ChatManager state so a non-render is visible.
        // NOTE: keep this list in sync with help_lines() in no_mapvote_bot.py (the all-chat fallback there).
        internal void SendHelp(Player p)
        {
            try
            {
                string[] lines = {                                      // keep IN SYNC with help_lines() in no_mapvote_bot.py
                    "<color=#FFFF00>=== SERVER COMMANDS ===</color>",
                    "<color=#55FF55>!votemap</color> - vote to change the map",
                    "<color=#55FF55>!rank</color> - your rank & points to next",
                    "<color=#55FF55>!skill</color> - your skill rating (points/life)",
                    "<color=#55FF55>!points</color> - points this life / last life",
                    "<color=#55FF55>!leaderboard</color> - top pilots",
                    "<color=#55FF55>!spec</color> - go to spectator",
                    "<color=#55FF55>!balance</color> - how team balancing works",
                    "<color=#55FF55>!squadup <player></color> - squad up for PvP (!y to accept)",
                    "<color=#55FF55>!forfeit</color> - vote to surrender (PvP; !ff)",
                    "<color=#55FF55>!notk</color> - no-team-killing policy",
                    "<color=#55FF55>!help</color> - this command list",
                };
                string msg = string.Join("\n", lines);
                Log?.LogInfo($"[help] -> {Sid(p)} : {lines.Length} lines, {msg.Length} chars, Cm={(Cm != null)}");
                TellPlayer(p, msg);
            }
            catch (Exception e) { Log?.LogError("SendHelp: " + e); }
        }

        // -------- player-vs-player kill (for the +bonus + "splashed" announce) --------
        // FactionHQ.ReportKillAction(killer, target, factor) fires for every kill a player
        // scores. We only report it when the TARGET is a human's aircraft on the OPPOSING
        // side (target.Player != null) -> i.e. a player downed an enemy player.
        internal static void OnKill(Player killer, object targetObj)
        {
            try
            {
                if (killer == null) return;
                string kid = Sid(killer);
                if (string.IsNullOrEmpty(kid) || kid == "0") return;
                if (targetObj is PilotDismounted) return;                   // hide ejected/rescued pilots entirely

                if (CustomKillFeed != null && CustomKillFeed.Value)
                {
                    RegisterStreakKill(killer);                              // count EVERY (non-pilot) kill toward the 5s streak
                    if (targetObj is Ship sunk) MaybeAnnounceShipSink(killer, sunk);   // any ship-sink callout
                }

                var ac = targetObj as Aircraft;          // players fly aircraft; AI aircraft have no Player
                if (ac == null) return;
                Player victim = ac.Player;
                if (victim == null) return;
                string vid = Sid(victim);
                if (string.IsNullOrEmpty(vid) || vid == "0" || vid == kid) return;     // human victim, not self
                if (killer.HQ != null && victim.HQ != null && killer.HQ == victim.HQ) return;  // enemy team only
                Out("{\"t\":\"kill\",\"kid\":\"" + kid + "\",\"kn\":\"" + Esc(RawNameOf(killer)) +
                    "\",\"vid\":\"" + vid + "\",\"vn\":\"" + Esc(RawNameOf(victim)) + "\"}");
            }
            catch (Exception e) { Log?.LogError("OnKill: " + e); }
        }

        // ======== custom kill feed: streak callouts + capital-ship sinks (native feed suppressed) ========
        sealed class KStreak { public float first, last; public int count, tier; }
        static readonly Dictionary<string, KStreak> _streaks = new Dictionary<string, KStreak>(StringComparer.Ordinal);
        static readonly Dictionary<int, float> _sunkShips = new Dictionary<int, float>();
        const float STREAK_WINDOW = 5f, STREAK_SETTLE = 0.8f;

        // Strategic-launcher (piledriver / ballistic / cruise) shot-downs spam the feed;
        // coalesce a burst into ONE summary line.
        static int _stratStrikes;
        static float _stratLast;
        static bool IsStrategicLauncher(string name)
        {
            if (string.IsNullOrEmpty(name)) return false;
            string n = name.ToLowerInvariant();
            return n.Contains("piledriver") || n.Contains("launcher") || n.Contains("ballistic")
                || n.Contains("strategic") || n.Contains("cruise");
        }
        internal static void PumpStrategic()
        {
            // Strategic-strike announce REMOVED (2026-06-24, user: "didn't work how I'd hoped").
            // Keep draining the counter so it can't accumulate; broadcast nothing.
            if (_stratStrikes != 0) _stratStrikes = 0;
        }

        static int StreakTier(int n) => n >= 50 ? 4 : n >= 25 ? 3 : n >= 10 ? 2 : n >= 5 ? 1 : 0;
        static string TierColour(int t) => t >= 4 ? "#FF1493" : t == 3 ? "#FF3B3B" : t == 2 ? "#FF8C00" : "#FFD200";

        // faction colour for a player's NAME in the kill feed (blue Boscali / red Primeva).
        static string FactionColour(Player p)
        {
            try
            {
                string f = (p != null && p.HQ != null && p.HQ.faction != null) ? p.HQ.faction.factionName : "";
                f = (f ?? "").ToLowerInvariant();
                if (f.StartsWith("bosc") || f == "bdf") return "#5BA3FF";
                if (f.StartsWith("prim") || f == "pala") return "#FF6B5B";
            }
            catch { }
            return "#CFCFCF";
        }

        // "[ABBR] Name" - rank tag in the RANK colour, name in the player's TEAM colour.
        static string RankNameTag(Player p)
        {
            string raw = SafeText(RawNameOf(p));
            string fc = FactionColour(p);
            LoadRankMap();
            if (RankMap.TryGetValue(Sid(p), out var rc) && !string.IsNullOrEmpty(rc.label))
                return $"<color={rc.color}>[{rc.label}]</color> <color={fc}>{raw}</color>";
            return $"<color={fc}>{raw}</color>";
        }

        // Count one kill (any unit) toward the killer's rolling 5s streak.
        static void RegisterStreakKill(Player p)
        {
            string sid = Sid(p);
            if (string.IsNullOrEmpty(sid) || sid == "0") return;
            float now = Time.time;
            if (!_streaks.TryGetValue(sid, out var s)) { _streaks[sid] = new KStreak { first = now, last = now, count = 1, tier = 0 }; return; }
            if (now - s.first > STREAK_WINDOW) { s.first = now; s.count = 1; s.tier = 0; }   // window closed -> fresh streak
            else s.count++;
            s.last = now;
        }

        // Off HQTick: once a burst settles (~0.8s), announce the exact count at each NEW tier (5/10/25/50).
        internal static void PumpKillStreaks()
        {
            if (_streaks.Count == 0) return;
            float now = Time.time;
            List<string> drop = null;
            foreach (var kv in _streaks)
            {
                var s = kv.Value;
                int tier = StreakTier(s.count);
                if (s.count >= 5 && now - s.last >= STREAK_SETTLE && tier > s.tier)
                {
                    s.tier = tier;                                           // mark first so we don't repeat this tier
                    var p = FindPlayerBySid(kv.Key);
                    if (p != null)
                    {
                        string n = s.count >= 50 ? "50+" : s.count.ToString();
                        string ln = RenderKillFeed("streak",
                            $"<color={TierColour(tier)}>{n}</color> <color=#FFFFFF>confirmed kills for</color> {RankNameTag(p)}<color=#FFFFFF>!</color>",
                            RawNameOf(p), "", "", "", "", n, "", "");
                        if (ln != null) Instance?.BroadcastAll(ln);
                        Log?.LogInfo($"[killfeed] streak {s.count} for {RawNameOf(p)} (tier {tier})");
                    }
                }
                if (now - s.last > 8f) (drop ?? (drop = new List<string>())).Add(kv.Key);   // stale cleanup
            }
            if (drop != null) foreach (var k in drop) _streaks.Remove(k);
        }

        // Ship sink -> one celebratory broadcast (deduped by ship instance). Fires for EVERY ship
        // class (the caller already gated on `is Ship`), each with its own class label + colour.
        static void MaybeAnnounceShipSink(Player killer, Unit ship)
        {
            try
            {
                if (killer == null || ship == null || ship.definition == null) return;
                float now = Time.time;
                int id = ship.GetInstanceID();
                if (_sunkShips.TryGetValue(id, out var t) && now - t < 15f) return;        // already announced this sink
                _sunkShips[id] = now;
                if (_sunkShips.Count > 64)                                                  // bounded cleanup
                {
                    var old = new List<int>();
                    foreach (var kv in _sunkShips) if (now - kv.Value > 30f) old.Add(kv.Key);
                    foreach (var k in old) _sunkShips.Remove(k);
                }
                string nm = SafeText(ship.definition.unitName ?? "");
                string low = nm.ToLowerInvariant();
                // The target is already known to be a Ship (gated at the call site), so EVERY ship type
                // announces. Pick a sensible class label + colour from the unit name; any ship not
                // specifically recognised still fires as a generic SHIP (never excluded).
                string cls, colour;
                if      (low.Contains("carrier"))   { cls = "CARRIER";   colour = "#FF1493"; }
                else if (low.Contains("destroyer")) { cls = "DESTROYER"; colour = "#FF8C00"; }
                else if (low.Contains("frigate"))   { cls = "FRIGATE";   colour = "#FFC000"; }
                else if (low.Contains("corvette"))  { cls = "CORVETTE";  colour = "#FFD000"; }
                else if (low.Contains("argus"))     { cls = "ARGUS";     colour = "#00E5FF"; }
                else if (low.Contains("dynamo"))    { cls = "DYNAMO";    colour = "#22B0FF"; }
                else if (low.Contains("shard"))     { cls = "SHARD";     colour = "#7CFFB0"; }
                else if (low.Contains("cursor"))    { cls = "CURSOR";    colour = "#C080FF"; }
                else                                { cls = "SHIP";      colour = "#FF8C00"; }
                // chat-feed style line: "[RANK] Name sunk <ship>"
                string ln = RenderKillFeed("ship_sink",
                    $"{RankNameTag(killer)} <color=#FFFFFF>sunk</color> <color={colour}>{nm}</color>",
                    RawNameOf(killer), "", "", "", "", "", nm, "");
                if (ln != null) Instance?.BroadcastAll(ln);
                Log?.LogInfo($"[killfeed] {cls} sink: {nm} by {RawNameOf(killer)}");
            }
            catch (Exception e) { Log?.LogError("MaybeAnnounceShipSink: " + e); }
        }

        // -------- rich unit label for the kill feed --------
        // On spawn we set a player aircraft's networked unitName to
        // "<rank-colour>ABBR</rank-colour> Name [Plane]". The native kill feed renders
        // unitName wrapped in the faction colour, so the ABBR shows in the rank colour and
        // the name+plane inherit the faction colour. NOTE: unitName is ALSO used on radar /
        // target labels / refuel-rearm text, so this shows there too (accepted trade-off).
        // Rank is read from plugin_ranks.txt and only refreshes on the player's next spawn.
        internal void LabelAircraft(Player p)
        {
            try
            {
                if (p == null) return;
                var ac = p.Aircraft;
                if (ac == null) return;
                string id = Sid(p);
                if (string.IsNullOrEmpty(id) || id == "0") return;
                string plane = "";
                try { var d = ac.definition; if (d != null) plane = SafeText(d.unitName); } catch { }
                if (CustomKillFeed != null && CustomKillFeed.Value)
                {
                    // Custom kill feed on: the native feed (which read unitName) is suppressed, so the unit
                    // label no longer needs the player's name. Show the PLANE only -> a pilot's name appears
                    // once, via their chat name (PlayerName), not duplicated on radar / map / lock-on.
                    ac.NetworkunitName = plane;
                    return;
                }
                // Custom kill feed OFF: the native feed shows and reads this unit label. With RankInName ON,
                // keep the SHORTHAND bracket tag ([ABBR] Name) so the native feed / radar / HUD lock show the
                // same [Rank] form as chat - never the un-bracketed full-rank form. With RankInName OFF the
                // label stays pure vanilla (raw name, no prefix, no [plane] decoration).
                string name = SafeText(RawNameOf(p));
                ac.NetworkunitName = (RankInName != null && RankInName.Value) ? Prefixed(id, name) : name;
            }
            catch (Exception e) { Log?.LogError("LabelAircraft: " + e); }
        }

        // -------- RankInName: embed the player's rank into their chat NAME --------
        // Strip any "[RANK] " prefix we previously added, to recover the real name.
        static string StripPrefix(string n)
        {
            if (string.IsNullOrEmpty(n) || n.Length < 2 || n[0] != '[') return n;
            int c = n.IndexOf(']');
            if (c > 0 && c + 2 <= n.Length && n[c + 1] == ' ') return n.Substring(c + 2);
            return n;
        }

        // The player's REAL name: from our cache if known, else strip a prefix off PlayerName.
        static string RawNameOf(Player p)
        {
            try
            {
                if (p == null) return "";
                string sid = Sid(p);
                if (!string.IsNullOrEmpty(sid) && RawNames.TryGetValue(sid, out var r) && !string.IsNullOrEmpty(r))
                    return r;
                return StripPrefix(p.PlayerName);
            }
            catch { return p != null ? p.PlayerName : ""; }
        }

        // "[ABBR] raw" when the bot has pushed a rank for this player; plain "raw" otherwise (so
        // we never show a guessed/wrong rank). The total is capped at 32 chars (the game runs
        // SanitizeRichText(32) on the name) by trimming the raw tail, so the rank tag itself is
        // never the part that gets clipped. The full raw name is still cached in RawNames for
        // the bot, so this only affects the in-game display of very long names.
        static string Prefixed(string sid, string raw)
        {
            LoadRankMap();
            if (RankMap.TryGetValue(sid, out var rc) && !string.IsNullOrEmpty(rc.label))
            {
                string tag = "[" + rc.label + "] ";         // SHORTHAND rank in name: consistent with the kill feed,
                                                            // and avoids the full-rank "[Flying Officer]" duplicate the
                                                            // HUD lock / map marker show alongside the unitName label.
                int room = 32 - tag.Length;
                if (room < 1) return raw;                       // pathological: tag alone fills the cap
                if (raw.Length > room) raw = raw.Substring(0, room);
                return tag + raw;
            }
            return raw;
        }

        // Called from the CmdSetPlayerName prefix: rewrite the name the client is setting (its
        // FIRST and only set -- the game rejects later sets) to include the rank tag, and remember
        // the real name. The tag is applied ONCE here, at join: returning players (rank already in
        // plugin_ranks.txt) show "[ABBR] Name" immediately; a brand-new player whose rank isn't
        // known yet shows the plain name this session and picks up the tag next session. We do NOT
        // re-tag mid-session: writing PlayerName later fires the NameChanged->JoinMessage hook on
        // each CLIENT (the server process is headless, base.IsHost==false, so we can't intercept
        // it), which would show a spurious duplicate "joined the game". The kill-feed/radar rank
        // (aircraft unitName in LabelAircraft) still refreshes every spawn, so live rank is shown.
        internal static void InjectRankIntoName(Player p, ref string name)
        {
            // Owned by Chat.RankInName ALONE - independent of the killfeed mode. (A 0.9.48 change gated this
            // on KillFeed.Custom, so turning the custom feed off silently killed rank embedding even with
            // RankInName still ON - exactly the coupling the owner reported. Decoupled in 1.0.1.)
            if (RankInName == null || !RankInName.Value || p == null) return;
            if (!string.IsNullOrEmpty(p.PlayerName)) return;    // only the first set; later sets are rejected anyway
            string sid = Sid(p);
            if (string.IsNullOrEmpty(sid) || sid == "0") return;
            string raw = name ?? "";
            RawNames[sid] = raw;
            name = Prefixed(sid, raw);
        }

        // -------- dismounted-pilot cleanup --------
        internal static void MaybeCleanupPilots()
        {
            try
            {
                if (CleanupPilots == null || !CleanupPilots.Value) return;
                float now = Time.time;
                if (now < _nextPilotSweep) return;
                _nextPilotSweep = now + 30f;                       // sweep at most every 30s
                float maxAge = Mathf.Max(30, PilotLifetime != null ? PilotLifetime.Value : 300);

                var live = UnityEngine.Object.FindObjectsOfType<PilotDismounted>();
                var seen = new HashSet<PilotDismounted>();
                int removed = 0;
                foreach (var pilot in live)
                {
                    if (pilot == null) continue;
                    seen.Add(pilot);
                    if (!PilotSeen.TryGetValue(pilot, out var first)) { PilotSeen[pilot] = now; continue; }
                    if (now - first < maxAge) continue;
                    try
                    {
                        if (pilot.Networkplayer != null) pilot.Networkplayer.RemovePilotDismounted(pilot);
                    }
                    catch { }
                    UnityEngine.Object.Destroy(pilot.gameObject);     // same despawn the game uses on capture/landing
                    removed++;
                }
                // forget pilots that are gone (captured/destroyed) so the dict doesn't grow
                foreach (var key in new List<PilotDismounted>(PilotSeen.Keys))
                    if (key == null || !seen.Contains(key)) PilotSeen.Remove(key);
                if (removed > 0) Log?.LogInfo($"[cleanup] despawned {removed} lingering pilot(s) (> {maxAge}s)");
            }
            catch (Exception e) { Log?.LogError("MaybeCleanupPilots: " + e); }
        }

        // -------- end of game: authoritative winner + awards --------
        internal void OnDeclareEndGame(FactionHQ hq, string endType)
        {
            try
            {
                if (Time.time - _lastEnd < 20f) return;                 // debounce paired/dup calls
                if (!string.Equals(endType, "Victory", StringComparison.OrdinalIgnoreCase)) return;
                _lastEnd = Time.time;
                // NOTE: do NOT advance the balance game-counter here - that happens once per mission START
                // (AdvanceGame in StartingRankFloorPatch), so move-exemptions span whole games correctly.

                var players = Humans();
                string winFaction = hq != null && hq.faction != null ? hq.faction.factionName : "";
                EmitAll("snap");                                        // final authoritative scores
                Out("{\"t\":\"win\",\"f\":\"" + Esc(winFaction) + "\"}");

                foreach (var p in players)                              // +WinPoints to the winning side
                    if (p.HQ == hq) Award(p, WinPoints.Value, "win");

                var ranked = players.OrderByDescending(ScoreOf).ToList();   // placement bonuses
                int[] bonus = { FirstPlace.Value, SecondPlace.Value, ThirdPlace.Value };
                string[] tag = { "1st", "2nd", "3rd" };
                for (int i = 0; i < ranked.Count && i < 3; i++) Award(ranked[i], bonus[i], tag[i]);

                Out("{\"t\":\"end\"}");
                Log.LogInfo($"NukeStats: end-of-game, winner={winFaction}, {players.Count} players.");
                // v0.8.7: NO match-end eject/bank - a skill life now PERSISTS across the match and ends
                // only on death or mid-air eject (the bot keeps the running per-life score in curLife).
            }
            catch (Exception e) { Log?.LogError("OnDeclareEndGame: " + e); }
        }

        static double ScoreOf(Player p)
        {
            try { return Convert.ToDouble(p.PlayerScore, CultureInfo.InvariantCulture); }
            catch { return 0; }
        }

        static void Award(Player p, int pts, string reason)
        {
            if (p == null || pts == 0) return;
            string id = Sid(p);
            if (string.IsNullOrEmpty(id) || id == "0") return;
            Out("{\"t\":\"award\",\"id\":\"" + id + "\",\"n\":\"" + Esc(RawNameOf(p)) +
                "\",\"pts\":" + pts + ",\"reason\":\"" + reason + "\"}");
        }

        // ======================= NuclearSkill: per-life skill tracking (v0.8.7) =======================
        // PERSISTENT points-per-DEATH. The running per-life SCORE now lives in the BOT (rec["curLife"],
        // fed by snap deltas) so it survives disconnects AND match-ends. This plugin is just the life
        // EVENT detector: it emits a "life" event (reason "death" or "eject") ONLY when the pilot DIES or
        // EJECTS mid-air. A ground dismount, a disconnect, a match-end, and a balance/admin move are all
        // life-NEUTRAL (the life stays open; the bot keeps accumulating). Captures emit a "capbonus"
        // event the bot folds into the current life. The bot derives the rating (points-per-life) + 0-10.
        internal static ConfigEntry<int>  CaptureSkillBonus, WinSkillBonus, LossSkillBonus;
        internal static ConfigEntry<bool> BalanceBySkill;

        // alive = a life is open; airborne = the aircraft was in the air last scan (tells a real mid-air
        // EJECT from a ground dismount). _balancing = SteamIDs ejected by an admin/balance move, so
        // SkillTick treats their aircraft-loss as life-NEUTRAL (balancing never ruins a rank).
        sealed class Life { public bool alive; public bool airborne; }
        static readonly Dictionary<string, Life> _lives = new Dictionary<string, Life>(StringComparer.Ordinal);
        static readonly HashSet<string> _balancing = new HashSet<string>(StringComparer.Ordinal);
        // AdminEject also stamps this guard (sid -> expiry time). The ON-DEATH path (CheckTeamkill, via the
        // ReportKilled patch) checks it and SKIPS the death + the "down"/"went down" killfeed for an admin- or
        // team-swap-ejected pilot - so an AIRBORNE eject (a balance move of a flyer, or the !swapteam/!forceteamswap
        // Cricket) is truly life- AND feed-neutral. (_balancing alone only neutralises the slower 1Hz SkillTick
        // scan; the on-death patch fires first and would otherwise bank a phantom death + spam chat.)
        static readonly Dictionary<string, float> _adminEjectGuard = new Dictionary<string, float>(StringComparer.Ordinal);
        internal static bool IsAdminEjecting(string sid) => !string.IsNullOrEmpty(sid) && _adminEjectGuard.TryGetValue(sid, out var exp) && Time.time < exp;
        internal static void GuardEject(string sid)
        {
            if (string.IsNullOrEmpty(sid) || sid == "0") return;
            float now = Time.time;
            _adminEjectGuard[sid] = now + 6f;                          // covers the async ReportKilled after StartEjectionSequence
            if (_adminEjectGuard.Count > 16)                           // opportunistic prune of expired entries
            {
                List<string> stale = null;
                foreach (var kv in _adminEjectGuard) if (kv.Value < now) (stale ?? (stale = new List<string>())).Add(kv.Key);
                if (stale != null) foreach (var s in stale) _adminEjectGuard.Remove(s);
            }
        }
        static Life LifeOf(string sid) { if (!_lives.TryGetValue(sid, out var l)) { l = new Life(); _lives[sid] = l; } return l; }
        static float _nextLifeScan;

        // Signal a completed life. The bot holds the running score (curLife) and banks it on receipt;
        // we just emit the END. reason = "death" (shot down/crashed) or "eject" (bailed from an airborne
        // plane). Ground dismounts, disconnects, match-end and balance/admin moves do NOT end a life.
        static void EndLife(string sid, Life l, string reason)
        {
            if (l == null || !l.alive) return;
            l.alive = false;
            if (string.IsNullOrEmpty(sid) || sid == "0") return;
            Out("{\"t\":\"life\",\"id\":\"" + sid + "\",\"r\":\"" + reason + "\"}");
            Log?.LogInfo($"[skill] life ended {sid} ({reason})");
        }

        // A capture adds CaptureBonus to the capturing player's CURRENT life - emitted as a "capbonus"
        // event the bot folds into curLife (banked at the next death/eject). Hooked from ReportCaptureLocationAction.
        internal static void OnCapture(Player p)
        {
            try
            {
                if (p == null) return;
                string sid = Sid(p); if (string.IsNullOrEmpty(sid) || sid == "0") return;
                var l = LifeOf(sid);
                if (!l.alive) l.alive = true;                                 // capture => active life
                int b = CaptureSkillBonus != null ? CaptureSkillBonus.Value : 250;
                Out("{\"t\":\"capbonus\",\"id\":\"" + sid + "\",\"pts\":" + b + "}");
                Log?.LogInfo($"[skill] capture +{b} for {RawNameOf(p)}");
            }
            catch (Exception e) { Log?.LogError("OnCapture: " + e); }
        }

        // Eject a player by ADMIN/BALANCE action (move/spectate/probation/teamkill-warn). Marks them so
        // SkillTick treats the resulting aircraft-loss as life-NEUTRAL - balancing never ruins a rank.
        internal static void AdminEject(Player p)
        {
            try
            {
                if (p == null || p.Aircraft == null) return;
                string sid = Sid(p);
                if (!string.IsNullOrEmpty(sid) && sid != "0") { _balancing.Add(sid); GuardEject(sid); }
                p.Aircraft.StartEjectionSequence();
            }
            catch (Exception e) { Log?.LogWarning("AdminEject: " + e); }
        }

        // Driven from HQTick (~1s): detect life START (got an aircraft) and real AIR-EJECT (lost an
        // airborne plane with no admin move). The running per-life SCORE lives in the BOT (curLife), so
        // we only emit the discrete life-END events here (death is emitted from the kill patch). Ground
        // dismounts, disconnects, match-end and balance/admin moves are all life-NEUTRAL. No match eject.
        internal static void SkillTick()
        {
            float now = Time.time;
            if (now < _nextLifeScan) return;
            _nextLifeScan = now + 1f;
            try
            {
                foreach (var p in Humans())
                {
                    string sid = Sid(p);
                    if (string.IsNullOrEmpty(sid) || sid == "0") continue;
                    var l = LifeOf(sid);
                    bool hasAc = false; try { hasAc = p.Aircraft != null; } catch { }
                    if (hasAc)
                    {
                        _balancing.Remove(sid);                       // flying again -> clear any admin-move marker
                        if (!l.alive) l.alive = true;                 // life start (the bot owns the running score)
                        try { l.airborne = !p.Aircraft.IsLanded(); } catch { l.airborne = true; }
                    }
                    else if (l.alive && l.airborne)
                    {
                        if (_balancing.Contains(sid))
                        {
                            // a balance/admin move ejected them - NOT a real eject: keep the life OPEN and
                            // count nothing (balancing never ruins a rank). Treat like a ground dismount.
                            _balancing.Remove(sid);
                            l.airborne = false;
                        }
                        else
                        {
                            // lost an airborne plane with no admin move -> a real AIR-EJECT -> end + count.
                            // (A real death is closed earlier in the kill patch with reason "death".)
                            EndLife(sid, l, "eject");
                        }
                    }
                    // A GROUND dismount (l.airborne == false) does NOTHING: the life stays OPEN so the
                    // bot's curLife keeps accumulating across sorties; it ends only on death or air-eject.
                }
                // Disconnects are deliberately NOT dropped - the life stays open so the bot keeps
                // accumulating curLife across a reconnect. _lives is bounded by unique SteamIDs per
                // session and is cleared on the daily server restart.
            }
            catch (Exception e) { Log?.LogError("SkillTick scan: " + e); }
        }

        // Skill rating per SteamID (points-per-life), pushed by the bot in plugin_skill.txt as "sid|rating".
        static readonly Dictionary<string, float> _skillMap = new Dictionary<string, float>(StringComparer.Ordinal);
        static float _skillAvg = 0f;
        static long _skillFileTicks = -1;
        static string SkillFilePath => Path.Combine(Paths.GameRootPath, "plugin_skill.txt");
        static void LoadSkillMap()
        {
            try
            {
                var fi = new FileInfo(SkillFilePath);
                if (!fi.Exists || fi.LastWriteTimeUtc.Ticks == _skillFileTicks) return;
                _skillFileTicks = fi.LastWriteTimeUtc.Ticks;
                _skillMap.Clear();
                double sum = 0; int n = 0;
                foreach (var line in File.ReadAllLines(SkillFilePath))
                {
                    var parts = line.Split('|');                            // sid|rating
                    if (parts.Length >= 2 && float.TryParse(parts[1].Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out var r))
                    { _skillMap[parts[0].Trim()] = r; sum += r; n++; }
                }
                _skillAvg = n > 0 ? (float)(sum / n) : 0f;
            }
            catch (Exception e) { Log?.LogError("LoadSkillMap: " + e); }
        }

        // ======================= teamkill enforcement (friendly fire) =======================
        // Detection: Unit.ReportKilled runs for every death; the dead unit's top damager (from
        // damageCredit) who is a PLAYER on the SAME faction as the dead unit = a teamkill (covers
        // friendly buildings/vehicles/aircraft). Escalation PER MATCH: 1st = eject + private warning;
        // 2nd = kick ("next is a ban") + set in-game rank 0 on rejoin; 3rd = ban. Bans persist
        // (plugin_bans.txt) and are enforced by kicking on sight. Defensive: failures no-op (never
        // a false kick). TK is rare/intentional in this game, so auto-enforcement is safe.
        internal static ConfigEntry<bool> TeamkillEnforce;
        internal static ConfigEntry<float> TeamkillMinDamage;   // fairness floor: min credited damage for a friendly kill to COUNT (0 = off)
        internal static ConfigEntry<bool> TeamkillCollateralEnforce;
        internal static ConfigEntry<float> TeamkillCollateralWindow;
        internal static ConfigEntry<float> TeamkillCollateralWindowNuclear;   // forward window for nuke-scale blasts
        internal static ConfigEntry<int> TeamkillSilentMinEnemies;            // overwhelming collateral -> no Moderation entry (0 = tier off)
        internal static ConfigEntry<float> TeamkillSilentRatio;               // ... and enemies must also be >= ratio * friendlies
        internal static ConfigEntry<bool> TeamkillBigUnitExempt;
        internal static ConfigEntry<int> TeamkillCollateralMaxPerMatch;
        struct KillRec { public float t; public float dmg; public bool enemy; public bool big; public string name; }   // name/dmg feed the per-blast unit list in the mod log
        static readonly Dictionary<string, List<KillRec>> _killWin = new Dictionary<string, List<KillRec>>(StringComparer.Ordinal);  // killer sid -> recent kills (any faction) for the collateral window
        // back/fwd frozen at defer time (a live config change must not skew an in-flight verdict). fwd extends
        // PAST the friendly kill for nuke-scale blasts; victims accumulates same-blast friendly names.
        class TkPending { public string sid, victim, method, weapon; public float dmg, eventT, dueAt, back, fwd; public List<string> victims; }
        static readonly List<TkPending> _tkJudge = new List<TkPending>();   // friendly kills awaiting their collateral verdict
        static readonly Dictionary<string, float> _tkReportStart = new Dictionary<string, float>(StringComparer.Ordinal);  // report-only per-event dedup anchor
        static readonly Dictionary<string, float> _tkCollatStart = new Dictionary<string, float>(StringComparer.Ordinal);  // collateral-verdict entry anchor
        // Each queued offence carries ITS OWN victim/method/weapon. method = the contract tag for the moderation
        // report: direct / splash / auto / "" when unknown.
        struct TkEvent { public string sid, victim, method, weapon; public float dmg, eventT; }   // eventT = Time.time of the OFFENCE
        static readonly List<TkEvent> _tkQueue = new List<TkEvent>();
        const int TK_QUEUE_MAX = 64;         // bounded (drained every TkTick; overflow only under an absurd flood)
        static readonly Dictionary<string, int> _tkCount = new Dictionary<string, int>(StringComparer.Ordinal);   // per match
        static readonly HashSet<string> _tkBanned = new HashSet<string>(StringComparer.Ordinal);                  // persistent
        static readonly HashSet<string> _tkRankZero = new HashSet<string>(StringComparer.Ordinal);               // rank 0 on next sight
        static readonly List<KeyValuePair<string, float>> _tkKicks = new List<KeyValuePair<string, float>>();    // delayed kicks
        static readonly Dictionary<string, float> _tkEventStart = new Dictionary<string, float>(StringComparer.Ordinal);  // killer sid -> start of the current blast/event (per-EVENT dedup anchor)
        static readonly Dictionary<string, int> _tkCollateralCount = new Dictionary<string, int>(StringComparer.Ordinal); // per match: exonerating verdicts per sid, for the anti-abuse cap
        const float TK_EVENT_DEDUP = 1.5f;   // one blast/event (same instigator within this window OF THE FIRST kill) = AT MOST one offence; anchored
        static System.Reflection.FieldInfo _dmgCreditFI;
        static float _nextTkScan;

        static string BanFilePath => Path.Combine(Paths.GameRootPath, "plugin_bans.txt");
        internal static void LoadBans()
        {
            try { if (File.Exists(BanFilePath)) foreach (var l in File.ReadAllLines(BanFilePath)) { var s = l.Trim(); if (s.Length > 0) _tkBanned.Add(s); } }
            catch (Exception e) { Log?.LogError("LoadBans: " + e); }
        }
        static void SaveBans()
        {
            try { File.WriteAllText(BanFilePath, string.Join("\n", _tkBanned) + "\n"); }
            catch (Exception e) { Log?.LogError("SaveBans: " + e); }
        }
        internal static void ClearMatchTeamkills() { _tkCount.Clear(); _tkRankZero.Clear(); _tkEventStart.Clear(); _tkReportStart.Clear(); _tkCollatStart.Clear(); _killWin.Clear(); _tkJudge.Clear(); _tkQueue.Clear(); _tkKicks.Clear(); _tkCollateralCount.Clear(); _lastLaunch.Clear(); _lastNuclearLaunch.Clear(); }   // per-match reset (bans persist)

        // Emit a teamkill-moderation event to the bot ([NOSTATS] line it tails) -> activity log + the webcc
        // Moderation tab, recording WHAT caused the eject/kick/ban (the teammate killed + the offense count).
        // ts=0 -> the bot stamps the real time on ingest.
        static void EmitTkMod(string sid, Player p, string action, int count, TkEvent ev, string nc = "",
                              List<KillRec> units = null)
        {
            string nm = p != null ? RawNameOf(p) : sid;
            string victim = !string.IsNullOrEmpty(ev.victim) ? ev.victim : "a teammate";
            // nc = not-counted reason ("auto"/"no-weapon"/"below-floor"/"collateral"/"big-unit"); "" = counted.
            // ts = wall-clock time of the OFFENCE itself (back-dated from ev.eventT); ts<=0 -> the bot stamps ingest time.
            double ts = 0;
            try
            {
                float age = ev.eventT > 0f ? Mathf.Max(0f, Time.time - ev.eventT) : 0f;
                ts = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0 - age;
            }
            catch { }
            // units = every unit that died in the same blast window. f: e=enemy, f=friendly; d=credited damage.
            // Capped at 24 + an overflow count so a city-nuke can't emit a multi-KB frame.
            string ujson = "";
            if (units != null && units.Count > 0)
            {
                var sb = new StringBuilder(64 + units.Count * 40);
                sb.Append(",\"units\":[");
                int shown = Math.Min(units.Count, 24);
                for (int i = 0; i < shown; i++)
                {
                    var k = units[i];
                    if (i > 0) sb.Append(',');
                    sb.Append("{\"n\":\"").Append(Esc(k.name ?? "?")).Append("\",\"f\":\"").Append(k.enemy ? 'e' : 'f')
                      .Append("\",\"d\":").Append(k.dmg.ToString("0", System.Globalization.CultureInfo.InvariantCulture)).Append('}');
                }
                sb.Append(']');
                if (units.Count > 24) sb.Append(",\"unitsMore\":").Append(units.Count - 24);
                ujson = sb.ToString();
            }
            Out("{\"t\":\"tk\",\"id\":\"" + sid + "\",\"n\":\"" + Esc(nm)
                + "\",\"victim\":\"" + Esc(victim) + "\",\"method\":\"" + Esc(ev.method ?? "")
                + "\",\"weapon\":\"" + Esc(ev.weapon ?? "") + "\",\"count\":" + count
                + ",\"dmg\":" + ev.dmg.ToString("0", System.Globalization.CultureInfo.InvariantCulture)
                + ",\"action\":\"" + action + "\",\"nc\":\"" + Esc(nc ?? "")
                + "\",\"ts\":" + ts.ToString("0.0", System.Globalization.CultureInfo.InvariantCulture) + ujson + "}");
        }

        // ---- collateral kill window + munition launch tracking (ported from 0.9.46) ----
        // Record one player-attributed unit kill into the killer rolling window (feeds the collateral verdict
        // AND the per-blast unit list in the mod log).
        static void NoteKillForCollateral(string sid, bool enemy, bool big, string name, float dmg)
        {
            if (!_killWin.TryGetValue(sid, out var l)) { l = new List<KillRec>(); _killWin[sid] = l; }
            l.Add(new KillRec { t = Time.time, dmg = dmg, enemy = enemy, big = big, name = name });
            if (l.Count > 128) l.RemoveRange(0, l.Count - 128);   // bounded per player
        }

        // Munition launch tracking. damageCredit ALWAYS keys the FIRING AIRCRAFT, never the munition - so the
        // weapon must be remembered at LAUNCH. Spawner.SpawnMissile is [Server]-only and every live missile/bomb
        // passes through it; keep the last launch per owner unit. blastYield > 200 is the game nuclear threshold.
        internal struct LaunchRec { public string weapon; public float yield, t; }
        static readonly Dictionary<long, LaunchRec> _lastLaunch = new Dictionary<long, LaunchRec>();          // owner unit persistentID.Id -> most recent launch
        static readonly Dictionary<long, LaunchRec> _lastNuclearLaunch = new Dictionary<long, LaunchRec>();   // ... and the most recent NUCLEAR one
        internal static void NoteLaunch(long ownerId, string weapon, float yield)
        {
            if (_lastLaunch.Count > 1024) _lastLaunch.Clear();   // bounded (keyed per OWNER unit, not per missile)
            var r = new LaunchRec { weapon = weapon, yield = yield, t = Time.time };
            _lastLaunch[ownerId] = r;
            if (yield > 200f)
            {
                if (_lastNuclearLaunch.Count > 128) _lastNuclearLaunch.Clear();
                _lastNuclearLaunch[ownerId] = r;
            }
        }
        // Launch lookup for the credited killer unit. GATED on the kill credited damage being munition-plausible
        // (>=500: guns credit ~100-300, missile/bomb/shockwave kills ~1000+). A live NUCLEAR launch takes
        // precedence over later conventional launches so post-nuke defensive shots can't mask it.
        static bool TryGetRecentLaunch(object topKey, float kredit, out LaunchRec rec)
        {
            rec = default;
            try
            {
                if (topKey == null || kredit < 500f) return false;
                long id = ((PersistentID)topKey).Id;
                if (_lastNuclearLaunch.TryGetValue(id, out rec) && Time.time - rec.t <= 45f) return true;
                return _lastLaunch.TryGetValue(id, out rec) && Time.time - rec.t <= 120f;
            }
            catch { return false; }
        }
        // Ungated live-nuke probe for DEDUP SPANS only (below-floor grazes carry tiny credit).
        static bool HasLiveNuclearLaunch(object topKey)
        {
            try
            {
                return topKey != null && _lastNuclearLaunch.TryGetValue(((PersistentID)topKey).Id, out var nl)
                    && Time.time - nl.t <= 45f;
            }
            catch { return false; }
        }

        // Alloc-cached name classifiers (bit0=strategic, bit1=auto-defence, bit2=big-objective), lower ONCE.
        static readonly Dictionary<string, byte> _nameClassCache = new Dictionary<string, byte>(StringComparer.Ordinal);
        static byte ClassifyUnitName(string name)
        {
            if (string.IsNullOrEmpty(name)) return 0;
            if (_nameClassCache.TryGetValue(name, out var b)) return b;
            if (_nameClassCache.Count > 512) _nameClassCache.Clear();   // bounded
            b = (byte)((IsStrategicLauncher(name) ? 1 : 0) | (IsAutoDefenseUnit(name) ? 2 : 0) | (IsBigObjectiveUnit(name) ? 4 : 0));
            _nameClassCache[name] = b;
            return b;
        }
        static bool CachedIsStrategic(string name) => (ClassifyUnitName(name) & 1) != 0;
        static bool CachedIsAutoDefense(string name) => (ClassifyUnitName(name) & 2) != 0;
        static bool CachedIsBigObjective(string name) => (ClassifyUnitName(name) & 4) != 0;

        // Big enemy OBJECTIVES (ship classes): killing one alongside a friendly = the friendly was collateral of a
        // real strike. Name-substring like IsAutoDefenseUnit.
        static bool IsBigObjectiveUnit(string name)
        {
            if (string.IsNullOrEmpty(name)) return false;
            string n = name.ToLowerInvariant();
            return n.Contains("carrier") || n.Contains("destroyer") || n.Contains("corvette")
                || n.Contains("frigate") || n.Contains("cruiser") || n.Contains("argus");
        }

        // Heli-dropped SAM/CRAM/AA names that auto-engage (deployed defenses) -- their friendly kills are AI-tasked,
        // not a deliberate human trigger-pull. Kept name-only (the damaging unit's definition.unitName, already
        // resolved at the kill site) so this never depends on a game-API member that could break plugin load.
        static bool IsAutoDefenseUnit(string name)
        {
            if (string.IsNullOrEmpty(name)) return false;
            string n = name.ToLowerInvariant();
            return n.Contains("sam") || n.Contains("cram") || n.Contains("c-ram") || n.Contains("phalanx")
                || n.Contains("flak") || n.Contains(" aa") || n.EndsWith("aa") || n.Contains("anti-air") || n.Contains("anti air");
        }

        // Classify HOW a friendly kill happened from the damaging unit's name + agency, AND whether it counts as a
        // DELIBERATE teamkill. A directly-piloted weapon (a pilot's gun/missile/bomb, a player ramming) IS
        // deliberate; an auto-engaging deployed defense (heli-dropped SAM/CRAM/AA) or an AI-tasked / strategic
        // launcher fired itself and must NOT escalate the human owner through warn->kick->BAN (the #6 innocent-ban
        // bug). Fail-open: on any ambiguity treat it as a deliberate weapon kill (preserves catch-genuine-TK).
        // `unitName` = the damaging unit's definition.unitName (may be null/empty); `killer` non-null = a resolved
        // human controller. Out `deliberate` => count it as an offence; returns the report-method tag.
        static string ClassifyTkMethod(string unitName, out bool deliberate)
        {
            deliberate = true;
            try
            {
                if (CachedIsStrategic(unitName) || CachedIsAutoDefense(unitName))
                {
                    deliberate = false;   // auto/AI-tasked -> report it, but do NOT escalate the owner
                    return "auto";
                }
            }
            catch { }
            return string.IsNullOrEmpty(unitName) ? "" : "direct";
        }

        static void Kick(Player p)
        {
            // KickPlayer(INetworkPlayer) is the void overload (KickPlayerAsync would pull in UniTask).
            try { if (p != null && p.Owner != null && NetworkManagerNuclearOption.i != null) NetworkManagerNuclearOption.i.KickPlayer(p.Owner); }
            catch (Exception e) { Log?.LogError("Kick: " + e); }
        }

        // KILLFEED customization: apply the per-line Mode (vanilla|custom|off) + Text template to a PLUGIN-emitted
        // feed line. Returns the string to broadcast, or null to SUPPRESS (mode=off). vanilla = the current default
        // wording (already formatted); when mode=custom the Text template is filled from the placeholder args.
        static string RenderKillFeed(string line, string vanilla, string killer, string killerPlane, string victim,
                                     string victimPlane, string weapon, string streak, string ship, string points)
        {
            string mode = "vanilla";
            try { if (_kfMode.TryGetValue(line, out var me) && me != null) { var mv = (me.Value ?? "").Trim().ToLowerInvariant(); if (mv.Length > 0) mode = mv; } } catch { }
            if (mode == "off") return null;
            if (mode == "custom")
            {
                string tpl = null;
                try { if (_kfText.TryGetValue(line, out var te) && te != null) tpl = te.Value; } catch { }
                if (string.IsNullOrEmpty(tpl)) return vanilla;   // custom + empty text -> fall back to vanilla wording
                return tpl
                    .Replace("{killer}", killer ?? "").Replace("{killer_plane}", killerPlane ?? "")
                    .Replace("{victim}", victim ?? "").Replace("{victim_plane}", victimPlane ?? "")
                    .Replace("{weapon}", weapon ?? "").Replace("{streak}", streak ?? "")
                    .Replace("{ship}", ship ?? "").Replace("{points}", points ?? "");
            }
            return vanilla;   // vanilla
        }

        // From the ReportKilled hook (every unit death): announce a PLAYER being shot down (by whom -
        // incl. AI / crash; enemy-PLAYER kills are left to the bot's "X splashed Y"), and run teamkill
        // enforcement when the top damager is a friendly player. One damageCredit scan serves both.
        internal static void CheckTeamkill(Unit dead)
        {
            try
            {
                if (dead == null) return;
                bool tkOn = (TeamkillEnforce == null || TeamkillEnforce.Value);
                bool announceOn = (CustomKillFeed == null || CustomKillFeed.Value);
                Player victim = null; try { if (dead is Aircraft dv) victim = dv.Player; } catch { }
                if (victim != null && IsAdminEjecting(Sid(victim))) return;
                if (victim != null)
                {
                    try { string vs = Sid(victim); var vl = LifeOf(vs); if (vl.alive) EndLife(vs, vl, "death"); }
                    catch (Exception e) { Log?.LogError("life-on-death: " + e); }
                }
                bool wantAnnounce = announceOn && victim != null;            // only real-player deaths
                if (!tkOn && !wantAnnounce) return;
                FactionHQ deadHQ = null; try { deadHQ = dead.NetworkHQ; } catch { }

                // PERF: recordOnly mode runs JUST the scan + NoteKillForCollateral (so a human ENEMY kill is
                // recorded for the collateral verdict) then returns; pure AI-vs-AI stays cheap.
                bool recordOnly = false;
                if (!wantAnnounce)
                {
                    bool deadHasHumans = false; bool anyHumans = false;
                    if (tkOn && deadHQ != null)
                        foreach (var hp in Humans()) { anyHumans = true; try { if (hp.HQ == deadHQ) { deadHasHumans = true; break; } } catch { } }
                    if (!deadHasHumans)
                    {
                        if (!tkOn || !anyHumans) return;   // nothing to enforce and nobody to credit
                        recordOnly = true;                 // enemy-AI death: record a human kill, skip the rest
                    }
                }

                // top damager from damageCredit (generic-typed fast path; boxed IDictionary fallback).
                if (_dmgCreditFI == null)
                    _dmgCreditFI = typeof(Unit).GetField("damageCredit", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);
                object dcRaw = _dmgCreditFI?.GetValue(dead);
                object topKey = null; float top = 0f; float dmgTotal = 0f; int dmgSources = 0;
                if (dcRaw is Dictionary<PersistentID, float> dcT)
                {
                    PersistentID topId = default; bool haveTop = false;
                    foreach (var e in dcT)
                    { dmgTotal += e.Value; dmgSources++; if (e.Value > top) { top = e.Value; topId = e.Key; haveTop = true; } }
                    if (haveTop) topKey = topId;
                }
                else if (dcRaw is System.Collections.IDictionary dc)
                    foreach (System.Collections.DictionaryEntry e in dc)
                    { float v; try { v = Convert.ToSingle(e.Value); } catch { continue; } dmgTotal += v; dmgSources++; if (v > top) { top = v; topKey = e.Key; } }
                if (recordOnly && topKey == null) return;

                Player killer = null; FactionHQ killerHQ = null; string killerName = null; string dmgUnitName = null;
                if (topKey != null && UnitRegistry.TryGetPersistentUnit((PersistentID)topKey, out var pu))
                {
                    try { killer = pu.player; } catch { }
                    try { killerHQ = pu.GetHQ(); } catch { }
                    try { dmgUnitName = pu.definition != null ? SafeText(pu.definition.unitName) : null; } catch { }   // KILLER aircraft/SAM/unit name
                    try { killerName = (killer != null) ? RawNameOf(killer) : dmgUnitName; } catch { }
                }
                if (recordOnly && killer == null) return;

                // VICTIM unit-type name (used by the down line, dmgcal, and the collateral big-unit check).
                string deadName = null; try { deadName = dead.definition != null ? SafeText(dead.definition.unitName) : null; } catch { }
                // WEAPON (munition) resolution for the kill snapshot + moderation log: damageCredit names the
                // AIRCRAFT, the munition comes from the launch map. Fallback: the damaging unit (aircraft).
                string killWeapon = dmgUnitName ?? "";
                if (TryGetRecentLaunch(topKey, top, out var kl0) && !string.IsNullOrEmpty(kl0.weapon)) killWeapon = kl0.weapon;

                if (killer != null && DamageCalibration != null && DamageCalibration.Value && !string.IsNullOrEmpty(deadName))
                {
                    try { Log.LogInfo($"[dmgcal] t={Time.time:0.0} victim={deadName} total={dmgTotal:0} top={top:0} by={dmgUnitName ?? "?"} killer={RawNameOf(killer)}"); } catch { }
                }

                // COLLATERAL WINDOW: only TRUSTED kills enter (deliberate, weapon-resolved, above floor).
                if (killer != null && killer != victim && killerHQ != null && deadHQ != null)
                {
                    string ksid = Sid(killer);
                    if (!string.IsNullOrEmpty(ksid) && ksid != "0")
                    {
                        bool winDelib; ClassifyTkMethod(dmgUnitName, out winDelib);
                        float winMinDmg = TeamkillMinDamage != null ? TeamkillMinDamage.Value : 0f;
                        bool winTrusted = winDelib && !string.IsNullOrEmpty(dmgUnitName)
                                          && !(winMinDmg > 0f && top < winMinDmg);
                        if (winTrusted)
                            NoteKillForCollateral(ksid, killerHQ != deadHQ, CachedIsBigObjective(deadName), deadName, top);
                        else if (killerHQ != deadHQ && DamageCalibration != null && DamageCalibration.Value)
                            Log?.LogInfo($"[tk] window-reject enemy kill {deadName ?? "?"} dmg={top:0} by {killerName ?? "?"} ({(!winDelib ? "auto-classified" : string.IsNullOrEmpty(dmgUnitName) ? "no-weapon" : "below-floor")})");
                    }
                }
                if (recordOnly) return;   // enemy-AI death recorded; no announce/killfeed/enforcement applies

                // KILL DATA -> bot: every human death with who/what downed them. Adds killer_plane / victim_plane /
                // weapon (contract) alongside the legacy "w" field.
                if (victim != null)
                {
                    bool kPlayer = killer != null && killer != victim;
                    string kdisp = kPlayer ? RawNameOf(killer) : (killerName ?? "");
                    bool ff = kPlayer && killerHQ != null && deadHQ != null && killerHQ == deadHQ;
                    Out("{\"t\":\"down\",\"v\":\"" + Sid(victim) + "\",\"vn\":\"" + Esc(RawNameOf(victim))
                        + "\",\"k\":\"" + Esc(kdisp) + "\",\"ks\":\"" + (kPlayer ? Sid(killer) : "")
                        + "\",\"kp\":" + (kPlayer ? 1 : 0) + ",\"ff\":" + (ff ? 1 : 0)
                        + ",\"w\":\"" + Esc(dmgUnitName ?? "")
                        + "\",\"killer_plane\":\"" + Esc(dmgUnitName ?? "")
                        + "\",\"victim_plane\":\"" + Esc(deadName ?? "")
                        + "\",\"weapon\":\"" + Esc(killWeapon) + "\"}");
                }

                // player shot-down message (skip enemy-player kills -> the bot says "X splashed Y").
                // Honors the killfeed Mode/Text: friendly-player = teamkill line, AI/unit = ai_kill, crash = went_down.
                if (wantAnnounce)
                {
                    bool enemyPlayerKill = killer != null && killer != victim && killerHQ != null && deadHQ != null && killerHQ != deadHQ;
                    if (!enemyPlayerKill)
                    {
                        string vt = RankNameTag(victim);
                        string vname = RawNameOf(victim);
                        if (killer != null && killer != victim)             // friendly player (also a teamkill)
                        {
                            string ln = RenderKillFeed("teamkill", $"{vt} <color=#FFFFFF>was shot down by</color> {RankNameTag(killer)}",
                                RawNameOf(killer), dmgUnitName ?? "", vname, deadName ?? "", killWeapon, "", "", "");
                            if (ln != null) Instance?.BroadcastAll(ln);
                        }
                        else if (killer == null && !string.IsNullOrEmpty(killerName))   // AI / unit
                        {
                            if (CachedIsStrategic(killerName))              // coalesce launcher spam -> one summary
                            { _stratStrikes++; _stratLast = Time.time; }
                            else
                            {
                                string ln = RenderKillFeed("ai_kill", $"{vt} <color=#FFFFFF>was shot down by</color> <color=#FF6A00>{killerName}</color>",
                                    killerName, killerName, vname, deadName ?? "", killWeapon, "", "", "");
                                if (ln != null) Instance?.BroadcastAll(ln);
                            }
                        }
                        else                                                 // crash / self / unknown
                        {
                            string ln = RenderKillFeed("went_down", $"{vt} <color=#FFFFFF>went down.</color>",
                                "", "", vname, deadName ?? "", "", "", "", "");
                            if (ln != null) Instance?.BroadcastAll(ln);
                        }
                    }
                }

                // teamkill enforcement: top damager is a friendly player (and not the victim own aircraft)
                if (tkOn && killer != null && killerHQ != null && deadHQ != null && killerHQ == deadHQ && killer != victim)
                {
                    string sid = Sid(killer);
                    if (!string.IsNullOrEmpty(sid) && sid != "0")
                    {
                        bool deliberate; string method = ClassifyTkMethod(dmgUnitName, out deliberate);
                        string weapon = dmgUnitName ?? "";
                        float minDmg = TeamkillMinDamage != null ? TeamkillMinDamage.Value : 0f;
                        bool noWeapon = string.IsNullOrEmpty(weapon);
                        bool belowFloor = minDmg > 0f && top < minDmg;
                        Log?.LogInfo($"[tk] friendly kill by {RawNameOf(killer)} -> {RawNameOf(victim)} dmg={top:0} method={(method.Length > 0 ? method : "?")} weapon={(weapon.Length > 0 ? weapon : "-")}");
                        if (!deliberate || noWeapon || belowFloor)
                        {
                            // REPORT-ONLY: flag in Moderation, never warn/kick/ban, never count. Deduped per blast.
                            string ncReason = !deliberate ? "auto" : (noWeapon ? "no-weapon" : "below-floor");
                            float nowR = Time.time;
                            float rspan = HasLiveNuclearLaunch(topKey) ? 40f : TK_EVENT_DEDUP;
                            bool rdup = _tkReportStart.TryGetValue(sid, out var rs) && Mathf.Abs(nowR - rs) < rspan;
                            if (!rdup)
                            {
                                _tkReportStart[sid] = nowR;
                                EmitTkMod(sid, killer, "report", 0,
                                    new TkEvent { sid = sid, victim = RawNameOf(victim), method = method, weapon = weapon, dmg = top, eventT = nowR }, ncReason);
                            }
                            Log?.LogInfo($"[tk] NOT counted ({ncReason}) - friendly kill by {RawNameOf(killer)} -> {RawNameOf(victim)} (dmg={top:0})");
                        }
                        else
                        {
                            // COLLATERAL CHECK: defer the verdict by a munition-sized forward window (nuclear = long).
                            float yield = 0f;
                            if (TryGetRecentLaunch(topKey, top, out var launch) && !string.IsNullOrEmpty(launch.weapon))
                            { weapon = launch.weapon; yield = launch.yield; }
                            bool nuclear = yield > 200f;   // the game own Shockwave-spawn threshold
                            float backS = TeamkillCollateralWindow != null ? Mathf.Clamp(TeamkillCollateralWindow.Value, 0.5f, 10f) : 2.5f;
                            float fwdS = nuclear
                                ? (TeamkillCollateralWindowNuclear != null ? Mathf.Clamp(TeamkillCollateralWindowNuclear.Value, 5f, 40f) : 20f)
                                : backS;
                            if (nuclear) backS = fwdS;
                            // Same-blast merge: a second friendly victim of the SAME blast joins the pending verdict.
                            bool merged = false;
                            float nowQ = Time.time;
                            for (int i = 0; i < _tkJudge.Count; i++)
                            {
                                var q = _tkJudge[i];
                                if (q.sid == sid && nowQ - q.eventT < Mathf.Max(TK_EVENT_DEDUP, q.fwd))
                                {
                                    string vn = RawNameOf(victim);
                                    if (!string.IsNullOrEmpty(vn))
                                    {
                                        if (q.victims == null) q.victims = new List<string>();
                                        if (!string.IsNullOrEmpty(q.victim) && q.victims.Count == 0) q.victims.Add(q.victim);
                                        q.victims.Add(vn);
                                    }
                                    if (top > q.dmg) q.dmg = top;
                                    if (fwdS > q.fwd) { q.fwd = fwdS; q.back = Mathf.Max(q.back, backS); q.dueAt = q.eventT + fwdS; }
                                    merged = true; break;
                                }
                            }
                            if (merged) { }
                            else if (_tkJudge.Count < TK_QUEUE_MAX)
                            {
                                _tkJudge.Add(new TkPending { sid = sid, victim = RawNameOf(victim), method = method, weapon = weapon,
                                                             dmg = top, eventT = Time.time, dueAt = Time.time + fwdS, back = backS, fwd = fwdS });
                                Log?.LogInfo($"[tk] friendly kill by {RawNameOf(killer)} -> {RawNameOf(victim)} ({method}{(weapon.Length > 0 ? " " + weapon : "")}{(nuclear ? ", NUCLEAR" : "")}) - collateral verdict in {fwdS:0.#}s");
                            }
                            else
                            {
                                float nowT = Time.time;
                                bool fdup = _tkEventStart.TryGetValue(sid, out var fs) && Mathf.Abs(nowT - fs) < TK_EVENT_DEDUP;
                                if (!fdup)
                                {
                                    _tkEventStart[sid] = nowT;
                                    if (_tkQueue.Count < TK_QUEUE_MAX)
                                        _tkQueue.Add(new TkEvent { sid = sid, victim = RawNameOf(victim), method = method, weapon = weapon, dmg = top, eventT = nowT });
                                }
                                Log?.LogInfo($"[tk] judge queue FULL - counted {RawNameOf(killer)} -> {RawNameOf(victim)} via the legacy path (no collateral verdict)");
                            }
                        }
                    }
                }
            }
            catch (Exception e) { Log?.LogError("CheckTeamkill: " + e); }
        }

        // Off HQTick: judge collateral verdicts, escalate queued teamkills, fire delayed kicks, enforce bans + rank-0.
        internal static void TkTick()
        {
            float now = Time.time;
            // ---- collateral verdicts: judge friendly kills whose window has elapsed (oldest first). ----
            if (_tkJudge.Count > 0)
            {
                bool anyDue = false;
                for (int i = 0; i < _tkJudge.Count; i++) if (now >= _tkJudge[i].dueAt) { anyDue = true; break; }
                if (anyDue)
                {
                    var due = new List<TkPending>();
                    for (int i = _tkJudge.Count - 1; i >= 0; i--)
                        if (now >= _tkJudge[i].dueAt) { due.Add(_tkJudge[i]); _tkJudge.RemoveAt(i); }
                    due.Reverse();                                    // oldest first (dedup anchor = first victim)
                    bool enforce = TeamkillCollateralEnforce != null && TeamkillCollateralEnforce.Value;
                    bool bigExempt = TeamkillBigUnitExempt == null || TeamkillBigUnitExempt.Value;
                    int silentMin = TeamkillSilentMinEnemies != null ? TeamkillSilentMinEnemies.Value : 10;
                    float silentRatio = TeamkillSilentRatio != null ? Mathf.Max(1f, TeamkillSilentRatio.Value) : 5f;
                    int capMax = TeamkillCollateralMaxPerMatch != null ? TeamkillCollateralMaxPerMatch.Value : 3;
                    foreach (var p in due)
                    {
                        int enemies = 0, friendlies = 0; bool bigEnemy = false;
                        List<KillRec> units = null;
                        if (_killWin.TryGetValue(p.sid, out var kl))
                            foreach (var k in kl)
                                if (k.t >= p.eventT - p.back && k.t <= p.eventT + p.fwd)
                                {
                                    if (k.enemy) { enemies++; if (k.big) bigEnemy = true; }
                                    else friendlies++;
                                    if (enforce) (units = units ?? new List<KillRec>()).Add(k);
                                }
                        string verdict = (bigEnemy && bigExempt) ? "big-unit"
                                       : (enemies >= friendlies && enemies > 0 ? "collateral" : "deliberate");
                        bool silent = verdict == "collateral"
                                   && silentMin > 0 && enemies >= silentMin && enemies >= silentRatio * friendlies;
                        if (verdict != "deliberate" && capMax > 0 && enforce)
                        {
                            _tkCollateralCount.TryGetValue(p.sid, out var cc);
                            if (cc >= capMax)
                            {
                                if (silent) { silent = false; Log?.LogInfo($"[tk] collateral cap reached for {p.sid} ({cc}/{capMax}) -> silent verdict downgraded to logged"); }
                                else { Log?.LogInfo($"[tk] collateral cap reached for {p.sid} ({cc}/{capMax} this match) -> treating as deliberate"); verdict = "deliberate"; }
                            }
                            else _tkCollateralCount[p.sid] = cc + 1;
                        }
                        string method = (enemies + friendlies) >= 2 ? "splash" : p.method;
                        string victimList = p.victims != null ? string.Join(", ", p.victims) : p.victim;
                        Log?.LogInfo($"[tk] collateral-check {p.sid} -> {victimList}: enemies={enemies} friendlies={friendlies} big={(bigEnemy ? 1 : 0)} dmg={p.dmg:0} method={method} -> {verdict}{(silent ? " (silent)" : "")}{(enforce ? "" : " (log-only)")}");
                        if (verdict != "deliberate" && enforce)
                        {
                            _tkEventStart[p.sid] = p.eventT;
                            if (silent)
                            {
                                _tkCollatStart[p.sid] = p.eventT;
                                _tkReportStart[p.sid] = p.eventT;
                                continue;
                            }
                            bool rdup = _tkCollatStart.TryGetValue(p.sid, out var rs) && Mathf.Abs(p.eventT - rs) < TK_EVENT_DEDUP;
                            if (!rdup)
                            {
                                _tkCollatStart[p.sid] = p.eventT;
                                _tkReportStart[p.sid] = p.eventT;
                                EmitTkMod(p.sid, FindPlayerBySid(p.sid), "report", 0,
                                    new TkEvent { sid = p.sid, victim = victimList, method = method, weapon = p.weapon, dmg = p.dmg, eventT = p.eventT },
                                    verdict, units);
                            }
                            continue;
                        }
                        bool dup = _tkEventStart.TryGetValue(p.sid, out var startT)
                                && Mathf.Abs(p.eventT - startT) < Mathf.Max(TK_EVENT_DEDUP, p.back + p.fwd);
                        if (dup)
                        {
                            Log?.LogInfo($"[tk] {p.sid} -> {victimList} -- same event, deduped (already handled this blast)");
                        }
                        else
                        {
                            _tkEventStart[p.sid] = p.eventT;
                            if (_tkQueue.Count < TK_QUEUE_MAX)
                                _tkQueue.Add(new TkEvent { sid = p.sid, victim = victimList, method = method,
                                                           weapon = p.weapon, dmg = p.dmg, eventT = p.eventT });
                        }
                    }
                }
            }
            if (_tkQueue.Count > 0)
            {
                var batch = new List<TkEvent>(_tkQueue); _tkQueue.Clear();
                foreach (var ev in batch)
                {
                    string sid = ev.sid;
                    int n = (_tkCount.TryGetValue(sid, out var c) ? c : 0) + 1;
                    _tkCount[sid] = n;
                    var p = FindPlayerBySid(sid);
                    if (n == 1)
                    {
                        if (p != null)
                        {
                            AdminEject(p);   // life-neutral: a teamkill-warning eject must not end a skill-life
                            Instance?.TellPlayer(p, "<color=#FF5555>FRIENDLY FIRE - first warning.</color> <color=#FFD200>Check your targets. Do it again this match and you'll be removed.</color>");
                        }
                        EmitTkMod(sid, p, "warn", n, ev);
                        Log?.LogInfo($"[tk] warn+eject {sid} (1)");
                    }
                    else if (n == 2)
                    {
                        _tkRankZero.Add(sid);
                        if (p != null) Instance?.TellPlayer(p, "<color=#FF5555>FRIENDLY FIRE - second warning. The next one is a BAN.</color>");
                        _tkKicks.Add(new KeyValuePair<string, float>(sid, now + 2.5f));   // let the message land, then kick
                        EmitTkMod(sid, p, "kick", n, ev);
                        Log?.LogInfo($"[tk] kick {sid} (2)");
                    }
                    else
                    {
                        _tkBanned.Add(sid); SaveBans();
                        if (p != null) Instance?.TellPlayer(p, "<color=#FF0000>BANNED for repeated team killing.</color>");
                        _tkKicks.Add(new KeyValuePair<string, float>(sid, now + 2.5f));
                        EmitTkMod(sid, p, "ban", n, ev);
                        Log?.LogInfo($"[tk] BAN {sid} (3+)");
                    }
                }
            }
            if (_tkKicks.Count > 0)
                for (int i = _tkKicks.Count - 1; i >= 0; i--)
                    if (now >= _tkKicks[i].Value) { var k = _tkKicks[i]; _tkKicks.RemoveAt(i); Kick(FindPlayerBySid(k.Key)); }
            if (now < _nextTkScan) return;
            _nextTkScan = now + 2f;
            // prune stale collateral-window entries (horizon must outlive the longest nuclear verdict window).
            try
            {
                foreach (var kv in _killWin)
                {
                    var l = kv.Value;
                    for (int i = l.Count - 1; i >= 0; i--)
                        if (now - l[i].t > 60f) l.RemoveAt(i);
                }
            }
            catch { }
            try
            {
                foreach (var p in Humans())
                {
                    string sid = Sid(p);
                    if (string.IsNullOrEmpty(sid) || sid == "0") continue;
                    if (_tkBanned.Contains(sid)) { Kick(p); continue; }                  // enforce ban on rejoin
                    if (_tkRankZero.Contains(sid)) { try { p.SetRank(0, true); } catch { } _tkRankZero.Remove(sid); Log?.LogInfo($"[tk] rank->0 {RawNameOf(p)}"); }
                }
            }
            catch (Exception e) { Log?.LogError("TkTick: " + e); }
        }

        // ===== ANTI-GRIEF: detect a single connection mass-commanding units to brick the server (the
        // reliable-send-buffer flood that mass-disconnects EVERYONE) and auto-kick THAT one offender (not the
        // lobby) + emit a report the webcc Reports tab shows with a Ban button. Two-factor by default: a player
        // must own > threshold GroundVehicles AND be SUSTAINED-flooding move-orders (a macro/held-key/loop) --
        // so a legit base-builder who 'select-all + move once' is never kicked. Reuses the teamkill kick/ban
        // path (Kick / _tkKicks / _tkBanned). Fail-open everywhere. =====
        internal static ConfigEntry<bool> GriefAutoKick, GriefRequireFlooding, GriefHardBan, GriefReportOnly, GriefExemptAdmins;
        internal static ConfigEntry<int>  GriefOwnedThreshold, GriefFloodPerSec, GriefBreakerDistinct, GriefBreakerWindow;
        static readonly Dictionary<string, int>   _orderAttempts = new Dictionary<string, int>();   // CmdSetDestination attempts since last GriefTick
        static readonly Dictionary<string, int>   _griefStreak   = new Dictionary<string, int>();    // consecutive high-rate ticks per player
        static readonly Dictionary<string, float> _griefActed    = new Dictionary<string, float>();  // last action time (throttle re-acting)
        static readonly Dictionary<string, float> _griefTrips    = new Dictionary<string, float>();  // sid -> last trip time (server-wide circuit breaker)
        static float _griefStormAt;   // last time a storm-suppression line was logged (throttle)
        static float _nextGriefScan, _lastGriefScan;
        const float GRIEF_INTERVAL = 2f;

        // called from FleetOrderFloodPatch.Prefix on EVERY CmdSetDestination attempt (before the rate/policy gates)
        internal static void NoteOrderAttempt(Player p)
        {
            try
            {
                if (p == null) return; string id = Sid(p);
                if (string.IsNullOrEmpty(id) || id == "0") return;
                _orderAttempts[id] = (_orderAttempts.TryGetValue(id, out var c) ? c : 0) + 1;
                try { _netOrders[id] = (_netOrders.TryGetValue(id, out var no) ? no : 0) + 1; } catch { }   // net-health: per-player order count since last emit (reset each emit)
            }
            catch { }
        }

        internal static void GriefTick()
        {
            try
            {
                if (GriefAutoKick == null) return;                 // not yet bound
                float now = Time.time;
                if (now < _nextGriefScan) return;
                _nextGriefScan = now + GRIEF_INTERVAL;
                float elapsed = Mathf.Max(0.5f, now - _lastGriefScan);   // REAL window (a lag hitch can exceed GRIEF_INTERVAL)
                _lastGriefScan = now;

                bool enabled      = GriefAutoKick.Value;
                int  ownThresh    = GriefOwnedThreshold != null ? Mathf.Max(1, GriefOwnedThreshold.Value) : 12;
                bool requireFlood = GriefRequireFlooding == null || GriefRequireFlooding.Value;
                int  floodPerSec  = GriefFloodPerSec != null ? Mathf.Max(1, GriefFloodPerSec.Value) : 3;
                bool reportOnly   = GriefReportOnly != null && GriefReportOnly.Value;
                bool hardBan      = GriefHardBan != null && GriefHardBan.Value;
                bool exemptAdmins = GriefExemptAdmins == null || GriefExemptAdmins.Value;
                bool diag         = CommandDiagLog != null && CommandDiagLog.Value;
                int  breakerDist  = GriefBreakerDistinct != null ? Mathf.Max(0, GriefBreakerDistinct.Value) : 3;   // 0 = breaker off
                float breakerWin  = GriefBreakerWindow != null ? Mathf.Max(1, GriefBreakerWindow.Value) : 6f;

                // snapshot + reset the per-player order-attempt counters for this window
                var attempts = new Dictionary<string, int>(_orderAttempts);
                _orderAttempts.Clear();

                // update sustained-flooding streaks (high order RATE held across consecutive ticks)
                var streakKeys = new List<string>(_griefStreak.Keys);
                foreach (var id in streakKeys) if (!attempts.ContainsKey(id)) _griefStreak[id] = 0;   // decay idle
                foreach (var kv in attempts)
                {
                    float rate = kv.Value / elapsed;
                    _griefStreak[kv.Key] = rate >= floodPerSec
                        ? (_griefStreak.TryGetValue(kv.Key, out var st) ? st : 0) + 1
                        : 0;
                }

                if (!enabled && !diag) return;   // disabled and not diagnosing -> nothing to do

                // count owned GroundVehicles per player in ONE pass over allUnits (vs O(players x units))
                var ownedCount = new Dictionary<Player, int>();
                try
                {
                    foreach (var u in UnitRegistry.allUnits)
                        if (u is GroundVehicle gv)
                        {
                            var ow = SafeOwner(gv);
                            if (ow != null) ownedCount[ow] = (ownedCount.TryGetValue(ow, out var oc) ? oc : 0) + 1;
                        }
                }
                catch { }

                foreach (var p in Humans())
                {
                    string sid = Sid(p);
                    if (string.IsNullOrEmpty(sid) || sid == "0") continue;
                    if (_tkBanned.Contains(sid)) continue;        // already banned; TkTick enforces it

                    int owned = ownedCount.TryGetValue(p, out var oc2) ? oc2 : 0;
                    int streak  = _griefStreak.TryGetValue(sid, out var s2) ? s2 : 0;
                    bool flooding = streak >= 2;                  // ~2 ticks (~4s) of sustained high order rate
                    int rateNow = attempts.TryGetValue(sid, out var a2) ? (int)(a2 / GRIEF_INTERVAL) : 0;

                    if (diag && (owned > 0 || rateNow > 0))
                        Log?.LogInfo($"[grief] {RawNameOf(p)} ({sid}) owned={owned} rate={rateNow}/s streak={streak} thr={ownThresh} flooding={flooding}");

                    if (!enabled) continue;
                    // AGGRESSIVE: sustained command-spam ALONE trips it -- catch a single connection
                    // re-commanding units >= floodPerSec/s (held ~4s), regardless of how many units they
                    // own (the spam can be on units they don't even own). OwnedUnitThreshold is now an
                    // OPTIONAL escalator: with RequireActiveFlooding=false, owning a huge fleet also trips.
                    bool trip = requireFlood ? flooding : (flooding || owned > ownThresh);
                    if (!trip) continue;
                    if (exemptAdmins && IsAdmin(p)) continue;          // never auto-kick an admin (legit mass-command)
                    if (_griefActed.TryGetValue(sid, out var t) && now - t < 15f) continue;   // throttle re-acting
                    _griefActed[sid] = now;

                    // SERVER-WIDE CIRCUIT BREAKER (#8): record this trip + prune the window. If MANY DISTINCT
                    // players trip together it's a synchronized order/lag SPIKE (congestion), not grief -> SUPPRESS
                    // all kicks/bans (we still emit each report so admins see it). Mirrors the bot's flood-breaker.
                    _griefTrips[sid] = now;
                    if (breakerWin > 0)
                        foreach (var s in new List<string>(_griefTrips.Keys))
                            if (now - _griefTrips[s] > breakerWin) _griefTrips.Remove(s);
                    bool storm = breakerDist > 0 && _griefTrips.Count >= breakerDist;

                    string action = reportOnly ? "report" : (storm ? "report" : (hardBan ? "ban" : "kick"));
                    if (storm)
                    {
                        if (now - _griefStormAt > 30f)
                        {
                            _griefStormAt = now;
                            Log?.LogWarning($"[grief] STORM: {_griefTrips.Count} players tripped together within {breakerWin}s "
                                + "-> treated as server congestion (not grief); auto-kicks SUPPRESSED");
                        }
                    }
                    else
                        Log?.LogWarning($"[grief] {action} {RawNameOf(p)} ({sid}) owned={owned} rate={rateNow}/s streak={streak}");
                    // emit the report ([NOSTATS] line the bot tails); ts=0 -> the bot stamps the real time on ingest
                    Out("{\"t\":\"report\",\"id\":\"" + sid + "\",\"n\":\"" + Esc(RawNameOf(p))
                        + "\",\"reason\":\"" + (storm ? "command-spam (SUPPRESSED: server-wide storm, likely congestion)" : "command-spam (sustained order rate)")
                        + "\",\"count\":" + owned + ",\"rate\":" + rateNow
                        + ",\"action\":\"" + action + "\",\"ts\":0}");
                    if (reportOnly || storm) continue;   // storm -> report only, never kick (don't amplify congestion into a mass-kick)
                    Instance?.TellPlayer(p, "<color=#FF0000>Auto-removed: commanding too many units at once (server protection).</color>");
                    if (hardBan) { _tkBanned.Add(sid); SaveBans(); }
                    _tkKicks.Add(new KeyValuePair<string, float>(sid, now + 2.5f));   // delayed kick (let the msg land) -> drained by TkTick
                }
            }
            catch (Exception e) { Log?.LogError("GriefTick: " + e); }
        }

        // ================= force-move / spectate + PvP auto-balance =================
        internal static ConfigEntry<bool> AutoMove, MoveOnlyUnspawned;
        internal static ConfigEntry<int>  RecheckSeconds, MoveDebounce, BalanceGraceSeconds, BalanceMoveExemptGames;
        internal static ConfigEntry<int>  BalanceMinPlayers, BalanceWarnSeconds;   // never balance under MinPlayers; warn WarnSeconds before moving
        internal static ConfigEntry<int>  BalanceNewJoinerSeconds, SquadMaxSize, SquadInviteSeconds;  // new-joiner protection window + !squadup tunables

        // numeric server-rank weight per SteamID (1..11), from plugin_ranks.txt 4th field.
        static readonly Dictionary<string, int> RankWeight = new Dictionary<string, int>();
        static float Weight(Player p)
        {
            try
            {
                if (BalanceBySkill == null || BalanceBySkill.Value)        // skill-based balance (default)
                {
                    LoadSkillMap();
                    if (_skillMap.Count > 0)
                    {
                        var sid = Sid(p);
                        return _skillMap.TryGetValue(sid, out var r) ? r : _skillAvg;   // unranked -> server average
                    }
                }
                LoadRankMap(); var id = Sid(p);                            // fallback: server-rank weight (no skill data yet)
                if (RankWeight.TryGetValue(id, out var w)) return w;
            }
            catch { }
            return 1f;   // last resort
        }

        static Player FindPlayerBySid(string sid)
        {
            if (string.IsNullOrEmpty(sid)) return null;
            foreach (var p in Humans()) if (Sid(p) == sid) return p;
            return null;
        }

        // ---- command channel (command centre -> bot -> here) ----
        // The bot drops ONE file per command in the game root: "plugin_cmd_<id>.txt" holding
        // "verb|steamId|faction". We process and DELETE each (so there's no dedup/replay to get
        // wrong). Writing those files needs SFTP/console access, so they're implicitly trusted.
        // A standalone persistent ticker so periodic plugin work keeps running even when HQ.Update is
        // absent (empty server, mission/scene transition, or a built-in PvP state that no longer ticks an
        // HQ). The HQ hook remains the fast path; this fallback ticks at ~2 Hz and PeriodicTick has a
        // per-frame guard, so being driven from both places is safe.
        internal class Ticker : MonoBehaviour
        {
            float _next;
            void Update() { try { if (Time.time >= _next) { _next = Time.time + 0.5f; PeriodicTick(); } } catch { } }
        }

        static float _nextCmdPoll;
        internal static void PollCommands()
        {
            try
            {
                float now = Time.time;
                PumpPending(now);                                        // run any due delayed moves
                PumpSwaps(now);                                          // advance any in-progress !swapteam/!forceteamswap
                if (now < _nextCmdPoll) return;
                _nextCmdPoll = now + 1f;                                 // glance at the drop folder ~1/sec
                TrackPresence(now);                                      // ~1/sec: maintain each player's join clock (new-joiner protection)
                MaybeWelcome(now);                                       // ~1/sec: fire the one-time private "plugin vX is active" notice
                string[] files;
                try { files = Directory.GetFiles(Paths.GameRootPath, "plugin_cmd_*.txt"); }
                catch { return; }
                if (files.Length == 0) return;
                Array.Sort(files, StringComparer.Ordinal);              // id-prefixed name => chronological
                foreach (var f in files)
                {
                    try { foreach (var raw in File.ReadAllLines(f)) { var l = raw.Trim(); if (l.Length > 0 && l[0] != '#') ExecCommand(l); } }
                    catch (Exception e) { Log?.LogError("cmd read: " + e); }
                    try { File.Delete(f); } catch (Exception e) { Log?.LogError("cmd delete: " + e); }
                }
            }
            catch (Exception e) { Log?.LogError("PollCommands: " + e); }
        }

        // ===================== LIVE CONFIG (webcc settings menu) =====================
        // The webcc settings menu reads/writes plugin tunables WITHOUT a redeploy. We drive this
        // generically off BepInEx's own ConfigFile.Keys, so EVERY Config.Bind key (and any future
        // one) is covered automatically — no hand-maintained registry to drift. DumpCfg emits the
        // current values as one [NOSTATS] {"t":"cfg",...} line the bot tails; SetCfg type-parses +
        // applies a value live (ConfigEntry.BoxedValue) and Config.Save()s it. Range validation is
        // done UPSTREAM in cc_web against the shipped settings catalogue, so here we only type-parse.
        // NOTE: Flood.Enforce / Flood.DropDeadNetIdRpcs apply LIVE — both flood-guard Harmony patches
        // are installed unconditionally at load and read their .Value inside the prefix each call, so
        // toggling them takes effect immediately. The ONE caveat: DropDeadNetIdRpcs fails open, so if
        // RpcHandler.HandleRpc never bound at load, turning it on later cannot retro-install the patch.
        static ConfigFile _cfgFile;   // cached in Awake; survives the plugin GameObject's destruction (Instance.Config would read Unity-null)
        static string CfgKey(ConfigDefinition d) => d.Section + "." + d.Key;
        static void AppendJsonVal(StringBuilder sb, object v)
        {
            if (v is bool b) sb.Append(b ? "true" : "false");
            else if (v is int || v is long || v is short || v is byte) sb.Append(Convert.ToString(v, CultureInfo.InvariantCulture));
            else if (v is float f) sb.Append((float.IsNaN(f) || float.IsInfinity(f)) ? "0" : f.ToString("R", CultureInfo.InvariantCulture));
            else if (v is double d) sb.Append((double.IsNaN(d) || double.IsInfinity(d)) ? "0" : d.ToString("R", CultureInfo.InvariantCulture));
            else { sb.Append('"').Append((v != null ? v.ToString() : "").Replace("\\", "\\\\").Replace("\"", "\\\"")).Append('"'); }
        }
        internal static void DumpCfg()
        {
            try
            {
                if (_cfgFile == null) return;
                var sb = new StringBuilder("{\"t\":\"cfg\",\"v\":{");
                bool first = true;
                foreach (var def in _cfgFile.Keys)
                {
                    var e = _cfgFile[def]; if (e == null) continue;
                    if (!first) sb.Append(','); first = false;
                    sb.Append('"').Append(CfgKey(def)).Append("\":");
                    AppendJsonVal(sb, e.BoxedValue);
                }
                sb.Append("}}");
                Out(sb.ToString());
            }
            catch (Exception ex) { Log?.LogError("DumpCfg: " + ex); }
        }
        // returns null on success, else a short error code.
        internal static string SetCfg(string key, string raw)
        {
            try
            {
                if (_cfgFile == null) return "no-config";
                if (string.IsNullOrEmpty(key)) return "no-key";
                foreach (var def in _cfgFile.Keys)
                {
                    if (!CfgKey(def).Equals(key, StringComparison.OrdinalIgnoreCase)) continue;
                    var e = _cfgFile[def];
                    var t = e.SettingType;
                    object val;
                    if (t == typeof(bool)) { string s = (raw ?? "").Trim().ToLowerInvariant(); val = (s == "1" || s == "true" || s == "on" || s == "yes"); }
                    else if (t == typeof(int)) { if (!int.TryParse((raw ?? "").Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out var i)) return "bad-int"; val = i; }
                    else if (t == typeof(float)) { if (!float.TryParse((raw ?? "").Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out var ff)) return "bad-float"; val = ff; }
                    else val = raw ?? "";
                    e.BoxedValue = val;
                    _cfgFile.Save();
                    Log?.LogInfo($"[cfg] set {CfgKey(def)} = {val}");
                    DumpCfg();                                   // re-broadcast so the bot/webcc reflect the new value immediately
                    return null;
                }
                return "unknown-key";
            }
            catch (Exception ex) { Log?.LogError("SetCfg: " + ex); return "error"; }
        }

        static void ExecCommand(string line)
        {
            try
            {
                var parts = line.Split('|');
                string verb = parts.Length > 0 ? parts[0].Trim().ToLowerInvariant() : "";
                Log?.LogInfo($"[cmd] recv: {(verb == "tell" ? "tell|…" : line)}");
                if (verb == "balance") { int n = BalanceOnce(true); Log?.LogInfo($"[cmd] balance -> {n} move(s)"); return; }
                if (verb == "dumpcfg") { DumpCfg(); return; }                  // webcc settings menu: re-emit current config
                if (verb == "ban" || verb == "unban")                          // webcc Reports tab: ban/unban a SteamID (immediate)
                {
                    string bsid = parts.Length > 1 ? parts[1].Trim() : "";
                    if (bsid.Length == 0) return;
                    if (verb == "ban")
                    {
                        _tkBanned.Add(bsid); SaveBans();
                        var bp = FindPlayerBySid(bsid);
                        if (bp != null) { try { Instance?.TellPlayer(bp, "<color=#FF0000>You have been banned from this server.</color>"); } catch { } _tkKicks.Add(new KeyValuePair<string, float>(bsid, Time.time + 1.5f)); }
                        Log?.LogInfo($"[cmd] BAN {bsid} (online={(bp != null)})");
                    }
                    else { _tkBanned.Remove(bsid); SaveBans(); Log?.LogInfo($"[cmd] UNBAN {bsid}"); }
                    return;
                }
                if (verb == "kick")                                            // anti-grief auto-kick (recoverable; NOT a ban). Used by the bot's command-flood detector.
                {
                    string ksid = parts.Length > 1 ? parts[1].Trim() : "";
                    if (ksid.Length == 0) return;
                    var kp = FindPlayerBySid(ksid);
                    if (kp != null) { try { Instance?.TellPlayer(kp, "<color=#FF0000>Removed: command flooding (server protection).</color>"); } catch { } _tkKicks.Add(new KeyValuePair<string, float>(ksid, Time.time + 1.0f)); }
                    Log?.LogInfo($"[cmd] KICK {ksid} (online={(kp != null)})");
                    return;
                }
                if (verb == "setcfg")                                          // webcc settings menu: setcfg|Section.Key|value
                {
                    string ck = parts.Length > 1 ? parts[1].Trim() : "";
                    string cv = parts.Length > 2 ? parts[2].Trim() : "";
                    var cerr = SetCfg(ck, cv);
                    Log?.LogInfo($"[cmd] setcfg {ck}={cv} -> {(cerr ?? "ok")}");
                    return;
                }
                if (verb == "tell")                                     // private message to one player (cuts chat spam)
                {
                    string tsid = parts.Length > 1 ? parts[1].Trim() : "";
                    string body = parts.Length > 2 ? string.Join("|", parts, 2, parts.Length - 2) : "";
                    var pl = FindPlayerBySid(tsid);
                    if (pl == null) { Log?.LogInfo($"[cmd] tell: player {tsid} not found ({Humans().Count} humans online)"); return; }
                    if (pl.Owner == null) { Log?.LogWarning($"[cmd] tell: {tsid} found but .Owner is null - cannot target"); return; }
                    Log?.LogInfo($"[cmd] tell -> {tsid} (Owner ok), delivering");
                    if (Instance != null)
                        foreach (var ln in body.Split('\u001f'))
                            if (!string.IsNullOrEmpty(ln)) Instance.TellPlayer(pl, ln);
                    return;
                }
                string sid = parts.Length > 1 ? parts[1].Trim() : "";
                var target = FindPlayerBySid(sid);
                if (target == null) { Log?.LogInfo($"[cmd] {verb}: player {sid} not found/offline"); return; }
                if (verb == "spec" || verb == "spectate" || verb == "unteam")
                {
                    Instance?.RequestMove(target, null, true);          // immediate (ejects if flying)
                    return;
                }
                if (verb == "help")                                     // private command list, delivered like !spec's reply
                {
                    Instance?.SendHelp(target);
                    return;
                }
                if (verb == "move" || verb == "join" || verb == "team")
                {
                    var hq = FindFaction(parts.Length > 2 ? parts[2].Trim() : "");
                    if (hq == null) { Log?.LogInfo($"[cmd] {verb}: unknown faction '{(parts.Length > 2 ? parts[2] : "")}'"); return; }
                    Instance?.RequestMove(target, hq, false);
                    return;
                }
                if (verb == "setrank")                                  // setrank|sid|N  -> set in-game rank
                {
                    if (parts.Length > 2 && int.TryParse(parts[2].Trim(), out int rk)) Instance?.SetPlayerRank(target, rk);
                    else Log?.LogInfo($"[cmd] setrank: bad rank '{(parts.Length > 2 ? parts[2] : "")}'");
                    return;
                }
                if (verb == "setfunds" || verb == "addfunds")           // setfunds|sid|N (set) / addfunds|sid|N (delta)
                {
                    if (parts.Length > 2 && float.TryParse(parts[2].Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out float amt))
                        Instance?.SetPlayerFunds(target, amt, verb == "addfunds");
                    else Log?.LogInfo($"[cmd] {verb}: bad amount '{(parts.Length > 2 ? parts[2] : "")}'");
                    return;
                }
                if (verb == "skyswap") { Instance?.HandleSkySwap(target, target); return; }   // drop the target into an armed jet high up
                if (verb == "swapteam" || verb == "forceteamswap")                           // move the target to the other team (panel-relayed; chat path is TryHandleChatCommand)
                { Instance?.BeginSwap(target, target, verb == "forceteamswap"); return; }
                Log?.LogInfo($"[cmd] unknown verb '{verb}'");
            }
            catch (Exception e) { Log?.LogError("ExecCommand: " + e); }
        }

        // ---- move orchestration: spectate is immediate; a team move of a FLYING player gets a
        // 10s chat warning then ejects them out of the jet so the move actually takes effect. ----
        sealed class Pending { public Player p; public FactionHQ to; public float due; }
        static readonly List<Pending> _pendingMoves = new List<Pending>();

        internal void RequestMove(Player target, FactionHQ to, bool isSpec)
        {
            if (target == null) return;
            if (isSpec) { DoMoveNow(target, null); return; }            // spectate: now, no warning
            if (IsFlying(target))
            {
                string fn = to != null && to.faction != null ? to.faction.factionName : "the other team";
                BroadcastAll($"<color=#FFC857>{RawNameOf(target)} is being moved to {fn} in 10 seconds.</color>");
                _pendingMoves.RemoveAll(x => x.p == target);           // collapse repeats
                _pendingMoves.Add(new Pending { p = target, to = to, due = Time.time + 10f });
                Log?.LogInfo($"[cmd] scheduled flying move: {RawNameOf(target)} -> {fn} in 10s");
            }
            else DoMoveNow(target, to);
        }

        internal void DoMoveNow(Player p, FactionHQ to)
        {
            if (p == null) return;
            AdminEject(p);   // leave the jet so the change shows (life-neutral: balance/admin move never ends a skill-life)
            if (MovePlayer(p, to))
            {
                if (to == null) TellPlayer(p, "<color=#36FFD0>You've been moved to spectate (no team).</color>");
                else TellPlayer(p, $"<color=#36FFD0>You've been moved to {(to.faction != null ? to.faction.factionName : "the other team")}.</color>");
            }
        }

        // Auto-balance spectate move: a FLYING player gets a 10s chat warning then is ejected to
        // spectate; an unspawned player is moved immediately. The join guard funnels them to the
        // smaller side on rejoin. (Debounce in BalanceOnce stops a 2nd schedule during the warning.)
        internal void RequestBalanceSpectate(Player p, string smallerName)
        {
            if (p == null) return;
            if (IsFlying(p))
            {
                BroadcastAll($"<color=#FFC857>{RawNameOf(p)} will be moved to spectate in 10s to balance teams - rejoin {smallerName} (fewer players), or type !spec now.</color>");
                _pendingMoves.RemoveAll(x => x.p == p);
                _pendingMoves.Add(new Pending { p = p, to = null, due = Time.time + 10f });
                Log?.LogInfo($"[balance] scheduled spectate for {RawNameOf(p)} in 10s");
            }
            else
            {
                DoMoveNow(p, null);
                TellPlayer(p, $"<color=#36FFD0>Teams were unbalanced - moved to spectate. Rejoin {smallerName}.</color>");
            }
        }

        static void PumpPending(float now)
        {
            for (int i = _pendingMoves.Count - 1; i >= 0; i--)
            {
                var pm = _pendingMoves[i];
                if (pm.p == null) { _pendingMoves.RemoveAt(i); continue; }
                if (now >= pm.due) { _pendingMoves.RemoveAt(i); Instance?.DoMoveNow(pm.p, pm.to); }
            }
        }

        // ---- join handling (the TEAM BLOCKER): returning false from the CmdSetFaction patch does NOT
        // reliably stop the join, so we ALLOW it and, on the very next tick, IMMEDIATELY move anyone who
        // joined the over-full side back to spectate - NO warning, NO grace period (a player can't have
        // spawned within one frame of joining, so this lands before they're in a jet). They get a short
        // note telling them to join the smaller side. This is the ONLY thing that fires on a join;
        // autobalance (MaybeBalance) is reserved for LEAVES. Cheap when idle. _joinProbation is now
        // vestigial (never populated) so OnPlayerSpawned is an inert safety net. ----
        static readonly List<Player> _bounceQueue = new List<Player>();
        static readonly HashSet<string> _joinProbation = new HashSet<string>(StringComparer.Ordinal);  // warned over-stackers
        internal static void QueueBounceCheck(Player p)
        {
            if (p == null) return;
            _bounceQueue.RemoveAll(x => x == p);
            _bounceQueue.Add(p);
        }

        internal static void PumpBounces()
        {
            if (_bounceQueue.Count == 0) return;
            for (int i = _bounceQueue.Count - 1; i >= 0; i--)
            {
                var p = _bounceQueue[i];
                _bounceQueue.RemoveAt(i);
                try
                {
                    if (p == null) continue;
                    string sid = Sid(p);
                    if (EnforceBalance == null || !EnforceBalance.Value) { _joinProbation.Remove(sid); continue; }
                    FactionHQ hq = null; try { hq = p.HQ; } catch { }
                    if (hq == null) { _joinProbation.Remove(sid); continue; }            // spectating / left
                    var other = OtherHQ(hq);
                    if (other == null || hq.preventJoin || other.preventJoin) { _joinProbation.Remove(sid); continue; }  // PvP only
                    int max = BalanceMaxDiff != null ? BalanceMaxDiff.Value : 2;
                    if (Side(hq).Count - Side(other).Count > max)                        // joined the over-full side -> INSTANT spectate (no warning)
                    {
                        _joinProbation.Remove(sid);                                      // not a probation case anymore - moved now
                        string smaller  = (other.faction != null) ? other.faction.factionName : "the other team";
                        string fullName = (hq.faction   != null) ? hq.faction.factionName    : "That team";
                        Instance?.DoMoveNow(p, null);                                    // straight to spectate, immediately
                        Instance?.TellPlayer(p, "<color=#FF5555>" + fullName + " has more players - moved to spectate.</color> " +
                            "<color=#FFD200>Reopen the map, click a faction, and join " + smaller + " (the smaller team).</color>");
                        Log?.LogInfo($"[balance] bounced {RawNameOf(p)} to spectate (joined the fuller side)");
                    }
                    else _joinProbation.Remove(sid);                                      // joined a fine side -> clear
                }
                catch (Exception e) { Log?.LogError("PumpBounces: " + e); }
            }
        }

        // Called when a player spawns (Player.SetAircraft). If they were warned for over-stacking and
        // the team is STILL too far ahead, eject them out of the jet and drop them to spectate.
        internal void OnPlayerSpawned(Player p)
        {
            try
            {
                if (p == null) return;
                string sid = Sid(p);
                if (!_joinProbation.Contains(sid)) return;
                FactionHQ hq = null; try { hq = p.HQ; } catch { }
                if (hq == null) { _joinProbation.Remove(sid); return; }
                var other = OtherHQ(hq);
                if (other == null || hq.preventJoin || other.preventJoin) { _joinProbation.Remove(sid); return; }
                int max = BalanceMaxDiff != null ? BalanceMaxDiff.Value : 2;
                _joinProbation.Remove(sid);
                if (Side(hq).Count - Side(other).Count > max)                            // still over-full -> eject to spectate
                {
                    string smaller = (other.faction != null) ? other.faction.factionName : "the smaller team";
                    AdminEject(p);   // life-neutral: balance probation eject must not end a skill-life
                    if (MovePlayer(p, null))
                        TellPlayer(p, "<color=#36FFD0>That team was full - moved to spectate. Rejoin " + smaller +
                            " (open the map, click a faction).</color>");
                    Log?.LogInfo($"[balance] ejected {RawNameOf(p)} on spawn (still over-full)");
                }
            }
            catch (Exception e) { Log?.LogError("OnPlayerSpawned: " + e); }
        }

        void BroadcastAll(string msg)
        {
            try { var cm = Cm ?? (Cm = UnityEngine.Object.FindObjectOfType<ChatManager>()); if (cm != null) cm.RpcServerMessage(msg, false); }
            catch (Exception e) { Log?.LogError("BroadcastAll: " + e); }
        }

        // ---- admin auth for the IN-GAME commands (config; the user named this SteamID) ----
        internal static ConfigEntry<string> AdminSteamIds;
        static bool IsAdmin(Player p)
        {
            try
            {
                if (AdminSteamIds == null) return false;
                string id = Sid(p);
                foreach (var a in AdminSteamIds.Value.Split(',', ' ', ';'))
                    if (a.Trim() == id && id.Length > 0) return true;
            }
            catch { }
            return false;
        }

        // resolve a player by name substring; messages the admin on no/ambiguous match.
        Player Resolve(Player admin, string namePart)
        {
            namePart = (namePart ?? "").Trim().ToLowerInvariant();
            if (namePart.Length == 0) { TellPlayer(admin, "name a player, e.g. !move bob primeva"); return null; }
            var hits = new List<Player>();
            foreach (var pl in Humans()) if (RawNameOf(pl).ToLowerInvariant().Contains(namePart)) hits.Add(pl);
            if (hits.Count == 0) { TellPlayer(admin, $"<color=#FF5555>No player matches '{namePart}'.</color>"); return null; }
            if (hits.Count > 1)
            {
                var names = new StringBuilder();
                foreach (var h in hits) { if (names.Length > 0) names.Append(", "); names.Append(RawNameOf(h)); }
                TellPlayer(admin, $"<color=#FFC857>Ambiguous '{namePart}': {names}. Be more specific.</color>");
                return null;
            }
            return hits[0];
        }

        // The two JOINABLE human factions (preventJoin == false). Co-op's AI side (preventJoin==true)
        // and any neutral/extra FactionHQ are skipped, so auto-balance picks the two real PvP teams
        // even on the BUILT-IN missions, which can expose more than two FactionHQs (the old "grab the
        // first two" version mis-detected those and silently disabled balancing). < 2 joinable sides =
        // co-op / not-a-PvP-match -> not balanceable.
        static bool TwoSides(out FactionHQ a, out FactionHQ b)
        {
            a = null; b = null;
            var joinable = new List<FactionHQ>();
            foreach (var hq in UnityEngine.Object.FindObjectsOfType<FactionHQ>())
                if (hq != null && hq.faction != null && !hq.preventJoin) joinable.Add(hq);
            if (joinable.Count < 2) return false;
            if (joinable.Count > 2)                                   // rare: pick the two most-populated teams
                joinable.Sort((x, y) => Side(y).Count.CompareTo(Side(x).Count));
            a = joinable[0]; b = joinable[1];
            return true;
        }

        // PvP mission = >= 2 JOINABLE factions (preventJoin == false). Co-op has one joinable side + a
        // preventJoin AI side. We try the MISSION DEFINITION first (timing-independent, reliable for our
        // custom JSON missions) and fall back to the live FactionHQs (covers BUILT-IN PvP maps whose
        // Mission.factions list may be constructed differently). Either signal saying >=2 -> PvP; co-op
        // yields 1 on both, so no false positive. Used by the rank floor.
        internal static bool IsPvpMission(Mission m)
        {
            try
            {
                if (m != null && m.factions != null)
                {
                    int joinable = 0;
                    foreach (var f in m.factions) if (f != null && !f.preventJoin) joinable++;
                    if (joinable >= 2) return true;
                }
            }
            catch { }
            try { return TwoSides(out _, out _); }      // live-FactionHQ backstop (built-in missions)
            catch { return false; }
        }

        static FactionHQ FindFaction(string key)
        {
            if (string.IsNullOrWhiteSpace(key)) return null;
            key = key.Trim().ToLowerInvariant();
            foreach (var hq in UnityEngine.Object.FindObjectsOfType<FactionHQ>())
            {
                if (hq == null || hq.faction == null) continue;
                string fn = (hq.faction.factionName ?? "").ToLowerInvariant();
                if (fn.Length == 0) continue;
                if (fn == key || fn.StartsWith(key) || key.StartsWith(fn)) return hq;
                if ((key == "bdf"  || key == "0") && fn.Contains("bosc")) return hq;   // Boscali = BDF
                if ((key == "pala" || key == "1") && fn.Contains("prim")) return hq;   // Primeva = PALA
            }
            return null;
        }

        // live humans on a side (skips ghosts from mid-disconnect)
        static List<Player> Side(FactionHQ hq)
        {
            var list = new List<Player>();
            if (hq == null) return list;
            try
            {
                foreach (var pr in hq.factionPlayers)
                {
                    var p = pr.Player; if (p == null) continue;
                    var s = Sid(p); if (!string.IsNullOrEmpty(s) && s != "0") list.Add(p);
                }
            }
            catch { }
            return list;
        }

        static bool IsFlying(Player p) { try { return p.Aircraft != null; } catch { return false; } }

        // The HQ SyncVar's public setter is named with angle brackets; the clean "HQ" property's
        // PRIVATE setter just forwards to it (and marks the SyncVar dirty -> syncs to clients).
        static readonly System.Reflection.MethodInfo HqSetter =
            typeof(Player).GetProperty("HQ", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance)?.GetSetMethod(true);

        // Move a player to `to` (null => spectate / no team). The game's SetFaction refuses a
        // change once HQ is set, so we do the surgery ourselves: RemovePlayer (old) -> set HQ
        // SyncVar -> AddPlayer (new), mirroring ServerApplyFaction.
        internal bool MovePlayer(Player p, FactionHQ to)
        {
            if (p == null || HqSetter == null) { Log?.LogError("MovePlayer: no player / HQ setter missing"); return false; }
            FactionHQ from = null; try { from = p.HQ; } catch { }
            if (from == to) return false;
            try
            {
                if (from != null) from.RemovePlayer(p);
                HqSetter.Invoke(p, new object[] { to });
                if (to != null) { to.AddPlayer(p); try { to.RequestTrackingStates(p); } catch { } }
                Log?.LogInfo($"[move] {RawNameOf(p)} {(from != null && from.faction != null ? from.faction.factionName : "none")} -> {(to != null && to.faction != null ? to.faction.factionName : "spectate")}");
                return true;
            }
            catch (Exception e) { Log?.LogError("MovePlayer: " + e); return false; }
        }

        // ---- admin: set a player's IN-GAME rank / IN-GAME funds (the spendable Allocation) ----
        // Both call the game's own [Server] methods (we run server-side). SetRank(true) writes a
        // scoreOffset so the rank STICKS (the game only auto-bumps rank UP from score, never down, so
        // a set rank holds unless the player out-scores it). NOTE: separate from the bot's persistent
        // SERVER rank in ranks.json - this changes what the GAME shows this match. A mission restart
        // re-applies the mission's playerStartingRank floor (StartingRankFloorPatch).
        internal void SetPlayerRank(Player target, int rank)
        {
            try { if (target != null) { target.SetRank(rank, true); Log?.LogInfo($"[admin] setrank {RawNameOf(target)} -> {target.PlayerRank}"); } }
            catch (Exception e) { Log?.LogError("SetPlayerRank: " + e); }
        }
        // funds = Player.Allocation (the player's personal spendable budget). add=false -> SetAllocation;
        // add=true -> AddAllocation (delta, may be negative).
        internal void SetPlayerFunds(Player target, float amount, bool add)
        {
            try
            {
                if (target == null) return;
                if (add) target.AddAllocation(amount); else target.SetAllocation(amount);
                Log?.LogInfo($"[admin] {(add ? "addfunds" : "setfunds")} {RawNameOf(target)} {(add ? "+" : "=")}{amount:0} -> {target.Allocation:0}");
            }
            catch (Exception e) { Log?.LogError("SetPlayerFunds: " + e); }
        }

        // ============ RANK CATCH-UP (rising start-rank floor over match time) ============
        // The starting-rank FLOOR rises +1 every PvpRankCatchupMinutes of match time (capped at
        // PvpRankCatchupMaxRank), so latecomers spawn at the risen floor and already-connected players below it
        // are raised too. A FLOOR only: nobody is ever lowered. 0 minutes = off.
        internal static float MatchStartTime = -1f;
        static int _catchupAnnounced = -1;
        static float _nextCatchupCheck = -1f;
        internal static void ResetCatchup() { MatchStartTime = Time.time; _catchupAnnounced = -1; }
        internal static int CatchupBonus()
        {
            try
            {
                int mins = PvpRankCatchupMinutes != null ? PvpRankCatchupMinutes.Value : 0;
                if (mins <= 0 || MatchStartTime < 0f) return 0;
                return (int)((Time.time - MatchStartTime) / (mins * 60f));
            }
            catch { return 0; }
        }
        internal static int CatchupFloor(int baseRank)
        {
            int bonus = CatchupBonus();
            if (bonus <= 0) return baseRank;
            int cap = PvpRankCatchupMaxRank != null ? PvpRankCatchupMaxRank.Value : 6;
            if (cap < baseRank) cap = baseRank;
            int f = baseRank + bonus;
            return f > cap ? cap : f;
        }
        internal static void CatchupTick()   // ~15s: raise ALREADY-CONNECTED players below the current floor
        {
            try
            {
                float now = Time.time;
                if (now < _nextCatchupCheck) return;
                _nextCatchupCheck = now + 15f;
                if (CatchupBonus() <= 0) return;
                Mission m = null; try { m = MissionManager.CurrentMission; } catch { }
                bool pvp = IsPvpMission(m);
                int baseRank = 0;
                try { if (m != null && m.missionSettings != null) baseRank = m.missionSettings.playerStartingRank; } catch { }
                if (pvp && PvpStartingRank != null && PvpStartingRank.Value > baseRank) baseRank = PvpStartingRank.Value;
                int floor = CatchupFloor(baseRank);
                if (floor <= baseRank) return;
                string fmode = FundsMode();
                int fper = RankFundsPerRank != null ? RankFundsPerRank.Value : 0;
                int raised = 0;
                foreach (var p in Humans())
                {
                    try
                    {
                        if (p == null || p.HQ == null) continue;
                        if (p.PlayerRank >= floor) continue;
                        int was = p.PlayerRank;
                        p.SetRank(floor, true);
                        raised++;
                        Log?.LogInfo($"[catchup] {RawNameOf(p)} {was} -> {floor} (rank catch-up floor)");
                        if (fper > 0 && fmode == "catchup_raised")
                        {
                            string fsid = Sid(p);
                            if (!string.IsNullOrEmpty(fsid) && fsid != "0")
                            {
                                long amt = (long)(floor - was) * fper * 1000000L;
                                p.AddAllocation(amt);
                                Log?.LogInfo($"[rankfunds] +{amt:0} to {RawNameOf(p)} (catch-up lift {was} -> {floor})");
                                Out("{\"t\":\"rankfunds\",\"id\":\"" + fsid + "\",\"n\":\"" + Esc(RawNameOf(p))
                                    + "\",\"rank\":" + floor + ",\"amt\":" + amt.ToString(CultureInfo.InvariantCulture) + "}");
                            }
                        }
                    }
                    catch { }
                }
                if (floor != _catchupAnnounced)
                {
                    _catchupAnnounced = floor;
                    Instance?.BroadcastAll($"<color=#9AD1FF>Rank catch-up:</color> <color=#FFFFFF>the starting rank now floors at</color> <color=#FFD700>{floor}</color>");
                    Log?.LogInfo($"[catchup] floor now {floor} (base {baseRank}, raised {raised})");
                }
                if (fper > 0 && fmode == "catchup_all")
                {
                    if (_catchupPaidFloor < 0) _catchupPaidFloor = baseRank;   // baseline at the unraised base so the FIRST step pays everyone
                    if (floor > _catchupPaidFloor)
                    {
                        long amt = (long)(floor - _catchupPaidFloor) * fper * 1000000L;
                        int paid = 0;
                        foreach (var p in Humans())
                        {
                            try
                            {
                                if (p == null) continue;
                                string fsid = Sid(p);
                                if (string.IsNullOrEmpty(fsid) || fsid == "0") continue;
                                p.AddAllocation(amt);
                                paid++;
                                Out("{\"t\":\"rankfunds\",\"id\":\"" + fsid + "\",\"n\":\"" + Esc(RawNameOf(p))
                                    + "\",\"rank\":" + floor + ",\"amt\":" + amt.ToString(CultureInfo.InvariantCulture) + "}");
                            }
                            catch { }
                        }
                        Log?.LogInfo($"[rankfunds] catch-up step -> +{amt:0} to all {paid} players (floor {_catchupPaidFloor} -> {floor})");
                        _catchupPaidFloor = floor;
                    }
                }
            }
            catch (Exception e) { Log?.LogError("CatchupTick: " + e); }
        }

        // ============ ACCUMULATIVE RANK FUNDS ============
        // The rank a player is FIRST seen at is recorded as the baseline and grants NOTHING (no join/start-floor
        // payout). Only a subsequent in-game rank INCREASE above that baseline grants in-game money =
        // (newRank - highestFundedRankThisMatch) x RankFundsPerRank(in MILLIONS) via the SAME funds path as admin
        // addfunds (Player.AddAllocation). MONOTONIC per match (per-steamid highest-funded map, never grant the
        // same rank twice, survives reconnect), RESET on mission change, prestige-safe (prestige is bot-side and
        // never lowers in-game rank). 0 = off. RankFundsMode picks WHEN it pays: "any_rankup" = this periodic scan
        // pays on every rank increase (natural or catch-up); "catchup_raised" (default) = only the catch-up floor
        // lifting a player pays, for the lift, done in CatchupTick (this scan is skipped); "catchup_all" = every
        // connected player is paid one rank of funds each time the catch-up floor steps up, also in CatchupTick.
        static readonly Dictionary<string, int> _rankFunded = new Dictionary<string, int>(StringComparer.Ordinal);
        static int _catchupPaidFloor = -999;   // catchup_all: last floor everyone was paid up to (reset per match)
        internal static void ResetRankFunds() { _rankFunded.Clear(); _catchupPaidFloor = -999; }
        // WHEN funds pay out: normalize the config value; anything unknown -> catchup_raised (the default).
        static string FundsMode()
        {
            string m = (RankFundsMode != null ? RankFundsMode.Value : "catchup_raised");
            m = (m ?? "").Trim().ToLowerInvariant();
            return (m == "any_rankup" || m == "catchup_all") ? m : "catchup_raised";
        }
        internal static void RankFundsTick()
        {
            try
            {
                int per = RankFundsPerRank != null ? RankFundsPerRank.Value : 0;
                if (per <= 0) return;   // feature off
                if (FundsMode() != "any_rankup") return;   // catchup_raised / catchup_all pay from CatchupTick, not this natural-rank-up scan
                foreach (var p in Humans())
                {
                    try
                    {
                        if (p == null) continue;
                        string sid = Sid(p);
                        if (string.IsNullOrEmpty(sid) || sid == "0") continue;
                        int rank = p.PlayerRank;
                        if (!_rankFunded.TryGetValue(sid, out var funded))
                        {
                            _rankFunded[sid] = rank;                   // FIRST sighting: record rank as the BASELINE
                            continue;                                  // never grant on the join/start floor
                        }
                        if (rank <= funded) continue;                  // no increase (monotonic)
                        long amount = (long)(rank - funded) * per * 1000000L;   // per is in MILLIONS; cumulative top-up
                        _rankFunded[sid] = rank;                        // mark BEFORE granting (never double-grant)
                        p.AddAllocation(amount);                        // same primitive as admin !addfunds
                        Log?.LogInfo($"[rankfunds] +{amount:0} funds to {RawNameOf(p)} for reaching rank {rank} (was funded to {funded})");
                        // let the bot surface an announce
                        Out("{\"t\":\"rankfunds\",\"id\":\"" + sid + "\",\"n\":\"" + Esc(RawNameOf(p))
                            + "\",\"rank\":" + rank + ",\"amt\":" + amount.ToString(CultureInfo.InvariantCulture) + "}");
                    }
                    catch { }
                }
            }
            catch (Exception e) { Log?.LogError("RankFundsTick: " + e); }
        }

        // ---- mission-timeout resolution: end the match with a RESULT a bit BEFORE the game's MaxTime, so the
        // bot's map vote can run before the mission auto-rotates. PvE (1 human side vs AI) -> declare the humans
        // defeated (gated by TimeoutForceDefeat); PvP (2 joinable sides) -> the higher TOTAL in-game score wins,
        // exact tie = draw (gated by PvpTimeoutResult). Lead = TimeoutLeadSeconds. Double-end guarded; 1 Hz from
        // HQTickPatch. (Still named PvETimeoutTick for the existing call site.) ----
        static float _lastTimeoutCheck = -999f;
        internal static void PvETimeoutTick()
        {
            try
            {
                float now = Time.time;
                if (now - _lastTimeoutCheck < 1f) return;            // 1 Hz, cheap
                _lastTimeoutCheck = now;
                if (GameManager.gameResolution != GameResolution.Ongoing) return;  // already ended -> guard
                bool pveOn = TimeoutForceDefeat != null && TimeoutForceDefeat.Value;
                bool pvpOn = PvpTimeoutResult != null && PvpTimeoutResult.Value;
                if (!pveOn && !pvpOn) return;

                float maxTime = CurrentMissionMaxTime();
                if (maxTime <= 0f) return;
                int lead = TimeoutLeadSeconds != null ? Mathf.Max(0, TimeoutLeadSeconds.Value) : 120;
                if (Time.timeSinceLevelLoad <= maxTime - lead) return;   // not within the lead window yet

                // Enumerate ALL HQs (the AI side has preventJoin==true, which TwoSides() hides).
                FactionHQ aiHQ = null;
                int human = 0, ai = 0;
                foreach (var hq in UnityEngine.Object.FindObjectsOfType<FactionHQ>())
                {
                    if (hq == null || hq.faction == null) continue;
                    if (hq.preventJoin) { ai++; aiHQ = hq; }         // AI-only side
                    else human++;                                    // human-joinable side
                }

                if (human == 1 && ai >= 1 && aiHQ != null)
                {
                    if (!pveOn) return;                              // PvE, but the PvE defeat is off
                    Log?.LogInfo($"[timeout] PvE timer ({Time.timeSinceLevelLoad:F0}s, {lead}s before {maxTime:F0}s) -> declaring AI victory (humans defeated).");
                    ForceVictory(aiHQ);                             // humans see Mission Failed
                    return;
                }

                if (pvpOn && TwoSides(out var A, out var B))
                {
                    double sa = TeamScore(A), sb = TeamScore(B);
                    string na = A.faction != null ? A.faction.factionName : "Team A";
                    string nb = B.faction != null ? B.faction.factionName : "Team B";
                    if (Math.Abs(sa - sb) < 0.0001)
                    {
                        Log?.LogInfo($"[timeout] PvP timer ({Time.timeSinceLevelLoad:F0}s) -> DRAW ({na} {sa:F0} = {nb} {sb:F0}).");
                        Instance?.BroadcastAll($"<color=#FFD200>** Time's up - it's a DRAW! {na} {sa:F0} : {sb:F0} {nb} **</color>");
                        ForceDraw(A, B);
                    }
                    else
                    {
                        var winHQ = sa > sb ? A : B; string wn = sa > sb ? na : nb;
                        Log?.LogInfo($"[timeout] PvP timer ({Time.timeSinceLevelLoad:F0}s) -> {wn} wins on score ({na} {sa:F0} vs {nb} {sb:F0}).");
                        Instance?.BroadcastAll($"<color=#7CFFB0>** Time's up - {wn} wins on score! {na} {sa:F0} : {sb:F0} {nb} **</color>");
                        ForceVictory(winHQ);
                    }
                }
            }
            catch (Exception e) { Log?.LogError("PvETimeoutTick: " + e); }
        }

        // total in-game score for a side = the faction's own score (FactionHQ.factionScore),
        // which is EXACTLY the per-faction total the game displays on the leaderboard / join menu /
        // aircraft-selection (e.g. PALA 118 vs BDF 117). factionScore is a faction-wide accumulation
        // (kills, successful sorties, captures, wreck collection) and is a SEPARATE, much larger value
        // than any single player's PERSONAL PlayerScore. 0.9.48 summed each player's PlayerScore
        // instead, which is a different number entirely and produced the wrong 12/0 readout - fixed
        // to read factionScore so the announced/compared totals match the scoreboard.
        static double TeamScore(FactionHQ hq)
        {
            if (hq == null) return 0;
            try { return hq.factionScore; }
            catch { return 0; }
        }

        // End a PvP match as a DRAW: prefer a real Draw-like EndType if the game has one, else declare BOTH human
        // teams defeated (no winner). Best-effort - exact decimal-score ties are near-impossible.
        static void ForceDraw(FactionHQ a, FactionHQ b)
        {
            try
            {
                if (GameManager.gameResolution != GameResolution.Ongoing) return;
                var m = typeof(FactionHQ).GetMethod("DeclareEndGame");
                if (m == null) return;
                var et = m.GetParameters()[0].ParameterType;
                object drawVal = null;
                foreach (var name in new[] { "Draw", "Tie", "Stalemate" })
                { try { drawVal = System.Enum.Parse(et, name); break; } catch { } }
                if (drawVal != null) { m.Invoke(a, new object[] { drawVal }); return; }
                object defeat; try { defeat = System.Enum.Parse(et, "Defeat"); } catch { return; }
                m.Invoke(a, new object[] { defeat });
                if (GameManager.gameResolution == GameResolution.Ongoing) m.Invoke(b, new object[] { defeat });
            }
            catch (Exception e) { Log?.LogError("ForceDraw: " + e); }
        }

        // Reflection helpers for game internals the plugin can't reference directly (EndType is internal;
        // DedicatedServerManager sits in an un-imported namespace). All FAIL SAFE (null/-1) so a wrong
        // name can never fire a false defeat - it just no-ops until the names are verified in testing.
        static System.Type _dsmType; static bool _dsmResolved;
        static System.Type FindGameType(string simpleName)
        {
            foreach (var a in System.AppDomain.CurrentDomain.GetAssemblies())
            {
                System.Type[] ts; try { ts = a.GetTypes(); } catch { continue; }
                foreach (var t in ts) if (t.Name == simpleName) return t;
            }
            return null;
        }
        static object GetMember(object o, string name)
        {
            if (o == null) return null;
            var t = o.GetType();
            var p = t.GetProperty(name); if (p != null) return p.GetValue(o);
            var f = t.GetField(name);    if (f != null) return f.GetValue(o);
            return null;
        }
        static float CurrentMissionMaxTime()
        {
            try
            {
                if (!_dsmResolved) { _dsmType = FindGameType("DedicatedServerManager"); _dsmResolved = true; }
                if (_dsmType == null) return -1f;
                object inst = null;
                var ip = _dsmType.GetProperty("Instance", System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Static);
                if (ip != null) inst = ip.GetValue(null);
                else { var f = _dsmType.GetField("Instance", System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Static); if (f != null) inst = f.GetValue(null); }
                object opt = GetMember(inst, "CurrentMissionOption");
                object mt = GetMember(opt, "MaxTime");
                return mt == null ? -1f : System.Convert.ToSingle(mt);
            }
            catch { return -1f; }
        }

        // ---- auto-balancer (PvP only), polled from HQTickPatch ----
        // DESIGN (2026-06-26): autobalance fires ONLY in response to a player LEAVING (a side's
        // human count drops) - NOT on joins, NOT continuously. Joining the fuller side is handled
        // separately + instantly by the join blocker (PumpBounces -> immediate spectate). So the two
        // mechanisms are cleanly split: LEAVE -> autobalance moves one to even up; JOIN over-full ->
        // the joiner is bounced. We arm on a population decrease, then HOLD for GraceSeconds (a few
        // minutes) so the gap can self-correct (a rejoin / someone filling the smaller side) before the
        // first move, and keep trying (debounce-paced) until teams are within MaxDifference, then disarm.
        static float _nextBalance, _lastMove = -999f;
        static int   _lastSideTotal = -1;     // last observed (A+B) human count; a DECREASE = someone left
        static bool  _balanceArmed;           // a leave armed autobalance; cleared once teams are even
        static float _unevenSince = -1f;      // Time.time the still-standing imbalance first appeared (grace anchor)
        internal static void MaybeBalance()
        {
            try
            {
                if (EnforceBalance == null || !EnforceBalance.Value) return;
                if (AutoMove == null || !AutoMove.Value) return;
                float now = Time.time;
                if (now < _nextBalance) return;
                _nextBalance = now + Mathf.Max(2, RecheckSeconds != null ? RecheckSeconds.Value : 6);
                if (!TwoSides(out var A, out var B)) { _lastSideTotal = -1; _balanceArmed = false; _unevenSince = -1f; return; }
                // MIN-PLAYERS GATE (user 2026-06-27): never auto-balance a small lobby. Counts ALL humans on the
                // server (incl. spectators). Below the threshold -> disarm + reset so nothing is ever moved/warned.
                int people = Humans().Count;
                int minP = BalanceMinPlayers != null ? BalanceMinPlayers.Value : 6;
                if (people < minP) { _lastSideTotal = Side(A).Count + Side(B).Count; _balanceArmed = false; _unevenSince = -1f; return; }
                int total = Side(A).Count + Side(B).Count;
                if (_lastSideTotal >= 0 && total < _lastSideTotal) _balanceArmed = true;   // a player left -> arm
                _lastSideTotal = total;
                if (!_balanceArmed) return;                                                // ONLY act after a leave
                int max = BalanceMaxDiff != null ? BalanceMaxDiff.Value : 2;
                if (Math.Abs(Side(A).Count - Side(B).Count) <= max)                        // teams even (self-corrected or fixed)
                    { _balanceArmed = false; _unevenSince = -1f; return; }                 // -> disarm + reset the warn clock
                // armed AND uneven: broadcast a one-time warning, then HOLD for WarnSeconds (a 5-minute warning by
                // default) so the gap can self-correct (a rejoin / someone filling the smaller side) before any move.
                float warn = BalanceWarnSeconds != null ? BalanceWarnSeconds.Value : 300;
                if (_unevenSince < 0f)                                                      // first detection of THIS imbalance episode
                {
                    _unevenSince = now;
                    int bigC = Math.Max(Side(A).Count, Side(B).Count), smallC = Math.Min(Side(A).Count, Side(B).Count);
                    int mins = Mathf.Max(1, Mathf.RoundToInt(warn / 60f));
                    Instance?.BroadcastAll($"<color=#FFC857>Teams are unbalanced ({bigC} v {smallC}). If it doesn't even out, a player will be moved to balance in {mins} minute{(mins == 1 ? "" : "s")}.</color>");
                    Log?.LogInfo($"[balance] imbalance {bigC}v{smallC} with {people} on server; warned, will move in {warn:0}s if unresolved");
                }
                if (now - _unevenSince < warn) return;                                      // still inside the warning window -> wait
                BalanceOnce(false);                                                        // move one; stay armed until even
            }
            catch (Exception e) { Log?.LogError("MaybeBalance: " + e); }
        }

        // A player auto-balanced in game G is EXEMPT from being moved again until MoveExemptGames games
        // later (default 2 => "at most once per 2 games"), so the same person isn't repeatedly the one
        // moved. _gameNum advances once per mission start (AdvanceGame); expired exemptions are pruned.
        static readonly Dictionary<string, int> _movedAtGame = new Dictionary<string, int>(StringComparer.Ordinal);
        static int _gameNum;
        internal static void AdvanceGame()
        {
            _gameNum++;
            int span = (BalanceMoveExemptGames != null ? BalanceMoveExemptGames.Value : 2);
            List<string> stale = null;
            foreach (var kv in _movedAtGame)
                if (_gameNum - kv.Value >= span) (stale ?? (stale = new List<string>())).Add(kv.Key);
            if (stale != null) foreach (var s in stale) _movedAtGame.Remove(s);   // exemption expired -> movable again
        }
        static bool MoveExempt(string sid)            // moved within the last MoveExemptGames games?
        {
            int span = (BalanceMoveExemptGames != null ? BalanceMoveExemptGames.Value : 2);
            return _movedAtGame.TryGetValue(sid, out var g) && (_gameNum - g) < span;
        }

        // ===================== NEW-JOINER PROTECTION + SQUADS (2026-06-27) =====================
        // Auto-balance protection layers, STRONGEST first (all sit INSIDE the MoveExempt filter, so a
        // player moved within the last MoveExemptGames games is never the pick while anyone non-exempt
        // remains; "everyone else moved within a couple of games" is what unlocks dipping into a
        // protected player):
        //   2) NEW JOINER - connected < NewJoinerSeconds (15 min) ago. Protected first and foremost;
        //      moved only if EVERY other non-exempt big-side player is also a new joiner.
        //   1) SQUAD      - in a !squadup group (up to MaxSize friends). WEAKER than new-joiner: a
        //      squad member is moved only if no unprotected, non-exempt player is available.
        //   0) unprotected.
        // Within the least-protected non-empty tier we still pick whoever evens the teams' total SKILL
        // best (the existing weight/target logic).

        // ---- presence / first-seen clock (drives new-joiner protection) ----
        static readonly Dictionary<string, float> _firstSeen = new Dictionary<string, float>(StringComparer.Ordinal);
        // per-session "Nuke-Option Plugin Version X is active" PRIVATE welcome: scheduled ~6s after first
        // sighting (so the joining client's chat UI is ready), shown ONCE per session, and reset on leave
        // so a rejoin re-shows it. Parallels the _firstSeen presence clock above.
        static readonly Dictionary<string, float> _welcomeDue = new Dictionary<string, float>(StringComparer.Ordinal);
        static readonly HashSet<string> _welcomed = new HashSet<string>(StringComparer.Ordinal);
        static void TrackPresence(float now)
        {
            try
            {
                var present = new HashSet<string>(StringComparer.Ordinal);
                foreach (var p in Humans()) { var s = Sid(p); if (!string.IsNullOrEmpty(s) && s != "0") present.Add(s); }
                foreach (var s in present) if (!_firstSeen.ContainsKey(s)) { _firstSeen[s] = now; _welcomeDue[s] = now + 6f; }   // first sighting -> join clock + schedule welcome
                if (_firstSeen.Count > present.Count)                                               // someone left -> forget them so a rejoin resets the clock + re-welcomes
                {
                    List<string> gone = null;
                    foreach (var kv in _firstSeen) if (!present.Contains(kv.Key)) (gone ?? (gone = new List<string>())).Add(kv.Key);
                    if (gone != null) foreach (var s in gone) { _firstSeen.Remove(s); _welcomeDue.Remove(s); _welcomed.Remove(s); }
                }
            }
            catch (Exception e) { Log?.LogError("TrackPresence: " + e); }
        }

        // Fire the one-time per-session PRIVATE "plugin version is active" notice for any player whose
        // scheduled welcome time has arrived. Called ~1/sec from PollCommands, right after TrackPresence.
        static void MaybeWelcome(float now)
        {
            if (_welcomeDue.Count == 0) return;
            try
            {
                foreach (var p in Humans())
                {
                    var s = Sid(p);
                    if (string.IsNullOrEmpty(s) || _welcomed.Contains(s)) continue;
                    if (!_welcomeDue.TryGetValue(s, out var due) || now < due) continue;
                    Instance?.TellPlayer(p, $"<color=#6cc8ff>Nuke-Option Plugin Version {Version} is active on this server.</color>");
                    _welcomed.Add(s);
                    _welcomeDue.Remove(s);
                }
            }
            catch (Exception e) { Log?.LogError("MaybeWelcome: " + e); }
        }
        static bool IsNewJoiner(string sid)
        {
            int win = BalanceNewJoinerSeconds != null ? BalanceNewJoinerSeconds.Value : 900;
            if (win <= 0) return false;
            if (string.IsNullOrEmpty(sid)) return false;
            if (!_firstSeen.TryGetValue(sid, out var t)) return true;          // just appeared this frame -> treat as new (protected)
            return (Time.time - t) < win;
        }

        // auto-balance protection tier (LOWER = moved sooner). See the region header above.
        static int ProtTier(string sid)
        {
            if (IsNewJoiner(sid)) return 2;     // strongest
            if (InSquad(sid))     return 1;     // weaker
            return 0;                           // unprotected
        }

        // ---- squads (persist across matches AND restarts: plugin_squads.txt) ----
        static readonly List<HashSet<string>> _squads = new List<HashSet<string>>();
        static readonly Dictionary<string, string> _squadName = new Dictionary<string, string>(StringComparer.Ordinal);  // sid -> last-known display name
        struct SquadInvite { public string Inviter; public string InviterName; public float Expiry; }
        static readonly Dictionary<string, SquadInvite> _squadInvites = new Dictionary<string, SquadInvite>(StringComparer.Ordinal);  // invitee sid -> invite

        static string SquadFilePath => Path.Combine(Paths.GameRootPath, "plugin_squads.txt");
        static int SquadMax => SquadMaxSize != null ? Math.Max(2, SquadMaxSize.Value) : 4;
        static HashSet<string> SquadOf(string sid)
        {
            if (string.IsNullOrEmpty(sid)) return null;
            foreach (var sq in _squads) if (sq.Contains(sid)) return sq;
            return null;
        }
        static bool InSquad(string sid) => SquadOf(sid) != null;
        static string SafeName(string nm) => (nm ?? "").Replace('\t', ' ').Replace('~', '-').Replace('\n', ' ').Replace('\r', ' ');

        internal static void LoadSquads()
        {
            try
            {
                _squads.Clear();
                if (!File.Exists(SquadFilePath)) return;
                foreach (var line in File.ReadAllLines(SquadFilePath))
                {
                    var l = line.Trim(); if (l.Length == 0) continue;
                    var set = new HashSet<string>(StringComparer.Ordinal);
                    foreach (var tok in l.Split('\t'))
                    {
                        var t = tok.Trim(); if (t.Length == 0) continue;
                        int bar = t.IndexOf('~');
                        string sid = bar >= 0 ? t.Substring(0, bar) : t;
                        string nm  = bar >= 0 ? t.Substring(bar + 1) : "";
                        if (sid.Length == 0) continue;
                        set.Add(sid);
                        if (nm.Length > 0) _squadName[sid] = nm;
                    }
                    if (set.Count >= 2) _squads.Add(set);     // a 1-person "squad" is meaningless -> drop
                }
                Log?.LogInfo($"[squad] loaded {_squads.Count} squad(s)");
            }
            catch (Exception e) { Log?.LogError("LoadSquads: " + e); }
        }
        static void SaveSquads()
        {
            try
            {
                var sb = new StringBuilder();
                foreach (var sq in _squads)
                {
                    if (sq.Count < 2) continue;
                    bool first = true;
                    foreach (var sid in sq)
                    {
                        if (!first) sb.Append('\t'); first = false;
                        _squadName.TryGetValue(sid, out var nm);
                        sb.Append(sid).Append('~').Append(SafeName(nm));
                    }
                    sb.Append('\n');
                }
                File.WriteAllText(SquadFilePath, sb.ToString());
            }
            catch (Exception e) { Log?.LogError("SaveSquads: " + e); }
        }
        static string SquadMateList(HashSet<string> sq, string exclude)
        {
            var others = new StringBuilder();
            if (sq == null) return "";
            foreach (var s in sq)
            {
                if (s == exclude) continue;
                if (others.Length > 0) others.Append(", ");
                _squadName.TryGetValue(s, out var nm);
                others.Append(string.IsNullOrEmpty(nm) ? s : nm);
            }
            return others.ToString();
        }

        // ---- !squadup (PUBLIC): bare = status, <player> = invite, leave = exit ----
        void HandleSquadup(Player p, string[] parts)
        {
            try
            {
                string me = Sid(p);
                if (!string.IsNullOrEmpty(me)) _squadName[me] = RawNameOf(p);

                if (parts.Length < 2)                                        // bare !squadup -> status (persists across matches)
                {
                    var sq = SquadOf(me);
                    if (sq == null) { TellPlayer(p, $"<color=#36FFD0>You're not in a squad.</color> Use <color=#55FF55>!squadup <player></color> to team up with a friend (up to {SquadMax}) so PvP auto-balance won't split you up."); return; }
                    TellPlayer(p, $"<color=#36FFD0>You're squadded with {SquadMateList(sq, me)}.</color> (<color=#55FF55>!squadup leave</color> to exit.)");
                    return;
                }
                string arg = parts[1].ToLowerInvariant();
                if (arg == "leave" || arg == "quit" || arg == "exit" || arg == "disband") { SquadLeave(p); return; }

                var tgt = Resolve(p, Join(parts, 1, parts.Length));         // invite : !squadup <player>
                if (tgt == null) return;                                     // Resolve already messaged the caller (no/ambiguous match)
                string ts = Sid(tgt);
                if (ts == me) { TellPlayer(p, "<color=#FFC857>You can't squad up with yourself.</color>"); return; }
                _squadName[ts] = RawNameOf(tgt);

                var mySquad = SquadOf(me);
                if (mySquad != null && mySquad.Contains(ts)) { TellPlayer(p, $"<color=#FFC857>{RawNameOf(tgt)} is already in your squad.</color>"); return; }
                if (mySquad != null && mySquad.Count >= SquadMax) { TellPlayer(p, $"<color=#FFC857>Your squad is full (max {SquadMax}).</color>"); return; }
                if (InSquad(ts)) { TellPlayer(p, $"<color=#FFC857>{RawNameOf(tgt)} is already in another squad - they'd need to !squadup leave first.</color>"); return; }

                int win = SquadInviteSeconds != null ? Math.Max(15, SquadInviteSeconds.Value) : 90;
                _squadInvites[ts] = new SquadInvite { Inviter = me, InviterName = RawNameOf(p), Expiry = Time.time + win };
                TellPlayer(tgt, $"<color=#FFD200>{RawNameOf(p)} wants to squad up with you.</color> Type <color=#55FF55>!y</color> to accept ({win}s). Squadmates stay together - PvP auto-balance won't split you.");
                TellPlayer(p, $"<color=#36FFD0>Invite sent to {RawNameOf(tgt)} - they need to type !y.</color>");
                Log?.LogInfo($"[squad] {RawNameOf(p)} invited {RawNameOf(tgt)}");
            }
            catch (Exception e) { Log?.LogError("HandleSquadup: " + e); }
        }

        // !y -> accept a pending squad invite. Returns TRUE if an invite was consumed (suppress the !y);
        // FALSE if there's no live invite, so the !y flows through to the bot (map-vote approval also uses !y).
        bool TryAcceptSquad(Player p)
        {
            try
            {
                string me = Sid(p);
                if (string.IsNullOrEmpty(me) || !_squadInvites.TryGetValue(me, out var inv)) return false;   // no pending invite -> not ours
                _squadInvites.Remove(me);
                if (Time.time >= inv.Expiry) return false;                  // expired -> let !y flow to the bot
                _squadName[me] = RawNameOf(p);

                if (InSquad(me)) { TellPlayer(p, "<color=#FFC857>You're already in a squad - !squadup leave first.</color>"); return true; }
                var host = SquadOf(inv.Inviter);
                if (host != null && host.Count >= SquadMax) { TellPlayer(p, $"<color=#FFC857>Couldn't squad up - {inv.InviterName}'s squad is full.</color>"); return true; }

                if (host == null)
                {
                    // Forming a BRAND-NEW squad: the inviter must still be on the server (they may have
                    // dropped during the invite window). If they've left, this is a dead invite - don't
                    // resurrect an absent, non-consenting player into a new squad.
                    if (FindPlayerBySid(inv.Inviter) == null)
                    { TellPlayer(p, $"<color=#FFC857>Couldn't squad up - {inv.InviterName} has left.</color>"); return true; }
                    host = new HashSet<string>(StringComparer.Ordinal) { inv.Inviter }; _squads.Add(host);
                }
                host.Add(me);
                SaveSquads();

                TellPlayer(p, $"<color=#36FFD0>Squadded up! You're now with {SquadMateList(host, me)}.</color> PvP auto-balance won't split you. (<color=#55FF55>!squadup leave</color> to exit.)");
                var hostP = FindPlayerBySid(inv.Inviter);
                if (hostP != null) TellPlayer(hostP, $"<color=#36FFD0>{RawNameOf(p)} accepted - your squad is now {SquadMateList(host, inv.Inviter)} + you.</color>");
                Log?.LogInfo($"[squad] {RawNameOf(p)} accepted {inv.InviterName} -> squad of {host.Count}");
                return true;
            }
            catch (Exception e) { Log?.LogError("TryAcceptSquad: " + e); return true; }
        }

        void SquadLeave(Player p)
        {
            try
            {
                string me = Sid(p);
                var sq = SquadOf(me);
                if (sq == null) { TellPlayer(p, "<color=#FFC857>You're not in a squad.</color>"); return; }
                sq.Remove(me);
                TellPlayer(p, "<color=#36FFD0>You've left your squad.</color>");
                if (sq.Count <= 1)                                           // a lone survivor isn't a squad -> dissolve it
                {
                    foreach (var s in sq) { var lp = FindPlayerBySid(s); if (lp != null) TellPlayer(lp, "<color=#FFC857>Your squad disbanded (everyone else left).</color>"); }
                    _squads.Remove(sq);
                }
                else
                    foreach (var s in sq) { var lp = FindPlayerBySid(s); if (lp != null) TellPlayer(lp, $"<color=#9fd6b0>{RawNameOf(p)} left the squad (still squadded: {SquadMateList(sq, s)} + you).</color>"); }
                SaveSquads();
                Log?.LogInfo($"[squad] {RawNameOf(p)} left; remaining {sq.Count}");
            }
            catch (Exception e) { Log?.LogError("SquadLeave: " + e); }
        }

        // Performs at most one move; returns moves done. force=true ignores the debounce
        // (used by !balance). Picks the not-already-moved player whose rank/skill weight best evens
        // the totals, then moves them to SPECTATE - the join guard funnels them to the smaller side on
        // rejoin (clean client UI; a direct force-move to a team does NOT work reliably - stale spawn menu).
        internal static int BalanceOnce(bool force)
        {
            if (!TwoSides(out var A, out var B)) return 0;
            if (A.preventJoin || B.preventJoin) return 0;                 // PvP only (co-op AI side blocks)
            var pa = Side(A); var pb = Side(B);
            int max = BalanceMaxDiff != null ? BalanceMaxDiff.Value : 2;
            if (Math.Abs(pa.Count - pb.Count) <= max) return 0;
            float now = Time.time;
            if (!force && now - _lastMove < Mathf.Max(2, MoveDebounce != null ? MoveDebounce.Value : 20)) return 0;

            FactionHQ big   = pa.Count > pb.Count ? A : B;
            FactionHQ small = big == A ? B : A;
            var bigPlayers   = big == A ? pa : pb;
            var smallPlayers = small == A ? pa : pb;

            float sumBig = 0f, sumSmall = 0f;
            foreach (var p in bigPlayers) sumBig += Weight(p);
            foreach (var p in smallPlayers) sumSmall += Weight(p);
            float target = (sumBig - sumSmall) / 2f;                      // ideal weight of the player to move

            // Eligible = anyone on the big side NOT move-exempt (i.e. not auto-balanced within the last
            // MoveExemptGames games). Flying players ARE eligible (they get a 10s warning + eject), so the
            // balancer keeps working mid-match when everyone's airborne, and naturally falls through to the
            // next-best pick when the ideal one is exempt. (MoveOnlyUnspawned unused - flying -> the warning.)
            var movable = new List<Player>();
            foreach (var p in bigPlayers)
                if (!MoveExempt(Sid(p)))
                    movable.Add(p);
            if (movable.Count == 0) return 0;                             // everyone on the big side is move-exempt -> wait

            // Protection tiers (move the LEAST-protected first): 0 = unprotected, 1 = squadded (weak),
            // 2 = new joiner <NewJoinerSeconds (strongest). Pick the LOWEST non-empty tier, so a new
            // joiner is only moved when every other non-exempt option is also a new joiner, and a squad
            // member only when no unprotected non-exempt option exists. (See the NEW-JOINER + SQUADS
            // region.) Then choose, within that tier, whoever evens the teams' total skill best.
            int minTier = int.MaxValue;
            foreach (var p in movable) { int t = ProtTier(Sid(p)); if (t < minTier) minTier = t; }
            var pool = new List<Player>();
            foreach (var p in movable) if (ProtTier(Sid(p)) == minTier) pool.Add(p);

            Player pick = pool[0];
            float best = Math.Abs(Weight(pick) - target);
            foreach (var p in pool) { float d = Math.Abs(Weight(p) - target); if (d < best) { best = d; pick = p; } }

            // Reserve the slot NOW (debounce + mark moved) so a 2nd player isn't scheduled during the
            // 10s warning window, then move to SPECTATE: flying -> 10s warning + eject, unspawned ->
            // immediate. The join guard funnels them to the smaller side on rejoin. (We deliberately do
            // NOT force-move straight to a team - that leaves a stale spawn menu and doesn't work.)
            _lastMove = now;
            _movedAtGame[Sid(pick)] = _gameNum;        // exempt this player from another move for MoveExemptGames games
            string tn = small.faction != null ? small.faction.factionName : "the smaller team";
            // Move the picked player STRAIGHT to the smaller side via the forceteamswap mechanic (team swap +
            // landed Cricket spawn + eject -> their UI resets to the new team), instead of sending them to
            // spectate to rejoin. BeginSwap recomputes dest = the side that is NOT theirs = the smaller side
            // here. admin=null (no admin-chat; BeginSwap notifies the moved player). Keeps points + skill-life.
            Instance?.BeginSwap(pick, null, true);
            Log?.LogInfo($"[balance] picked {RawNameOf(pick)} (tier {minTier} [0=open,1=squad,2=newjoiner], weight {Weight(pick):0.0}/target {target:0.0}) -> force-swap to {tn}; flying={IsFlying(pick)}");
            return 1;
        }

        static string Join(string[] a, int start, int end)
        {
            var sb = new StringBuilder();
            for (int i = start; i < end && i < a.Length; i++) { if (sb.Length > 0) sb.Append(' '); sb.Append(a[i]); }
            return sb.ToString();
        }

        // ============ ADMIN TEST: !swapteam / !forceteamswap (move team, keep points+life) ============
        // Two competing implementations of "move a player to the other team and reset their client spawn-menu
        // UI to the new faction, WITHOUT them losing points or their open skill-life". The trick (verified):
        // Spawner.SpawnAircraft(... spawningHangar=null, destHQ, explicit GlobalPosition ...) is a [Server]
        // method we can call directly; Aircraft.OnStartServer auto-binds the player and the owning client's
        // OnStartClient teleports its local plane there + attaches the HUD + DynamicMap.SetFaction (the UI
        // reset), then we AdminEject so they drop back to the now-correct spawn menu. Every eject is
        // GuardEject-protected so it's life- and killfeed-neutral.
        //   !swapteam     : spectate -> wait despawn -> swap team -> spawn Cricket -> wait ~2s -> eject.
        //   !forceteamswap: swap team -> wait ~1s -> spawn Cricket -> wait ~2s -> eject (no initial spectate).
        // Cricket spawns HIGH over OPEN OCEAN in a quiet corner of the current map (far from every base and the
        // fight), so the brief un-piloted moment + auto-eject can never crash into terrain, a base, or another
        // plane. One ocean corner per map (verified open water via the terrain atlas).
        struct SpawnXZ { public float x, z; public SpawnXZ(float x, float z) { this.x = x; this.z = z; } }
        static readonly SpawnXZ HEART_OCEAN = new SpawnXZ(-33000f, -40000f);   // Heartland SW open ocean (nearest base ~27km)
        static readonly SpawnXZ IGNUS_OCEAN = new SpawnXZ(  8000f, -33000f);   // Ignus deep-south open ocean (nearest base ~35km)

        // Parse an "x,z" drop-point config value; malformed -> the per-map ocean fallback.
        static SpawnXZ ParseXZ(ConfigEntry<string> e, SpawnXZ fb)
        {
            try
            {
                var s = e != null ? (e.Value ?? "") : "";
                var parts = s.Split(',');
                if (parts.Length == 2
                    && float.TryParse(parts[0].Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out float x)
                    && float.TryParse(parts[1].Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out float z)
                    && !float.IsNaN(x) && !float.IsInfinity(x) && !float.IsNaN(z) && !float.IsInfinity(z))
                    return new SpawnXZ(x, z);   // finite-only: a pasted NaN/Infinity falls back, never a NaN spawn
            }
            catch { }
            return fb;
        }

        // Faction-safe drop point for the CURRENT map + the DESTINATION team: a swapped/sky-dropped player
        // spawns over their own side (Heartland: PALA grid D7 north / BDF grid J7 south; Ignus: PALA far
        // west / BDF far east - matches where each side's bases are). Unknown faction -> the old neutral
        // open-ocean corner, so odd missions never spawn anyone somewhere worse than before.
        static SpawnXZ FactionDropPos(FactionHQ hq)
        {
            bool ignus = DetectIgnus();
            string fac = "";
            try { fac = (hq != null && hq.faction != null) ? (hq.faction.factionName ?? "") : ""; } catch { }
            fac = fac.ToLowerInvariant();
            if (fac.Contains("primeva")) return ignus ? ParseXZ(SkyDropIgnusPala, IGNUS_OCEAN) : ParseXZ(SkyDropHeartlandPala, HEART_OCEAN);
            if (fac.Contains("boscali")) return ignus ? ParseXZ(SkyDropIgnusBdf, IGNUS_OCEAN) : ParseXZ(SkyDropHeartlandBdf, HEART_OCEAN);
            return ignus ? IGNUS_OCEAN : HEART_OCEAN;
        }

        static AircraftDefinition _cricketDef;
        static bool _cricketCatalogLogged;
        static AircraftDefinition ResolveCricket()
        {
            if (_cricketDef != null) return _cricketDef;
            try
            {
                var list = Encyclopedia.i != null ? Encyclopedia.i.aircraft : null;
                if (list != null)
                    foreach (var d in list)
                    {
                        if (d == null) continue;
                        string un = d.unitName ?? "", co = d.code ?? "";
                        if (un.IndexOf("Cricket", StringComparison.OrdinalIgnoreCase) >= 0
                         || co.IndexOf("CI-22", StringComparison.OrdinalIgnoreCase) >= 0
                         || co.Replace("-", "").IndexOf("CI22", StringComparison.OrdinalIgnoreCase) >= 0)
                        { _cricketDef = d; break; }
                    }
                if (_cricketDef != null) Log?.LogInfo($"[swap] Cricket resolved: '{_cricketDef.unitName}' (code {_cricketDef.code})");
                else if (!_cricketCatalogLogged && list != null)        // dump the catalog ONCE so we can find the real name
                {
                    _cricketCatalogLogged = true;
                    var sb = new StringBuilder("[swap] CI-22 Cricket not found. aircraft catalog: ");
                    foreach (var d in list) if (d != null) sb.Append(d.unitName).Append('|').Append(d.code).Append("  ");
                    Log?.LogWarning(sb.ToString());
                }
            }
            catch (Exception e) { Log?.LogError("ResolveCricket: " + e); }
            return _cricketDef;
        }

        // Map = Heartland vs Ignus, from the mission name (mirrors the bot's cc_web mapping:
        // Escalation => Heartland, Terminal Control => Ignus). Default Heartland when unknown.
        static bool DetectIgnus()
        {
            try
            {
                string n = null;
                try { n = MissionManager.CurrentMission != null ? MissionManager.CurrentMission.Name : null; } catch { }
                if (string.IsNullOrEmpty(n)) return false;
                n = n.ToLowerInvariant();
                return n.Contains("terminal") || n.Contains("ignus") || n.Contains("carrier duel");   // Carrier Duel runs on Ignus too (mirrors cc_web)
            }
            catch { return false; }
        }

        static GlobalPosition SwapPos(FactionHQ destHQ)
        {
            SpawnXZ c = FactionDropPos(destHQ);                          // over the DESTINATION team's own side
            float alt = SwapAltitude != null ? SwapAltitude.Value : 3000f;   // high up -> nothing to crash into
            return new GlobalPosition(c.x, alt, c.z);
        }

        // Spawn player p into a CI-22 Cricket HIGH over open ocean in a quiet corner of the map. A couple seconds
        // later AdminEject runs -> the client UI resets to the new team. The airborne eject is kept life-/points-
        // neutral by the _adminEjectGuard (no death, no "went down", no lost streak), and being far out over the
        // sea means the brief un-piloted plane can never hit terrain, a base, or another aircraft. Returns the
        // Aircraft, or null on failure.
        static Aircraft SpawnCricket(Player p, FactionHQ destHQ)
        {
            try
            {
                if (p == null) return null;
                var def = ResolveCricket();
                if (def == null || def.unitPrefab == null) { Log?.LogError("[swap] no Cricket prefab"); return null; }
                var spawner = NetworkSceneSingleton<Spawner>.i;
                if (spawner == null) { Log?.LogError("[swap] no Spawner singleton yet"); return null; }
                GuardEject(Sid(p));                                          // airborne eject -> the guard keeps it life/points-neutral
                var gpos = SwapPos(destHQ);                                  // high over the destination team's side of this map
                var ac = spawner.SpawnAircraft(p, def.unitPrefab, default(Loadout), 1f, default(LiveryKey),
                             gpos, Quaternion.identity, Vector3.zero, null /*spawningHangar -> airborne*/, destHQ,
                             null /*uniqueName*/, 1f /*skill*/, 0.5f /*bravery*/);
                Log?.LogInfo($"[swap] spawned Cricket for {RawNameOf(p)} @ ({gpos.x:0},{gpos.y:0},{gpos.z:0}) over-ocean on {(destHQ != null && destHQ.faction != null ? destHQ.faction.factionName : "?")}");
                return ac;
            }
            catch (Exception e) { Log?.LogError("SpawnCricket: " + e); return null; }
        }

        // ---- step scheduler (parallel to _pendingMoves, pumped 1Hz from PollCommands) ----
        enum SwapPhase { Eject0, WaitDespawn, MoveToDest, Spawn, WaitThenEject, Done }
        sealed class SwapJob { public Player p, admin; public FactionHQ destHQ; public bool force; public bool sky; public SwapPhase phase; public float due, deadline; }
        static readonly List<SwapJob> _swaps = new List<SwapJob>();

        // PUBLIC: a player moves THEMSELVES to the other team via bare !swapteam, allowed ONLY when that team
        // has FEWER players (so it can never make PvP more lopsided). PvE is excluded automatically (TwoSides
        // is false with only one joinable faction). Keeps their points + life. Admin !swapteam <player> /
        // !forceteamswap (no balance check) stay separate, below the admin gate.
        internal void HandlePublicSwap(Player p)
        {
            try
            {
                if (!TwoSides(out var A, out var B)) { TellPlayer(p, "<color=#FFC857>!swapteam only works in a PvP match with two teams.</color>"); return; }
                FactionHQ mine = null; try { mine = p.HQ; } catch { }
                if (mine == null || (!ReferenceEquals(mine, A) && !ReferenceEquals(mine, B)))
                { TellPlayer(p, "<color=#FFC857>Pick a team first, then type !swapteam to switch to the smaller side.</color>"); return; }
                FactionHQ other = ReferenceEquals(mine, A) ? B : A;
                int mineN = Side(mine).Count, otherN = Side(other).Count;
                string on = other.faction != null ? other.faction.factionName : "the other team";
                if (otherN >= mineN)
                {
                    TellPlayer(p, $"<color=#FFC857>Can't swap to {on} - it isn't smaller ({otherN} vs your {mineN}). !swapteam only lets you move to the team with FEWER players, to keep it fair.</color>");
                    return;
                }
                BeginSwap(p, p, false);   // self-swap (swapteam mechanic): spectate -> swap -> brief Cricket -> eject; keeps points + life
            }
            catch (Exception e) { Log?.LogError("HandlePublicSwap: " + e); }
        }

        internal void BeginSwap(Player tgt, Player admin, bool force)
        {
            try
            {
                if (tgt == null) return;
                if (!TwoSides(out var A, out var B)) { TellPlayer(admin, "<color=#FFC857>Swap needs a PvP match with two joinable teams.</color>"); return; }
                FactionHQ orig = null; try { orig = tgt.HQ; } catch { }
                FactionHQ dest = ReferenceEquals(orig, A) ? B : A;       // the side that is NOT theirs
                if (dest == null || ReferenceEquals(dest, orig)) { TellPlayer(admin, "<color=#FFC857>Couldn't pick an other team to swap to.</color>"); return; }
                if (ResolveCricket() == null) { TellPlayer(admin, "<color=#FF5555>Can't swap: CI-22 Cricket not found in the aircraft catalog (see log).</color>"); return; }
                _swaps.RemoveAll(j => j.p == tgt);                       // collapse repeats / restart cleanly
                float now = Time.time;
                GuardEject(Sid(tgt));
                var job = new SwapJob { p = tgt, admin = admin, destHQ = dest, force = force };
                if (force)
                {
                    if (IsFlying(tgt)) AdminEject(tgt);                  // keep the spawn-replace auto-eject life-neutral
                    job.phase = SwapPhase.MoveToDest; job.due = now + 1f;
                }
                else { job.phase = SwapPhase.Eject0; job.due = now; }
                _swaps.Add(job);
                string df = dest.faction != null ? dest.faction.factionName : "the other team";
                if (admin != null) TellPlayer(admin, $"<color=#36FFD0>{(force ? "forceteamswap" : "swapteam")} {RawNameOf(tgt)} -> {df} started…</color>");
                else TellPlayer(tgt, $"<color=#FFC857>You're being moved to {df} to balance the teams - you keep your points and progress.</color>");
                Log?.LogInfo($"[swap] begin {(force ? "force " : "")}{RawNameOf(tgt)} -> {df}{(admin == null ? " [autobalance]" : "")}");
            }
            catch (Exception e) { Log?.LogError("BeginSwap: " + e); }
        }

        internal static void PumpSwaps(float now)
        {
            PumpSkyRefunds(now);                                         // !skyswap: deferred loadout-cost refund pump
            for (int i = _swaps.Count - 1; i >= 0; i--)
            {
                var j = _swaps[i];
                if (j.p == null) { _swaps.RemoveAt(i); continue; }
                if (now < j.due) continue;
                try
                {
                    switch (j.phase)
                    {
                        case SwapPhase.Eject0:
                            AdminEject(j.p); Instance?.MovePlayer(j.p, null);            // life-neutral eject -> spectate
                            j.phase = SwapPhase.WaitDespawn; j.due = now + 1f; j.deadline = now + 5f; break;
                        case SwapPhase.WaitDespawn:
                            bool gone = false; try { gone = j.p.Aircraft == null; } catch { gone = true; }
                            if (gone || now >= j.deadline) { j.phase = SwapPhase.MoveToDest; j.due = now; }
                            else j.due = now + 1f; break;
                        case SwapPhase.MoveToDest:
                            Instance?.MovePlayer(j.p, j.destHQ);                          // server-side faction flip (UI not reset yet)
                            j.phase = SwapPhase.Spawn; j.due = now + 0.2f; break;
                        case SwapPhase.Spawn:
                            if (j.sky)   // !skyswap: drop into an armed jet high up and LEAVE them flying it (no eject)
                            {
                                var sac = SpawnSky(j.p, j.destHQ);
                                if (sac == null) Instance?.TellPlayer(j.admin, "<color=#FF5555>skyswap failed: couldn't spawn the jet (see log).</color>");
                                else Instance?.TellPlayer(j.admin, $"<color=#36FFD0>skyswap: {RawNameOf(j.p)} is airborne. Enjoy.</color>");
                                j.phase = SwapPhase.Done; break;
                            }
                            var ac = SpawnCricket(j.p, j.destHQ);
                            if (ac == null) { Instance?.TellPlayer(j.admin, "<color=#FF5555>Swap failed: couldn't spawn the Cricket (see log).</color>"); j.phase = SwapPhase.Done; }
                            else { j.phase = SwapPhase.WaitThenEject; j.due = now + 2f; } break;
                        case SwapPhase.WaitThenEject:
                            AdminEject(j.p);                                              // eject -> client drops to the NEW team's spawn menu
                            string df = j.destHQ != null && j.destHQ.faction != null ? j.destHQ.faction.factionName : "the new team";
                            Instance?.TellPlayer(j.admin, $"<color=#36FFD0>Swap complete: {RawNameOf(j.p)} is now on {df}.</color>");
                            j.phase = SwapPhase.Done; break;
                    }
                    if (j.phase == SwapPhase.Done) _swaps.RemoveAt(i);
                }
                catch (Exception e) { Log?.LogError("PumpSwaps: " + e); _swaps.RemoveAt(i); }
            }
        }

        // ============ ADMIN: !skyswap (drop the target into a fully-armed KR-67 Ifrit high in the sky) ============
        // Works in ANY mode: PvP moves the target to the other joinable team first, PvE moves them to the AI
        // (preventJoin) side; if there is no enemy destination they stay put and just get the jet. Ported verbatim
        // from 0.9.46. The airborne spawn is life/points-neutral (GuardEject) and the loadout cost is refunded.
        static AircraftDefinition _skyDef; static string _skyDefKey; static bool _skyCatalogLogged;
        static AircraftDefinition ResolveSky()
        {
            string key = SkyAircraft != null ? (SkyAircraft.Value ?? "").Trim() : "Ifrit";
            if (key.Length == 0) key = "Ifrit";
            if (_skyDef != null && string.Equals(_skyDefKey, key, StringComparison.OrdinalIgnoreCase)) return _skyDef;
            try
            {
                var list = Encyclopedia.i != null ? Encyclopedia.i.aircraft : null;
                if (list != null)
                    foreach (var d in list)
                    {
                        if (d == null) continue;
                        string un = d.unitName ?? "", co = d.code ?? "";
                        if (un.IndexOf(key, StringComparison.OrdinalIgnoreCase) >= 0
                         || co.IndexOf(key, StringComparison.OrdinalIgnoreCase) >= 0)
                        { _skyDef = d; _skyDefKey = key; break; }
                    }
                if (_skyDef != null) Log?.LogInfo($"[skyswap] '{key}' resolved: '{_skyDef.unitName}' (code {_skyDef.code})");
                else if (!_skyCatalogLogged && list != null)
                {
                    _skyCatalogLogged = true;
                    var sb = new StringBuilder($"[skyswap] '{key}' not found. aircraft catalog: ");
                    foreach (var d in list) if (d != null) sb.Append(d.unitName).Append('|').Append(d.code).Append("  ");
                    Log?.LogWarning(sb.ToString());
                }
            }
            catch (Exception e) { Log?.LogError("ResolveSky: " + e); }
            return _skyDef;
        }

        static WeaponManager PrefabWeaponManager(AircraftDefinition def)
        {
            try
            {
                if (def == null || def.unitPrefab == null) return null;
                var ac = def.unitPrefab.GetComponent<Aircraft>();
                return ac != null ? ac.weaponManager : null;
            }
            catch { return null; }
        }

        static bool StationOffers(HardpointSet hs, string want, out WeaponMount mount)
        {
            mount = null;
            if (hs == null || hs.weaponOptions == null || string.IsNullOrEmpty(want)) return false;
            foreach (var m in hs.weaponOptions)
            {
                if (m == null) continue;
                string wn = (m.info != null ? m.info.weaponName : null) ?? m.mountName ?? m.jsonKey ?? "";
                if (wn.IndexOf(want, StringComparison.OrdinalIgnoreCase) >= 0) { mount = m; return true; }
            }
            return false;
        }

        static Loadout BuildSkyLoadout(AircraftDefinition def)
        {
            try
            {
                var wm = PrefabWeaponManager(def);
                if (wm == null || wm.hardpointSets == null || wm.hardpointSets.Length == 0) return null;
                int n = wm.hardpointSets.Length;

                var weapons = new List<WeaponMount>(n);
                List<WeaponMount> baseW = null;
                try
                {
                    var lo = def.aircraftParameters != null ? def.aircraftParameters.loadouts : null;
                    if (lo != null && lo.Count > 1 && lo[1] != null) baseW = lo[1].weapons;
                    else if (lo != null && lo.Count > 0 && lo[0] != null) baseW = lo[0].weapons;
                }
                catch { }
                for (int i = 0; i < n; i++) weapons.Add(baseW != null && i < baseW.Count ? baseW[i] : null);

                string prim = SkyPrimaryWeapon != null ? (SkyPrimaryWeapon.Value ?? "").Trim() : "Scimitar";
                string sec  = SkySecondaryWeapon != null ? (SkySecondaryWeapon.Value ?? "").Trim() : "Scythe";
                int secWant = SkySecondaryStations != null ? Mathf.Max(0, SkySecondaryStations.Value) : 1;

                var secStations = new List<int>();
                if (sec.Length > 0)
                    for (int i = 0; i < n; i++) if (StationOffers(wm.hardpointSets[i], sec, out _)) secStations.Add(i);
                var secChosen = new HashSet<int>();
                for (int k = secStations.Count - 1; k >= 0 && secChosen.Count < secWant; k--) secChosen.Add(secStations[k]);

                var log = new StringBuilder("[skyswap] loadout: ");
                for (int i = 0; i < n; i++)
                {
                    WeaponMount m = null;
                    if (secChosen.Contains(i) && StationOffers(wm.hardpointSets[i], sec, out m)) weapons[i] = m;
                    else if (prim.Length > 0 && StationOffers(wm.hardpointSets[i], prim, out m)) weapons[i] = m;
                    var cur = weapons[i];
                    log.Append('[').Append(i).Append(']')
                       .Append(cur != null ? (cur.info != null && !string.IsNullOrEmpty(cur.info.weaponName) ? cur.info.weaponName : (cur.mountName ?? cur.jsonKey ?? "?")) : "-")
                       .Append(' ');
                }
                Log?.LogInfo(log.ToString());
                return new Loadout { weapons = weapons };
            }
            catch (Exception e) { Log?.LogError("BuildSkyLoadout: " + e); return null; }
        }

        static Aircraft SpawnSky(Player p, FactionHQ hq)
        {
            try
            {
                if (p == null) return null;
                var def = ResolveSky();
                if (def == null || def.unitPrefab == null) { Log?.LogError("[skyswap] no aircraft prefab"); return null; }
                var spawner = NetworkSceneSingleton<Spawner>.i;
                if (spawner == null) { Log?.LogError("[skyswap] no Spawner singleton"); return null; }
                float alt   = SkyAltitude != null ? SkyAltitude.Value : 12000f;
                float speed = SkySpeed != null ? Mathf.Max(0f, SkySpeed.Value) : 180f;
                SpawnXZ c = FactionDropPos(hq);                              // over the destination team's own side, high up
                var gpos = new GlobalPosition(c.x, alt, c.z);
                Vector3 toCentre = new Vector3(-c.x, 0f, -c.z);              // face the map centre so they fly INTO the map,
                var rot = toCentre.sqrMagnitude > 1f                         // never off the edge (identity at exact centre)
                    ? Quaternion.LookRotation(toCentre.normalized, Vector3.up) : Quaternion.identity;
                Vector3 vel = rot * Vector3.forward * speed;                 // forward launch so it doesn't stall
                Loadout loadout = BuildSkyLoadout(def);                      // null -> default weapons (fail-safe)
                GuardEject(Sid(p));                                          // any aircraft-replace stays life/points-neutral
                var ac = spawner.SpawnAircraft(p, def.unitPrefab, loadout, 1f, default(LiveryKey),
                             gpos, rot, vel, null /*airborne*/, hq, null, 1f, 0.5f);
                if (ac == null) { Log?.LogError("[skyswap] SpawnAircraft returned null"); return null; }
                _skyRefunds.Add(new SkyRefund { p = p, ac = ac, due = Time.time + 0.5f, tries = 0 });
                Log?.LogInfo($"[skyswap] spawned '{def.unitName}' for {RawNameOf(p)} @ {alt:0}m on {(hq != null && hq.faction != null ? hq.faction.factionName : "?")}");
                return ac;
            }
            catch (Exception e) { Log?.LogError("SpawnSky: " + e); return null; }
        }

        internal void HandleSkySwap(Player admin, Player tgt)
        {
            try
            {
                if (tgt == null) tgt = admin;
                if (tgt == null) return;
                if (ResolveSky() == null) { TellPlayer(admin, "<color=#FF5555>skyswap: aircraft not found in the catalog (see log).</color>"); return; }
                FactionHQ orig = null; try { orig = tgt.HQ; } catch { }
                FactionHQ dest = null;
                if (TwoSides(out var A, out var B)) dest = ReferenceEquals(orig, A) ? B : A;   // PvP: the other joinable team
                else dest = AiHQ();                                                            // PvE: the AI (preventJoin) side
                _swaps.RemoveAll(j => j.p == tgt);
                if (dest != null && !ReferenceEquals(dest, orig))
                {
                    GuardEject(Sid(tgt));
                    _swaps.Add(new SwapJob { p = tgt, admin = admin, destHQ = dest, force = true, sky = true,
                                             phase = SwapPhase.Eject0, due = Time.time + 0.5f });
                    string dn = dest.faction != null ? dest.faction.factionName : "the enemy side";
                    TellPlayer(admin, $"<color=#36FFD0>skyswap: moving {RawNameOf(tgt)} to {dn} and dropping them into a {(_skyDef != null ? _skyDef.unitName : "jet")} at {(SkyAltitude != null ? SkyAltitude.Value : 12000f):0}m...</color>");
                    Log?.LogInfo($"[skyswap] queued {RawNameOf(tgt)} -> {dn} (team change + sky) by {RawNameOf(admin)}");
                    return;
                }
                FactionHQ hq = orig;
                if (hq == null) hq = FirstJoinableHQ();
                if (hq == null) { TellPlayer(admin, "<color=#FFC857>skyswap: no joinable faction to spawn on.</color>"); return; }
                if (IsFlying(tgt)) AdminEject(tgt);
                _swaps.Add(new SwapJob { p = tgt, admin = admin, destHQ = hq, force = true, sky = true,
                                         phase = SwapPhase.Spawn, due = Time.time + 0.5f });
                TellPlayer(admin, $"<color=#36FFD0>skyswap: dropping {RawNameOf(tgt)} into a {(_skyDef != null ? _skyDef.unitName : "jet")} at {(SkyAltitude != null ? SkyAltitude.Value : 12000f):0}m (no enemy side to switch to)...</color>");
                Log?.LogInfo($"[skyswap] queued {RawNameOf(tgt)} by {RawNameOf(admin)} (same-team fallback: no enemy destination)");
            }
            catch (Exception e) { Log?.LogError("HandleSkySwap: " + e); }
        }

        sealed class SkyRefund { public Player p; public Aircraft ac; public float due; public int tries; }
        static readonly List<SkyRefund> _skyRefunds = new List<SkyRefund>();
        static void PumpSkyRefunds(float now)
        {
            for (int i = _skyRefunds.Count - 1; i >= 0; i--)
            {
                var r = _skyRefunds[i];
                if (now < r.due) continue;
                bool done = false;
                try
                {
                    float cost = (r.ac != null && r.ac.weaponManager != null) ? r.ac.weaponManager.GetCurrentValue(true) : 0f;
                    if (cost > 0f && r.p != null) { r.p.AddAllocation(cost); Log?.LogInfo($"[skyswap] refunded {cost:0} loadout cost to {RawNameOf(r.p)}"); done = true; }
                }
                catch (Exception e) { Log?.LogWarning("[skyswap] refund error: " + e); done = true; }
                r.tries++; r.due = now + 0.5f;
                if (done || r.tries >= 6 || r.p == null || r.ac == null) _skyRefunds.RemoveAt(i);
            }
        }

        static FactionHQ FirstJoinableHQ()
        {
            try
            {
                foreach (var hq in UnityEngine.Object.FindObjectsOfType<FactionHQ>())
                { try { if (hq != null && hq.faction != null && !hq.preventJoin) return hq; } catch { } }
            }
            catch { }
            return null;
        }

        static FactionHQ AiHQ()
        {
            FactionHQ ai = null;
            try
            {
                foreach (var hq in UnityEngine.Object.FindObjectsOfType<FactionHQ>())
                {
                    if (hq == null || hq.faction == null) continue;
                    if (hq.preventJoin) ai = hq;                         // AI-only side
                }
            }
            catch { }
            return ai;
        }

        // ================= !forfeit : a team votes to SURRENDER (PvP only) =================
        // A player types !forfeit to start (or add to) a vote among THEIR team to end the match as a
        // loss for them / a win for the other team. Passes when a MAJORITY of the team's current players
        // have agreed. The vote stays open for a short window; a fresh vote can't START until the cooldown
        // (default 90s, measured from the previous vote's start) elapses. Keyed by faction name, reset on
        // a new mission. Forfeit = the OTHER team's HQ declares Victory (same path as a normal win).
        sealed class ForfeitVote { public readonly HashSet<string> voters = new HashSet<string>(StringComparer.Ordinal); public float startedAt; }
        static readonly Dictionary<string, ForfeitVote> _forfeitVotes = new Dictionary<string, ForfeitVote>(StringComparer.Ordinal);
        const float ForfeitWindow = 60f;            // seconds a started vote keeps collecting agreement
        internal static void ClearForfeitVotes() { _forfeitVotes.Clear(); }

        internal void HandleForfeit(Player p)
        {
            try
            {
                if (ForfeitEnabled != null && !ForfeitEnabled.Value) { TellPlayer(p, "<color=#FFC857>Forfeit is disabled.</color>"); return; }
                FactionHQ callerHQ = null; try { callerHQ = p.HQ; } catch { }
                if (callerHQ == null) { TellPlayer(p, "<color=#FFC857>Join a team first - spectators can't call a forfeit.</color>"); return; }
                if (!TwoSides(out var A, out var B)) { TellPlayer(p, "<color=#FFC857>Forfeit votes are only for PvP matches.</color>"); return; }
                FactionHQ otherHQ = (callerHQ == A) ? B : (callerHQ == B) ? A : null;
                if (otherHQ == null) { TellPlayer(p, "<color=#FFC857>Couldn't find your opposing team.</color>"); return; }
                string myFac  = callerHQ.faction != null ? callerHQ.faction.factionName : "your team";
                string foeFac = otherHQ.faction  != null ? otherHQ.faction.factionName  : "the other team";

                float now = Time.time;
                float cd  = ForfeitCooldownSeconds != null ? ForfeitCooldownSeconds.Value : 90;
                float window = Math.Min(ForfeitWindow, cd);
                _forfeitVotes.TryGetValue(myFac, out var vote);
                bool active = vote != null && (now - vote.startedAt) < window;
                bool started = false;
                if (!active)                                                 // need to START a new vote
                {
                    if (vote != null && (now - vote.startedAt) < cd)         // still cooling down
                    {
                        int left = (int)Math.Ceiling(cd - (now - vote.startedAt));
                        TellPlayer(p, $"<color=#FFC857>Forfeit vote on cooldown - try again in {left}s.</color>");
                        return;
                    }
                    vote = new ForfeitVote { startedAt = now };
                    _forfeitVotes[myFac] = vote;
                    started = true;
                }
                vote.voters.Add(Sid(p));

                // tally against the CURRENT team (someone who left no longer counts; threshold tracks live size)
                var team = Side(callerHQ);
                var teamSids = new HashSet<string>(StringComparer.Ordinal);
                foreach (var tp in team) teamSids.Add(Sid(tp));
                int yes = 0; foreach (var v in vote.voters) if (teamSids.Contains(v)) yes++;
                int need = team.Count / 2 + 1;                               // majority of the current team

                if (yes >= need)
                {
                    _forfeitVotes.Remove(myFac);
                    BroadcastAll($"<color=#FF6A6A>** {myFac} has FORFEITED the match - {foeFac} wins! **</color>");
                    Log?.LogInfo($"[forfeit] {myFac} forfeited ({yes}/{team.Count}) -> declaring {foeFac} victory");
                    ForceVictory(otherHQ);
                    return;
                }
                // not passed yet: tell the FORFEITING team only (don't tip off the enemy)
                string lead = started ? $"{RawNameOf(p)} called a FORFEIT vote. " : "";
                foreach (var tp in team)
                    TellPlayer(tp, $"<color=#FFC857>{lead}Forfeit (surrender) vote: {yes}/{need} of {myFac}. Type <color=#55FF55>!forfeit</color> to agree.</color>");
            }
            catch (Exception e) { Log?.LogError("HandleForfeit: " + e); }
        }

        // Declare `winner`'s faction the victor -> ends the match (same call the PvE timeout uses).
        static void ForceVictory(FactionHQ winner)
        {
            try
            {
                if (winner == null) return;
                if (GameManager.gameResolution != GameResolution.Ongoing) return;   // already ended -> guard
                var m = typeof(FactionHQ).GetMethod("DeclareEndGame");
                if (m == null) { Log?.LogError("[forfeit] DeclareEndGame not found"); return; }
                object victory;
                try { victory = System.Enum.Parse(m.GetParameters()[0].ParameterType, "Victory"); }
                catch (Exception e) { Log?.LogError("[forfeit] EndType parse: " + e); return; }
                m.Invoke(winner, new object[] { victory });
            }
            catch (Exception e) { Log?.LogError("ForceVictory: " + e); }
        }

        // ---- in-game chat commands ----
        // PUBLIC: !autobalance/!ab (explainer). ADMIN (SteamID in [Admin] SteamIds):
        // !move <player> <faction>, !spec [player], !join <player> <faction>, !balance.
        internal bool TryHandleChatCommand(ChatManager cm, Player p, string msg)
        {
            try
            {
                string t = (msg ?? "").TrimStart();
                if (t.Length == 0 || t[0] != '!') return false;
                var parts = t.Substring(1).Split(new[] { ' ', '\t' }, StringSplitOptions.RemoveEmptyEntries);
                if (parts.Length == 0) return false;
                string cmd = parts[0].ToLowerInvariant();

                if (cmd == "autobalance" || cmd == "ab") { Cm = cm; ExplainAutobalance(p); return true; }

                // PUBLIC: any player may call/second a forfeit (surrender) vote for their own team.
                if (cmd == "forfeit" || cmd == "ff" || cmd == "surrender") { Cm = cm; HandleForfeit(p); return true; }

                // PUBLIC: anyone may send THEMSELVES to spectate with a bare !spec / !spectate.
                if ((cmd == "spec" || cmd == "spectate") && parts.Length == 1)
                {
                    Cm = cm; RequestMove(p, null, true); return true;
                }
                // PUBLIC: a bare !swapteam moves YOU to the other team, but only if it has FEWER players (PvP
                // balance). Admin "!swapteam <player>" / !forceteamswap fall through to the admin gate below.
                if (cmd == "swapteam" && parts.Length == 1) { Cm = cm; HandlePublicSwap(p); return true; }

                // PUBLIC: !squadup - team up with friends (up to MaxSize) so PvP auto-balance won't split you.
                if (cmd == "squadup" || cmd == "squad" || cmd == "su") { Cm = cm; HandleSquadup(p, parts); return true; }
                // PUBLIC: !y accepts a PENDING squad invite only. With no live (unexpired) invite this
                // returns false so the !y flows through to the bot untouched (the map-vote approval poll
                // also tallies !y). NOTE: a player with a LIVE squad invite who types !y during a map-vote
                // approval poll spends it on the squad-accept (rare race; everyone else's votes still tally).
                if (cmd == "y" || cmd == "yes") { Cm = cm; return TryAcceptSquad(p); }

                bool ours = cmd == "move" || cmd == "team" || cmd == "join"
                         || cmd == "spec" || cmd == "spectate" || cmd == "unteam" || cmd == "balance"
                         || cmd == "setrank" || cmd == "setfunds" || cmd == "addfunds"
                         || cmd == "swapteam" || cmd == "forceteamswap" || cmd == "skyswap";
                if (!ours) return false;                                  // not ours -> normal chat
                Cm = cm;
                if (!IsAdmin(p)) { TellPlayer(p, "<color=#FF5555>You're not authorised to use that command.</color>"); return true; }

                if (cmd == "balance")
                {
                    int n = BalanceOnce(true);
                    TellPlayer(p, n > 0 ? "<color=#36FFD0>Balance pass: moved 1 player.</color>"
                                        : "<color=#FFC857>Balance pass: nothing to do (need a lopsided PvP match with someone movable).</color>");
                    return true;
                }
                if (cmd == "setrank")                                     // !setrank <player> <n> : set in-game rank
                {
                    if (parts.Length < 3) { TellPlayer(p, "<color=#FFC857>usage: !setrank <player> <number></color>"); return true; }
                    if (!int.TryParse(parts[parts.Length - 1], out int rk)) { TellPlayer(p, "<color=#FF5555>Rank must be a whole number.</color>"); return true; }
                    var tgt = Resolve(p, Join(parts, 1, parts.Length - 1));
                    if (tgt != null) { SetPlayerRank(tgt, rk); TellPlayer(p, $"<color=#36FFD0>Set {RawNameOf(tgt)}'s in-game rank to {tgt.PlayerRank}.</color>"); }
                    return true;
                }
                if (cmd == "setfunds" || cmd == "addfunds")              // !setfunds/!addfunds <player> <amount> : in-game funds
                {
                    bool add = cmd == "addfunds";
                    if (parts.Length < 3) { TellPlayer(p, $"<color=#FFC857>usage: !{cmd} <player> <amount></color>"); return true; }
                    if (!float.TryParse(parts[parts.Length - 1], NumberStyles.Float, CultureInfo.InvariantCulture, out float amt))
                    { TellPlayer(p, "<color=#FF5555>Amount must be a number.</color>"); return true; }
                    var tgt = Resolve(p, Join(parts, 1, parts.Length - 1));
                    if (tgt != null) { SetPlayerFunds(tgt, amt, add); TellPlayer(p, $"<color=#36FFD0>{(add ? "Added" : "Set")} {RawNameOf(tgt)}'s funds {(add ? "by " : "to ")}{amt:0} (now {tgt.Allocation:0}).</color>"); }
                    return true;
                }
                if (cmd == "spec" || cmd == "spectate" || cmd == "unteam")
                {
                    Player tgt = parts.Length >= 2 ? Resolve(p, Join(parts, 1, parts.Length)) : p;
                    if (tgt != null) { RequestMove(tgt, null, true); if (tgt != p) TellPlayer(p, $"<color=#36FFD0>Moved {RawNameOf(tgt)} to spectate.</color>"); }
                    return true;
                }
                if (cmd == "swapteam" || cmd == "forceteamswap")          // ADMIN TEST: move team + brief Cricket spawn + eject (resets the client UI)
                {
                    if (parts.Length < 2) { TellPlayer(p, $"<color=#FFC857>usage: !{cmd} <player></color>"); return true; }
                    var tgt = Resolve(p, Join(parts, 1, parts.Length));
                    if (tgt != null) BeginSwap(tgt, p, cmd == "forceteamswap");
                    return true;
                }
                if (cmd == "skyswap")                                     // ADMIN: drop the target (or self) into an armed jet high up
                {
                    Player tgt = parts.Length >= 2 ? Resolve(p, Join(parts, 1, parts.Length)) : p;
                    if (tgt != null) HandleSkySwap(p, tgt);
                    return true;
                }
                // move / team / join :  <player> <faction>   (faction is the last token)
                if (parts.Length < 3) { TellPlayer(p, $"<color=#FFC857>usage: !{cmd} <player> <boscali|primeva></color>"); return true; }
                string facKey = parts[parts.Length - 1];
                var hq = FindFaction(facKey);
                if (hq == null) { TellPlayer(p, $"<color=#FF5555>Unknown faction '{facKey}' (use boscali / primeva).</color>"); return true; }
                var target = Resolve(p, Join(parts, 1, parts.Length - 1));
                if (target != null)
                {
                    RequestMove(target, hq, false);
                    string fn = hq.faction != null ? hq.faction.factionName : "the team";
                    TellPlayer(p, IsFlying(target)
                        ? $"<color=#36FFD0>{RawNameOf(target)} -> {fn} (airborne: 10s warning sent).</color>"
                        : $"<color=#36FFD0>Moved {RawNameOf(target)} to {fn}.</color>");
                }
                return true;
            }
            catch (Exception e) { Log?.LogError("TryHandleChatCommand: " + e); return false; }
        }

        void ExplainAutobalance(Player p)
        {
            bool on = EnforceBalance != null && EnforceBalance.Value;
            bool mv = AutoMove != null && AutoMove.Value;
            int max = BalanceMaxDiff != null ? BalanceMaxDiff.Value : 2;
            TellPlayer(p, "<color=#36FFD0>== Auto-balance (PvP only) ==</color>");
            TellPlayer(p, $"Teams are kept within {max} of each other. If you join the side that already has more players you're moved straight to spectate (no warning) - just reopen the map and join the smaller side.");
            if (mv) { int gmin = (BalanceGraceSeconds != null ? BalanceGraceSeconds.Value : 180) / 60;
                TellPlayer(p, $"When someone LEAVES and a side ends up more than {max} ahead, the server waits ~{Mathf.Max(1, gmin)} min (in case the gap fills back in), then moves ONE player from the bigger side to spectate (rejoin the smaller side) - picking whoever keeps both teams' total skill as even as possible. Airborne picks get a 10s warning first."); }
            else    TellPlayer(p, "Auto-move is currently OFF (join-blocking only).");
            TellPlayer(p, "New pilots (first ~15 min) are never moved; friends who <color=#55FF55>!squadup</color> are kept together and only moved as a last resort.");
            TellPlayer(p, $"<color=#FFC857>Co-op (PvE) is never balanced.</color>  Status: {(on ? "ON" : "OFF")}.");
        }
        // ================= end force-move / auto-balance =================

        // -------- chat reformat --------
        static void LoadRankMap()
        {
            try
            {
                var fi = new FileInfo(RankFilePath);
                if (!fi.Exists || fi.LastWriteTimeUtc.Ticks == _rankFileTicks) return;
                _rankFileTicks = fi.LastWriteTimeUtc.Ticks;
                RankMap.Clear();
                RankWeight.Clear();
                foreach (var line in File.ReadAllLines(RankFilePath))
                {
                    var parts = line.Split('|');                        // sid|ABBR|#hex[|rankIndex][|FullName]
                    if (parts.Length >= 3)
                    {
                        string sid = parts[0].Trim();
                        int w = 1;                                       // 4th field = numeric rank 1..11 (for balancing)
                        if (parts.Length >= 4) int.TryParse(parts[3].Trim(), out w);
                        string full = (parts.Length >= 5 && parts[4].Trim().Length > 0) ? parts[4].Trim() : parts[1];
                        RankMap[sid] = (parts[1], parts[2].Trim(), full);
                        RankWeight[sid] = w < 1 ? 1 : w;
                    }
                }
            }
            catch (Exception e) { Log?.LogError("LoadRankMap: " + e); }
        }

        // returns true if we rebroadcast a custom line (suppress native chat); false -> native
        internal bool FormatAndBroadcast(ChatManager cm, Player player, string message, bool allChat)
        {
            Cm = cm;                          // cache for TellPlayer (team-balance block messages)
            // RankInName mode: the rank is in the player's NAME, so let chat flow natively
            // (the game then renders "[RANK] Name: msg" AND runs its text-to-speech). The bot
            // still sees chat via the native CmdSendChatMessage log line (CHAT_RE), so we emit
            // nothing here -- emitting {"t":"chat"} too would double-log it.
            if (RankInName != null && RankInName.Value) return false;
            if (!ReformatChat.Value) return false;
            try
            {
                // Let '!'-prefixed messages pass through UNMODIFIED - that's both commands (!rank)
                // and the map votes (!1 .. !6) - so the external bot still sees them in the log and
                // can respond / tally votes. Everything else (incl. bare numbers) is ordinary chat
                // and gets reformatted. (Rerouting suppresses the original line, so we must not
                // reroute anything the bot needs to read.)
                string t = message.TrimStart();
                if (t.Length > 0 && t[0] == '!') return false;

                string id = Sid(player);
                if (_chatThrottle.TryGetValue(id, out var last) && Time.time - last < 0.4f)
                    return true;                                        // light anti-spam (we bypass server rate-limit)
                _chatThrottle[id] = Time.time;

                // Report the message to the bot so it shows in the activity feed. The
                // normal CmdSendChatMessage log line is skipped when we reroute (our
                // Prefix returns false), so without this the bot can't see reformatted
                // chat. Commands/votes (!.. / digit) aren't rerouted -> they still come
                // through the normal path, so there's no double-logging. Esc() = JSON-safe.
                Out("{\"t\":\"chat\",\"id\":\"" + id + "\",\"n\":\"" + Esc(player.PlayerName) +
                    "\",\"msg\":\"" + Esc(message) + "\",\"all\":" + (allChat ? "true" : "false") + "}");

                LoadRankMap();
                string label = "", color = "#FFFFFF";
                if (RankMap.TryGetValue(id, out var rc)) { label = rc.label; color = rc.color; }  // shorthand rank tag
                string name = SafeText(player.PlayerName);
                string msg = SafeText(message);
                string ally = allChat ? "" : "(ally) ";
                string who = string.IsNullOrEmpty(label) ? $"[{name}]" : $"[{name} - {label}]";
                // name+rank tag in the rank colour; the message itself in white.
                string outLine = $"{ally}<color={color}>{who}</color> <color=#FFFFFF>{msg}</color>";

                if (allChat) cm.RpcServerMessage(outLine, false);
                else foreach (var v in Humans())
                        if (v.HQ == player.HQ && v.Owner != null) cm.RpcTargetServerMessage(v.Owner, outLine, false);
                return true;
            }
            catch (Exception e) { Log?.LogError("FormatAndBroadcast threw: " + e); return false; }   // -> native chat
        }

        // -------- string helpers --------
        static string SafeText(string s)   // for raw-rendered server messages: strip markup + control chars
        {
            if (string.IsNullOrEmpty(s)) return "";
            var sb = new StringBuilder(s.Length);
            foreach (char c in s) sb.Append(c == '<' || c == '>' || c < 0x20 ? ' ' : c);
            return sb.ToString();
        }
        static string Num(object o) { try { return Convert.ToString(o, CultureInfo.InvariantCulture) ?? "0"; } catch { return "0"; } }
        static string Esc(string s)        // JSON string escaping
        {
            if (string.IsNullOrEmpty(s)) return "";
            var sb = new StringBuilder(s.Length + 8);
            foreach (char c in s)
            {
                if (c == '"' || c == '\\') sb.Append('\\').Append(c);
                else if (c < 0x20) sb.Append(' ');
                else sb.Append(c);
            }
            return sb.ToString();
        }

        // ---------------- profanity (racist-slur) gate ----------------
        // The in-game filter doesn't work, so we screen chat here. If ANY single token of
        // a message resolves to a racist slur, the WHOLE message is swapped for the canned
        // line below, BEFORE it broadcasts. We deliberately DO NOT touch ordinary swearing
        // (fuck/cunt/shit/crap and Aussie banter) - only racial/ethnic slurs. The list is
        // curated to be liberal on slur SPELLINGS (leetspeak, spacing, repeats, a few
        // Cyrillic/accented look-alikes are all normalised away) while avoiding collisions
        // with innocent words via two passes:
        //   * STRONG (substring, whole de-spaced message): only distinctive roots that
        //     cannot form inside innocent text - catches "fucknigger" and "n i g g e r".
        //   * FULL  (anchored, per whitespace token): the complete list - anchoring lets
        //     short roots match safely, so "coon"/"spic"/"paki"/"abo" hit but raccoon,
        //     spicy, Pakistan, about, Japan, squawk, minigame, niqab, Nigeria do NOT.
        // Deliberate exclusions (innocent bare tokens / Aussie usage): fag (=cigarette),
        // nip, mick, paddy, dink (dinky-di), cracker, slope (skiing), spook, honky, negro.
        internal const string ProfanityReplacement = "I am an idiot and need help!";

        static readonly string[] FullSlurs =
        {
            // n-word family (liberal: single-g, q-substitution, -uh/-let endings)
            "nigger","nigga","niga","niqqa","niqqer","niqa","nikka","nicca","nigguh","niglet",
            // anti-black
            "jigaboo","jiggaboo","porchmonkey","pickaninny","picaninny","golliwog","gollywog",
            "spearchucker","mooncricket","darkie","darky","coon",
            // anti-asian
            "chink","gook","zipperhead","slopehead","chingchong","jap",
            // anti-hispanic
            "wetback","beaner","spic",
            // anti-arab / south-asian / muslim
            "raghead","towelhead","cameljockey","dothead","muzzie","currymuncher","paki",
            // anti-indigenous (AU-relevant)
            "boong","abo","injun","squaw",
            // anti-jewish
            "kike","kyke",
            // roma
            "gyppo","gippo",
            // organised hate
            "kkk","siegheil","seigheil","heilhitler","gasthejews",
        };

        // Distinctive roots that are safe to match as a substring anywhere (no innocent
        // word/place-name forms them, even across word boundaries once spaces are stripped).
        static readonly string[] StrongSlurs =
        {
            "nigger","nigga","niqqa","niqqer","nigguh","niglet",
            "jigaboo","jiggaboo","porchmonkey","pickaninny","picaninny","golliwog","gollywog",
            "spearchucker","mooncricket","chingchong","cameljockey","currymuncher",
            "siegheil","seigheil","heilhitler","gasthejews",
        };

        // Innocent words that embed a strong root as a substring -> never flag these tokens.
        // (Only the n-word collides with a real English word: "snigger" = laugh slyly.)
        static readonly HashSet<string> SlurAllowlist = new HashSet<string>(StringComparer.Ordinal)
        {
            "snigger","sniggers","sniggered","sniggering","sniggeringly","sniggerer","sniggerers",
        };

        // Each root char -> "c+" so repeats (niiigger) and leet-doubled forms still match.
        static string ExpandSlur(string root)
        {
            var sb = new StringBuilder(root.Length * 2);
            foreach (char c in root) sb.Append(c).Append('+');
            return sb.ToString();
        }

        static Regex _tokenRx, _strongRx;
        static Regex TokenRx => _tokenRx ?? (_tokenRx =
            new Regex("^(?:" + string.Join("|", FullSlurs.Select(ExpandSlur)) + ")$", RegexOptions.CultureInvariant));
        static Regex StrongRx => _strongRx ?? (_strongRx =
            new Regex(string.Join("|", StrongSlurs.Select(ExpandSlur)), RegexOptions.CultureInvariant));

        // Collapse to bare lowercase a-z, mapping common leetspeak and a few Cyrillic/
        // accented look-alikes to their latin base and dropping everything else.
        static string NormalizeForSlur(string s)
        {
            if (string.IsNullOrEmpty(s)) return "";
            var sb = new StringBuilder(s.Length);
            foreach (char ch in s)
            {
                char c = char.ToLowerInvariant(ch);
                switch (c)
                {
                    case '0': c = 'o'; break;
                    case '1': case '|': case '!': c = 'i'; break;
                    case '3': c = 'e'; break;
                    case '4': case '@': c = 'a'; break;
                    case '5': case '$': c = 's'; break;
                    case '6': case '9': c = 'g'; break;   // ni66er / ni99er
                    case '7': c = 't'; break;
                    // Cyrillic homoglyphs
                    case 'а': c = 'a'; break; case 'е': case 'ё': c = 'e'; break;
                    case 'о': c = 'o'; break; case 'с': c = 'c'; break;
                    case 'р': c = 'p'; break; case 'у': c = 'y'; break;
                    case 'х': c = 'x'; break; case 'і': c = 'i'; break;
                    // accented latin
                    case 'à': case 'á': case 'â': case 'ä': case 'ã': case 'å': c = 'a'; break;
                    case 'è': case 'é': case 'ê': case 'ë': c = 'e'; break;
                    case 'ì': case 'í': case 'î': case 'ï': c = 'i'; break;
                    case 'ò': case 'ó': case 'ô': case 'ö': case 'õ': c = 'o'; break;
                    case 'ù': case 'ú': case 'û': case 'ü': c = 'u'; break;
                    case 'ñ': c = 'n'; break; case 'ç': c = 'c'; break;
                }
                if (c >= 'a' && c <= 'z') sb.Append(c);
            }
            return sb.ToString();
        }

        // Strip leading/trailing punctuation from a token so "spic!" / "(coon)" still anchor,
        // while interior leet ("sp!c") survives into NormalizeForSlur.
        static string TrimEdges(string s)
        {
            int i = 0, j = s.Length - 1;
            while (i <= j && !char.IsLetterOrDigit(s[i])) i++;
            while (j >= i && !char.IsLetterOrDigit(s[j])) j--;
            return (i > j) ? "" : s.Substring(i, j - i + 1);
        }

        internal static bool IsRacist(string raw)
        {
            try
            {
                if (ProfanityFilter != null && !ProfanityFilter.Value) return false;
                if (string.IsNullOrWhiteSpace(raw)) return false;
                var sbWhole = new StringBuilder(raw.Length);
                foreach (var tok in raw.Split((char[])null, StringSplitOptions.RemoveEmptyEntries))
                {
                    string n = NormalizeForSlur(TrimEdges(tok));
                    if (n.Length == 0) continue;
                    if (SlurAllowlist.Contains(n)) continue;            // innocent word that embeds a slur ("snigger")
                    if (n.Length >= 3 && TokenRx.IsMatch(n)) return true; // standalone slur token (anchored, full list)
                    sbWhole.Append(n);                                   // de-spaced stream (allowlisted words excluded)
                }
                string whole = sbWhole.ToString();
                return whole.Length >= 5 && StrongRx.IsMatch(whole);    // concatenated / spaced-out distinctive slurs
            }
            catch (Exception e) { Log?.LogError("IsRacist: " + e); return false; }
        }
    }

    // Authoritative winner: the winning faction's HQ declares the end. Read the result
    // by name ("Victory"/"Defeat") so we don't need the internal EndType enum.
    // Authoritative winner: the winning faction's HQ declares the end.
    [HarmonyPatch(typeof(FactionHQ), "DeclareEndGame")]
    internal static class DeclareEndGamePatch
    {
        static bool _fired;
        static void Postfix(FactionHQ __instance, object[] __args)
        {
            string end = (__args != null && __args.Length > 0 && __args[0] != null) ? __args[0].ToString() : "";
            if (!_fired) { _fired = true; NukeStatsPlugin.Log?.LogInfo("[diag] DeclareEndGame fired: " + end); }
            NukeStatsPlugin.Instance?.OnDeclareEndGame(__instance, end);
        }
    }

    // PvP team-balance: block a player from joining a side that's already too far ahead.
    // Hook the server-side faction-set handler (build-specific hash - re-derive after updates).
    // Only enforced in PvP (both sides joinable); co-op has a preventJoin AI side -> skipped.
    [HarmonyPatch(typeof(Player), "UserCode_CmdSetFaction_-1594139491")]
    internal static class BlockJoinPatch
    {
        static bool _fired;
        // Returning false here does NOT reliably stop the faction assignment (the join still takes,
        // so the old "please join the other team" message did nothing). Instead we ALLOW the join
        // and queue the player; PumpBounces (next HQTick) moves them to spectate if it left the
        // teams too lopsided, and tells them how to join the smaller side.
        static void Postfix(Player __instance)
        {
            if (!_fired) { _fired = true; NukeStatsPlugin.Log?.LogInfo("[diag] CmdSetFaction hooked (team balance)"); }
            if (__instance != null) NukeStatsPlugin.QueueBounceCheck(__instance);
        }
    }

    // Periodic snapshot driver. Our own MonoBehaviour.Update() does not tick on the
    // dedicated server, so we piggy-back the snapshot on FactionHQ.Update -- a method
    // the server calls every frame for each faction during a live mission. The shared
    // Time.time gate in MaybeSnapshot throttles all callers to one snap per interval.
    [HarmonyPatch(typeof(FactionHQ), "Update")]
    internal static class HQTickPatch
    {
        static bool _fired;
        static void Postfix()
        {
            if (!_fired) { _fired = true; NukeStatsPlugin.Log?.LogInfo("[diag] FactionHQ.Update tick hooked"); }
            NukeStatsPlugin.PeriodicTick();
        }
    }

    // On spawn, stamp the player's aircraft with a rich networked unitName
    // ("<rank>ABBR</rank> Name [Plane]") so the native kill feed shows rank + name + plane.
    [HarmonyPatch(typeof(Player), "SetAircraft")]
    internal static class AircraftLabelPatch
    {
        static bool _fired;
        static void Postfix(Player __instance)
        {
            if (!_fired) { _fired = true; NukeStatsPlugin.Log?.LogInfo("[diag] SetAircraft hooked (kill-feed labelling)"); }
            NukeStatsPlugin.Instance?.LabelAircraft(__instance);
            NukeStatsPlugin.Instance?.OnPlayerSpawned(__instance);   // eject over-stackers who spawn anyway
        }
    }

    // Player-vs-player kills: FactionHQ.ReportKillAction(killer, target, factor). We read
    // killer + target here and emit a "kill" event only for human-vs-human enemy kills.
    [HarmonyPatch(typeof(FactionHQ), "ReportKillAction")]
    internal static class KillPatch
    {
        static bool _fired;
        static void Postfix(object[] __args)
        {
            if (!_fired) { _fired = true; NukeStatsPlugin.Log?.LogInfo("[diag] ReportKillAction hooked"); }
            if (__args != null && __args.Length >= 2 && __args[0] is Player killer)
                NukeStatsPlugin.OnKill(killer, __args[1]);
        }
    }

    // Suppress the native GLOBAL kill feed (it floods with AI units). Returning false skips the
    // ClientRpc send. The personal "you killed X" display (TargetCreditMessage -> KillDisplay) is a
    // SEPARATE RPC and is unaffected. Custom streak / ship-sink callouts replace the global feed.
    [HarmonyPatch(typeof(MessageManager), "RpcKillMessage")]
    internal static class KillFeedSuppressPatch
    {
        static bool _fired;
        static bool Prefix()
        {
            if (!_fired) { _fired = true; NukeStatsPlugin.Log?.LogInfo("[diag] RpcKillMessage hooked (kill-feed suppression)"); }
            return !(NukeStatsPlugin.CustomKillFeed != null && NukeStatsPlugin.CustomKillFeed.Value);   // false = suppress
        }
    }

    // Hide the "pilot rescued/captured" feed line (spammy; user request) while the custom feed is on.
    [HarmonyPatch(typeof(MessageManager), "RpcPilotCaptureMessage")]
    internal static class PilotMsgSuppressPatch
    {
        static bool _fired;
        static bool Prefix()
        {
            if (!_fired) { _fired = true; NukeStatsPlugin.Log?.LogInfo("[diag] RpcPilotCaptureMessage hooked (rescue hidden)"); }
            return !(NukeStatsPlugin.CustomKillFeed != null && NukeStatsPlugin.CustomKillFeed.Value);   // false = suppress
        }
    }

    // NuclearSkill: a base capture gives the capturing player +CaptureBonus to their current life's score.
    [HarmonyPatch(typeof(FactionHQ), "ReportCaptureLocationAction")]
    internal static class CapturePatch
    {
        static bool _fired;
        static void Postfix(object[] __args)
        {
            if (!_fired) { _fired = true; NukeStatsPlugin.Log?.LogInfo("[diag] ReportCaptureLocationAction hooked (skill captures)"); }
            if (__args != null && __args.Length > 0 && __args[0] is Player p) NukeStatsPlugin.OnCapture(p);
        }
    }

    // Teamkill detection: every unit death runs ReportKilled; CheckTeamkill flags a friendly kill by a player.
    [HarmonyPatch(typeof(Unit), "ReportKilled")]
    internal static class TeamkillPatch
    {
        static bool _fired;
        static void Prefix(Unit __instance)
        {
            if (!_fired) { _fired = true; NukeStatsPlugin.Log?.LogInfo("[diag] ReportKilled hooked (teamkill enforcement)"); }
            NukeStatsPlugin.CheckTeamkill(__instance);
        }
    }

    // MUNITION LAUNCH TRACKING (ported from 0.9.46). damageCredit keys the FIRING unit for gun/missile/bomb/
    // shockwave alike - the munition identity exists ONLY at spawn. Spawner.SpawnMissile is [Server]-only and
    // every live missile/bomb passes through it; record (owner unit -> munition name + blastYield + time).
    // blastYield also detects nuke-scale blasts for the long collateral window. Fail-open: any reflection miss
    // leaves the old damaging-unit-name behaviour. SpawnMissile has TWO overloads - patch both explicitly.
    internal static class SpawnMissileRecord
    {
        static System.Reflection.FieldInfo _fiYield;
        static bool _fired, _yieldMissing;

        internal static void Record(Missile result, Unit owner)
        {
            try
            {
                if (!_fired) { _fired = true; NukeStatsPlugin.Log?.LogInfo("[diag] Spawner.SpawnMissile hooked (munition launch tracking)"); }
                if (result == null || owner == null) return;
                string name = null;
                try { name = result.definition != null ? result.definition.unitName : null; } catch { }
                if (string.IsNullOrEmpty(name)) return;
                float yield = 0f;
                if (!_yieldMissing)
                {
                    if (_fiYield == null)
                    {
                        _fiYield = HarmonyLib.AccessTools.Field(typeof(Missile), "blastYield");
                        if (_fiYield == null) { _yieldMissing = true; NukeStatsPlugin.Log?.LogWarning("[tk] Missile.blastYield not found - nuclear window detection off (weapon names still work)"); }
                    }
                    if (_fiYield != null)
                        try { yield = Convert.ToSingle(_fiYield.GetValue(result)); } catch { }
                }
                NukeStatsPlugin.NoteLaunch(owner.persistentID.Id, name, yield);
            }
            catch { }
        }
    }

    [HarmonyPatch(typeof(Spawner), "SpawnMissile",
        typeof(MissileDefinition), typeof(Vector3), typeof(Quaternion), typeof(Vector3), typeof(Unit), typeof(Unit))]
    internal static class SpawnMissileDefPatch
    {
        static void Postfix(Missile __result, Unit owner) => SpawnMissileRecord.Record(__result, owner);
    }

    [HarmonyPatch(typeof(Spawner), "SpawnMissile",
        typeof(GameObject), typeof(Vector3), typeof(Quaternion), typeof(Vector3), typeof(Unit), typeof(Unit))]
    internal static class SpawnMissileGoPatch
    {
        static void Postfix(Missile __result, Unit owner) => SpawnMissileRecord.Record(__result, owner);
    }

    // ANTI-EXPLOIT: suppress radar/spotting + radar-jamming score entirely.
    // FactionHQ.RewardPlayer is the sole score funnel; its 5th param RewardType distinguishes
    // the reason. RewardType (verified via ilspycmd on Assembly-CSharp.dll):
    //   None=0, Kill=1, Recon=2, Jamming=3, Supply=4, Refuel=5, Repair=6,
    //   RescuePilots=7, CapturePilots=8, CaptureLocation=9
    // Recon (radar/sensor DETECTION) is the score-explosion vector: it fires from
    // RadarLocator_OnRadarWarning / Sensor.DetectTarget on every fresh detection and
    // accumulates fast with many AI aircraft. Jamming is the analogous passive radar reward.
    // Returning false from this Prefix skips the original method body entirely, so NO
    // AddScore / AddAllocation / sortieScore / credit popup happens for these reasons.
    // Kills (1), captures (9), supply/refuel/repair/rescue (4-8) are untouched.
    // NOTE: self-destruct-weapon kills route through RewardType.Kill and are intentionally
    // NOT affected here (separate exploit, monitored only).
    [HarmonyPatch(typeof(FactionHQ), "RewardPlayer")]
    internal static class SuppressSpottingScorePatch
    {
        // consumed by RewardPlayerPatch.Postfix so suppressed rewards don't emit a score event
        [ThreadStatic] internal static bool Suppressed;
        static bool _fired;
        // bind missionType by name (Harmony matches the original's 5th parameter)
        static bool Prefix(object missionType)
        {
            int mt;
            try { mt = System.Convert.ToInt32(missionType); } catch { return true; }
            if (mt == 2 /*Recon*/ || mt == 3 /*Jamming*/)
            {
                if (!_fired) { _fired = true; NukeStatsPlugin.Log?.LogInfo("[diag] spotting/jamming score SUPPRESSED (anti-exploit)"); }
                Suppressed = true;
                return false; // skip original RewardPlayer body -> no score, no funds, no popup
            }
            return true;
        }
    }

    // Score gains: the central path appears to be FactionHQ.RewardPlayer(player, ...).
    [HarmonyPatch(typeof(FactionHQ), "RewardPlayer")]
    internal static class RewardPlayerPatch
    {
        static bool _fired;
        static void Postfix(object[] __args)
        {
            // a Prefix returning false still runs Postfixes; skip telemetry for suppressed spotting/jamming
            if (SuppressSpottingScorePatch.Suppressed) { SuppressSpottingScorePatch.Suppressed = false; return; }
            if (!_fired) { _fired = true; NukeStatsPlugin.Log?.LogInfo("[diag] RewardPlayer fired"); }
            if (__args != null && __args.Length > 0 && __args[0] is Player p) NukeStatsPlugin.EmitOne(p, "score");
        }
    }

    // NOTE: there is deliberately NO Player.AddScore patch. FactionHQ.RewardPlayer is the sole
    // funnel for in-mission score (kills, recon, supply, refuel, captures, repair, rescue all
    // call it, and it calls AddScore), so RewardPlayerPatch already covers every gain. Patching
    // AddScore as well doubled every score event in console.log -- pure noise/CPU, removed in 0.4.0.

    // Reroute player chat to a server message so we control format + colour. EXPLICIT
    // method target (build-specific hash) matching the proven Nuclei style — the earlier
    // reflection TargetMethod() patched a handle that didn't actually intercept calls.
    // Re-derive the hash after a game update. No HarmonyWrapSafe: surface any failure.
    [HarmonyPatch(typeof(ChatManager), "UserCode_CmdSendChatMessage_-456754112")]
    internal static class ChatReformatPatch
    {
        static bool Prefix(ChatManager __instance, ref string __0, bool __1, INetworkPlayer __2)
        {
            try
            {
                // Profanity gate FIRST, before any mode branching, so it applies whether we
                // reroute chat (Reformat) or let it flow natively (RankInName). Replacing __0
                // (passed by ref) means the cleaned text is what broadcasts AND what the bot
                // reads from the native CmdSendChatMessage log line -> everything stays in sync.
                if (NukeStatsPlugin.IsRacist(__0))
                    __0 = NukeStatsPlugin.ProfanityReplacement;

                string message = __0; bool allChat = __1; INetworkPlayer sender = __2;
                Player player = null;
                bool got = sender != null && sender.TryGetPlayer<Player>(out player) && player != null;
                if (!got || string.IsNullOrWhiteSpace(message)) return true;
                // Admin team commands (!move/!spec/!join/!balance) + the public !autobalance
                // explainer are handled here and suppressed (so they don't broadcast as chat).
                if (NukeStatsPlugin.Instance.TryHandleChatCommand(__instance, player, message)) return false;
                return !NukeStatsPlugin.Instance.FormatAndBroadcast(__instance, player, message, allChat);
            }
            catch (Exception e) { try { NukeStatsPlugin.Log?.LogError("chat Prefix threw: " + e); } catch { } return true; }
        }
    }

    // RANK FLOOR FIX. The game seeds a player's mission starting rank only when
    // !saveData.Rejoined; a reconnecting player (Rejoined=true) keeps their SAVED rank, which
    // is 0 if their old connection was saved before they ever ranked (e.g. dropped in faction
    // select). That stranded rejoiners at rank 0 on missions whose playerStartingRank is 2/3.
    // Fix: after ServerMissionStartPlayer runs, ensure the player is at LEAST the mission's
    // starting rank. No-op for everyone already at/above it (so legit higher ranks are kept).
    [HarmonyPatch(typeof(NetworkManagerNuclearOption), "ServerMissionStartPlayer")]
    internal static class StartingRankFloorPatch
    {
        static bool _fired;
        static object _lastMission;
        static void Postfix(Mission __0, Player __1)
        {
            if (!_fired) { _fired = true; NukeStatsPlugin.Log?.LogInfo("[diag] ServerMissionStartPlayer hooked (rank floor)"); }
            try
            {
                if (!ReferenceEquals(_lastMission, __0)) { _lastMission = __0; NukeStatsPlugin.AdvanceGame(); NukeStatsPlugin.ClearMatchTeamkills(); NukeStatsPlugin.ClearForfeitVotes(); NukeStatsPlugin.ResetCatchup(); NukeStatsPlugin.ResetRankFunds(); }  // new game -> advance balance move-exemption + reset teamkill + forfeit + catch-up + rank funds
                if (__0 == null || __1 == null || __0.missionSettings == null) return;
                int want = __0.missionSettings.playerStartingRank;
                // PvP matches (Escalation/Terminal): floor EVERY player to PvpStartingRank, on top of the
                // mission's own value (covers the built-in PvP maps we can't edit). Co-op is unaffected.
                int pvp = NukeStatsPlugin.PvpStartingRank != null ? NukeStatsPlugin.PvpStartingRank.Value : 0;
                if (pvp > want && NukeStatsPlugin.IsPvpMission(__0)) want = pvp;
                want = NukeStatsPlugin.CatchupFloor(want);   // rank catch-up: a latecomer spawns at the risen floor
                if (__1.PlayerRank < want)
                {
                    int was = __1.PlayerRank;
                    __1.SetRank(want, setScoreOffset: true);
                    NukeStatsPlugin.Log?.LogInfo($"[rankfloor] {__1.PlayerName} {was} -> {want} (mission/PvP starting-rank floor)");
                }
            }
            catch (Exception e) { NukeStatsPlugin.Log?.LogError("StartingRankFloor: " + e); }
        }
    }

    // RankInName: rewrite the player's name to "[RANK] Name" as the client first sets it, so
    // native chat (and the game's text-to-speech) shows the rank without us rerouting chat.
    [HarmonyPatch(typeof(Player), "UserCode_CmdSetPlayerName_-1114485719")]
    internal static class NameInjectPatch
    {
        static bool _fired;
        static void Prefix(Player __instance, ref string __0)
        {
            if (!_fired) { _fired = true; NukeStatsPlugin.Log?.LogInfo("[diag] CmdSetPlayerName hooked (rank-in-name)"); }
            try { NukeStatsPlugin.InjectRankIntoName(__instance, ref __0); }
            catch (Exception e) { NukeStatsPlugin.Log?.LogError("NameInject: " + e); }
        }
    }

    // FLOOD GUARD A: per-player rate limit on fleet move-orders. A runaway CmdSetDestination stream
    // (held key / macro / a client re-firing at a destroyed unit) overflows every client's reliable
    // send buffer and mass-disconnects the lobby at match start. We drop the offender's EXCESS orders
    // server-side (no kick; other players untouched). The game's own limiter is per-UNIT, so commanding
    // many ships multiplies its cap and dead-unit orders bypass it entirely -- this per-SENDER cap closes
    // that gap. sender is the 2nd param of UserCode_CmdSetDestination_1791143641 => Harmony __1.
    [HarmonyPatch(typeof(UnitCommand), "UserCode_CmdSetDestination_1791143641")]
    internal static class FleetOrderFloodPatch
    {
        static bool _fired;
        static bool Prefix(UnitCommand __instance, INetworkPlayer __1)
        {
            if (!_fired) { _fired = true; NukeStatsPlugin.Log?.LogInfo("[diag] CmdSetDestination hooked (flood guard A + command policy)"); }
            try
            {
                if (__1 == null || !__1.TryGetPlayer<Player>(out Player player) || player == null) return true;
                NukeStatsPlugin.NoteOrderAttempt(player);   // anti-grief: track per-player order rate for GriefTick
                if (!NukeStatsPlugin.AllowCommandTarget(__instance, player)) return false;   // gameplay rule: target not allowed
                return NukeStatsPlugin.AllowFleetOrder(player);   // anti-flood: per-sender rate limit (false => drop this order)
            }
            catch (Exception e) { NukeStatsPlugin.Log?.LogError("FleetOrderFlood: " + e); return true; }
        }
    }

    // FLOOD GUARD B: silently drop a ServerRpc whose target netId has no live object. The game already
    // drops these (return false) but first LOGS + pushes a client error + builds a network reader; under
    // a flood (a client re-firing at a just-destroyed unit) that storm exhausts the ByteBuffer pool and
    // overflows send buffers. We short-circuit with the SAME result, minus the amplifier. RPCs to a dead
    // netId NEVER reach the per-unit handler (they exit HandleRpc before dispatch), so Layer A cannot see
    // them -- this is the only place to catch that path. Applied MANUALLY from Awake (RpcHandler is
    // internal / HandleRpc private). HandleRpc(player, netId, ...) => netId is Harmony __1; returns bool.
    internal static class DeadNetIdDropPatch
    {
        delegate bool TryGetIdDel(uint netId, out NetworkIdentity identity);
        static object _boundHandler;
        static TryGetIdDel _tryGetId;
        static bool _fired;

        static bool Prefix(object __instance, uint __1, ref bool __result)
        {
            try
            {
                var cfg = NukeStatsPlugin.FloodDropDeadNet;
                if (cfg == null || !cfg.Value || __instance == null) return true;
                if (!ReferenceEquals(__instance, _boundHandler))     // (re)bind once per RpcHandler instance
                {
                    _boundHandler = __instance; _tryGetId = null;
                    var loc = AccessTools.Field(__instance.GetType(), "_objectLocator")?.GetValue(__instance);
                    if (loc != null)
                    {
                        var mi = AccessTools.Method(typeof(IObjectLocator), "TryGetIdentity");
                        if (mi != null) _tryGetId = (TryGetIdDel)Delegate.CreateDelegate(typeof(TryGetIdDel), loc, mi);
                    }
                }
                var del = _tryGetId;
                if (del == null) return true;                        // couldn't bind -> let the game handle it
                if (!del(__1, out _))                                 // no live object for this netId
                {
                    if (!_fired) { _fired = true; NukeStatsPlugin.Log?.LogInfo("[diag] HandleRpc dead-netId drop ACTIVE (flood guard B)"); }
                    NukeStatsPlugin.NoteDeadNetIdDrop();
                    __result = false;                                // match the game's own drop result
                    return false;                                    // skip body: no log, no SetError, no reader/pool churn
                }
            }
            catch (Exception e) { NukeStatsPlugin.Log?.LogError("DeadNetIdDrop: " + e); }
            return true;
        }
    }

    // FLOOD GUARD C: raise the per-connection reliable-send-buffer cap on the Mirage.SocketLayer.Config that
    // NetworkManagerNuclearOption.ConfigureNetwork just built + assigned to Server.PeerConfig (a reference type,
    // so the mutation sticks for the Peer/AckSystem built right after). The game caps it at 3000; a busy server's
    // transient fleet-order/RPC burst overflows that -> BufferFullException -> the whole lobby drops. We raise it
    // (default 12000 = 4x) so the burst drains instead of overflowing, and read the field back to PROVE the new
    // value. Field-or-property + reflection-only (no hard SocketLayer ref). Never LOWERS it. Fail-open everywhere.
    // Applied MANUALLY from Awake (private target method; reflective field set).
    internal static class MirageBufferRaisePatch
    {
        const string Member = "MaxReliablePacketsInSendBufferPerConnection";
        static bool _fired;

        static object GetMember(object o, string name)
        {
            if (o == null) return null;
            var p = AccessTools.Property(o.GetType(), name);
            if (p != null) return p.GetValue(o);
            var f = AccessTools.Field(o.GetType(), name);
            return f != null ? f.GetValue(o) : null;
        }

        static void Postfix(object __instance)   // __instance = NetworkManagerNuclearOption
        {
            try
            {
                var flag = NukeStatsPlugin.MirageRaiseSendBuffer;
                if (flag == null || !flag.Value || __instance == null) return;

                var server = GetMember(__instance, "Server");          // Mirage.NetworkServer (libs Mirage.dll)
                if (server == null) { NukeStatsPlugin.Log?.LogWarning("[flood] Layer C: Server null, skipped"); return; }
                var peerCfg = GetMember(server, "PeerConfig");          // Mirage.SocketLayer.Config (reference type)
                if (peerCfg == null) { NukeStatsPlugin.Log?.LogWarning("[flood] Layer C: PeerConfig null, skipped"); return; }

                var t = peerCfg.GetType();
                var fld = AccessTools.Field(t, Member);
                var prop = fld == null ? AccessTools.Property(t, Member) : null;
                if (fld == null && prop == null) { NukeStatsPlugin.Log?.LogWarning("[flood] Layer C: " + Member + " not found, skipped"); return; }

                int target = NukeStatsPlugin.MirageSendBufferLimit != null ? NukeStatsPlugin.MirageSendBufferLimit.Value : 12000;
                if (target < 3000) target = 3000;   // never go BELOW the game default

                int before = 0;
                try { before = System.Convert.ToInt32(fld != null ? fld.GetValue(peerCfg) : prop.GetValue(peerCfg)); } catch { }
                if (target <= before)
                {
                    if (!_fired) { _fired = true; NukeStatsPlugin.Log?.LogInfo($"[diag] Layer C: {Member} already {before} >= target {target}, left as-is"); }
                    return;
                }
                if (fld != null) fld.SetValue(peerCfg, target); else prop.SetValue(peerCfg, target);
                int after = 0;
                try { after = System.Convert.ToInt32(fld != null ? fld.GetValue(peerCfg) : prop.GetValue(peerCfg)); } catch { }

                if (!_fired || after != target)
                {
                    _fired = true;
                    NukeStatsPlugin.Log?.LogInfo($"[diag] Layer C ACTIVE: {Member} {before} -> {after} (target {target}, {(double)target / 3000.0:0.#}x default)");
                }
            }
            catch (Exception e) { NukeStatsPlugin.Log?.LogError("MirageBufferRaise: " + e); }
        }
    }

    // NET-HEALTH: capture the DisconnectReason for each forced drop. Mirage's PUBLIC Disconnected event
    // gives us the player but NOT the reason; the reason only exists on the PRIVATE
    // NetworkServer.Peer_OnDisconnected(IConnection, DisconnectReason) callback. We postfix it (read-only)
    // to tally per-SteamID forced-DC count + last reason for the {"t":"net"} telemetry line. Applied MANUALLY
    // from Awake (private target); fail-open -- if the method can't be resolved at load, the patch is simply
    // never installed (net telemetry still emits, lastDc just stays empty). Never throws into the netcode.
    internal static class DcReasonPatch
    {
        static bool _fired;
        static void Postfix(object __0, object __1)   // __0 = IConnection, __1 = DisconnectReason (kept loose to avoid a hard ref)
        {
            try
            {
                if (!_fired) { _fired = true; NukeStatsPlugin.Log?.LogInfo("[diag] Peer_OnDisconnected postfix ACTIVE (net-health DisconnectReason capture)"); }
                if (__0 == null) return;
                string reason = __1 != null ? __1.ToString() : "";
                string sid = NukeStatsPlugin.SidForConnection(__0);
                if (string.IsNullOrEmpty(sid)) return;     // couldn't map this IConnection to a current player
                NukeStatsPlugin.NoteForcedDc(sid, reason);
            }
            catch (Exception e) { NukeStatsPlugin.Log?.LogError("DcReason: " + e); }
        }
    }

}
