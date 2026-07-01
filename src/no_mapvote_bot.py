#!/usr/bin/env python3
"""
Nuclear Option - automated map-vote bot (mod-free).

Two channels, because the console and the remote-command port are separate:
  ACTIONS -> native TCP remote-command port (-ServerRemoteCommands <port>):
             send-chat-message, get-mission-time, set-next-mission, set-time-remaining
  VOTES   -> read player chat out of the GPanel console output and tally it

Flow (log-driven, not time-polled):
  IDLE   : tail the console; when a "[DedicatedServerManager] Mission complete"
           line appears (MISSION_END_RE) -- which also covers missions that end
           early -- post the rank roster and open the next-map vote. Players can
           also start a vote any time with !votemap.
  VOTING : read chat, record each player's choice (last vote wins). When the
           window closes: pick the winner, queue it as the next mission, cut the
           current mission short to roll over, and announce the result.

The ONLY piece you must wire to your setup is ConsoleSource.poll() -- how the bot
gets new console lines. A local-file tail is provided (good for testing or if you
can run the bot where the log lives). For remote reading over SFTP or a panel
websocket, swap poll() -- see the note on that class.

Quick check with no setup:   python no_mapvote_bot.py --selftest
Run for real:                python no_mapvote_bot.py
Command centre (unified):    commandcentre.bat  (single-window TUI: live console +
                             players table + activity feed + a command console;
                             reads the feed this bot publishes - see the
                             "command-centre dashboard feed" section below)
Command centre (legacy):     python no_mapvote_bot.py --centre   (or centre.bat)
"""

import json
import math
import os
import random
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
import traceback
from collections import Counter

# Windows std streams default to cp1252, which raises UnicodeEncodeError on player
# names with non-Latin-1 glyphs (e.g. □ U+25A1) and mis-decodes piped/pasted UTF-8
# input. A failed print would otherwise crash main(). Force UTF-8 + replacement so
# logging can never take the bot down and command-centre input decodes cleanly.
for _stream in (sys.stdout, sys.stderr, sys.stdin):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass

# ----------------------------------------------------------------------------
# CONFIG  -- adjust these
# ----------------------------------------------------------------------------

# Optional config written by the installer (~/.nuke-option-toolkit/). If a value is present
# there it wins; otherwise we fall back to the existing env var, then the default — so a
# classic run.bat (env-var) setup is completely unaffected. Set NOST_DATA_DIR to relocate.
import json as _json
_TK_DIR = os.environ.get("NOST_DATA_DIR") or os.path.join(os.path.expanduser("~"), ".nuke-option-toolkit")
def _tk_load(_name):
    try:
        with open(os.path.join(_TK_DIR, _name), encoding="utf-8") as _f:
            return _json.load(_f)
    except (OSError, ValueError):
        return {}
_TK_CFG = _tk_load("config.json")
_TK_SEC = _tk_load("secrets.json")
def _cfg(dotted, env=None, default=""):
    """ENV wins (the live run.bat setup), then config.json/secrets.json, then default — so a
    classic env-var install is NEVER overridden by a stray config file."""
    if env:
        _v = os.environ.get(env)
        if _v not in (None, ""):
            return _v
    for _src in (_TK_SEC, _TK_CFG):
        _cur = _src
        for _k in dotted.split("."):
            _cur = _cur.get(_k) if isinstance(_cur, dict) else None
        if _cur not in (None, ""):
            return _cur
    return default

RCMD_HOST = _cfg("server.rcmd_host", "NO_RCMD_HOST", "your-host.example.net")   # relay/server host
RCMD_PORT = int(_cfg("server.rcmd_port", "NO_RCMD_PORT", "5550") or 5550)

# Mission pool. Every vote offers 2 random Escalation + 2 random Terminal Control
# maps drawn from these lists (all Group "User", MaxTime 10800s per the server's
# MissionRotation). Players vote by typing the number (1-4) shown for each option.
MISSION_GROUP    = "User"
MISSION_MAX_TIME = 10800         # seconds (3h) -- matches the server's MissionRotation

ESCALATION_MISSIONS = [
    "Escalation Co-op as BDF - Afternoon",
    "Escalation Co-op as BDF - Clear Skies",
    "Escalation Co-op as BDF - Dawn",
    "Escalation Co-op as BDF - Dusk",
    "Escalation Co-op as BDF - Night",
    "Escalation Co-op as BDF - Overcast",
    "Escalation Co-op as PALA - Afternoon",
    "Escalation Co-op as PALA - Clear Skies",
    "Escalation Co-op as PALA - Dawn",
    "Escalation Co-op as PALA - Dusk",
    "Escalation Co-op as PALA - Overcast",
    "Escalation Co-op as PALA - Thunderstorm",
]
TERMINAL_CONTROL_MISSIONS = [
    "Terminal Control Co-op as BDF - Dawn",
    "Terminal Control Co-op as BDF - Day",
    "Terminal Control Co-op as BDF - Dusk",
    "Terminal Control Co-op as PALA - Dawn",
    "Terminal Control Co-op as PALA - Day",
    "Terminal Control Co-op as PALA - Dusk",
]
# Base PvP missions (group "User", verified on the server 2026-06-23). Kept SEPARATE
# from the coop lists above so the random co-op map-vote pool is unchanged, but the
# command centre's `nextmap` autocomplete/exact-match can reach them (so `nextmap
# escalation` loads the PvP "Escalation", not "Escalation Co-op as BDF ..."). The
# upcoming 30+-player PvP-only vote will draw from this list. (Bare "Terminal Control"
# does NOT exist on the server - only its co-op variants.)
PVP_MISSIONS = [
    "Escalation",
    "Terminal Control",
    "Altercation",
    "Confrontation",
    "Domination",
    "Breakout",
]

# The curated OFFICIAL mission pool this server ships (every mission in the stock MissionRotation). Any
# mission present/enabled BEYOND this set = unofficial (uploaded or Steam Workshop). The mission audit flags
# unofficial-enabled or edited-official missions so owners can see when the pool diverges from stock.
OFFICIAL_MISSIONS = set(ESCALATION_MISSIONS) | set(TERMINAL_CONTROL_MISSIONS) | set(PVP_MISSIONS)

# Weather/time variants treated as "dark". A single ballot may contain at most
# MAX_DARK_PER_VOTE of these, so at least one of the four options is always a
# brighter map (Afternoon / Clear Skies / Day / Dawn). Note: Dawn is NOT dark.
DARK_VARIANTS     = ("Night", "Thunderstorm", "Overcast", "Dusk")
MAX_DARK_PER_VOTE = 3

# Two FIXED extra options appended to every ballot (keys 5-6): the stock built-in
# PvP Escalation / Terminal Control missions (Group "BuiltIn"). These are always
# the same regular mission and are labelled with a red [PVP] tag in chat.
PVP_OPTIONS = [
    ("BuiltIn", "Escalation",       "Escalation <color=#FF5555>[PVP]</color>"),
    ("BuiltIn", "Terminal Control", "Terminal Control <color=#FF5555>[PVP]</color>"),
    ("BuiltIn", "Altercation",      "Altercation <color=#8FA9C9>· dogfight focus</color>"),
    ("BuiltIn", "Confrontation",    "Confrontation <color=#8FA9C9>· combined arms</color>"),
    ("BuiltIn", "Domination",       "Domination <color=#8FA9C9>· air superiority</color>"),
    ("BuiltIn", "Breakout",         "Breakout <color=#8FA9C9>· naval attack</color>"),
]

# Current ballot, rebuilt each vote by open_vote(). Keys "1".."6" map to
#   (group, mission_name, max_time_seconds, friendly_label)
# 1-2 = random Escalation co-op, 3-4 = random Terminal Control co-op, 5-6 = PvP.
VOTE_OPTIONS = {}

VOTE_DURATION        = 60    # how long the map-vote ballot stays open (seconds)
APPROVAL_DURATION    = 60    # !votemap yes/no poll length (seconds)
ROLLOVER_SECONDS     = 10    # cut current mission to this many seconds after a vote
POST_VOTE_COOLDOWN   = 90    # don't open another vote for this long after applying one
CONSOLE_POLL_INTERVAL = 1.5  # how often to read new console lines (SFTP-friendly)

# --- Console source: SFTP tail. Credentials come from environment variables so
# no secrets live in this file. Set them in your shell before running:
#   export NO_SFTP_HOST=your-sftp-host.example.net
#   export NO_SFTP_PORT=2022
#   export NO_SFTP_USER=your-username
#   export NO_SFTP_PASS='your-new-password'      # rotate the one you pasted!
#   export NO_SFTP_LOGPATH=/path/to/remote/console.log
SFTP_HOST     = _cfg("server.sftp_host", "NO_SFTP_HOST", "")
try:
    SFTP_PORT = int(str(_cfg("server.sftp_port", "NO_SFTP_PORT", "2022")).strip())
except ValueError:
    print("[bot] sftp port is not a number; falling back to 2022")
    SFTP_PORT = 2022
SFTP_USER     = _cfg("server.sftp_user", "NO_SFTP_USER", "")
SFTP_PASS     = _cfg("sftp_pass", "NO_SFTP_PASS", "")           # secrets.json
SFTP_LOG_PATH = _cfg("server.log_path", "NO_SFTP_LOGPATH", "")  # remote path to the console log

# Own-PC installs set a LOCAL console path; if present the bot tails it directly instead of
# over SFTP (and points commands at 127.0.0.1). Empty => classic remote/SFTP behaviour.
LOCAL_CONSOLE_PATH = _cfg("server.local_console_path", "NO_LOCAL_CONSOLE", "")
CONSOLE_LOG_PATH = LOCAL_CONSOLE_PATH or "console.log"


# get-mission-time response field that holds the seconds remaining. Leave None to
# auto-search for a key containing "remain". Run once with DEBUG=True, look at the
# printed response, and set this to the exact field name if auto-detect misses.
MISSION_TIME_KEY = None      # e.g. "remaining" or "timeLeft"

DEBUG = True                 # print raw command responses (confirm field names)

# ----------------------------------------------------------------------------
# Custom server-rank system
# ----------------------------------------------------------------------------
CAPTURE_POINTS     = 1       # points to your team for each base your side captures
WIN_POINTS         = 2       # points to each online player when your team wins
KILL_BONUS         = 50      # BONUS points to a player for downing an enemy player (PvP kill)
UNDERDOG_PER_PLAYER = 10     # EXTRA kill points per player your side is OUTNUMBERED by (PvP balance incentive)
SHOW_RANK_ON_CHAT  = False   # plugin shows [Name - Rank] inline now; no separate rank tag
RANK_CHAT_THROTTLE = 0       # min seconds between rank lines for the same player (0 = every message)
JOIN_POLL_INTERVAL = 5       # how often to refresh players + announce new joiners (seconds)
LOG_CONVERSATION   = True    # show player chat ([CHAT]) and bot replies ([BOT]) in activity.log
                             # set False for just the curated events (joins/votes/captures/wins)

# Real per-player score from the NukeStats BepInEx plugin (see NukeStats/README.md).
# The plugin emits "[NOSTATS] {json}" lines into console.log carrying each player's
# REAL in-game score. Flip this True ONLY after those lines are confirmed flowing:
# ranks then track the accumulated real score and the derived capture/win point
# awards below are switched off (wins/losses are still recorded). Until then the bot
# behaves exactly as before (the ingest stays inert with no [NOSTATS] lines present).
USE_PLUGIN_SCORE   = True
PLUGIN_RANK_PUSH_INTERVAL = 120   # how often to push the chat-rank file to the container (s)

# (points needed, full name, abbreviation, colour). Colours run a simple blue up
# through cool tones, then the top three are bronze / silver / gold medal shades.
RANKS = [
    (0,      "Officer Cadet",     "OFFCDT",  "#8FA9C9"),
    (50,     "Pilot Officer",     "PLTOFF",  "#6E97D6"),
    (200,    "Flying Officer",    "FLGOFF",  "#4C84E4"),
    (500,    "Flight Lieutenant", "FLTLT",   "#34C24A"),  # green
    (1000,   "Squadron Leader",   "SQNLDR",  "#FF8C00"),  # orange
    (2500,   "Wing Commander",    "WGCDR",   "#C9A800"),  # dark yellow
    (5000,   "Group Captain",     "GPCAPT",  "#FF3B3B"),  # red
    (10000,  "Air Commodore",     "AIRCDRE", "#B01818"),  # deep crimson
    (25000,  "Air Vice-Marshal",  "AVM",     "#CD7F32"),  # bronze
    (50000,  "Air Marshal",       "AIRMSHL", "#D2D6DB"),  # silver
    (100000, "Air Chief Marshal", "ACM",     "#FFD700"),  # gold
]
# The ladder above is the built-in DEFAULT. The live ladder (titles/thresholds/abbrs/colours +
# the rank-up announcement template) is editable from the webcc "Ranks" modal and persisted to
# rank_ladder.json; load_rank_ladder() rebuilds RANKS from it at startup (fail-open to DEFAULT).
DEFAULT_RANKS           = list(RANKS)
DEFAULT_RANKUP_TEMPLATE = "<color={color}>** RANK UP ** {name} is now {rank} ({abbr})!</color>"
RANKUP_TEMPLATE         = DEFAULT_RANKUP_TEMPLATE
RANK_LADDER_FILE        = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rank_ladder.json")
RANK_FILE    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ranks.json")
RANK_DATA    = {}            # steamid -> {"name": str, "points": int}
PLAYER_NAMES = {}            # steamid -> last-seen display name (for chat rank lines)
WELCOMED        = set()      # sids welcomed this session (cleared on leave) - dedups the join welcome
WELCOME_QUEUE   = {}         # sid -> (deadline_ts, name): delayed welcomes, dropped if they leave first
WELCOME_DELAY   = 5.0        # seconds to wait after first-seen before welcoming (let their client load)
ADMIN_SIDS      = set(os.environ.get("NO_ADMIN_SIDS", "").split()) or set(_TK_CFG.get("server", {}).get("admin_sids") or _TK_CFG.get("admin_sids") or []) or {"7656119xxxxxxxxxx"}   # NO_ADMIN_SIDS env -> config -> live default

# Per-match tracking: a match_history.json (one record per match: mission, result,
# duration, per-player points/captures/won) and an append-only points_ledger.jsonl
# (one line per point award - the audit trail). ranks.json (lifetime totals) is the
# source of truth and is unchanged; these are additive.
_BASE_DIR          = os.path.dirname(os.path.abspath(__file__))
MATCH_HISTORY_FILE = os.path.join(_BASE_DIR, "match_history.json")
LEDGER_FILE        = os.path.join(_BASE_DIR, "points_ledger.jsonl")
SKILL_LEDGER_FILE  = os.path.join(_BASE_DIR, "skill_ledger.jsonl")   # per-life audit (score, reason, counted)
SCHEDULE_FILE      = os.path.join(_BASE_DIR, "schedule.json")        # web-CC scheduled restarts/updates (this bot executes them)
SCHED_WARN         = [300, 60]    # warn players in-chat this many seconds before a scheduled restart/update
_sched_warned      = {}           # item id -> set(thresholds already announced) (in-memory; ok to forget on restart)
CUR_MATCH          = None    # active match accumulator (see match_*), None between matches
SCORE_ACCUM        = {}       # sid -> [name, total in-game score gained this match]; one ledger
                             # "score" line per player flushed at match_finalize (snaps are too
                             # frequent to ledger individually). See ledger_award / _flush_score_accum.
GAIN_CLAMP_MAX     = 1000.0   # hard upper clamp on a single snap's credited gain (defence-in-depth vs the
                             # 2026-06-24 score-explosion class): the SPIKE alert still fires on the RAW gain,
                             # but never more than this is actually banked into points/curLife in one tick.
SPIKE_THRESHOLD    = 1000.0   # a single snap gain above this is logged + flagged live (exploit tripwire,
                             # cf. the 2026-06-24 score-explosion). Informational only (pts:0 in ledger).
CURRENT_MISSION    = "(unknown)"  # name of the mission currently running (for match records)
# Mission-time warnings: announce when remaining time crosses these thresholds (once each per mission).
WARN_THRESHOLDS = [3600, 1200, 600, 300, 60]   # 60 / 20 / 10 / 5 / 1 min remaining
_warnings_fired = set()                          # thresholds already announced this mission
_warn_mission   = None                           # mission name the fired-set belongs to (reset on change)

# Start-of-match participation bonus + 'stay for the next match' reminders (keyed to mission
# elapsed time, mtime[0]). All per-mission state resets when a new mission starts (detected by
# the elapsed clock jumping back to ~0). See check_match_milestones().
START_BONUS_PTS    = 250                          # points to every player present at kickoff
START_BONUS_WINDOW = 60                           # seconds: 'within the first minute of a new mission'
STAY_MARKS         = [6300, 7500, 8700]           # 105 / 125 / 145 min ELAPSED -> 'stay for next match'
_ms_mission        = None                         # mission the milestone state belongs to
_ms_last_elapsed   = 0.0                          # previous elapsed reading (detect the reset to ~0)
_ms_cycle_at       = 0.0                          # wall-time a start-bonus cycle last opened (anti-double)
_ms_start_done     = False                        # start bonus already granted this mission (one-shot at the 1-min mark)
START_BONUS_FILE   = os.path.join(_BASE_DIR, "start_bonus_granted.json")  # persists {match_key: [sids granted]} so a mid-match bot restart never re-awards the +250
_ms_start_said     = False                        # announced the kickoff line this mission
_ms_stay_fired     = set()                        # which STAY_MARKS have fired this mission

# Real-score ingest (from the NukeStats plugin's [NOSTATS] lines). Lifetime points now
# come from the plugin's match-end AWARD events (win + placement bonuses), applied to
# ranks.json. LIVE_SCORE/STATS_META are per-match caches for the feed + W/L tally only.
LIVE_SCORE         = {}      # steamid -> latest in-match PlayerScore (display only)
STATS_META         = {}      # steamid -> {"name","faction","rank","teamkills"} (this match)
POS                = {}      # steamid -> (x, z, ts, kind): latest world pos+kind(p/h) of a FLYING player (live map; ~2s)
DOWNED             = {}      # steamid -> death ts: set on a life death/eject so the map shows them DEAD instantly (not after 6s pos-staleness); cleared when they fly again
KILLFEED           = []      # recent deaths (newest FIRST) for the webcc killfeed: {vname,vsid,vfac,kname,ksid,kfac,kp,x,z,ts,reason}; trimmed to KILLFEED_MAX
KILLFEED_MAX       = 30
_recent_kill       = {}      # victim sid -> {kname,ksid,kfac,kp,ts}: who/what downed them (from kill/down events; correlated onto the killfeed)
AIR                = None     # latest AI/player aircraft counts from the plugin's "air" line (perf panel)
AIR_TS             = 0.0      # when AIR was last updated (stale => hide the panel)
NET                = None     # latest connection-health/RTT-probe telemetry from the plugin's "net" line (Connection Stress panel)
NET_TS             = 0.0      # when NET was last updated (stale => omit from state)
ENT                = None     # latest {"a":[AI aircraft],"s":[ships]} from the plugin's "ent" line (live map; ~5s)
ENT_TS             = 0.0      # when ENT was last updated (stale => omit from state)

# webcc settings menu: live plugin config snapshot (from the plugin's [NOSTATS] {"t":"cfg"} line)
PLUGIN_CFG         = {}       # "Section.Key" -> current value, reported live by the plugin
PLUGIN_CFG_TS      = 0.0      # when PLUGIN_CFG was last refreshed
# bot-owned settings the bot reads at startup (a bot restart fully applies them). Overrides set via the
# settings menu are persisted to bot_overrides.json and re-applied here on the next start.
TICK_RATE = 60   # server engine frame/tick rate (Hz). 30-120. Applied by the launch wrapper on the next
                 # SERVER (re)start, NOT by a bot restart. The wrapper generator reads _read_tick_rate().
_BOT_OVERRIDE_KEYS = ("MISSION_MAX_TIME", "VOTE_DURATION", "APPROVAL_DURATION",
                      "KILL_BONUS", "UNDERDOG_PER_PLAYER", "START_BONUS_PTS", "START_BONUS_WINDOW",
                      "TICK_RATE")
try:
    with open(os.path.join(_BASE_DIR, "bot_overrides.json"), "r", encoding="utf-8") as _bof:
        _bo = json.load(_bof)
    for _k in _BOT_OVERRIDE_KEYS:
        if _k in _bo and isinstance(_bo[_k], (int, float)) and not isinstance(_bo[_k], bool):
            globals()[_k] = int(_bo[_k]) if float(_bo[_k]).is_integer() else _bo[_k]
except (OSError, ValueError):
    pass

# Human-readable activity feed (the "watch" screen tails this). One tidy line per
# meaningful event, so the user sees plain English instead of raw rcmd JSON.
ACTIVITY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "activity.log")


def _plain(text):
    """Strip TMP <color> tags so a chat label reads cleanly in the plain feed."""
    return re.sub(r"</?color[^>]*>", "", text)


def activity(msg, tag=""):
    """Append a timestamped, human-readable line to activity.log and echo it to the
    raw log too. `tag` (e.g. "MAP", "WIN") is padded to a fixed column so every line
    lines up in the watch window. Never raises -- logging must never crash the bot."""
    line = f"[{tag}]".ljust(8) + msg if tag else msg
    try:
        print(f"[activity] {line}")
    except Exception:        # noqa: BLE001
        pass
    try:
        with open(ACTIVITY_FILE, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%I:%M:%S %p')}  {line}\n")
    except OSError:
        pass


_COLOR_RE = re.compile(r"</?color[^>]*>")


def _strip_color(s):
    """Drop <color=..> tags so a chat line reads cleanly in the activity feed."""
    return _COLOR_RE.sub("", str(s))

# ----------------------------------------------------------------------------
# Command-centre dashboard feed. The single-window command centre
# (command_centre.py) is a separate VIEWER process; the bot publishes everything
# it needs to local files so the dashboard needs no SFTP/relay creds of its own:
#   * console_mirror.log   - every raw server-console line the bot reads, so the
#                            dashboard can show the live BepInEx/server console.
#   * dashboard_state.json - a periodic snapshot of the mission/vote header and
#                            the player table (server rank, in-game rank, plane,
#                            match points). Written atomically.
# Publishing must NEVER crash the bot -> everything here is best-effort.
# ----------------------------------------------------------------------------
CONSOLE_MIRROR_FILE  = os.path.join(_BASE_DIR, "console_mirror.log")
DASHBOARD_STATE_FILE = os.path.join(_BASE_DIR, "dashboard_state.json")
ADMIN_CMD_FILE       = os.path.join(_BASE_DIR, "admin_commands.jsonl")  # command-centre admin queue (e.g. grant points)
STATE_WRITE_INTERVAL = 1          # rewrite dashboard_state.json every 1s so the web map picks up
                                 # the plugin's position feed (~2s) promptly; the webcc interpolates
                                 # between anchors at 60fps for a smooth map regardless of this cadence.
_CONSOLE_MIRROR_MAX  = 2_000_000  # bytes; past this the mirror is trimmed to the last N lines
_MIRROR_KEEP         = 3000
ROSTER_BY_SID        = {}         # sid -> last get-player-list entry (faction for the table)


def mirror_console(line):
    """Append one raw console line to console_mirror.log (the dashboard tails it)."""
    try:
        with open(CONSOLE_MIRROR_FILE, "a", encoding="utf-8") as f:
            f.write(line.rstrip("\r\n") + "\n")
    except OSError:
        pass


def mirror_console_batch(lines):
    """Append a whole poll's worth of console lines in ONE open/write/close. The plugin
    emits [NOSTATS] snapshots many times/sec, so a single poll can carry dozens of lines;
    a per-line open+close was the costliest syscall in the poll loop on Windows. Bytes on
    disk are identical to the per-line writes. Best-effort; never affects parsing."""
    if not lines:
        return
    try:
        with open(CONSOLE_MIRROR_FILE, "a", encoding="utf-8") as f:
            f.write("".join(l.rstrip("\r\n") + "\n" for l in lines))
    except OSError:
        pass


def trim_console_mirror():
    """Keep console_mirror.log bounded so it can never grow without limit."""
    try:
        if os.path.getsize(CONSOLE_MIRROR_FILE) <= _CONSOLE_MIRROR_MAX:
            return
        with open(CONSOLE_MIRROR_FILE, "r", encoding="utf-8", errors="replace") as f:
            tail = f.readlines()[-_MIRROR_KEEP:]
        tmp = CONSOLE_MIRROR_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(tail)
        os.replace(tmp, CONSOLE_MIRROR_FILE)
    except OSError:
        pass


def write_dashboard_state(*, state, server_up, online, votes, vote_ends_at,
                          vote_context, approval, mtime):
    """Atomically write dashboard_state.json: the mission/vote header plus the
    per-player table (server rank, in-game rank, plane, match points)."""
    try:
        now = time.time()
        players = []
        for sid in online:
            rec  = RANK_DATA.get(sid, {})
            meta = STATS_META.get(sid, {})
            ros  = ROSTER_BY_SID.get(sid, {})
            pts  = player_points(sid)                          # COMBINED across the host's servers when sharing is on (display)
            _, rname, abbr, color = RANKS[rank_index_for(pts)]
            # live map: fresh pos => flying; DOWNED (just died/ejected) or stale pos => shot-down at LAST location
            _pp = POS.get(sid)
            _have = bool(_pp) and _pp[0] is not None
            if _have and sid not in DOWNED and (now - _pp[2]) < 6:
                _px, _pz, _grounded = _pp[0], _pp[1], False
            elif _have:
                _px, _pz, _grounded = _pp[0], _pp[1], True        # DOWNED => dead INSTANTLY (no 6s wait)
            else:
                _px, _pz, _grounded = None, None, False
            _kls = _pp[3] if (_have and len(_pp) > 3) else None   # "p"/"h" for the map glyph (heli vs plane)
            players.append({
                "sid":          sid,
                "name":         (ros.get("displayName") or PLAYER_NAMES.get(sid)
                                 or rec.get("name") or meta.get("name") or sid),
                "faction":      ros.get("faction") or meta.get("faction") or "",
                "aircraft":     meta.get("aircraft") or "",
                "rank_abbr":    abbr,
                "rank_name":    rname,
                "rank_color":   color,
                "points":       round(float(pts), 1),
                "ingame_rank":  meta.get("rank"),
                "match_points": round(float(LIVE_SCORE.get(sid, 0.0)), 1),
                "teamkills":    meta.get("teamkills"),
                "wins":         rec.get("wins", 0),
                "losses":       rec.get("losses", 0),
                "skill":        skill_rating(rec),          # points-per-life (None until 5 lives)
                "x":            _px,                        # world pos (last known if grounded)
                "z":            _pz,
                "grounded":     _grounded,                  # True => shot down/landed (✝ on map)
                "klass":        _kls,                       # "h" => heli glyph (+), else plane (▲)
                "fresh":        bool(meta) and (now - meta.get("t", 0) < 30),
            })
        players.sort(key=lambda p: (-p["match_points"], -p["points"], p["name"].lower()))

        vote = None
        if state == "VOTING":
            counts = Counter(votes.values())
            vote = {
                "context": vote_context,
                "ends_in": max(0, int(vote_ends_at - now)),
                "options": [{"key": k, "label": _plain(v[3]), "votes": counts.get(k, 0)}
                            for k, v in sorted(VOTE_OPTIONS.items())],
            }
        data = {
            "ts":           now,
            "bot_pid":      os.getpid(),
            "server_up":    server_up,
            "mission":      CURRENT_MISSION,
            "state":        state,
            "online_count": len(online),
            "time_current": mtime[0],
            "time_max":     mtime[1],
            "time_at":      mtime[2],
            "plugin_live":  any(p["fresh"] for p in players),
            "vote":         vote,
            "approval":     approval,
            "players":      players,
            "air":          AIR if (AIR and now - AIR_TS < 15) else None,   # AI/player aircraft counts (perf panel)
            "net":          ({**NET, "ts": round(NET_TS, 2)} if (NET and now - NET_TS < 15) else None),   # connection-health telemetry + reading timestamp (so the webcc NET graph samples once per reading, not per poll)
            "entities":     ENT if (ENT and now - ENT_TS < 15) else None,   # AI aircraft + ships for the live map
            "killfeed":     [k for k in KILLFEED[:KILLFEED_MAX] if now - k.get("ts", 0) < 1200],  # recent deaths (newest first, <20 min) for the webcc killfeed
            "plugin_cfg":   _dashboard_plugin_cfg(),             # live plugin config (public-listing keys overlaid from global_optin.json so they don't 'keep turning off')
            "mission_pool": mission_pool_state(),                # votemap pool toggles for the webcc Mission Pool modal
            "server_messages": server_messages_state(),          # automated chat messages for the webcc Messages modal
            "rank_ladder": rank_ladder_state(),                  # editable rank ladder (titles/points/colours/template) for the webcc Ranks modal
            "shared_ranks": shared_ranks_state(),                # cross-server shared-rank status + combined board for the webcc Shared Ranks card
            "reports": reports_state(),                          # anti-grief auto-kick/flag reports for the webcc Reports tab
            "ban_log": ban_log_state(),                          # persistent per-SteamID ban log (repeat-offender tracking) for the webcc Reports tab
            "server_config": server_config_state(),              # DedicatedServerConfig.json fields for the webcc Server Settings tab
            "sys_messages": sysmsg_state(),                       # built-in automated-message overrides for the webcc Messages tab
            "help_config": help_state(),                          # !help command list (text + show/hide gates) for the webcc Help editor
            "mission_audit": mission_audit_state(),               # official vs custom/workshop missions + integrity + eligibility (webcc Mission Pool)
            "votemap": votemap_cfg_state(),                       # dynamic vote-pool config (ballot size/mode/includes) for the webcc Votemap settings
            "banned_players": banned_players_state(),             # plugin_bans.txt -> webcc Moderation 'Banned' tab
            "global_sync": global_sync_state(),                   # public-listing (server directory) status for the webcc
        }
        tmp = DASHBOARD_STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        # os.replace can hit WinError 5 (Access denied) when a reader (the command
        # centre TUI) has the file open at that instant; retry briefly before giving up
        # so a transient lock doesn't drop the update (and leave a stale .tmp behind).
        for _attempt in range(5):
            try:
                os.replace(tmp, DASHBOARD_STATE_FILE)
                break
            except PermissionError:
                if _attempt == 4:
                    raise
                time.sleep(0.04)
    except Exception as e:   # noqa: BLE001 - publishing must never take the bot down
        try:
            print(f"[dashboard] state write failed: {e}")
        except Exception:    # noqa: BLE001
            pass

# ----------------------------------------------------------------------------
# Chat parser  -- derived from your sample console line:
# 81587.130: [ChatManager] CmdSendChatMessage allChat:True
#            connection(SteamConnection(7656119xxxxxxxxxx)) Player(Clone) test
# ----------------------------------------------------------------------------

CHAT_RE = re.compile(
    r"\[ChatManager\]\s+CmdSendChatMessage\s+"
    r"allChat:(?P<allchat>True|False)\s+"
    r"connection\(SteamConnection\((?P<steamid>\d+)\)\)\s+"
    r"(?P<obj>\S+)\s+"
    r"(?P<msg>.*)"
)

# A mission ending (for any reason) logs e.g.:
#   [DedicatedServerManager] Mission complete. Waiting 60 seconds before closing...
# We open the next vote on this, which also covers missions that end early.
MISSION_END_RE = re.compile(r"\[DedicatedServerManager\].*Mission complete", re.IGNORECASE)

# Capture + result lines (confirmed via --scanlog / --ctxlog):
#   "Adding airbase <id> to <Side>HQ"      -> <Side> just took base <id> (gives us
#                                             the capturing side for the next line)
#   "AIRBASE <name> TOTAL CAPTURE <n>"     -> a base was just captured
#   "[GameResolution] FinishGame Victory"  -> mission result (Victory / Defeat)
# ADD_AIRBASE_RE group(2) = side. Sides seen: Boscali / Primeva.
ADD_AIRBASE_RE = re.compile(r"Adding airbase (\S+) to (\w+)HQ")
CAPTURE_RE     = re.compile(r"AIRBASE (.+?) TOTAL CAPTURE")
GAME_RESULT_RE = re.compile(r"\[GameResolution\]\s+FinishGame\s+(\w+)")
# NukeStats plugin lines: "[NOSTATS] {json}" (see NukeStats/). Carries real per-player score.
NOSTATS_RE     = re.compile(r"\[NOSTATS\]\s*(\{.*\})\s*$")

THANKS_INTERVAL = 900        # "thanks for playing" cadence (seconds) - was 600 (10->15 min)
LEADERBOARD_INTERVAL = 1800  # auto-post the leaderboard to chat every 30 min during a match
SPECTIP_INTERVAL = 1020      # post spectator / team-switch help (seconds) - was 720 (12->17 min)


def parse_chat_line(line):
    """Return {'steamid','allchat','message'} for a player chat line, else None.

    The name field in the log is just the Unity object ('Player(Clone)'), not the
    player's display name, so we key votes on SteamID -- which is unique anyway.
    """
    m = CHAT_RE.search(line)
    if not m:
        return None
    return {
        "steamid": m.group("steamid"),
        "allchat": m.group("allchat") == "True",
        "message": m.group("msg").strip(),
    }


def extract_vote(message):
    """Map a chat message to a VOTE_OPTIONS key, or None. Votes must be '!'-prefixed
    (e.g. !1, or !vote 1) so a bare number typed in normal chat isn't counted."""
    msg = message.strip()
    if msg.lower().startswith("!vote"):
        msg = msg[len("!vote"):].strip()
    elif msg.startswith("!"):
        msg = msg[1:].strip()            # !1 -> 1
    else:
        return None                      # bare text/number is ordinary chat, not a vote
    parts = msg.split()
    token = parts[0] if parts else ""
    return token if token in VOTE_OPTIONS else None


# ----------------------------------------------------------------------------
# Remote-command client  -- JSON over TCP, 4-byte little-endian length prefix
# ----------------------------------------------------------------------------

class RemoteCommand:
    def __init__(self, host, port, timeout=5):
        self.host, self.port, self.timeout = host, port, timeout
        self.sock = None

    def _connect(self):
        self.sock = socket.create_connection((self.host, self.port), self.timeout)
        self.sock.settimeout(self.timeout)

    def _recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("remote-command socket closed")
            buf += chunk
        return buf

    def send(self, name, *args, return_code=False):
        """Send one command; return the decoded JSON response (or raw text/None).
        With return_code=True, return (status_code, response) instead -- the command
        centre uses that to show Success vs an error code. status_code is None on a
        connection failure."""
        payload = json.dumps(
            {"name": name, "arguments": [str(a) for a in args]}
        ).encode("utf-8")
        frame = len(payload).to_bytes(4, "little") + payload
        for attempt in (1, 2):  # reconnect once on a dead socket
            try:
                if self.sock is None:
                    self._connect()
                self.sock.sendall(frame)
                # Response framing: 4-byte status code (2000 = Success), then a
                # 4-byte body length, then the JSON body.
                code = int.from_bytes(self._recv_exact(4), "little")
                length = int.from_bytes(self._recv_exact(4), "little")
                if not 0 <= length <= 8_000_000:   # desynced/garbage frame -> reconnect & resync
                    raise ConnectionError(f"implausible reply length {length}")
                body = self._recv_exact(length).decode("utf-8", "replace")
                try:
                    resp = json.loads(body)
                except json.JSONDecodeError:
                    resp = body
                if DEBUG:
                    print(f"[rcmd] {name}{args} -> code={code} {resp}")
                return (code, resp) if return_code else resp
            except (OSError, ConnectionError) as e:
                print(f"[rcmd] {name} failed ({e})"
                      + ("; reconnecting" if attempt == 1 else " again"))
                try:
                    if self.sock:
                        self.sock.close()
                finally:
                    self.sock = None
        return (None, None) if return_code else None

    # convenience wrappers
    def say(self, message):
        if LOG_CONVERSATION:
            activity(_plain(message), "BOT")
        return self.send("send-chat-message", message)

    def set_next_mission(self, group, name, max_time):
        return self.send("set-next-mission", group, name, max_time)

    def set_time_remaining(self, seconds):
        return self.send("set-time-remaining", seconds)

    def get_mission_time(self):
        return self.send("get-mission-time")

    def get_player_list(self):
        return self.send("get-player-list")


def find_number(obj, key_hint):
    """Recursively find a numeric value whose key contains key_hint (case-insensitive).
    Lets us read the mission-time response without knowing its exact schema."""
    hint = key_hint.lower()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and hint in k.lower() and isinstance(v, (int, float)):
                return float(v)
        for v in obj.values():
            r = find_number(v, key_hint)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_number(v, key_hint)
            if r is not None:
                return r
    return None


# ----------------------------------------------------------------------------
# Console source  -- HOW the bot reads new console lines.
#
# Provided: tail a LOCAL file. Use this for testing, or if you run the bot on a
# box where the console log is accessible.
#
# For your real setup, replace poll() with one of:
#   * panel websocket  -- if GPanel exposes an API/console websocket, run a small
#                         background thread that pushes lines into a queue and have
#                         poll() drain that queue. (Real-time, best option.)
#   * SFTP tail        -- keep an SFTP/SSH session open to the remote log and read
#                         new bytes each tick (paramiko). (Polling, always works.)
# Tell me which you have and I'll write that adapter.
# ----------------------------------------------------------------------------

class ConsoleSource:
    def __init__(self, path):
        self.path = path
        self.pos = 0
        self._buf = ""
        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(0, 2)          # start at end: skip old backlog
                self.pos = f.tell()
        except FileNotFoundError:
            print(f"[console] log not found yet: {self.path}")

    def poll(self):
        """Return a list of new complete lines since the last call."""
        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(0, 2)
                size = f.tell()
                if size < self.pos:       # file rotated / truncated
                    self.pos = 0
                f.seek(self.pos)
                self._buf += f.read()
                self.pos = f.tell()
        except FileNotFoundError:
            return []
        *complete, self._buf = self._buf.split("\n")
        return complete


class SFTPConsoleSource:
    """Reads new console lines from a remote log file over SFTP (paramiko).

    Keeps the SSH/SFTP session open and tails by byte offset; reconnects on
    failure. Requires:  pip install paramiko
    Point SFTP_LOG_PATH at the remote console log (the file in the SFTP / File
    Manager that grows as players chat -- usually a .log in the server root or a
    logs/ folder).
    """

    def __init__(self, host, port, user, password, remote_path):
        self.host, self.port = host, port
        self.user, self.password = user, password
        self.remote_path = remote_path
        self.pos = None          # byte offset; established on first poll
        self._buf = ""
        self._ssh = None
        self._sftp = None

    def _connect(self):
        import paramiko
        self._ssh = paramiko.SSHClient()
        self._ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self._ssh.connect(self.host, port=self.port, username=self.user,
                          password=self.password, timeout=10,
                          look_for_keys=False, allow_agent=False)
        self._sftp = self._ssh.open_sftp()
        print(f"[sftp] connected to {self.host}:{self.port}")

    def _close(self):
        try:
            if self._sftp:
                self._sftp.close()
            if self._ssh:
                self._ssh.close()
        finally:
            self._sftp = self._ssh = None

    def poll(self):
        try:
            if self._sftp is None:
                self._connect()
            size = self._sftp.stat(self.remote_path).st_size
            if self.pos is None:          # first read: start at end, skip backlog
                self.pos = size
                return []
            if size < self.pos:           # file rotated / truncated
                self.pos = 0
            if size == self.pos:
                return []
            with self._sftp.open(self.remote_path, "r") as f:
                f.seek(self.pos)
                data = f.read(size - self.pos)
                self.pos = size
            if isinstance(data, bytes):
                data = data.decode("utf-8", "replace")
            self._buf += data
        except Exception as e:            # noqa: BLE001 - reconnect on any failure
            print(f"[sftp] poll error ({e}); will reconnect next tick")
            self._close()
            return []
        *complete, self._buf = self._buf.split("\n")
        return complete


# ----------------------------------------------------------------------------
# Vote logic
# ----------------------------------------------------------------------------

def mission_variant(name):
    """The trailing weather/time tag, e.g. 'Night' from '... - Night'."""
    return name.rsplit(" - ", 1)[-1].strip()


def is_dark(name):
    """True if this map's variant is one we cap with MAX_DARK_PER_VOTE."""
    return mission_variant(name) in DARK_VARIANTS


def friendly_label(name):
    """Shorter label for chat, e.g. 'Escalation BDF - Night'."""
    return name.replace(" Co-op as ", " ")


# --- Mission pool (votemap): owners toggle which missions appear in the vote (e.g. PvP-only, no Terminal).
# Stored in mission_pool.json as the DISABLED set. Server flavour, not a gameplay-locked setting, so it's
# owner=missionpool.
MISSION_POOL_FILE = os.path.join(_BASE_DIR, "mission_pool.json")
_mission_disabled = set()


def _all_pool_missions():
    """[(name, category)] for every toggleable mission: the co-op variants + the 2 stock PvP options."""
    out = [(m, "Escalation Co-op") for m in ESCALATION_MISSIONS]
    out += [(m, "Terminal Control Co-op") for m in TERMINAL_CONTROL_MISSIONS]
    out += [(p[1], "PvP") for p in PVP_OPTIONS]
    return out


def load_mission_pool():
    global _mission_disabled
    try:
        with open(MISSION_POOL_FILE, encoding="utf-8") as f:
            _mission_disabled = set(json.load(f).get("disabled", []))
    except (OSError, ValueError):
        _mission_disabled = set()


def save_mission_pool():
    try:
        tmp = MISSION_POOL_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"disabled": sorted(_mission_disabled)}, f, indent=1)
        os.replace(tmp, MISSION_POOL_FILE)
    except OSError:
        pass


def mission_enabled(name):
    return name not in _mission_disabled


def set_mission_enabled(name, on):
    if name not in {n for n, _ in _all_pool_missions()}:
        return False
    if on:
        _mission_disabled.discard(name)
    else:
        _mission_disabled.add(name)
    save_mission_pool()
    return True


def mission_pool_state():
    return [{"name": n, "label": friendly_label(n), "cat": c, "on": mission_enabled(n)}
            for n, c in _all_pool_missions()]


# ── Votemap (dynamic vote pool) configuration ─────────────────────────────────────────────────────
# The end-of-mission / !votemap ballot is sized from TWO pools INDEPENDENTLY so the count of each map
# TYPE is explicit (the old single "ballot_size" only counted the co-op maps, which was confusing):
#   * coop_count  PvE co-op (+ enabled custom) maps  — drawn from _votemap_pool()
#   * pvp_count   PvP built-in modes                 — drawn from the ENABLED PVP_OPTIONS only
# Default 4 + 2 = the regular 6-option ballot. Each pool has a selection MODE that controls the
# likelihood mix (balanced/random/weighted for co-op; fixed/random/weighted for PvP) and an optional
# per-category / per-mode weight table for "weighted". A high-population rule can override the split
# into a PvP-heavy ballot once enough players are online (force_pvp_*). Decoupling pvp_count from the
# pool toggles is deliberate: enabling extra built-in modes in the Mission Pool enlarges what the PvP
# slots can draw from WITHOUT growing the ballot (so the regular 6 stays 6).
VOTEMAP_CONFIG_FILE = os.path.join(_BASE_DIR, "votemap_config.json")
_VOTEMAP_DEFAULTS = {
    "enabled":           True,       # master kill-switch: off => no auto map-vote (server rotation advances)
    "coop_count":        4,          # PvE co-op (+custom) maps on the ballot
    "pvp_count":         2,          # PvP built-in modes on the ballot
    "coop_mode":         "balanced", # balanced (even round-robin) | random (uniform) | weighted
    "pvp_mode":          "fixed",    # fixed (PVP_OPTIONS order) | random | weighted
    "include_pvp":       True,       # master toggle for the PvP slots
    "include_custom":    True,       # let enabled custom USER missions into the co-op pool
    "coop_weights":      {},         # {category: relative_likelihood} for coop_mode == weighted
    "pvp_weights":       {},         # {pvp_mission_name: relative_likelihood} for pvp_mode == weighted
    "guaranteed":        [],         # mission NAMES always pinned onto every ballot (they count toward the
                                     # relevant type's slot count; like the always-on PvP pair, generalised)
    "avoid_recent":      0,          # don't re-offer the last N winning maps (0 = off; only the exact-ballot
                                     # anti-repeat applies). Guaranteed missions are exempt.
    "force_pvp_enabled": True,       # high-pop override: force a PvP-heavy ballot (Tomo wants this ON)
    "force_pvp_players": 24,         # ... once at least this many players are online
    "force_pvp_coop":    0,          # co-op maps while forcing (0 = PvP-only)
    "force_pvp_pvp":     6,          # PvP modes while forcing (capped by how many are enabled)
}
_COOP_CATEGORIES = ("Escalation", "Terminal Control", "Custom")   # weightable co-op pool keys


def _vm_int(v, default, lo, hi):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def _vm_weights(v):
    """Normalize a {name: number>=0} weight table; drop junk. Empty dict == all-equal."""
    out = {}
    if isinstance(v, dict):
        for k, w in v.items():
            try:
                w = float(w)
            except (TypeError, ValueError):
                continue
            # w >= 0 already rejects NaN; also reject +inf so it can't dominate weighted sampling
            if isinstance(k, str) and 0 <= w != float("inf"):
                out[k] = w
    return out


def _vm_strlist(v):
    """Normalize a list of mission-name strings (dedup, preserve order, drop blanks/junk)."""
    out, seen = [], set()
    if isinstance(v, list):
        for x in v:
            if isinstance(x, str) and x.strip() and x not in seen:
                seen.add(x)
                out.append(x)
    return out


def _votemap_cfg():
    cfg = dict(_VOTEMAP_DEFAULTS)
    raw = {}
    try:
        with open(VOTEMAP_CONFIG_FILE, encoding="utf-8") as f:
            j = json.load(f)
        if isinstance(j, dict):
            raw = dict(j)
    except (OSError, ValueError):
        pass
    # migrate the v1 schema (ballot_size -> coop_count, mode -> coop_mode)
    if "coop_count" not in raw and "ballot_size" in raw:
        raw["coop_count"] = raw["ballot_size"]
    if "coop_mode" not in raw and "mode" in raw:
        raw["coop_mode"] = raw["mode"]
    for k in cfg:
        if k in raw:
            cfg[k] = raw[k]
    _np = len(PVP_OPTIONS)
    cfg["coop_count"]        = _vm_int(cfg["coop_count"], 4, 0, 12)
    cfg["pvp_count"]         = _vm_int(cfg["pvp_count"], 2, 0, _np)
    cfg["force_pvp_players"] = _vm_int(cfg["force_pvp_players"], 24, 1, 200)
    cfg["force_pvp_coop"]    = _vm_int(cfg["force_pvp_coop"], 0, 0, 12)
    cfg["force_pvp_pvp"]     = _vm_int(cfg["force_pvp_pvp"], 6, 0, _np)
    if cfg["coop_mode"] not in ("balanced", "random", "weighted"):
        cfg["coop_mode"] = "balanced"
    if cfg["pvp_mode"] not in ("fixed", "random", "weighted"):
        cfg["pvp_mode"] = "fixed"
    cfg["include_pvp"]    = bool(cfg["include_pvp"])
    cfg["include_custom"] = bool(cfg["include_custom"])
    cfg["enabled"]        = bool(cfg["enabled"])
    cfg["coop_weights"]   = _vm_weights(cfg["coop_weights"])
    cfg["pvp_weights"]    = _vm_weights(cfg["pvp_weights"])
    cfg["guaranteed"]     = _vm_strlist(cfg["guaranteed"])
    cfg["avoid_recent"]   = _vm_int(cfg["avoid_recent"], 0, 0, 10)
    # never let the NORMAL split collapse to an empty ballot via config alone (guaranteed missions also
    # backstop this, but a 0/0 split with nothing pinned would otherwise fall through to the safety net)
    if cfg["coop_count"] + (cfg["pvp_count"] if cfg["include_pvp"] else 0) < 1 and not cfg["guaranteed"]:
        cfg["coop_count"] = 1
    return cfg


# per-key integer bounds (lo, hi); clamp at the SOURCE so the file never stores out-of-range values
_VOTEMAP_INT_BOUNDS = {
    "coop_count":        (0, 12),
    "pvp_count":         (0, len(PVP_OPTIONS)),
    "avoid_recent":      (0, 10),
    "force_pvp_players": (1, 200),
    "force_pvp_coop":    (0, 12),
    "force_pvp_pvp":     (0, len(PVP_OPTIONS)),
}
_VOTEMAP_BOOL_KEYS = ("enabled", "include_pvp", "include_custom", "force_pvp_enabled")
_VOTEMAP_ALIASES   = {"ballot_size": "coop_count", "mode": "coop_mode"}


def set_votemap_cfg(key, value):
    key = _VOTEMAP_ALIASES.get(key, key)            # accept v1 keys from an un-refreshed webcc
    if key not in _VOTEMAP_DEFAULTS:
        return False
    cfg = _votemap_cfg()
    if key in _VOTEMAP_INT_BOUNDS:
        lo, hi = _VOTEMAP_INT_BOUNDS[key]
        v = _vm_int(value, None, lo, hi)
        if v is None:
            return False
        cfg[key] = v
    elif key == "coop_mode":
        if str(value) not in ("balanced", "random", "weighted"):
            return False
        cfg[key] = str(value)
    elif key == "pvp_mode":
        if str(value) not in ("fixed", "random", "weighted"):
            return False
        cfg[key] = str(value)
    elif key == "coop_weights":
        allow = set(_COOP_CATEGORIES)
        cfg[key] = {k: w for k, w in _vm_weights(value).items() if k in allow}   # whitelist pool categories
    elif key == "pvp_weights":
        allow = {p[1] for p in PVP_OPTIONS}
        cfg[key] = {k: w for k, w in _vm_weights(value).items() if k in allow}   # whitelist built-in modes
    elif key == "guaranteed":
        allow = _votable_names()
        cfg[key] = [n for n in _vm_strlist(value) if n in allow]                 # only pin real votable maps
    elif key in _VOTEMAP_BOOL_KEYS:
        cfg[key] = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "on", "yes")
    else:
        return False
    cfg.pop("ballot_size", None)            # strip any legacy v1 keys so the file converges to clean v2
    cfg.pop("mode", None)
    try:
        tmp = VOTEMAP_CONFIG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=1)
        os.replace(tmp, VOTEMAP_CONFIG_FILE)
    except OSError:
        return False
    return True


def votemap_cfg_state():
    c = _votemap_cfg()
    c["vote_duration"] = VOTE_DURATION
    # convenience for the webcc: live totals + the rows the weight/force/guaranteed UI needs
    pvp_n = c["pvp_count"] if c["include_pvp"] else 0
    c["total_normal"] = c["coop_count"] + pvp_n
    c["total_forced"] = c["force_pvp_coop"] + (c["force_pvp_pvp"] if c["include_pvp"] else 0)
    c["pvp_options"]  = [{"name": p[1], "on": mission_enabled(p[1])} for p in PVP_OPTIONS]
    c["pvp_enabled_count"] = sum(1 for p in PVP_OPTIONS if mission_enabled(p[1]))
    pool = _votemap_pool()
    c["coop_categories"]   = [cat for cat in _COOP_CATEGORIES if cat in pool]
    c["coop_available"]    = sum(len(ms) for ms in pool.values())     # enabled co-op/custom maps in the pool
    # the full votable universe (for the "add guaranteed" picker) + friendly labels for the current pins
    votable = [{"name": n, "label": friendly_label(n), "cat": cat} for n, cat in _all_pool_missions()]
    votable += [{"name": n, "label": friendly_label(n), "cat": "Custom"} for n in _enabled_custom_names()]
    c["votable"] = votable
    c["guaranteed_labels"] = [{"name": n, "label": friendly_label(n),
                               "pvp": n in PVP_MISSIONS, "on": mission_enabled(n)} for n in c["guaranteed"]]
    return c


def _enabled_custom_names():
    """Enabled custom USER missions (from the mission audit) -> votable. Workshop missions are excluded
    from the in-game vote (numeric id / Workshop group); they still cycle via the server rotation."""
    a = mission_audit_state() or {}
    return [u.get("name") for u in (a.get("unofficial") or [])
            if u.get("enabled") and u.get("name") and u.get("group") != "Workshop"]


load_mission_pool()


# --- Server message manager: owner-defined automated chat messages with triggers. Stored in
# server_messages.json. The webcc Messages modal queues "servermsg" CRUD ops; the BOT owns the file
# (single writer) and reflects state in the dashboard, exactly like the mission pool. Triggers:
#   interval    -> every N minutes while players are online (and the server is idle, not mid-vote)
#   clock       -> once daily at HH:MM (server local time)
#   match_start -> when a genuinely new match begins
#   match_end   -> when a match ends
SERVER_MESSAGES_FILE = os.path.join(_BASE_DIR, "server_messages.json")
_server_messages = []            # list of {id,text,trigger,interval_min,at,color,enabled}
_msg_last_fired = {}             # id -> epoch  (interval throttle; runtime only, not persisted)
_msg_last_day = {}               # id -> "YYYY-MM-DD" already-fired marker for clock triggers
_msg_id_seq = 0
MSG_TRIGGERS = ("interval", "clock", "match_start", "match_end")
MSG_TEXT_MAX = 240
MSG_MAX_COUNT = 40
_MSG_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_MSG_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")
_MSG_ID_RE = re.compile(r"^msg_[0-9a-z]+$")


def _new_msg_id():
    global _msg_id_seq
    _msg_id_seq += 1
    return "msg_" + format(int(time.time() * 1000), "x") + format(_msg_id_seq, "x")


def _balance_color_tags(t):
    """Drop a trailing unterminated tag (a hard length-cap can cut a <color=#hex> in half,
    which would corrupt every following chat line) and auto-close any dangling <color> tags."""
    t = re.sub(r"</?c(?:o(?:l(?:o(?:r(?:=#?[0-9A-Fa-f]{0,6})?)?)?)?)?$", "", t)   # strip a trailing cut color-tag prefix (<c.. or </c..); a bare '<' or real text is kept
    opens = len(re.findall(r"<color=#[0-9A-Fa-f]{6}>", t))
    closes = len(re.findall(r"</color>", t))
    if opens > closes:
        t += "</color>" * (opens - closes)
    return t


def _msg_sanitize_text(text):
    """One-line, control-char-free, length-capped chat text (the message goes straight to rc.say).
    Tag-aware: the length cap never leaves a half-cut <color> tag, and dangling tags auto-close."""
    t = re.sub(r"[\x00-\x1f\x7f]", " ", str(text if text is not None else ""))
    t = re.sub(r"\s+", " ", t).strip()
    return _balance_color_tags(t[:MSG_TEXT_MAX])


def _msg_clean(m):
    """Coerce one raw message dict into a validated record, or None if it has no usable text."""
    if not isinstance(m, dict):
        return None
    text = _msg_sanitize_text(m.get("text"))
    if not text:
        return None
    trig = str(m.get("trigger") or "interval")
    if trig not in MSG_TRIGGERS:
        trig = "interval"
    try:
        iv = int(float(m.get("interval_min", 30)))
    except (TypeError, ValueError):
        iv = 30
    iv = max(1, min(1440, iv))
    at = str(m.get("at") or "").strip()
    if _MSG_HHMM_RE.match(at):
        hh, mm = at.split(":")
        at = f"{int(hh):02d}:{mm}"
    else:
        at = "12:00"
    color = str(m.get("color") or "").strip()
    if not _MSG_HEX_RE.match(color):
        color = ""
    if re.search(r"<color=#[0-9A-Fa-f]{6}>", text):    # per-word colours already in the text -> no outer wrap (avoid bleed)
        color = ""
    mid = str(m.get("id") or "")
    if not _MSG_ID_RE.match(mid):
        mid = _new_msg_id()
    return {"id": mid, "text": text, "trigger": trig, "interval_min": iv,
            "at": at, "color": color, "enabled": bool(m.get("enabled", True))}


def load_server_messages():
    global _server_messages
    try:
        with open(SERVER_MESSAGES_FILE, encoding="utf-8") as f:
            raw = json.load(f).get("messages", [])
    except (OSError, ValueError):
        raw = []
    out = []
    for m in raw if isinstance(raw, list) else []:
        c = _msg_clean(m)
        if c:
            out.append(c)
    _server_messages = out


def save_server_messages():
    try:
        tmp = SERVER_MESSAGES_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"messages": _server_messages}, f, indent=1)
        os.replace(tmp, SERVER_MESSAGES_FILE)
    except OSError:
        pass


def server_messages_state():
    return [dict(m) for m in _server_messages]


# ── editable rank ladder (webcc "Ranks" modal) ─────────────────────────────────────
def _rank_ladder_validate(ranks, template):
    """Validate + normalise a proposed ladder. Returns (rows_tuples, template, warnings)
    or raises ValueError. rows = list of (threshold, name, abbr, colour)."""
    if not isinstance(ranks, list) or not ranks:
        raise ValueError("need at least one rank")
    rows = []
    for r in ranks:
        if not isinstance(r, dict):
            raise ValueError("bad rank row")
        try:
            th = int(float(r.get("threshold", 0)))
        except (TypeError, ValueError):
            raise ValueError("threshold must be a number")
        if th < 0:
            th = 0
        name = str(r.get("name") or "").strip()
        abbr = str(r.get("abbr") or "").strip()
        color = str(r.get("color") or "").strip()
        if not name or any(c in name for c in "|\n\r"):
            raise ValueError("a rank name is required and cannot contain | or newlines")
        name = name[:40]
        if not abbr or any(c in abbr for c in "|[]\n\r \t"):
            raise ValueError("an abbreviation is required and cannot contain spaces, [, ], | or newlines")
        abbr = abbr[:12]
        if not _MSG_HEX_RE.match(color):
            raise ValueError(f"the colour for '{name}' must be #RRGGBB")
        rows.append([th, name, abbr, color])
    rows.sort(key=lambda x: x[0])
    rows[0][0] = 0                                       # the lowest rank is always the floor (0 points)
    for i in range(1, len(rows)):
        if rows[i][0] <= rows[i - 1][0]:
            raise ValueError("thresholds must be strictly ascending and unique")
    if len({r[1] for r in rows}) != len(rows):
        raise ValueError("rank names must be unique")
    if len({r[2] for r in rows}) != len(rows):
        raise ValueError("abbreviations must be unique")
    warnings = []
    if any(len(r[2]) <= 2 for r in rows):
        warnings.append("a very short abbreviation can be mistaken for a clan tag in chat")
    tmpl = str(template if template is not None else DEFAULT_RANKUP_TEMPLATE).strip()
    if not tmpl:
        raise ValueError("the rank-up template cannot be empty")
    if "{name}" not in tmpl:
        raise ValueError("the rank-up template must include {name}")
    if tmpl.count("<color") != tmpl.count("</color>"):
        raise ValueError("the rank-up template has unbalanced <color> tags")
    if len(tmpl) > 240:
        raise ValueError("the rank-up template is too long")
    return [tuple(r) for r in rows], tmpl, warnings


def save_rank_ladder(ranks, template):
    """Atomic write of the ladder (+ .bak). ranks = list of (threshold, name, abbr, colour)."""
    try:
        payload = {"version": 1, "rankup_template": template,
                   "ranks": [{"threshold": r[0], "name": r[1], "abbr": r[2], "color": r[3]} for r in ranks]}
        if os.path.exists(RANK_LADDER_FILE):
            try:
                with open(RANK_LADDER_FILE, "rb") as _src, open(RANK_LADDER_FILE + ".bak", "wb") as _dst:
                    _dst.write(_src.read())
            except OSError:
                pass
        tmp = RANK_LADDER_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=1)
        os.replace(tmp, RANK_LADDER_FILE)
    except OSError:
        pass


def load_rank_ladder():
    """Load rank_ladder.json into RANKS + RANKUP_TEMPLATE (fail-open to the built-in default).
    Seeds the file with today's ladder on first run. Resets the rank-tag regex cache so a
    renamed abbr cannot leak its old tag into PLAYER_NAMES / ranks.json via _strip_rank_tag."""
    global RANKS, RANKUP_TEMPLATE, _RANK_TAG_RE
    try:
        with open(RANK_LADDER_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("rank_ladder.json root must be an object")
        rows, tmpl, _ = _rank_ladder_validate(data.get("ranks"), data.get("rankup_template"))
        RANKS = rows
        RANKUP_TEMPLATE = tmpl
    except FileNotFoundError:
        RANKS = list(DEFAULT_RANKS)
        RANKUP_TEMPLATE = DEFAULT_RANKUP_TEMPLATE
        save_rank_ladder(RANKS, RANKUP_TEMPLATE)         # seed today's ladder verbatim (no visible change)
    except (OSError, ValueError, TypeError, AttributeError) as e:
        print(f"[rank-ladder] using the default ladder ({e})")
        RANKS = list(DEFAULT_RANKS)
        RANKUP_TEMPLATE = DEFAULT_RANKUP_TEMPLATE
    _RANK_TAG_RE = None


def rank_ladder_state():
    return {"rankup_template": RANKUP_TEMPLATE,
            "ranks": [{"threshold": r[0], "name": r[1], "abbr": r[2], "color": r[3]} for r in RANKS]}


def rank_ladder_apply(payload):
    """Validate + persist + rebuild RANKS in place. Returns {ok, error?, warnings?}. The caller
    pushes plugin_ranks + logs activity on success (cc_web 'ok' means queued, not yet applied)."""
    global RANKS, RANKUP_TEMPLATE, _RANK_TAG_RE
    try:
        rows, tmpl, warnings = _rank_ladder_validate((payload or {}).get("ranks"),
                                                      (payload or {}).get("rankup_template"))
    except (ValueError, TypeError, AttributeError) as e:
        return {"ok": False, "error": str(e)}
    RANKS = rows
    RANKUP_TEMPLATE = tmpl
    _RANK_TAG_RE = None
    save_rank_ladder(RANKS, RANKUP_TEMPLATE)
    return {"ok": True, "warnings": warnings}


def rankup_line(name, rname, abbr, color):
    """Render the configurable rank-up announcement. Strips < > from the player name so a
    hostile display name cannot hijack the surrounding colour tags."""
    safe = str(name).replace("<", "").replace(">", "")
    tmpl = RANKUP_TEMPLATE or DEFAULT_RANKUP_TEMPLATE     # never broadcast a blank line
    try:
        return (tmpl.replace("{color}", color).replace("{name}", safe)
                .replace("{rank}", rname).replace("{abbr}", abbr))
    except Exception:                                    # noqa: BLE001 - never break a rank-up
        return f"<color={color}>** RANK UP ** {safe} is now {rname} ({abbr})!</color>"


# ── cross-server shared ranks (write-own-file aggregate; display only) ───────────────
# A host running several of these servers can point them all at one shared directory; each
# bot keeps writing its OWN local ranks.json unchanged (the ms-baseline math, ledger and
# --audit invariant are NEVER touched) and additionally publishes a copy as ranks_<id>.json
# into the share. A combined leaderboard sums points per SteamID across those files at READ
# time only. No lock, no merge, no foreign-file mutation -> zero concurrent-writer hazard.
SHARED_RANKS_FILE    = os.path.join(_BASE_DIR, "shared_ranks.json")
SHARED_RANKS_ENABLED = False
SHARED_RANKS_DIR     = ""
SERVER_INSTANCE_ID   = ""
_SHARED_PUB_AT       = 0.0           # last aggregate publish (throttle)
_SHARED_BOARD_CACHE  = ([], 0.0)     # (rows, computed_at): cache the combined board off the 1Hz dashboard

# #2 daemon-thread shared I/O: the publish into the (possibly slow/locked/network) shared dir runs
# on a background daemon, NEVER on the bot's main loop. maybe_publish_aggregate()/enable just set a
# pending flag; the worker drains it. Concurrency-safe by construction (write-own-file + atomic replace).
_SHARED_PUB_PENDING  = False         # set by the throttle / enable; cleared by the daemon after a publish


def _shared_pub_worker():
    """Daemon: publishes this server's rankshare file off the main loop so a slow/locked shared
    folder can never stall the bot tick. publish_ranks_aggregate() is already OSError-fail-open;
    this loop additionally swallows everything so the daemon can never die."""
    global _SHARED_PUB_PENDING, _OTHER_RANKS_CACHE
    while True:
        try:
            pending = _SHARED_PUB_PENDING
            _SHARED_PUB_PENDING = False
            if pending:
                publish_ranks_aggregate()
            if SHARED_RANKS_ENABLED:          # #XSRV-2: keep the READ caches warm OFF the main loop so a
                _OTHER_RANKS_CACHE = (_compute_other_ranks(), time.time())   # rank display/award never globs the share inline
                try:
                    shared_ranks_state()      # warms the board (30s) + peer-count (30s) caches off-loop too
                except Exception:             # noqa: BLE001
                    pass
        except Exception:                     # noqa: BLE001 - a publish failure must never kill the daemon
            pass
        time.sleep(2)


def _start_shared_pub_worker():
    """Start the publish daemon once (idempotent-ish; only called at load)."""
    try:
        import threading
        threading.Thread(target=_shared_pub_worker, name="shared-ranks-pub", daemon=True).start()
    except Exception as e:                     # noqa: BLE001 - sharing stays off rather than crash boot
        print(f"[shared-ranks] worker start failed: {e}")


def _gen_instance_id():
    """Deterministic per (host, install dir): two server folders -- even a verbatim clone -- get DIFFERENT
    ids, so they never publish the same rankshare_<id>.json and clobber each other (the folder-clone
    collision that silently breaks carry-over). Stable across restarts (same host+dir -> same id)."""
    import hashlib, socket
    seed = (socket.gethostname() + "|" + os.path.abspath(_BASE_DIR)).encode("utf-8", "replace")
    return hashlib.sha1(seed).hexdigest()[:12]


def save_shared_ranks_cfg(enabled, dir_, instance_id=None):
    try:
        iid = instance_id if instance_id is not None else (SERVER_INSTANCE_ID or _gen_instance_id())
        tmp = SHARED_RANKS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"enabled": bool(enabled), "dir": str(dir_ or ""), "instance_id": iid}, f, indent=1)
        os.replace(tmp, SHARED_RANKS_FILE)
    except OSError:
        pass


def load_shared_ranks_cfg():
    global SHARED_RANKS_ENABLED, SHARED_RANKS_DIR, SERVER_INSTANCE_ID
    data = {}
    try:
        with open(SHARED_RANKS_FILE, encoding="utf-8") as f:
            data = json.load(f) or {}
    except (OSError, ValueError):
        data = {}
    SHARED_RANKS_ENABLED = bool(data.get("enabled", False))
    SHARED_RANKS_DIR = str(data.get("dir", "") or "")
    # ALWAYS derive the id from host+dir (don't trust a persisted/copied value) so a folder clone can't
    # inherit another instance's id and collide on the shared rankshare_<id>.json. Persist if it changed.
    iid = _gen_instance_id()
    SERVER_INSTANCE_ID = iid
    if str(data.get("instance_id", "") or "").strip() != iid:
        save_shared_ranks_cfg(SHARED_RANKS_ENABLED, SHARED_RANKS_DIR, iid)


def publish_ranks_aggregate():
    """Write THIS server's lifetime ranks into the shared dir as ranks_<id>.json (atomic,
    write-own-file only). Best-effort; a failure logs and never blocks the bot."""
    if not (SHARED_RANKS_ENABLED and SHARED_RANKS_DIR and SERVER_INSTANCE_ID):
        return
    try:
        if not os.path.isdir(SHARED_RANKS_DIR):
            return
        # list() snapshots under the GIL so the MAIN loop mutating RANK_DATA (award/snap) can't raise
        # "dictionary changed size during iteration" on this daemon thread (#XSRV-1).
        snap = {sid: {"name": rec.get("name", ""), "points": rec.get("points", 0),
                      "wins": rec.get("wins", 0), "losses": rec.get("losses", 0)}
                for sid, rec in list(RANK_DATA.items()) if isinstance(rec, dict)}
        dest = os.path.join(SHARED_RANKS_DIR, f"rankshare_{SERVER_INSTANCE_ID}.json")
        tmp = dest + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"server": SERVER_INSTANCE_ID, "updated": int(time.time()), "ranks": snap}, f)
        os.replace(tmp, dest)
    except OSError as e:                          # noqa: BLE001
        print(f"[shared-ranks] publish failed: {e}")


def maybe_publish_aggregate():
    """Throttled (>=45s) request, called from save_ranks(). NON-BLOCKING: only flags the daemon
    publisher (#2), so a slow/locked shared folder can never stall the bot's main loop. Never raises."""
    global _SHARED_PUB_AT, _SHARED_PUB_PENDING
    if not SHARED_RANKS_ENABLED:
        return
    now = time.time()
    if now - _SHARED_PUB_AT < 45:
        return
    _SHARED_PUB_AT = now
    _SHARED_PUB_PENDING = True


def read_aggregate_ranks():
    """Sum points (+ W/L) per SteamID across every ranks_*.json in the shared dir. DISPLAY ONLY;
    never folded back into RANK_DATA or the ms baseline. Tolerant of a peer file mid-replace."""
    import glob
    agg = {}
    if not (SHARED_RANKS_DIR and os.path.isdir(SHARED_RANKS_DIR)):
        return agg
    for path in glob.glob(os.path.join(SHARED_RANKS_DIR, "rankshare_*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        ranks = d.get("ranks", {}) if isinstance(d, dict) else {}
        for sid, rec in (ranks.items() if isinstance(ranks, dict) else []):
            if not isinstance(rec, dict):
                continue
            a = agg.setdefault(sid, {"name": "", "points": 0.0, "wins": 0, "losses": 0})
            try:
                a["points"] += float(rec.get("points", 0) or 0)
                a["wins"] += int(rec.get("wins", 0) or 0)
                a["losses"] += int(rec.get("losses", 0) or 0)
            except (TypeError, ValueError):
                pass
            if rec.get("name"):
                a["name"] = rec["name"]
    return agg


_OTHER_RANKS_CACHE = ({}, 0.0)
_SHARED_PEERS_CACHE = (0, 0.0)       # (count, computed_at): cache the peer-file glob so the 1Hz dashboard doesn't list the share each tick (#XSRV-2)


def _compute_other_ranks():
    """Glob + sum the OTHER servers' rankshare files (excludes our own). This does the file I/O; it is
    called OFF the main loop by the shared-ranks daemon (#XSRV-2) so a slow/locked share never stalls a
    rank display/award. Tolerant of a peer file mid-replace. Empty unless sharing is enabled."""
    out = {}
    if SHARED_RANKS_ENABLED and SHARED_RANKS_DIR and os.path.isdir(SHARED_RANKS_DIR):
        import glob
        mine = f"rankshare_{SERVER_INSTANCE_ID}.json"
        for path in glob.glob(os.path.join(SHARED_RANKS_DIR, "rankshare_*.json")):
            if os.path.basename(path) == mine:
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    d = json.load(f)
                ranks = d.get("ranks", {}) if isinstance(d, dict) else {}
            except (OSError, ValueError):
                continue
            for sid, rec in (ranks.items() if isinstance(ranks, dict) else []):
                if isinstance(rec, dict):
                    try:
                        out[sid] = out.get(sid, 0.0) + float(rec.get("points", 0) or 0)
                    except (TypeError, ValueError):
                        pass
    return out


def _other_ranks():
    """Cached {sid: points} summed across the OTHER servers (excludes our own file). The shared-ranks
    daemon keeps this cache warm every ~2s, so a rank display/award on the MAIN loop reads the cache and
    never globs the (possibly slow) share inline (#XSRV-2). The inline refresh below is only a fallback
    if the daemon hasn't updated in 60s (e.g. not started). Empty unless sharing is enabled."""
    global _OTHER_RANKS_CACHE
    cached, at = _OTHER_RANKS_CACHE
    now = time.time()
    if now - at < 60:
        return cached
    out = _compute_other_ranks()
    _OTHER_RANKS_CACHE = (out, now)
    return out


def shared_ranks_state():
    """Status (+ a cached combined top-12) for the webcc Shared Ranks card."""
    global _SHARED_BOARD_CACHE
    global _SHARED_PEERS_CACHE
    exists = bool(SHARED_RANKS_DIR and os.path.isdir(SHARED_RANKS_DIR))
    peers, board = 0, []
    if SHARED_RANKS_ENABLED and exists:
        pcached, pat = _SHARED_PEERS_CACHE          # 30s-cached so the 1Hz dashboard never lists a slow share each tick (#XSRV-2)
        if time.time() - pat < 30:
            peers = pcached
        else:
            try:
                import glob
                peers = len(glob.glob(os.path.join(SHARED_RANKS_DIR, "rankshare_*.json")))
            except OSError:
                peers = 0
            _SHARED_PEERS_CACHE = (peers, time.time())
        cached, at = _SHARED_BOARD_CACHE
        now = time.time()
        if now - at < 30:
            board = cached
        else:
            agg = read_aggregate_ranks()
            rows = sorted(agg.items(), key=lambda kv: kv[1]["points"], reverse=True)[:12]
            board = [{"name": v["name"] or sid, "points": round(v["points"], 1),
                      "wins": v["wins"], "losses": v["losses"], "rank": rank_index_for(v["points"])}
                     for sid, v in rows]
            _SHARED_BOARD_CACHE = (board, now)
    return {"enabled": SHARED_RANKS_ENABLED, "dir": SHARED_RANKS_DIR, "server_id": SERVER_INSTANCE_ID,
            "exists": exists, "peer_files": peers, "board": board}


def set_shared_ranks(enabled, dir_):
    global SHARED_RANKS_ENABLED, SHARED_RANKS_DIR, _SHARED_PUB_AT, _SHARED_BOARD_CACHE, _OTHER_RANKS_CACHE
    global _SHARED_PUB_PENDING
    SHARED_RANKS_ENABLED = bool(enabled)
    SHARED_RANKS_DIR = str(dir_ or "").strip()
    save_shared_ranks_cfg(SHARED_RANKS_ENABLED, SHARED_RANKS_DIR)
    _SHARED_BOARD_CACHE = ([], 0.0)
    if SHARED_RANKS_ENABLED:
        _SHARED_PUB_AT = 0.0
        _SHARED_PUB_PENDING = True               # #2: flag the daemon to publish OUR file (off the main loop)
        # Warm the peer cache NOW (synchronously -- this runs on the admin-command handler, not the hot
        # loop) so the immediate rank re-push below bakes the COMBINED rank into every player's name tag,
        # not the local-only rank. Without this, a player joining right after you toggle sharing on gets
        # their LOCAL rank baked (the plugin bakes the name ONCE at connect) until the daemon warms the
        # cache ~2s later -- the "cross-server rank didn't show" symptom.
        try:
            _OTHER_RANKS_CACHE = (_compute_other_ranks(), time.time())
        except Exception:                        # noqa: BLE001 - enabling must never raise
            pass
    else:
        _OTHER_RANKS_CACHE = ({}, 0.0)           # sharing off -> ranks revert to local immediately
    _RANK_PUSH_FLAG[0] = True                     # re-push plugin_ranks.txt (combined ranks + peer lines) on the very next loop
    return {"ok": True}


def _msg_find(mid):
    for m in _server_messages:
        if m["id"] == mid:
            return m
    return None


def server_msg_apply(op, payload):
    """Apply one CRUD op queued by the webcc Messages modal. Returns (ok, info)."""
    op = str(op or "")
    payload = payload if isinstance(payload, dict) else {}
    if op == "add":
        if len(_server_messages) >= MSG_MAX_COUNT:
            return False, f"message limit reached ({MSG_MAX_COUNT})"
        rec = _msg_clean(payload)
        if not rec:
            return False, "empty message text"
        rec["id"] = _new_msg_id()
        _server_messages.append(rec)
        save_server_messages()
        return True, f"added ({rec['trigger']})"
    mid = str(payload.get("id") or "")
    m = _msg_find(mid)
    if op == "delete":
        if not m:
            return False, "not found"
        _server_messages.remove(m)
        _msg_last_fired.pop(mid, None)
        _msg_last_day.pop(mid, None)
        save_server_messages()
        return True, "deleted"
    if op == "toggle":
        if not m:
            return False, "not found"
        m["enabled"] = bool(payload.get("on", not m["enabled"]))
        save_server_messages()
        return True, ("enabled" if m["enabled"] else "disabled")
    if op == "update":
        if not m:
            return False, "not found"
        merged = dict(m)
        for k in ("text", "trigger", "interval_min", "at", "color", "enabled"):
            if k in payload:
                merged[k] = payload[k]
        rec = _msg_clean(merged)
        if not rec:
            return False, "empty message text"
        rec["id"] = m["id"]
        _server_messages[_server_messages.index(m)] = rec
        save_server_messages()
        return True, "updated"
    return False, f"unknown op {op}"


def _msg_fire(rc, m):
    text = m.get("text") or ""
    color = m.get("color") or ""
    line = f"<color={color}>{text}</color>" if color else text
    try:
        rc.say(line)
    except Exception as e:                           # noqa: BLE001  (never break the loop on a chat hiccup)
        print(f"[servermsg] say error: {e}")


def check_server_messages(rc, now, online, state):
    """Time-based triggers (interval + daily clock). Call each loop tick while players are online."""
    if not online or not _server_messages:
        return
    lt = time.localtime(now)
    today = time.strftime("%Y-%m-%d", lt)
    hhmm = time.strftime("%H:%M", lt)
    for m in _server_messages:
        if not m.get("enabled"):
            continue
        trig = m.get("trigger")
        if trig == "interval":
            if m["id"] not in _msg_last_fired:
                _msg_last_fired[m["id"]] = now       # seed: first fire is one full interval after creation/boot
                continue
            iv = max(1, int(m.get("interval_min", 30))) * 60
            if state == "IDLE" and now - _msg_last_fired[m["id"]] >= iv:
                _msg_last_fired[m["id"]] = now
                _msg_fire(rc, m)
        elif trig == "clock":
            if hhmm == m.get("at") and _msg_last_day.get(m["id"]) != today:
                _msg_last_day[m["id"]] = today
                _msg_fire(rc, m)


def fire_event_messages(rc, event):
    """Event triggers (match_start / match_end). Fires only while players are present."""
    if not ROSTER_BY_SID or not _server_messages:
        return
    for m in _server_messages:
        if m.get("enabled") and m.get("trigger") == event:
            _msg_fire(rc, m)


load_server_messages()
load_rank_ladder()
load_shared_ranks_cfg()
_start_shared_pub_worker()                       # #2: start the off-loop shared-ranks publisher daemon


# --- Anti-grief reports: the plugin emits "[NOSTATS] {t:report}" when it auto-kicks/flags a single
# connection flooding unit-commands to brick the server. The bot records them (plugin_reports.json) for the
# webcc Reports tab + a one-click Ban (which drops a plugin ban| command -> immediate _tkBanned + kick).
REPORTS_FILE = os.path.join(_BASE_DIR, "plugin_reports.json")
REPORTS_MAX = 200
_reports = []          # [{seq,id,name,reason,count,rate,action,ts,banned}]
_report_seq = 0


def load_reports():
    global _reports, _report_seq
    try:
        with open(REPORTS_FILE, encoding="utf-8") as f:
            _reports = json.load(f).get("reports", [])
    except (OSError, ValueError):
        _reports = []
    _report_seq = max([r.get("seq", 0) for r in _reports], default=0)


def save_reports():
    try:
        tmp = REPORTS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"reports": _reports[-REPORTS_MAX:]}, f, indent=1)
        os.replace(tmp, REPORTS_FILE)
    except OSError:
        pass


# --- Ban log: a persistent record of every ban an operator logs from the Reports tab, keyed by SteamID,
# so REPEAT offenders (banned more than once) are visible across matches/restarts. This is the audit trail,
# separate from the live plugin/game enforcement ban lists.
BAN_LOG_FILE = os.path.join(_BASE_DIR, "ban_log.json")
_ban_log = {}          # sid -> {"name": str, "entries": [{"ts": int, "reason": str}]}


def load_ban_log():
    global _ban_log
    try:
        with open(BAN_LOG_FILE, encoding="utf-8") as f:
            j = json.load(f)
        _ban_log = j if isinstance(j, dict) else {}
    except (OSError, ValueError):
        _ban_log = {}


def save_ban_log():
    try:
        tmp = BAN_LOG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_ban_log, f, indent=1)
        os.replace(tmp, BAN_LOG_FILE)
    except OSError:
        pass


def log_ban(sid, name, reason):
    """Append a ban event under this SteamID; returns the player's total logged-ban count."""
    sid = str(sid or "").strip()
    if not sid:
        return 0
    rec = _ban_log.setdefault(sid, {"name": "", "entries": []})
    if name:
        rec["name"] = str(name)
    rec["entries"].append({"ts": int(time.time()), "reason": str(reason or "")[:200]})
    rec["entries"] = rec["entries"][-50:]      # cap per-player history
    save_ban_log()
    return len(rec["entries"])


def ban_log_state():
    """Summary for the webcc, repeat offenders first."""
    out = []
    for sid, rec in _ban_log.items():
        ents = rec.get("entries", []) if isinstance(rec, dict) else []
        if not ents:
            continue
        out.append({"id": sid, "name": rec.get("name", "") or sid, "count": len(ents),
                    "last_ts": ents[-1].get("ts", 0), "last_reason": ents[-1].get("reason", "")})
    out.sort(key=lambda x: (x["count"], x["last_ts"]), reverse=True)
    return out


def remove_ban_log(sid):
    """Delete a player's whole ban-log history (the webcc 🗑 button). Returns True if anything was removed.
    This is SEPARATE from clearing reports -- the moderation 'Clear all' only touches reports, never this log."""
    sid = str(sid or "").strip()
    if sid and sid in _ban_log:
        _ban_log.pop(sid, None)
        save_ban_log()
        return True
    return False


load_ban_log()


def add_report(rec):
    global _report_seq, _reports
    _report_seq += 1
    rec["seq"] = _report_seq
    rec.setdefault("banned", False)
    _reports.append(rec)
    _reports = _reports[-REPORTS_MAX:]
    save_reports()


def reports_state():
    return list(reversed(_reports[-REPORTS_MAX:]))   # newest first for the webcc


def set_report_banned(sid, banned):
    changed = False
    for r in _reports:
        if r.get("id") == sid:
            r["banned"] = bool(banned)
            changed = True
    if changed:
        save_reports()
    return changed


_banned_cache = {"ts": 0.0, "players": []}


def refresh_banned_players():
    """Merge the PLUGIN ban list (plugin_bans.txt) + the GAME-native ban list (ban_list.txt) into
    [{id,name,lists}] for the webcc Moderation 'Banned' tab. Read-only over SFTP; cached. NOTE: an
    in-memory-only game ban (e.g. a fresh votekick not yet written to file) may not appear here -- the
    'Unban by SteamID' box handles those (it sends banlist-remove regardless)."""
    raw = {"plugin": [], "game": []}

    def _read(sftp, path):
        try:
            with sftp.open(path, "rb") as f:
                return [ln.strip() for ln in f.read().decode("utf-8", "replace").splitlines() if ln.strip()]
        except IOError:
            return []

    def _op(sftp):
        raw["plugin"] = _read(sftp, "plugin_bans.txt")
        raw["game"] = _read(sftp, "ban_list.txt")     # game-native; lines may be "<sid> [reason]"

    try:
        _sftp_op(_op)
    except Exception:                              # noqa: BLE001
        pass
    pset = {ln.split()[0] for ln in raw["plugin"] if ln.split()}
    gset = {ln.split()[0] for ln in raw["game"] if ln.split()}
    players = []
    for sid in sorted(pset | gset):
        nm = (RANK_DATA.get(sid, {}) or {}).get("name") or PLAYER_NAMES.get(sid) or ""
        lists = (["plugin"] if sid in pset else []) + (["game"] if sid in gset else [])
        players.append({"id": sid, "name": nm, "lists": lists})
    _banned_cache.update({"ts": time.time(), "players": players})
    return players


def banned_players_state():
    return list(_banned_cache.get("players", []))


def clear_report(seq):
    """Remove ONE report by its unique seq (webcc Reports 'Clear'). The bot is the single writer of
    plugin_reports.json, so removing it here means /api/state stops re-serving it on the next poll."""
    global _reports
    before = len(_reports)
    _reports = [r for r in _reports if r.get("seq") != seq]
    if len(_reports) != before:
        save_reports()
    return before - len(_reports)


def clear_all_reports():
    """Clear ALL reports (webcc Reports 'Clear all')."""
    global _reports
    n = len(_reports)
    if n:
        _reports = []
        save_reports()
    return n


load_reports()


# --- Anti-grief: command-flood (rate-limit storm) auto-kick --------------------
# The game rate-limits each connection's UnitCommand RPCs and logs one
#   [RateLimitAttribute] connection(SteamConnection(<sid>)) RPC rate limit exceeded for '<rpc>', dropping call
# line for EVERY dropped call. A legit player generates ~0 of these (the game
# allows a ~20 burst + ~5/s); a macro/exploit move-flood generates dozens/sec.
# A sustained storm from ONE connection is a near-zero-false-positive griefer
# fingerprint -> file a Reports-tab entry + auto-kick the offender (recoverable).
# This is the RELIABLE detector: the plugin's GriefTick order-rate check sits
# DOWNSTREAM of the game's limiter, so it only ever sees the ~5/s that pass
# through and can't reach a flood threshold. We read the game's own drop lines
# (the TRUE flood intensity) instead. Tunable via grief_flood.json (edit + restart
# the bot). Defaults are deliberately conservative.
GRIEF_CFG_FILE = os.path.join(_BASE_DIR, "grief_flood.json")
_GRIEF_FLOOD_DEFAULTS = {
    "enabled": True,
    "action": "kick",        # "kick" (recoverable) | "ban" | "report" (detect-only, no removal)
    "drops_per_window": 30,  # trip when ONE sid exceeds this many rate-limit drops...
    "window_sec": 3.0,       # ...within this rolling window (30/3s ~= 25 cmd/s OVER the cap -> macro only)
    "cooldown_sec": 30.0,    # don't re-act on the same sid within this many seconds
    "exempt_admins": True,   # never auto-kick an ADMIN_SIDS member (set false to self-test)
    # ONLY these RPCs (matched on the name suffix) can trigger a flood-kick. Griefing is a unit MOVE-order
    # storm (CmdSetDestination). A server-wide drop storm on a NON-command RPC like CmdUpdateTrackingInfo is
    # network congestion (a BufferFull blip), NOT grief, and must never mass-kick -- the 2026-06-30 incident
    # kicked ~15 players at once on CmdUpdateTrackingInfo. Empty list [] = allow any RPC (legacy behaviour).
    "rpc_allow": ["CmdSetDestination"],
    # Circuit breaker: if this many DIFFERENT players trip within breaker_window_sec, it's a server-wide
    # storm -> suppress ALL flood-kicks (never amplify a congestion event into a mass-kick). 0 = disabled.
    "breaker_distinct": 3,
    "breaker_window_sec": 6.0,
}


def _load_grief_flood():
    cfg = dict(_GRIEF_FLOOD_DEFAULTS)
    try:
        with open(GRIEF_CFG_FILE, encoding="utf-8") as f:
            j = json.load(f)
        if isinstance(j, dict):
            for k in cfg:
                if k in j:
                    cfg[k] = j[k]
    except (OSError, ValueError):
        pass
    return cfg


_GRIEF_FLOOD = _load_grief_flood()

RATELIMIT_DROP_RE = re.compile(
    r"\[RateLimitAttribute\]\s+connection\(SteamConnection\((\d{6,20})\)\)\s+RPC rate limit exceeded for '([^']+)'")

_rl_drops = {}   # sid -> [timestamps within the rolling window]
_rl_acted = {}   # sid -> last auto-action time (cooldown gate)
_rl_trips = {}   # sid -> last trip time (for the server-wide circuit breaker)
_rl_storm_at = 0.0   # last time a storm-suppression line was logged (throttle)


def note_ratelimit_drop(sid, rpc, now):
    """One rate-limit-drop line seen for `sid`. Trip the auto-kick only on a SUSTAINED, SINGLE-connection
    move-order storm: drops on non-command RPCs (congestion, e.g. CmdUpdateTrackingInfo) are ignored, and a
    near-simultaneous storm across many players is suppressed by the circuit breaker (server congestion)."""
    global _rl_storm_at
    cfg = _GRIEF_FLOOD
    if not cfg.get("enabled", True) or not sid:
        return
    short = rpc.split(".")[-1] if rpc else ""
    allow = cfg.get("rpc_allow") or []
    if allow and short not in allow:
        return                          # not a grief (move-order) RPC -> a drop here is congestion, never a kick
    try:
        win = float(cfg.get("window_sec", 3.0))
        thr = int(cfg.get("drops_per_window", 30))
        cooldown = float(cfg.get("cooldown_sec", 30.0))
    except (TypeError, ValueError):
        return
    dq = _rl_drops.setdefault(sid, [])
    dq.append(now)
    cutoff = now - win
    while dq and dq[0] < cutoff:
        dq.pop(0)
    if len(dq) < thr:
        return
    if now - _rl_acted.get(sid, 0) < cooldown:
        return
    if cfg.get("exempt_admins", True) and sid in ADMIN_SIDS:
        return
    _rl_acted[sid] = now
    n = len(dq)
    dq.clear()
    # circuit breaker: a near-simultaneous trip by many DISTINCT players = server-wide congestion, not grief.
    try:
        bwin = float(cfg.get("breaker_window_sec", 6.0))
        bdist = int(cfg.get("breaker_distinct", 3))
    except (TypeError, ValueError):
        bwin, bdist = 6.0, 3
    _rl_trips[sid] = now
    for s in [s for s, t in _rl_trips.items() if now - t > bwin]:
        _rl_trips.pop(s, None)
    if bdist > 0 and len(_rl_trips) >= bdist:
        if now - _rl_storm_at > 30:
            _rl_storm_at = now
            activity(f"Command-flood STORM: {len(_rl_trips)} players hit the rate-limiter together "
                     f"-> treated as server congestion (not grief); auto-kicks SUPPRESSED", "!")
        return
    _grief_flood_act(sid, rpc, n, win)


def _grief_flood_act(sid, rpc, n, win):
    cfg = _GRIEF_FLOOD
    action = str(cfg.get("action", "kick")).lower()
    if action not in ("kick", "ban", "report"):
        action = "kick"
    who = (PLAYER_NAMES.get(sid) or RANK_DATA.get(sid, {}).get("name") or sid)
    short = rpc.split(".")[-1] if rpc else "RPC"
    try:
        rate = round(n / max(0.1, win), 1)
    except (TypeError, ValueError):
        rate = 0
    add_report({
        "id": sid, "name": who, "reason": f"command-flood (rate-limit storm on {short})",
        "count": n, "rate": rate, "action": action, "ts": time.time(),
        "banned": (action == "ban"),
    })
    try:
        if action == "kick":
            _drop_plugin_cmd("kick|" + sid)
            activity(f"AUTO-KICK {who} - command flood ({n} drops/{win:g}s on {short})", "!")
        elif action == "ban":
            _drop_plugin_cmd("ban|" + sid)
            set_report_banned(sid, True)
            activity(f"AUTO-BAN {who} - command flood ({n} drops/{win:g}s on {short})", "!")
        else:
            activity(f"FLOOD REPORT {who} - command flood ({n} drops/{win:g}s on {short})", "!")
    except Exception as e:   # noqa: BLE001
        print(f"[grief-flood] action error: {e}")
    print(f"[grief-flood] {action} {sid} ({who}) {n} drops/{win:g}s on {rpc}")


# --- System messages: owner overrides (enable / text / interval / delay) for the BUILT-IN automated
# messages (join/welcome, the periodic "thanks", the auto leaderboard post, the spectate tip). Stored in
# system_messages.json; the webcc Messages tab edits them. Defaults preserve current behaviour.
SYSMSG_FILE = os.path.join(_BASE_DIR, "system_messages.json")
_sysmsg = {}
# (key, label, has_text, default_text, has_interval, default_interval, has_delay, default_delay, note)
_SYSMSG_DEFS = [
    ("welcome", "Join / welcome message", True,
     "", False, 0, True, WELCOME_DELAY,
     "Posted ~delay seconds after a player joins (shows their rank + points). A custom text REPLACES the "
     "default line; placeholders {name} {rank} {pts} are filled in."),
    ("thanks", "“Thanks for playing” reminder", True,
     "<color=#FFD200>Thanks for playing!</color> For a list of commands type <color=#55FF55>!help</color>",
     True, THANKS_INTERVAL, False, 0, "A periodic friendly nudge to all players while the server is active."),
    ("leaderboard", "Auto leaderboard post", False, "", True, LEADERBOARD_INTERVAL, False, 0,
     "Posts the top-5 by points + skill to chat on this interval."),
    ("spectip", "Spectate / team-switch tip", False, "", True, SPECTIP_INTERVAL, False, 0,
     "Shows how to spectate / switch to the smaller team (PvP matches only)."),
]

# ── !help command editor (#6) ──────────────────────────────────────────────────────────────────────
# The in-game !help list is built from this registry. Each command's LINE TEXT is editable (stored in the
# sysmsg store under "help_<id>", but deliberately NOT in _SYSMSG_DEFS so it doesn't clutter the automated
# Messages list) and each command can be SHOWN/HIDDEN. "Auto-hide when a feature is off" is authoritative
# for votemap (reads the votemap kill-switch); plugin-owned commands (spec/swapteam/squadup/forfeit) can be
# hidden from the LIST here, but the plugin still answers them until a future plugin flag (display-only).
#   entry = (id, group, color_hex, label_default, gate_default, gate_kind)
#   gate_kind: "bot" enforced toggle | "votemap" -> _votemap_cfg()["enabled"] | "plugin" display-only
#              | "always_on" (help) | the label_default carries its own <color> tags (verbatim current text)
HELP_CFG_FILE = os.path.join(_BASE_DIR, "help_config.json")
_HELP_REGISTRY = [
    ("rank",        "stats", "#55FF55", "<color=#55FF55>!rank</color> - rank & points",                        True, "bot"),
    ("skill",       "stats", "#55FF55", "<color=#55FF55>!skill</color> - average points per life rating",      True, "bot"),
    ("points",      "stats", "#55FF55", "<color=#55FF55>!points</color> - life points",                        True, "bot"),
    ("leaderboard", "stats", "#55FF55", "<color=#55FF55>!leaderboard</color> - top pilots",                    True, "bot"),
    ("spec",        "teams", "#36FFD0", "<color=#36FFD0>!spec</color> - spectate",                             True, "plugin"),
    ("swapteam",    "teams", "#36FFD0", "<color=#36FFD0>!swapteam</color> - switch to the smaller team",       True, "plugin"),
    ("balance",     "teams", "#36FFD0", "<color=#36FFD0>!balance</color> - how balancing works",               True, "bot"),
    ("squadup",     "teams", "#36FFD0", "<color=#36FFD0>!squadup <player></color> - squad up (PvP)",           True, "plugin"),
    ("votemap",     "match", "#FFC857", "<color=#FFC857>!votemap</color> - vote a new map",                    True, "votemap"),
    ("forfeit",     "match", "#FFC857", "<color=#FFC857>!forfeit</color> - surrender (PvP)",                   True, "plugin"),
    ("notk",        "info",  "#cfd8e3", "<color=#cfd8e3>!notk</color> - no team-killing",                      True, "bot"),
    ("help",        "info",  "#cfd8e3", "<color=#cfd8e3>!help</color> - this list",                            True, "always_on"),
]
_HELP_GROUP_ORDER   = ("stats", "teams", "match", "info")
_HELP_DEFAULT_GATES = {e[0]: e[4] for e in _HELP_REGISTRY}
# editable text keys (live in the sysmsg store, not the automated Messages list)
_HELP_TEXT_DEFAULTS = {("help_" + e[0]): e[3] for e in _HELP_REGISTRY}
_HELP_TEXT_DEFAULTS["help_header"] = "<color=#FFFF00>=== SERVER COMMANDS ===</color>"

_SYSMSG_KEYS = {d[0] for d in _SYSMSG_DEFS} | set(_HELP_TEXT_DEFAULTS)


def load_sysmsg():
    global _sysmsg
    try:
        with open(SYSMSG_FILE, encoding="utf-8") as f:
            _sysmsg = json.load(f)
        if not isinstance(_sysmsg, dict):
            _sysmsg = {}
    except (OSError, ValueError):
        _sysmsg = {}


def save_sysmsg():
    try:
        tmp = SYSMSG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_sysmsg, f, indent=1)
        os.replace(tmp, SYSMSG_FILE)
    except OSError:
        pass


def _sysmsg_rec(key):
    v = _sysmsg.get(key)
    return v if isinstance(v, dict) else {}


def sysmsg_on(key):
    return bool(_sysmsg_rec(key).get("enabled", True))


def sysmsg_text(key, default):
    t = _sysmsg_rec(key).get("text")
    return t if isinstance(t, str) and t.strip() else default


def sysmsg_interval(key, default):
    try:
        i = float(_sysmsg_rec(key).get("interval"))
        return i if i > 0 else default
    except (TypeError, ValueError):
        return default


def sysmsg_delay(key, default):
    try:
        d = float(_sysmsg_rec(key).get("delay"))
        return d if d >= 0 else default
    except (TypeError, ValueError):
        return default


def sysmsg_set(key, fields):
    if key not in _SYSMSG_KEYS:
        return False
    v = dict(_sysmsg_rec(key))
    if "enabled" in fields:
        v["enabled"] = bool(fields["enabled"])
    if "text" in fields:
        v["text"] = _msg_sanitize_text(str(fields["text"] or ""))   # tag-safe trim (don't slice a <color=> tag)
    if "interval" in fields:
        try:
            v["interval"] = max(10.0, float(fields["interval"]))
        except (TypeError, ValueError):
            pass
    if "delay" in fields:
        try:
            v["delay"] = max(0.0, min(120.0, float(fields["delay"])))
        except (TypeError, ValueError):
            pass
    _sysmsg[key] = v
    save_sysmsg()
    return True


def sysmsg_state():
    out = []
    for (key, label, has_text, dtext, has_int, dint, has_delay, ddelay, note) in _SYSMSG_DEFS:
        v = _sysmsg_rec(key)
        out.append({"key": key, "label": label, "enabled": bool(v.get("enabled", True)),
                    "has_text": has_text, "text": v.get("text", "") if has_text else "", "default_text": dtext,
                    "has_interval": has_int, "interval": sysmsg_interval(key, dint) if has_int else 0,
                    "has_delay": has_delay, "delay": sysmsg_delay(key, ddelay) if has_delay else 0,
                    "note": note})
    return out


load_sysmsg()


def _help_cfg():
    gates = dict(_HELP_DEFAULT_GATES)
    try:
        with open(HELP_CFG_FILE, encoding="utf-8") as f:
            j = json.load(f)
        if isinstance(j, dict) and isinstance(j.get("gates"), dict):
            for k, val in j["gates"].items():
                if k in gates:
                    gates[k] = bool(val)
    except (OSError, ValueError):
        pass
    return {"gates": gates}


def set_help_gate(cmd_id, on):
    if cmd_id not in _HELP_DEFAULT_GATES or cmd_id in ("help", "votemap"):
        return False                                    # help is always shown; votemap follows its kill-switch
    cfg = _help_cfg()
    cfg["gates"][cmd_id] = bool(on)
    try:
        tmp = HELP_CFG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"gates": cfg["gates"]}, f, indent=1)
        os.replace(tmp, HELP_CFG_FILE)
    except OSError:
        return False
    return True


def _help_gate_open(entry, hcfg, vm_enabled=None):
    """Is this command currently shown in the !help list? vm_enabled lets the caller pass the votemap
    kill-switch once (avoids a votemap_config.json read per command on the ~1Hz dashboard path)."""
    cmd_id, kind = entry[0], entry[5]
    if kind == "always_on":
        return True
    if kind == "votemap":                                # authoritative: track the votemap kill-switch
        return bool(_votemap_cfg()["enabled"] if vm_enabled is None else vm_enabled)
    return bool(hcfg["gates"].get(cmd_id, True))         # bot-enforced + plugin display-only toggles


def help_state():
    """Rows for the webcc Help editor: each command's group/colour, editable text (raw; empty == default),
    and whether it's currently shown. The header line is editable too."""
    hcfg = _help_cfg()
    vm_enabled = _votemap_cfg()["enabled"]
    rows = []
    for e in _HELP_REGISTRY:
        cmd_id, grp, col, lbl_default, _gd, kind = e
        rows.append({"cmd": cmd_id, "group": grp, "color": col, "kind": kind,
                     "sysmsg_key": "help_" + cmd_id, "label_default": lbl_default,
                     "text": _sysmsg_rec("help_" + cmd_id).get("text", ""),
                     "shown": _help_gate_open(e, hcfg, vm_enabled),
                     "gate_locked": cmd_id in ("help", "votemap")})
    return {"rows": rows, "order": list(_HELP_GROUP_ORDER),
            "header": _sysmsg_rec("help_header").get("text", ""),
            "header_default": _HELP_TEXT_DEFAULTS["help_header"]}


def _as_ballot(missions):
    """Turn an ordered list of mission names into a {"1": (...), ...} ballot."""
    return {str(i): (MISSION_GROUP, n, MISSION_MAX_TIME, friendly_label(n))
            for i, n in enumerate(missions, start=1)}


# Remember the previous ballot's mission set so we don't offer the exact same maps twice in a row.
_prev_ballot_set = None


def _votemap_pool():
    """The dynamic vote pool grouped by category: enabled co-op missions + (per config) enabled custom
    USER missions. PvP options are appended separately by open_vote."""
    cfg = _votemap_cfg()
    pool = {}
    e = [m for m in ESCALATION_MISSIONS if mission_enabled(m)]
    t = [m for m in TERMINAL_CONTROL_MISSIONS if mission_enabled(m)]
    if e:
        pool["Escalation"] = e
    if t:
        pool["Terminal Control"] = t
    if cfg["include_custom"]:
        c = [n for n in _enabled_custom_names() if mission_enabled(n)]
        if c:
            pool["Custom"] = c
    return pool


def _weighted_sample(items, weights, keyfn, n):
    """Pick up to n of items WITHOUT replacement by relative weight. A missing key defaults to 1.0; an
    explicit 0 excludes (unless every remaining item is 0, then it falls back to uniform among them)."""
    pool = list(items)
    n = min(n, len(pool))
    out = []
    while len(out) < n and pool:
        ws = []
        for it in pool:
            w = weights.get(keyfn(it), 1.0)
            ws.append(w if (isinstance(w, (int, float)) and w > 0) else 0.0)
        tot = sum(ws)
        if tot <= 0:
            pick = random.choice(pool)
        else:
            r = random.uniform(0, tot); acc = 0.0; pick = pool[-1]
            for it, w in zip(pool, ws):
                acc += w
                if r <= acc:
                    pick = it
                    break
        out.append(pick)
        pool.remove(pick)
    return out


# Friendly labels for the built-in PvP modes (these carry the [PVP] / descriptor tag).
_PVP_LABEL = {p[1]: p[2] for p in PVP_OPTIONS}

# Recent winning maps (newest last); open_vote keeps the last avoid_recent of them off the co-op fill.
_recent_winners = []


def _votable_names():
    """Every mission NAME that can legitimately appear on a ballot: the co-op variants + the PvP modes +
    enabled custom USER missions. Used to validate guaranteed pins."""
    names = {n for n, _ in _all_pool_missions()}
    names.update(_enabled_custom_names())
    return names


def _ballot_entry(name):
    """(group, name, max_time, label) for a votable mission name (co-op variant, custom, or PvP mode)."""
    label = _PVP_LABEL.get(name) or friendly_label(name)
    return (mission_group(name), name, MISSION_MAX_TIME, label)


def _coop_cat(name):
    """Which co-op category a mission name belongs to (matches _votemap_pool() keys)."""
    if name in ESCALATION_MISSIONS:
        return "Escalation"
    if name in TERMINAL_CONTROL_MISSIONS:
        return "Terminal Control"
    return "Custom"


def build_coop(prev_set, target, cfg, exclude):
    """Return (names, chosen_set) for the random CO-OP/custom portion: `target` maps from _votemap_pool()
    minus `exclude` (guaranteed maps already placed + recently-played maps), honouring coop_mode:
        balanced -> even round-robin across categories (Escalation / Terminal Control / Custom)
        random   -> uniform across the flat pool
        weighted -> pick a category per slot by coop_weights, then a random map from it
    Keeps at most MAX_DARK_PER_VOTE 'dark' maps and avoids the exact previous set when possible. Returns
    ([], frozenset()) when there's nothing to pick. open_vote() assembles the full ordered ballot."""
    mode = cfg["coop_mode"]
    weights = cfg["coop_weights"]
    pool = {c: [n for n in ms if n not in exclude] for c, ms in _votemap_pool().items()}
    pool = {c: ms for c, ms in pool.items() if ms}
    flat = [n for ms in pool.values() for n in ms]
    if target <= 0 or not flat:
        return [], frozenset()
    cats = list(pool.keys())
    target = min(target, len(flat))

    def _pick():
        if mode == "random" or len(cats) <= 1:
            return random.sample(flat, target)
        bins = {c: random.sample(pool[c], len(pool[c])) for c in cats}   # shuffled copy per category
        chosen = []
        if mode == "weighted":
            while len(chosen) < target:
                live = [c for c in cats if bins[c]]
                if not live:
                    break
                cat = _weighted_sample(live, weights, lambda c: c, 1)[0]
                chosen.append(bins[cat].pop())
            return chosen
        i = 0                                                            # balanced round-robin
        while len(chosen) < target:
            b = bins[cats[i % len(cats)]]; i += 1
            if b:
                chosen.append(b.pop())
            if all(not bins[c] for c in cats):
                break
        return chosen

    # Previous ballot's per-category subsets (>=2 maps): avoid re-offering a whole family pair two votes
    # in a row, which the exact-full-set check alone misses (it only rejects when ALL slots repeat).
    prev_by_cat = {}
    if prev_set:
        for nm in prev_set:
            prev_by_cat.setdefault(_coop_cat(nm), set()).add(nm)
        prev_by_cat = {c: frozenset(s) for c, s in prev_by_cat.items() if len(s) >= 2}

    def _family_repeat(chosen):
        by_cat = {}
        for nm in chosen:
            by_cat.setdefault(_coop_cat(nm), set()).add(nm)
        return any(frozenset(by_cat.get(c, ())) == sub for c, sub in prev_by_cat.items())

    best = None
    for _ in range(400):
        chosen = _pick()
        if sum(is_dark(n) for n in chosen) > MAX_DARK_PER_VOTE:
            continue
        best = chosen                                          # a dark-cap-valid fallback if we can't do better
        if prev_set is None or (frozenset(chosen) != prev_set and not _family_repeat(chosen)):
            return chosen, frozenset(chosen)
    chosen = best if best is not None else _pick()   # over the dark cap with no alternative, or a forced repeat
    return chosen, frozenset(chosen)


def _pick_pvp(n, cfg, exclude=()):
    """Pick up to n PvP built-in mode NAMES, only from those toggled ON in the mission pool and not in
    `exclude`. Decoupled from how many modes are enabled: 'fixed' keeps the historical leading pair
    (Escalation + Terminal Control) regardless of how many extra modes are enabled."""
    enabled = [p[1] for p in PVP_OPTIONS if mission_enabled(p[1]) and p[1] not in exclude]
    if n <= 0 or not enabled:
        return []
    mode = cfg["pvp_mode"]
    if mode == "random":
        return random.sample(enabled, min(n, len(enabled)))
    if mode == "weighted":
        return _weighted_sample(enabled, cfg["pvp_weights"], lambda x: x, n)
    return enabled[:n]                               # fixed: PVP_OPTIONS order (Escalation, Terminal Control, ...)


def open_vote(online_count=0):
    """Build a fresh ballot into VOTE_OPTIONS. Layout: [guaranteed co-op][random co-op][guaranteed PvP]
    [random PvP], numbered 1..N. coop_count + pvp_count size the two pools independently (default 4 + 2 =
    the regular 6). Guaranteed missions are always pinned and count toward their type's slot count (a
    generalisation of the always-on PvP pair). A high-population rule can override the split into a
    PvP-heavy ballot; avoid_recent keeps the last N winners off the random co-op fill."""
    global VOTE_OPTIONS, _prev_ballot_set
    cfg = _votemap_cfg()
    coop_n = cfg["coop_count"]
    pvp_n  = cfg["pvp_count"] if cfg["include_pvp"] else 0
    if cfg["force_pvp_enabled"] and online_count >= cfg["force_pvp_players"]:
        coop_n = cfg["force_pvp_coop"]
        pvp_n  = cfg["force_pvp_pvp"] if cfg["include_pvp"] else 0

    # guaranteed pins: keep only those still enabled + valid, deduped, in config order
    votable = _votable_names()
    guaranteed = [n for n in cfg["guaranteed"] if mission_enabled(n) and n in votable]
    g_coop = [n for n in guaranteed if n not in PVP_MISSIONS]
    g_pvp  = [n for n in guaranteed if n in PVP_MISSIONS]

    avoid = set(_recent_winners[-cfg["avoid_recent"]:]) if cfg["avoid_recent"] else set()

    coop_fill = max(0, coop_n - len(g_coop))
    coop_names, _prev_ballot_set = build_coop(_prev_ballot_set, coop_fill, cfg, set(g_coop) | avoid)
    pvp_names = _pick_pvp(max(0, pvp_n - len(g_pvp)), cfg, exclude=set(g_pvp))

    ordered = g_coop + coop_names + g_pvp + pvp_names
    if not ordered:                                  # safety net: fill from ENABLED co-op missions only (never strand the map on a removed one)
        coop_pool = [m for m in (ESCALATION_MISSIONS + TERMINAL_CONTROL_MISSIONS) if mission_enabled(m)]
        if not coop_pool:                            # nothing enabled at all -> leave the ballot empty; server rotation advances
            VOTE_OPTIONS = {}
            return VOTE_OPTIONS
        ordered = random.sample(coop_pool, min(4, len(coop_pool)))
    VOTE_OPTIONS = {str(i): _ballot_entry(n) for i, n in enumerate(ordered, start=1)}
    return VOTE_OPTIONS


def announce_options(rc):
    rc.say(f"<color=#FFFF00>=== NEXT MAP VOTE ===</color> "
           f"type <color=#55FF55>!1</color>-<color=#55FF55>!{len(VOTE_OPTIONS)}</color> in chat ({VOTE_DURATION}s)")
    for k in VOTE_OPTIONS:
        rc.say(f"  <color=#55FF55>!{k}</color> = {VOTE_OPTIONS[k][3]}")


def apply_winner(rc, votes, first_vote_at, force_switch=False):
    global CURRENT_MISSION
    if votes:
        tally = Counter(votes.values())
        top = max(tally.values())
        tied = [k for k, c in tally.items() if c == top]
        if len(tied) == 1:
            winner_key, source = tied[0], "vote"
        else:
            # tie-breaker: whichever tied map received its first vote earliest
            winner_key = min(tied, key=lambda k: first_vote_at.get(k, float("inf")))
            source = "vote (tie -> first voted)"
    else:
        if not VOTE_OPTIONS:                         # empty ballot (all missions disabled) -> nothing to apply; let server rotation advance
            rc.say("<color=#FFC83D>No eligible maps to vote on - the server rotation will pick the next mission.</color>")
            activity("Map vote had no eligible missions; left the next map to the server rotation", "MAP")
            return
        winner_key = random.choice(list(VOTE_OPTIONS))
        source = "random (no votes)"
    group, name, max_time, label = VOTE_OPTIONS[winner_key]
    rc.set_next_mission(group, name, max_time)
    _recent_winners.append(name)             # feed avoid_recent (keep a small rolling window)
    del _recent_winners[:-12]
    # Use the SAME canonical form refresh_current_mission() will settle on (friendly_label of the mission
    # name), NOT _plain(label) -- the ballot label carries a "[PVP]" suffix, so _plain(label) differs from the
    # refreshed value and the changing key would reset the mission-time-warning dedupe set, double-firing the
    # "Mission time: X remaining" line. Keeping the key stable across the refresh prevents that.
    CURRENT_MISSION = friendly_label(name)   # the mission the next match will run

    if force_switch:
        # mid-mission (!votemap) vote: cut the current mission over to the winner now.
        rc.set_time_remaining(ROLLOVER_SECONDS)
    summary = ", ".join(
        f"{VOTE_OPTIONS[k][3]}:{c}" for k, c in Counter(votes.values()).most_common()
    ) or "-"
    rc.say(f"<color=#55FF55>Winner: {label}</color> ({source}). Tally: {summary}")
    print(f"[vote] winner={label} via {source} tally={dict(Counter(votes.values()))}")
    if votes:
        activity(f"Next map: {_plain(label)}   (votes: {_plain(summary)})", "MAP")
    else:
        activity(f"No votes cast - picked {_plain(label)} at random", "MAP")


def mission_group(name):
    """Server-side group for a mission NAME: BuiltIn for the stock PvP option, User for every co-op map."""
    return "BuiltIn" if name in PVP_MISSIONS else MISSION_GROUP


def force_change_map(rc, name):
    """Admin (web CC 'Change map'): cut the LIVE match over to an explicit mission NOW. The caller
    (main loop) then suppresses the automatic mission-end vote so this choice sticks (no ballot override)."""
    global CURRENT_MISSION
    if not name:
        return
    group = mission_group(name)
    rc.set_next_mission(group, name, MISSION_MAX_TIME)       # queue it (3h)
    CURRENT_MISSION = friendly_label(name)                   # keep the warn-dedupe key stable
    rc.set_time_remaining(ROLLOVER_SECONDS)                  # force the cut now (same as a !votemap force-switch)
    rc.say(f"<color=#55FF55>Admin changed the map -> {friendly_label(name)}</color>")
    activity(f"ADMIN changed map -> {friendly_label(name)}", "MAP")
    print(f"[admin] force-change map -> {group}/{name}")


_RANK_TAG_RE = None


def _strip_rank_tag(name):
    """Remove a leading '[ABBR] ' rank tag that the NukeStats plugin (RankInName mode)
    embeds into the in-game name. The dedicated-server roster (get-player-list displayName)
    reports that tagged name, so without this the tag would leak into PLAYER_NAMES and
    ranks.json and break welcome/!rank/resolve_player. Only strips a KNOWN rank abbreviation
    so a real bracketed name (e.g. a clan tag) is left untouched."""
    global _RANK_TAG_RE
    if not name:
        return name
    if _RANK_TAG_RE is None:
        # match either the short abbr (kill-feed) OR the full rank name (chat tag), longest first
        tags = sorted({str(r[2]) for r in RANKS} | {str(r[1]) for r in RANKS}, key=len, reverse=True)
        _RANK_TAG_RE = re.compile(r"^\[(?:" + "|".join(re.escape(a) for a in tags) + r")\]\s(.+)$")
    m = _RANK_TAG_RE.match(name)
    return m.group(1) if m else name


def _extract_players(resp):
    """Pull the player-dict list out of a get-player-list reply, caching display
    names. Filters to dicts so a malformed reply can't crash downstream p.get()."""
    if isinstance(resp, dict):
        raw = resp.get("Players") or resp.get("players")
        if isinstance(raw, list):
            players = [p for p in raw if isinstance(p, dict)]
            for p in players:
                nm = _strip_rank_tag(p.get("displayName"))
                if nm is not None:
                    p["displayName"] = nm          # clean the dict so ROSTER_BY_SID/tables match
                sid = str(p.get("steamId") or "")
                if sid and nm:
                    PLAYER_NAMES[sid] = nm
            return players
    return []


def get_players(rc):
    """Return the list of in-game player dicts (or []), caching display names."""
    return _extract_players(rc.get_player_list())


# ----------------------------------------------------------------------------
# Server-rank tracking (persisted in ranks.json, keyed by SteamID)
# ----------------------------------------------------------------------------

def load_ranks():
    global RANK_DATA
    try:
        with open(RANK_FILE, "r", encoding="utf-8") as f:
            RANK_DATA = json.load(f)
        if not isinstance(RANK_DATA, dict):          # corrupt/partial write or a wrong-file restore -> a list/None/str
            print(f"[ranks] {RANK_FILE} is not a JSON object ({type(RANK_DATA).__name__}); ignoring it, keeping the .bak")
            RANK_DATA = {}                            # every hot-path RANK_DATA.get()/.items() would AttributeError otherwise
        else:
            print(f"[ranks] loaded {len(RANK_DATA)} record(s) from {RANK_FILE}")
    except FileNotFoundError:
        RANK_DATA = {}
    except (json.JSONDecodeError, OSError) as e:
        print(f"[ranks] could not read {RANK_FILE} ({e}); starting fresh")
        RANK_DATA = {}


# bump the filename whenever the skill MODEL changes so a one-time clean recompute runs once.
# v087 = PERSISTENT points-per-DEATH: the bot accumulates a per-life score (curLife) from snap
# deltas that survives disconnects AND match-ends; a life is banked ONLY on death or mid-air eject
# (no match-end bank/eject; balance/admin moves are life-neutral). Old skillPoints/lives were
# computed under the previous match-bank rules, so zero them (and any partial curLife) once.
SKILL_RESET_FLAG = os.path.join(_BASE_DIR, "skill_reset_v087.done")


def maybe_reset_skills():
    """One-time: zero every pilot's skillPoints/lives so the rating recomputes cleanly
    under the new life logic (a life ends only on death/eject - no exit/dc/match). Runs once
    (guarded by a flag file); save_ranks() snapshots the old ranks.json first."""
    if os.path.exists(SKILL_RESET_FLAG):
        return
    n = 0
    for rec in RANK_DATA.values():
        if rec.get("skillPoints") or rec.get("lives") or rec.get("curLife"):
            rec["skillPoints"] = 0.0
            rec["lives"] = 0
            rec["curLife"] = 0.0          # drop any partial in-progress life
            n += 1
    save_ranks()
    try:
        with open(SKILL_RESET_FLAG, "w", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
    except OSError:
        pass
    activity(f"Skill ratings reset for {n} pilots (corrected scoring) - rebuilds from 5 lives", "INFO")
    print(f"[skill] one-time reset: zeroed {n} pilots")


def save_ranks():
    tmp = None
    try:
        # Before overwriting, keep a one-step undo (.bak) of the last known-good,
        # non-empty file plus a once-a-day snapshot. ranks.json is the lifetime
        # standings, so a bad/empty overwrite must never be silently unrecoverable.
        if os.path.exists(RANK_FILE):
            try:
                with open(RANK_FILE, encoding="utf-8") as f:
                    cur = json.load(f)
            except (OSError, json.JSONDecodeError):
                cur = None
            if isinstance(cur, dict) and cur:
                shutil.copyfile(RANK_FILE, RANK_FILE + ".bak")
                snap = os.path.join(os.path.dirname(RANK_FILE) or ".",
                                    f"ranks_backup_{time.strftime('%Y-%m-%d')}.json")
                if not os.path.exists(snap):
                    shutil.copyfile(RANK_FILE, snap)
        tmp = RANK_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(RANK_DATA, f, indent=2)
        for _attempt in range(5):              # Windows: dest may be briefly locked by a reader
            try:
                os.replace(tmp, RANK_FILE)
                break
            except PermissionError:
                if _attempt == 4:
                    raise
                time.sleep(0.04)
    except OSError as e:
        if tmp:
            try:
                os.remove(tmp)                  # don't leave a stale ranks.json.tmp behind
            except OSError:
                pass
        print(f"[ranks] save failed: {e}")
    maybe_publish_aggregate()                    # cross-server share: best-effort throttled publish (display only)


_LAST_RANK_SAVE = 0.0
def _maybe_save_ranks():
    """Throttle ranks.json writes from frequent score-accumulation events (>=5s apart).
    Important events (rank-ups, match end, awards) still call save_ranks() directly."""
    global _LAST_RANK_SAVE
    now = time.time()
    if now - _LAST_RANK_SAVE >= 5:
        save_ranks()
        _LAST_RANK_SAVE = now


def rank_index_for(points):
    idx = 0
    for i, r in enumerate(RANKS):
        if points >= r[0]:
            idx = i
        else:
            break
    return idx


def points_to_next(points):
    idx = rank_index_for(points)
    return round(RANKS[idx + 1][0] - points, 1) if idx + 1 < len(RANKS) else None


def rank_tag(points):
    _, _, abbr, color = RANKS[rank_index_for(points)]
    return f"<color={color}>[{abbr}]</color>"


def _team_colour(sid):
    """In-chat team colour for a player: PALA (Primeva) red, BDF (Boscali) blue."""
    f = (STATS_META.get(sid, {}).get("faction") or "").lower()
    if f == "primeva":
        return "#FF4444"      # PALA
    if f == "boscali":
        return "#4488FF"      # BDF
    return "#FFFFFF"


def kill_name(sid, fallback=""):
    """For the kill feed: rank tag (in its rank colour) + name (in team colour)."""
    name = PLAYER_NAMES.get(sid) or fallback or sid
    return f"{rank_tag(player_points(sid))} <color={_team_colour(sid)}>{name}</color>"


def _pts(n):
    """Points with one decimal for chat (e.g. '31.1 pts')."""
    return f"{n:.1f} pts"


def rank_progress(points):
    """Return (label, colour, tail) for a point total, e.g.
    ('[FLGOFF] Flying Officer', '#4C84E4', '3 pts to Flight Lieutenant').
    tail is 'top rank!' once the player is maxed out. Shared by !rank + joins."""
    idx = rank_index_for(points)
    _, rname, abbr, color = RANKS[idx]
    nxt = points_to_next(points)
    tail = "top rank!" if nxt is None else f"{_pts(nxt)} to {RANKS[idx + 1][1]}"
    return f"[{abbr}] {rname}", color, tail


def local_points(steamid):
    """This server's OWN lifetime points for the player (what ranks.json / the ledger hold)."""
    return RANK_DATA.get(str(steamid), {}).get("points", 0)


def player_points(steamid):
    """Points used for RANK DISPLAY: local points PLUS, when cross-server sharing is on, the points the
    player earned on the host's OTHER servers -> the SAME combined rank/points show on every server.
    Display only; the award + ledger path uses local_points() so ranks.json and --audit stay per-server."""
    sid = str(steamid)
    pts = RANK_DATA.get(sid, {}).get("points", 0)
    if SHARED_RANKS_ENABLED:
        try:
            pts = pts + _other_ranks().get(sid, 0)
        except Exception:        # noqa: BLE001 - rank display must never raise
            pass
    return pts


def combined_rankup(steamid, new_local_pts, delta):
    """#4 annIdx: gate rank-up announcements on the COMBINED (this server + the host's other servers)
    total when cross-server sharing is ON, so the announced rank matches the combined rank the player
    actually shows. Returns (crossed, new_idx) where new_idx indexes RANKS for the announcement.
    With sharing OFF other==0 -> identical to the local old_idx/new_idx gate. Never raises."""
    try:
        other = _other_ranks().get(str(steamid), 0) if SHARED_RANKS_ENABLED else 0
    except Exception:            # noqa: BLE001 - a rank-up gate must never raise into the hot path
        other = 0
    old_idx = rank_index_for((new_local_pts - delta) + other)
    new_idx = rank_index_for(new_local_pts + other)
    return (new_idx > old_idx), new_idx


def award_points(steamid, name, n):
    """Add n points to a player; return (old_rank_idx, new_rank_idx, new_points)."""
    sid = str(steamid)
    rec = RANK_DATA.setdefault(sid, {"name": name or sid, "points": 0})
    if name:
        rec["name"] = name
    old_idx = rank_index_for(rec.get("points", 0))
    rec["points"] = max(0.0, round(rec.get("points", 0) + n, 1))   # one decimal (real score is fractional); never negative
    return old_idx, rank_index_for(rec["points"]), rec["points"]


def ensure_player(steamid, name):
    """Make sure every player who's seen online has a record (rank 0 = Officer
    Cadet if they've never scored), so the roster isn't limited to point-earners.
    Returns True if RANK_DATA changed (new record or updated name) -> caller saves."""
    sid = str(steamid)
    if not sid:
        return False
    rec = RANK_DATA.get(sid)
    if rec is None:
        RANK_DATA[sid] = {"name": name or sid, "points": 0.0}
        return True
    if name and rec.get("name") != name:
        rec["name"] = name
        return True
    return False


def announce_rank_roster(rc, players, header):
    """One compact message: header + every online player's rank tag + points."""
    if not players:
        rc.say(f"{header} (no players online)")
        return
    parts = []
    for p in players:
        sid = str(p.get("steamId"))
        name = p.get("displayName") or sid
        parts.append(f"{rank_tag(player_points(sid))} {name}:{player_points(sid)}")
    rc.say(f"{header}  " + "   ".join(parts))


def award_and_announce(rc, all_players, recipients, points, header, reason="", kind=""):
    """Award `points` to recipients, save, record per-match + ledger, announce ranks."""
    rankups = []
    for p in recipients:
        sid = str(p.get("steamId"))
        name = p.get("displayName") or sid
        old_idx, new_idx, new_pts = award_points(sid, name, points)
        match_award(sid, name, p.get("faction") or "", points, reason, kind, new_pts)
        crossed, ann_idx = combined_rankup(sid, new_pts, points)   # #4: announce the COMBINED rank crossing
        if crossed:
            rankups.append((name, ann_idx))
    save_ranks()
    announce_rank_roster(rc, all_players, header)
    for name, idx in rankups:
        _, rname, abbr, color = RANKS[idx]
        rc.say(rankup_line(name, rname, abbr, color))
        activity(f"{name} promoted to {rname} ({abbr})!", "RANK")


def handle_capture(rc, side, base):
    """A base was captured by `side`; award the online players on that side."""
    if not side:
        return
    if USE_PLUGIN_SCORE:
        return        # real per-player score drives ranks; no derived capture points
    players = get_players(rc)
    team = [p for p in players if (p.get("faction") or "").lower() == side.lower()]
    if not team:
        return        # enemy/AI capture, or nobody on that side -> ignore
    print(f"[rank] {base} captured by {side}; +{CAPTURE_POINTS} to {len(team)} player(s)")
    activity(f"{base} captured by {side}   -  +{_pts(CAPTURE_POINTS)} each to {len(team)} player(s)", "CAP")
    award_and_announce(rc, players, team, CAPTURE_POINTS,
                       f"<color=#FFD200>{base} captured!</color> +{CAPTURE_POINTS} to your team -",
                       reason=f"capture: {base} ({side})", kind="capture")


# The server logs FinishGame Victory/Defeat relative to a FIXED faction: Boscali
# is faction 0 in every mission file, so Victory => Boscali won, Defeat => Primeva
# won. This is why a "Co-op as PALA" win (players are Primeva) is logged as Defeat.
# Confirmed for the PALA/Primeva case; the BDF/Boscali + PvP cases use the same
# rule -- worth eyeballing bot_output.log on a real Boscali win to confirm.
RESULT_WINNER = {"victory": "Boscali", "defeat": "Primeva"}


def handle_result(rc, result):
    """Mission ended naturally -> award the win to the side that actually won
    (resolved via RESULT_WINNER), never to 'everyone'. In co-op the winning side
    is the players' side iff they won; in PvP it's whichever faction won.
    Returns True once handled; returns False (without scoring) if the roster was
    unreadable, so the caller can let a re-emitted result line retry instead of
    locking in a false defeat."""
    if USE_PLUGIN_SCORE:
        # the NukeStats plugin determines the winner authoritatively (server-side) and
        # emits a 'win' + 'award' events; don't run the unreliable faction-0 inference.
        return True
    winner = RESULT_WINNER.get(result.lower())
    if not winner:
        print(f"[rank] result={result!r}; unrecognised, no points")
        activity(f"Mission over (result: {result})", "END")
        match_set_result(f"ended ({result})")
        return True
    # Read the roster, distinguishing a genuinely empty winning side from a failed
    # read. get_player_list() returns None on a transient rc blip; treating that as
    # "nobody on the winning side" would downgrade a real win to a recorded defeat,
    # so retry once and, if still unreadable, defer without scoring anything.
    resp = rc.get_player_list()
    if not isinstance(resp, dict):
        time.sleep(2)
        resp = rc.get_player_list()
    if not isinstance(resp, dict):
        print(f"[rank] {winner} won ({result}); roster unreadable -> deferring (no defeat recorded)")
        activity(f"{winner} won but the player list was unreadable - not scoring yet", "INFO")
        return False
    players = _extract_players(resp)
    team = [p for p in players if (p.get("faction") or "").lower() == winner.lower()]
    if team:
        match_set_result(f"Victory ({winner})")
        print(f"[rank] {winner} won ({result}); +{WIN_POINTS} to {len(team)} player(s)")
        activity(f"VICTORY! {winner} wins   -  +{_pts(WIN_POINTS)} each to {len(team)} player(s)", "WIN")
        award_and_announce(rc, players, team, WIN_POINTS,
                           f"<color=#36FFD0>VICTORY!</color> {winner} wins the mission "
                           f"- +{WIN_POINTS} to the team -",
                           reason=f"win ({winner})", kind="win")
    else:
        # the winning side has nobody online -> the players present lost this one
        print(f"[rank] {winner} won ({result}); no online players on the winning side")
        activity(f"Defeat - {winner} won the mission (no points this round)", "LOSS")
        match_set_result(f"Defeat ({winner} won)")
        rc.say(f"<color=#FF5555>Defeat.</color> {winner} won the mission.")
    return True


# ----------------------------------------------------------------------------
# Per-match tracking (match_history.json + points_ledger.jsonl)
# ----------------------------------------------------------------------------

def _match_player(sid, name, faction):
    """Get-or-create this match's record for a player."""
    p = CUR_MATCH["players"].setdefault(
        sid, {"name": name or sid, "faction": faction or "", "points": 0, "captures": 0, "won": False})
    if name:
        p["name"] = name
    if faction:
        p["faction"] = faction
    return p


def match_ensure(mission=None):
    """Lazily start a match accumulator if none is open (matches are created on the
    first award/result and finalised on Mission complete)."""
    global CUR_MATCH
    if CUR_MATCH is None:
        CUR_MATCH = {
            "match_id": time.strftime("%Y-%m-%d %H:%M:%S"),
            "mission": mission or CURRENT_MISSION,
            "started": time.strftime("%Y-%m-%d %H:%M"),
            "started_mono": time.time(),
            "result": None,
            "players": {},
        }
    return CUR_MATCH


def ledger_award(sid, name, pts, category, reason, balance, match=None):
    """Append one discrete points event to points_ledger.jsonl for the admin audit / !why.
    category in {score, score-spike, kill, win, place_1st, place_2nd, place_3rd, capture, grant}.
    NOTE for --audit: only categories that actually moved lifetime points carry a real `pts`;
    purely informational lines (capture, score-spike) carry pts:0 with the value in `reason`, so
    summing `pts` across the ledger still equals the points awarded (ledger <= ranks invariant)."""
    try:
        with open(LEDGER_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "match": match,
                "steamid": str(sid), "name": str(name),
                "pts": round(float(pts), 1), "category": str(category),
                "reason": str(reason), "balance": round(float(balance), 1),
            }) + "\n")
    except OSError as e:
        print(f"[ledger] {category} append failed: {e}")


def _flush_score_accum(match_id):
    """Write ONE aggregated 'score' ledger line per player for the in-game score they
    accumulated this match (snaps are far too frequent to ledger one-by-one), then reset."""
    for _sid, (_nm, _gain) in SCORE_ACCUM.items():
        if _gain:
            ledger_award(_sid, _nm, _gain, "score", "in-game score (match total)",
                         RANK_DATA.get(_sid, {}).get("points", 0), match=match_id)
    SCORE_ACCUM.clear()


def match_award(sid, name, faction, pts, reason, kind, balance):
    """Record one point award into the current match + append to the audit ledger."""
    match_ensure()
    p = _match_player(sid, name, faction)
    p["points"] += pts
    if kind == "capture":
        p["captures"] += 1
    elif kind == "win":
        p["won"] = True
    category = kind if kind in ("capture", "win") else "score"
    ledger_award(sid, name, pts, category, reason, balance,
                 match=CUR_MATCH["match_id"] if CUR_MATCH else None)


def match_set_result(result_str):
    """Record the match outcome (called from handle_result, before Mission complete)."""
    match_ensure()
    CUR_MATCH["result"] = result_str


def match_finalize(rc, online_players):
    """Mission ended -> stamp result/duration, fold in online (0-pt) participants,
    persist to match_history.json, announce a summary, and clear the accumulator."""
    global CUR_MATCH
    if CUR_MATCH is None:
        return       # no captures and no result tracked this round -> nothing to record
    match_ensure()
    m = CUR_MATCH
    _flush_score_accum(m["match_id"])              # one aggregated "score" ledger line per player
    for p in online_players:                       # count present players who didn't score
        sid = str(p.get("steamId") or "")
        if sid:
            _match_player(sid, p.get("displayName"), p.get("faction"))
    if not m["players"]:
        CUR_MATCH = None
        return
    record = {
        "match_id": m["match_id"], "mission": m["mission"],
        "started": m["started"], "ended": time.strftime("%Y-%m-%d %H:%M"),
        "duration_min": max(0, round((time.time() - m["started_mono"]) / 60)),
        "result": m["result"] or "ended early (vote)",
        "players": {sid: {k: pv[k] for k in ("name", "faction", "points", "captures", "won")}
                    for sid, pv in m["players"].items()},
    }
    # Load existing history, recovering from corruption the way load_ranks() does:
    # if the file is unreadable / not a list, set it aside (.corrupt) and start
    # fresh so future matches still record instead of being silently dropped forever.
    hist = []
    if os.path.exists(MATCH_HISTORY_FILE):
        try:
            with open(MATCH_HISTORY_FILE, encoding="utf-8") as f:
                hist = json.load(f)
            if not isinstance(hist, list):
                raise ValueError("match history is not a list")
        except (OSError, ValueError, json.JSONDecodeError) as e:
            print(f"[match] history unreadable ({e}); backing up to .corrupt and starting fresh")
            try:
                os.replace(MATCH_HISTORY_FILE, MATCH_HISTORY_FILE + ".corrupt")
            except OSError:
                pass
            hist = []
    hist.append(record)
    try:
        tmp = MATCH_HISTORY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(hist, f, indent=2)
        os.replace(tmp, MATCH_HISTORY_FILE)
    except OSError as e:
        print(f"[match] history save failed: {e}")
    scored = sorted(((pv["name"], pv["points"]) for pv in m["players"].values() if pv["points"] > 0),
                    key=lambda t: -t[1])
    rc.say(f"<color=#FFD200>== Match over - {_plain(record['mission'])} - "
           f"{record['result']} - {record['duration_min']} min ==</color>")
    rc.say("This match: " + (", ".join(f"{nm} +{pts}" for nm, pts in scored[:10])
                             or "no points scored"))
    print(f"[match] finalised {record['match_id']} ({len(m['players'])} players, {record['result']})")
    CUR_MATCH = None


def fold_match_stats():
    """{steamid: {'matches': n, 'wins': n}} folded from match_history.json."""
    stats = {}
    try:
        with open(MATCH_HISTORY_FILE, encoding="utf-8") as f:
            hist = json.load(f)
    except (OSError, json.JSONDecodeError):
        return stats
    for rec in hist:
        for sid, pv in rec.get("players", {}).items():
            s = stats.setdefault(sid, {"matches": 0, "wins": 0})
            s["matches"] += 1
            if pv.get("won"):
                s["wins"] += 1
    return stats


def player_match_detail(sid):
    """Per-player record: matches, wins, win%, best single-match points, last-5 W/L."""
    sid = str(sid)
    out = {"matches": 0, "wins": 0, "winpct": 0, "best": 0, "last5": ""}
    try:
        with open(MATCH_HISTORY_FILE, encoding="utf-8") as f:
            hist = json.load(f)
    except (OSError, json.JSONDecodeError):
        return out
    seq = []
    for rec in hist:
        pv = rec.get("players", {}).get(sid)
        if not pv:
            continue
        out["matches"] += 1
        out["best"] = max(out["best"], pv.get("points", 0))
        won = bool(pv.get("won"))
        if won:
            out["wins"] += 1
        seq.append("W" if won else "L")
    if out["matches"]:
        out["winpct"] = round(100 * out["wins"] / out["matches"])
        out["last5"] = " ".join(seq[-5:])
    return out


def recent_ledger_for(sid, n=4):
    """Last n ledger awards for a SteamID (most recent last)."""
    sid = str(sid)
    rows = []
    try:
        with open(LEDGER_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if str(e.get("steamid")) == sid:
                    rows.append(e)
    except OSError:
        return []
    return rows[-n:]


def queue_welcome(sid, name, delay=None):
    """Schedule a welcome ~`delay`s after first sighting so the player's client/chat has
    loaded enough to actually see it. Deduped via WELCOMED and the queue itself; if the
    player leaves before the deadline the entry is dropped in the roster-poll left-handler,
    so a quick join/leave produces no welcome. Drained from the main loop (single-threaded)."""
    if delay is None:
        delay = sysmsg_delay("welcome", WELCOME_DELAY)   # owner-tunable join delay (webcc Messages tab)
    sid = str(sid)
    if not sid or sid in WELCOMED or sid in WELCOME_QUEUE:
        return
    WELCOME_QUEUE[sid] = (time.time() + delay, name)


def say_welcome(rc, sid, name):
    """Welcome a player ONCE per session (deduped via WELCOMED, cleared when they leave).
    The team is shown by the game's own client-side "X joined [faction]" message, so the
    bot's welcome just carries the rank + points."""
    sid = str(sid)
    if not sid or sid in WELCOMED:
        return
    WELCOMED.add(sid)
    if ensure_player(sid, name):
        save_ranks()
    pts = player_points(sid)
    label, color, tail = rank_progress(pts)
    if sysmsg_on("welcome"):                                   # owner can disable the join message (webcc Messages tab)
        custom = sysmsg_text("welcome", "")
        if custom:                                            # custom text REPLACES the default; {name}{rank}{pts}
            line = custom.replace("{name}", name).replace("{rank}", label).replace("{pts}", _pts(pts))
        else:
            line = (f"<color=#36FFD0>Welcome</color> <color={color}>{label}</color> "
                    f"<color=#FFFFFF>{name}</color>  -  {_pts(pts)}")
            if rank_index_for(pts) == 0:  # OFFCDT (lowest tier) -> nudge them to !help
                line += "  -  <color=#FFD200>type !help for commands</color>"
        rc.say(line)
    # (full help is on-demand via !help now - don't dump 9 lines to all-chat on every join)
    activity(f"{name} joined   ({label}, {_pts(pts)})   -  {len(ROSTER_BY_SID)} online", "JOIN")


def underdog_bonus(kid, vid):
    """Extra kill points when the KILLER outranks DOWN: i.e. the killer's server-rank
    TIER is BELOW the victim's. Scaled by how many rank tiers separate them, +10 each.
    Uses the 11-tier RANKS ladder index of each side's LOCAL points (rank_index_for(local_points(...)))
    -- NOT the cross-server combined total: this is an in-match, single-server balance incentive, and
    keeping it on local points means the kill award/ledger stay reproducible from local state alone (no
    foreign-points coupling, no inflation vector when a high-rank player carries in from another server).
    Returns 0 if the killer is the same or higher rank than the victim, or if either id is missing."""
    if not kid or not vid:
        return 0
    killer_idx = rank_index_for(local_points(kid))
    victim_idx = rank_index_for(local_points(vid))
    levels = victim_idx - killer_idx        # how many tiers the killer was BELOW the victim
    return UNDERDOG_PER_PLAYER * levels if levels > 0 else 0


def _fac_of(sid):
    """Best-effort faction for a SteamID (for killfeed team colours). '' if unknown / AI."""
    p = ROSTER_BY_SID.get(sid)
    if p and p.get("faction"):
        return p["faction"]
    m = STATS_META.get(sid)
    if m and m.get("faction"):
        return m["faction"]
    return ""


def _record_killer(vid, kname, ksid, kfac, kp, ff=0, weapon=""):
    """Remember who/what downed victim `vid` (from a kill/down event) and back-fill the most
    recent killfeed entry for them that doesn't yet have a killer (the death `life` event and the
    kill/down event can arrive in either order)."""
    now = time.time()
    _recent_kill[vid] = {"kname": kname, "ksid": ksid, "kfac": kfac, "kp": kp,
                         "ff": ff, "weapon": weapon, "ts": now}
    for k in KILLFEED[:8]:
        if k.get("vsid") == vid and not k.get("kname") and now - k.get("ts", 0) < 8:
            k["kname"], k["ksid"], k["kfac"], k["kp"] = kname, ksid, kfac, kp
            k["ff"], k["weapon"] = ff, weapon
            break


def handle_stats_line(rc, obj):
    """Ingest one [NOSTATS] object from the NukeStats plugin.
      snap/score -> cache the player's meta + live score (feed/display only)
      win        -> authoritative winner: announce + tally W/L (replaces faction-0 guess)
      award      -> apply the plugin's match-end points (+win / +placement) to ranks.json
      end        -> match boundary: clear the per-match caches
    Inert unless the plugin is actually emitting these lines."""
    if not isinstance(obj, dict):
        return
    t = obj.get("t")
    if t == "chat":
        # The plugin reroutes reformatted chat, which suppresses the normal
        # CmdSendChatMessage log line -> the bot can't see those messages. The plugin
        # re-reports each broadcast message here so it still lands in the activity feed.
        # (Commands/votes aren't rerouted, so they keep coming via the normal parse -
        # no double logging.)
        sid = str(obj.get("id") or "")
        if obj.get("n") and sid:
            PLAYER_NAMES[sid] = obj["n"]
        msg = (obj.get("msg") or "").strip()
        if LOG_CONVERSATION and msg:
            name = obj.get("n") or PLAYER_NAMES.get(sid) or RANK_DATA.get(sid, {}).get("name") or sid
            ally = "" if obj.get("all", True) else "(ally) "
            activity(f"{ally}{name}: {msg}", "CHAT")
        return
    if t == "cfg":
        # webcc settings menu: the plugin's current live config values (one snapshot dict).
        global PLUGIN_CFG, PLUGIN_CFG_TS
        v = obj.get("v")
        if isinstance(v, dict):
            PLUGIN_CFG = {str(k): v[k] for k in v}
            PLUGIN_CFG_TS = time.time()
        return
    if t == "report":
        # anti-grief: the plugin auto-kicked/flagged a single connection flooding unit-commands.
        rid = str(obj.get("id") or "")
        nm = str(obj.get("n") or PLAYER_NAMES.get(rid) or (RANK_DATA.get(rid, {}).get("name") if rid else "") or "?")
        rec = {"id": rid, "name": nm, "reason": str(obj.get("reason") or "?"),
               "count": int(obj.get("count") or 0), "rate": int(obj.get("rate") or 0),
               "action": str(obj.get("action") or "report"),
               "ts": time.time()}          # plugin sends ts:0 -> stamp the real time on ingest
        rec["banned"] = (rec["action"] == "ban")
        add_report(rec)
        activity(f"AUTO-{rec['action'].upper()}: {nm} - unit-flood (owned {rec['count']}, {rec['rate']}/s)", "!")
        return
    if t == "tk":
        # teamkill enforcement escalation (warn = eject / kick / ban) -> moderation log + the webcc Moderation tab.
        # Records WHAT caused it: the teammate killed + the offense number.
        rid = str(obj.get("id") or "")
        nm = str(obj.get("n") or PLAYER_NAMES.get(rid) or (RANK_DATA.get(rid, {}).get("name") if rid else "") or "?")
        victim = str(obj.get("victim") or "a teammate")
        method = str(obj.get("method") or "")        # HOW it happened: "weapon (FS-12)" / "SAM (auto)" / "CRAM (auto)" / ...
        count = int(obj.get("count") or 0)
        action = str(obj.get("action") or "warn")
        if action not in ("warn", "kick", "ban"):
            action = "warn"
        ordn = {1: "1st", 2: "2nd", 3: "3rd"}.get(count, f"{count}th")
        reason = f"team-killed {victim} ({ordn} offense)"
        rec = {"id": rid, "name": nm, "reason": reason, "method": method, "count": count, "rate": 0,
               "action": action, "ts": time.time(), "banned": (action == "ban")}
        add_report(rec)
        verb = {"warn": "warned + ejected", "kick": "kicked", "ban": "BANNED"}[action]
        via = f" via {method}" if method else ""
        activity(f"TEAMKILL - {nm} {verb}: team-killed {victim}{via} ({ordn} offense)", "!")
        return
    if t == "pos":
        # live map: fast position update for flying players. Stale entries (>~6s) mean the
        # player is no longer flying -> the command centre renders them as dead/ejected.
        ts = time.time()
        for pp in obj.get("p", []):
            psid = str(pp.get("id") or "")
            if psid:
                POS[psid] = (pp.get("x"), pp.get("z"), ts, pp.get("k"))   # k = "p" plane / "h" heli (None on old plugin)
                DOWNED.pop(psid, None)                                     # they're flying again -> no longer "downed"
        return
    if t == "air":
        # AI aircraft limiter telemetry: per-side AI/player aircraft counts + caps (perf panel).
        global AIR, AIR_TS
        AIR = {"s": obj.get("s", []), "ai": obj.get("ai", 0), "pl": obj.get("pl", 0),
               "teamcap": obj.get("teamcap"), "totcap": obj.get("totcap")}
        AIR_TS = time.time()
        return
    if t == "net":
        # connection-health / RTT-probe telemetry (Connection Stress panel); payload shape is plugin-defined.
        global NET, NET_TS
        NET = {k: v for k, v in obj.items() if k != "t"}
        NET_TS = time.time()
        return
    if t == "ent":
        # live map: per-AI-aircraft + per-ship world positions (each carries a per-unit instance id "i"
        # for client-side interpolation; no SteamID -> rendered without a name label).
        global ENT, ENT_TS
        ENT = {"a": obj.get("a", []), "s": obj.get("s", [])}
        ENT_TS = time.time()
        return
    if t == "life":
        # NuclearSkill v0.8.7 (PERSISTENT points-per-DEATH): a "life" is a running score the BOT
        # accumulates per pilot (rec["curLife"], fed by snap deltas in the snap handler) that survives
        # disconnects AND match-ends. The plugin emits a life-END event ONLY when the pilot DIES
        # ("death") or EJECTS mid-air ("eject"); ground dismounts and balance/admin moves do NOT end
        # it. On a counted life-end we BANK curLife into skillPoints, +1 life, and reset curLife.
        if not USE_PLUGIN_SCORE:
            return
        sid = str(obj.get("id") or "")
        if not sid or sid == "0":
            return
        reason = str(obj.get("r") or "death")
        counted = reason in ("death", "eject")    # the ONLY life-ending reasons now (legacy match/exit/dc ignored)
        # live map + killfeed: mark them DOWN now (so the map shows dead instantly) and log the death
        # with the last-known location (POS is fresh at death since they were just flying).
        if counted:
            _now = time.time()
            DOWNED[sid] = _now
            _vpos = POS.get(sid)
            _vname = PLAYER_NAMES.get(sid) or RANK_DATA.get(sid, {}).get("name") or sid
            _rk = _recent_kill.get(sid)           # killer info if a kill/down event already arrived for this victim
            _rk = _rk if (_rk and _now - _rk["ts"] < 8) else None
            KILLFEED.insert(0, {
                "name":  _vname, "vname": _vname, "vsid": sid, "vfac": _fac_of(sid),
                "kname": (_rk["kname"] if _rk else ""), "ksid": (_rk["ksid"] if _rk else ""),
                "kfac":  (_rk["kfac"] if _rk else ""),  "kp":   (_rk["kp"] if _rk else 0),
                "ff": (_rk.get("ff", 0) if _rk else 0), "weapon": (_rk.get("weapon", "") if _rk else ""),
                "x": (_vpos[0] if _vpos else None), "z": (_vpos[1] if _vpos else None),
                "ts": _now, "reason": reason})
            del KILLFEED[KILLFEED_MAX:]            # keep newest KILLFEED_MAX (we insert at the front)
        rec = RANK_DATA.setdefault(sid, {"name": PLAYER_NAMES.get(sid, sid), "points": 0})
        banked = round(max(0.0, rec.get("curLife", 0.0)), 1)
        # audit ledger (what banked, for which reason)
        try:
            with open(SKILL_LEDGER_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "steamid": sid,
                                    "name": PLAYER_NAMES.get(sid, sid), "score": banked,
                                    "reason": reason, "counted": counted}) + "\n")
        except OSError:
            pass
        if not counted:                      # not a death/eject -> life stays OPEN, nothing banked
            return
        rec["skillPoints"] = round(rec.get("skillPoints", 0.0) + banked, 1)
        rec["lives"] = rec.get("lives", 0) + 1
        if banked > 0:                       # a scoreless life (e.g. respawn -> immediate eject) must
            rec["lastLife"] = banked         # NOT clobber your last SCORING life shown by !points
        rec["curLife"] = 0.0                  # start the next life fresh
        _maybe_save_ranks()
        _SKILL_PUSH_FLAG[0] = True            # coalesced plugin_skill.txt push next loop
        return
    if t == "capbonus":
        # NuclearSkill: a location capture adds to the pilot's CURRENT life (banked at next death/eject).
        if not USE_PLUGIN_SCORE:
            return
        sid = str(obj.get("id") or "")
        if not sid or sid == "0":
            return
        try:
            pts = float(obj.get("pts", 0))
        except (TypeError, ValueError):
            return
        rec = RANK_DATA.setdefault(sid, {"name": PLAYER_NAMES.get(sid, sid), "points": 0})
        rec["curLife"] = round(rec.get("curLife", 0.0) + pts, 1)
        # Audit-only: the lifetime points from a capture arrive via the snap/score stream;
        # this line just makes captures visible in the ledger. pts:0 keeps --audit sums correct.
        ledger_award(sid, PLAYER_NAMES.get(sid, sid), 0, "capture",
                     f"base capture bonus +{pts:g} (to current life / in-game score)",
                     rec.get("points", 0), match=CUR_MATCH["match_id"] if CUR_MATCH else None)
        return
    if t == "kill":
        # a player downed an enemy player: announce the "splash" + award the kill bonus.
        kid = str(obj.get("kid") or "")
        vid = str(obj.get("vid") or "")
        kn = obj.get("kn") or PLAYER_NAMES.get(kid) or kid
        vn = obj.get("vn") or PLAYER_NAMES.get(vid) or vid
        if obj.get("kn") and kid:
            PLAYER_NAMES[kid] = obj["kn"]
        if obj.get("vn") and vid:
            PLAYER_NAMES[vid] = obj["vn"]
        if vid:                                  # killfeed: an enemy PLAYER downed them (works pre-deploy via the existing kill event)
            _record_killer(vid, kn, kid, _fac_of(kid), 1)
        extra = underdog_bonus(kid, vid)
        bonus = KILL_BONUS + extra
        rc.say(f"{kill_name(kid, kn)} <color=#C8FBFF>just</color> "
               f"<color=#FF6A00>splashed</color> {kill_name(vid, vn)}"
               f"<color=#FFD200>!</color>"
               + (f" <color=#36FFD0>(+{extra} underdog)</color>" if extra else ""))
        activity(f"{kn} splashed {vn}  (+{bonus}{' underdog' if extra else ''})", "KILL")
        if USE_PLUGIN_SCORE and kid and kid != "0":
            old_idx, new_idx, total = award_points(kid, kn, bonus)
            ledger_award(kid, kn, bonus, "kill",
                         f"kill splash: +{KILL_BONUS} base" + (f" +{extra} underdog" if extra else ""),
                         total, match=CUR_MATCH["match_id"] if CUR_MATCH else None)
            _maybe_save_ranks()
            crossed, ann_idx = combined_rankup(kid, total, bonus)   # #4: combined-rank crossing
            if crossed:
                _, rname, abbr, color = RANKS[ann_idx]
                rc.say(rankup_line(kn, rname, abbr, color))
                activity(f"{kn} promoted to {rname} ({abbr})!", "RANK")
                save_ranks()
                _RANK_PUSH_FLAG[0] = True       # coalesced push at end of loop (was inline SSH)
        return
    if t == "down":
        # killfeed enrichment (plugin v0.9.0+): who/what shot a player down, incl AI/SAM unit names.
        # kp=1 => an enemy/friendly PLAYER (ks=their sid -> team colour); else k = the AI/unit name.
        vid = str(obj.get("v") or "")
        if not vid:
            return
        kn = str(obj.get("k") or "")
        kp = 1 if int(obj.get("kp") or 0) else 0
        ksid = str(obj.get("ks") or "")
        kfac = _fac_of(ksid) if (kp and ksid) else ""
        ff = 1 if int(obj.get("ff") or 0) else 0          # plugin-authoritative friendly-fire (teamkill) flag
        weapon = str(obj.get("w") or "")                  # damaging unit/weapon name (webcc killfeed only)
        _record_killer(vid, kn, ksid, kfac, kp, ff, weapon)
        return
    if t == "win":
        handle_plugin_win(rc, obj.get("f") or "")
        return
    if t == "award":
        if not USE_PLUGIN_SCORE:
            return
        sid = str(obj.get("id") or "")
        if not sid or sid == "0":
            return
        try:
            pts = int(round(float(obj.get("pts", 0))))
        except (TypeError, ValueError):
            return
        if pts == 0:
            return
        name = obj.get("n") or STATS_META.get(sid, {}).get("name") or sid
        old_idx, new_idx, total = award_points(sid, name, pts)
        save_ranks()
        _ar = (obj.get("reason") or "").strip().lower()
        _cat = {"1st": "place_1st", "2nd": "place_2nd", "3rd": "place_3rd", "win": "win"}.get(_ar, "win")
        ledger_award(sid, name, pts, _cat, f"{_cat}: {obj.get('reason', '')}",
                     total, match=CUR_MATCH["match_id"] if CUR_MATCH else None)
        activity(f"{name}  +{pts}  ({obj.get('reason', '')})", "RANK")
        crossed, ann_idx = combined_rankup(sid, total, pts)   # #4: combined-rank crossing
        if crossed:
            _, rname, abbr, color = RANKS[ann_idx]
            rc.say(rankup_line(name, rname, abbr, color))
            activity(f"{name} promoted to {rname} ({abbr})!", "RANK")
            _RANK_PUSH_FLAG[0] = True   # coalesced push at end of loop (was inline SSH)
        return
    if t == "end":
        # Deliberately DO NOT reset each player's "ms" baseline here. The game keeps
        # PlayerScore non-zero through the post-mission delay, so snapshots keep arriving
        # with the final score for ~80s after "end". If we zeroed the baseline now, the
        # very next such snapshot (s == final, prev == 0) would re-credit the whole match
        # score -> every player's match earnings double-counted once. Leaving "ms" at the
        # final score makes those lingering snaps a no-op (s == prev), and the new match's
        # score reset (s < prev) trips the existing decrease-rebaseline path cleanly.
        STATS_META.clear()
        LIVE_SCORE.clear()
        save_ranks()
        return
    # snap / score: cache meta, and accumulate the player's REAL in-game score into their
    # lifetime points. "ms" is the last in-match score we credited; we add the increase
    # since then. It's stored in the record (restart-safe) and reset to 0 at match end,
    # so points == the player's total accumulated score across matches.
    sid = str(obj.get("id") or "")
    if not sid or sid == "0":
        return
    name = obj.get("n") or STATS_META.get(sid, {}).get("name") or sid
    STATS_META[sid] = {"name": name, "faction": obj.get("f") or "",
                       "rank": obj.get("rk"), "teamkills": obj.get("tk"),
                       "aircraft": obj.get("ac") or "", "t": time.time()}
    PLAYER_NAMES[sid] = name
    try:
        s = float(obj.get("s", 0))
    except (TypeError, ValueError):
        return
    if not math.isfinite(s):                       # reject 'inf'/'nan' before it poisons ms/points (ranks.json corruption)
        return
    LIVE_SCORE[sid] = s
    if not USE_PLUGIN_SCORE:
        return
    rec = RANK_DATA.get(sid)
    if rec is None or "ms" not in rec:
        # First time we've seen this player's in-match score this session: adopt it as the
        # baseline and credit NOTHING (they accrue from their NEXT increase). Without this,
        # a record made by ensure_player (which has no "ms") would give prev=0 and one-shot
        # credit the player's ENTIRE accumulated in-match score as lifetime points.
        RANK_DATA.setdefault(sid, {"name": name or sid, "points": 0})["ms"] = s
        return
    prev = rec["ms"]
    if s > prev:                                   # gained score -> credit the increase
        gain = s - prev
        award = min(gain, GAIN_CLAMP_MAX)          # clamp what we BANK this tick; the raw gain still drives the spike alert below
        old_idx, new_idx, _new_pts = award_points(sid, name, award)
        RANK_DATA[sid]["ms"] = s
        # NuclearSkill: the same (clamped) gain feeds the running per-life score, banked at next death/eject.
        RANK_DATA[sid]["curLife"] = round(RANK_DATA[sid].get("curLife", 0.0) + award, 1)
        # Audit: accumulate this match's (clamped) score for ONE ledger line at finalize (snaps are ~1/s).
        _acc = SCORE_ACCUM.setdefault(sid, [name, 0.0]); _acc[0] = name; _acc[1] = round(_acc[1] + award, 1)
        # Exploit tripwire: a single snap jump this large is abnormal (cf. 2026-06-24). Flag it
        # live + in the ledger (pts:0 -> audit-neutral; the real award is the "score" aggregate).
        if gain > SPIKE_THRESHOLD:
            activity(f"!! SCORE SPIKE: {name} +{gain:g} in one tick (check for exploit)", "!")
            ledger_award(sid, name, 0, "score-spike", f"single-tick gain +{gain:g} (>{SPIKE_THRESHOLD:g})",
                         RANK_DATA[sid].get("points", 0), match=CUR_MATCH["match_id"] if CUR_MATCH else None)
        _maybe_save_ranks()
        crossed, ann_idx = combined_rankup(sid, _new_pts, award)   # #4: combined-rank crossing
        if crossed:
            _, rname, abbr, color = RANKS[ann_idx]
            rc.say(rankup_line(name, rname, abbr, color))
            activity(f"{name} promoted to {rname} ({abbr})!", "RANK")
            save_ranks()
            _RANK_PUSH_FLAG[0] = True   # coalesced push at end of loop (was inline SSH)
    elif rec is not None and s < prev:             # score reset/decreased -> rebaseline, no credit
        rec["ms"] = s


def handle_plugin_win(rc, faction):
    """The plugin reported the authoritative winning faction (PvE or PvP). Announce it
    and tally each online player's win/loss from their last-known faction (STATS_META).
    This replaces the unreliable faction-0 FinishGame inference that mislabelled wins."""
    if not faction:
        return
    activity(f"VICTORY! {faction} wins the mission", "WIN")
    if USE_PLUGIN_SCORE:
        rc.say(f"<color=#36FFD0>VICTORY!</color> {faction} wins the mission!")
    fl = faction.lower()
    changed = False
    for sid, meta in STATS_META.items():
        ensure_player(sid, meta.get("name") or sid)   # win event precedes award events
        rec = RANK_DATA.get(sid)
        if rec is None:
            continue
        if (meta.get("faction") or "").lower() == fl:
            rec["wins"] = rec.get("wins", 0) + 1
        else:
            rec["losses"] = rec.get("losses", 0) + 1
        changed = True
    if changed:
        save_ranks()


# Set by hot-path rank-ups (kill/award/snap) to request ONE coalesced plugin_ranks push
# at the end of the current main loop, instead of a blocking SSH handshake inline per
# rank-up (a kill burst could otherwise fire several ~15s-timeout connects mid-poll,
# stalling chat/vote parsing). A list so the hot paths mutate it without a global decl.
_RANK_PUSH_FLAG = [False]
# default-on / boot: if sharing is already enabled at startup, warm the peer cache + flag a combined rank
# push NOW (this runs AFTER _RANK_PUSH_FLAG is defined, unlike the load_shared_ranks_cfg site above), so the
# FIRST connect after boot already gets its combined name tag instead of waiting ~2s for the daemon warm.
if SHARED_RANKS_ENABLED:
    try:
        _OTHER_RANKS_CACHE = (_compute_other_ranks(), time.time())
        _RANK_PUSH_FLAG[0] = True
    except Exception:                             # noqa: BLE001 - boot must never fail on the share
        pass


def push_plugin_ranks():
    """Write sid|rank-label|#colour lines to plugin_ranks.txt on the container so the
    NukeStats plugin can render [Name - Rank] chat in the rank colour. Best-effort.
    Atomic (.tmp + rename) so the plugin never latches a torn/empty read and blanks tags."""
    lines = []
    seen = set()
    for sid, rec in list(RANK_DATA.items()):                   # snapshot: the poll loop mutates RANK_DATA on another thread
        idx = rank_index_for(player_points(sid))               # COMBINED rank across the host's servers when sharing is on
        _, rname, abbr, color = RANKS[idx]
        # sid|ABBR|#colour|rankIndex(1..11)|FullName
        #   ABBR     -> kill-feed / radar tag
        #   rankIndex-> numeric rank for PvP auto-balance
        #   FullName -> the CHAT name tag (e.g. "[Wing Commander] Tomo"; reads better in TTS)
        lines.append(f"{sid}|{abbr}|{color}|{idx + 1}|{rname}")
        seen.add(sid)
    if SHARED_RANKS_ENABLED:                                   # cross-server: also tag players whose points live ONLY on a peer
        try:                                                   # server so their carried-over rank shows at join. The plugin bakes
            for sid in _other_ranks():                         # the name tag ONCE at connect, so the line must exist BEFORE they join.
                if sid in seen:                                # local record already emitted above (local always wins)
                    continue
                idx = rank_index_for(player_points(sid))       # combined == the peer points for a peer-only sid
                if idx <= 0:                                   # rank-0 stub: no tag to show, skip
                    continue
                _, rname, abbr, color = RANKS[idx]
                lines.append(f"{sid}|{abbr}|{color}|{idx + 1}|{rname}")
                seen.add(sid)
        except Exception as e:                                 # noqa: BLE001 - a display push must never raise
            print(f"[plugin-ranks] peer merge skipped: {e}")
    body = ("\n".join(lines) + "\n").encode("utf-8")

    def _w(sftp):
        with sftp.open("plugin_ranks.txt.tmp", "wb") as f:
            f.write(body)
        try:
            sftp.rename("plugin_ranks.txt.tmp", "plugin_ranks.txt")
        except OSError:
            try:
                sftp.remove("plugin_ranks.txt")
            except OSError:
                pass
            sftp.rename("plugin_ranks.txt.tmp", "plugin_ranks.txt")
    try:
        _sftp_op(_w)
    except Exception as e:                        # noqa: BLE001
        print(f"[plugin-ranks] push failed: {e}")


# ===================== NuclearSkill: rating (points-per-life) + 0-10 ranking =====================
SKILL_MIN_LIVES = 5            # a player's skill rating only counts after this many completed lives
_SKILL_PUSH_FLAG = [False]     # set on a life update -> coalesced plugin_skill.txt push next loop


def skill_rating(rec):
    """Points-per-life rating, or None until the player has >= SKILL_MIN_LIVES lives."""
    if not rec:
        return None
    lives = rec.get("lives", 0)
    if lives < SKILL_MIN_LIVES:
        return None
    return rec.get("skillPoints", 0.0) / lives


def skill_table():
    """[(sid, rec, P)] for every QUALIFIED player, sorted by rating descending."""
    out = []
    for sid, rec in list(RANK_DATA.items()):   # snapshot: the poll loop mutates RANK_DATA on another thread
        P = skill_rating(rec)
        if P is not None:
            out.append((sid, rec, P))
    out.sort(key=lambda x: x[2], reverse=True)
    return out


def skill_ranking(P, table):
    """0-10 ranking: SR = SR_min + (P-P_min)(SR_max-SR_min)/(P_max-P_min), with SR_min=0, SR_max=10."""
    if not table:
        return 0.0
    p_max = table[0][2]
    p_min = table[-1][2]
    if p_max <= p_min:
        return 10.0
    return 10.0 * (P - p_min) / (p_max - p_min)


def _skill_namecolour(rec):
    """'[ABBR] Name' in the player's server-rank colour, for the !skill 'next up' line."""
    idx = rank_index_for(rec.get("points", 0))
    _, _rname, abbr, color = RANKS[idx]
    return f"<color={color}>[{abbr}]</color> {rec.get('name', '?')}"


def skill_tier_info():
    """Chat lines explaining the 0-10 skill scale + the current top-3 pilots, appended to the
    rank-ladder preview so players see BOTH the rank ladder and the skill rankings."""
    table = skill_table()
    if not table:
        return [f"<color=#36FFD0>SKILL RANKINGS: not enough qualified pilots yet "
                f"(need {SKILL_MIN_LIVES}+ lives)</color>"]
    lines = [
        "<color=#FFFF00>=== SKILL RANKINGS (avg pts per life) ===</color>",
        f"<color=#36FFD0>0/10 = unranked (<{SKILL_MIN_LIVES} lives) | 10/10 = best pts-per-life</color>",
        "<color=#36FFD0>Top skill pilots:</color>",
    ]
    for i in range(min(3, len(table))):
        _sid, rec, P = table[i]
        sr = skill_ranking(P, table)
        lines.append(f"<color=#36FFD0>  {i + 1}. {_skill_namecolour(rec)} - "
                     f"{P:.0f} pts/life · {sr:.1f}/10</color>")
    return lines


def push_plugin_skill():
    """Write 'sid|rating' for every qualified player to plugin_skill.txt so the plugin can
    balance teams by skill. Atomic (.tmp + rename) so the plugin never reads a torn file."""
    lines = [f"{sid}|{P:.2f}" for sid, _rec, P in skill_table()]
    body = ("\n".join(lines) + "\n").encode("utf-8")

    def _w(sftp):
        with sftp.open("plugin_skill.txt.tmp", "wb") as f:
            f.write(body)
        try:
            sftp.rename("plugin_skill.txt.tmp", "plugin_skill.txt")
        except OSError:
            try:
                sftp.remove("plugin_skill.txt")
            except OSError:
                pass
            sftp.rename("plugin_skill.txt.tmp", "plugin_skill.txt")
    try:
        _sftp_op(_w)
    except Exception as e:                        # noqa: BLE001
        print(f"[plugin-skill] push failed: {e}")


def refresh_current_mission(rc):
    """Best-effort update of CURRENT_MISSION from the server. Called at startup AND
    periodically, so the name self-heals after a reconnect (e.g. the bot was restarted
    while the server was down -> the one-time startup read failed -> "(unknown)")."""
    global CURRENT_MISSION
    try:
        mr = rc.send("get-mission")
        if isinstance(mr, dict):
            cm = (mr.get("currentMission") or {}).get("Key", {})
            if cm.get("Name"):
                CURRENT_MISSION = friendly_label(cm["Name"])
    except Exception:        # noqa: BLE001
        pass


def check_mission_time_warnings(rc, mtime, mission_name):
    """Announce remaining mission time as it crosses WARN_THRESHOLDS (once each per mission;
    the fired-set resets when the mission changes). mtime = [current, max, fetched_at]."""
    global _warnings_fired, _warn_mission
    if _warn_mission != mission_name:
        _warnings_fired = set()
        _warn_mission = mission_name
    if not mtime or mtime[1] <= 0:
        return
    remaining = mtime[1] - mtime[0]
    for t in sorted(WARN_THRESHOLDS, reverse=True):
        if t in _warnings_fired or remaining > t:
            continue
        _warnings_fired.add(t)
        mins = t // 60
        label = "1 minute" if mins == 1 else f"{mins} minutes"
        rc.say(f"<color=#FFAA00>Mission time: {label} remaining.</color>")
        print(f"[timer] {label} remaining")


def _match_grant_key(elapsed, now, mission):
    """A restart-STABLE identity for the current match: mission name + a coarse match-start epoch.
    The start epoch is (wall_now - elapsed) bucketed to 300s so poll jitter on either reading maps to
    the same key across a bot restart. Deliberately NOT CUR_MATCH['match_id'] (that's a strftime made
    at lazy match-create -> a new value every restart, which is exactly what re-granted the bonus)."""
    start_bucket = int((now - elapsed) // 300)
    return f"{mission or '(unknown)'}|{start_bucket}"


def _load_grant_set(key):
    """Set of sids already granted the start bonus for this match key (restart-safe). {} / missing -> empty."""
    try:
        with open(START_BONUS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return set(d.get(key, [])) if isinstance(d, dict) else set()
    except (OSError, json.JSONDecodeError):
        return set()


def _save_grant_set(key, sids):
    """Persist the granted-sid set under `key`, keeping only the few most recent match keys (bounded file)."""
    try:
        try:
            with open(START_BONUS_FILE, encoding="utf-8") as f:
                d = json.load(f)
            if not isinstance(d, dict):
                d = {}
        except (OSError, json.JSONDecodeError):
            d = {}
        d[key] = sorted(sids)
        if len(d) > 8:                               # keep only the newest match keys (insertion order)
            for stale in list(d.keys())[:-8]:
                d.pop(stale, None)
        tmp = START_BONUS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2)
        os.replace(tmp, START_BONUS_FILE)
    except OSError as e:
        print(f"[start-bonus] grant-marker save failed: {e}")


def check_match_milestones(rc, mtime):
    """Start-of-match participation bonus + 'stay for the next match' reminders, keyed to the
    mission's elapsed clock (mtime = [elapsed, max, fetched_at]). Per-mission state resets when a
    new mission begins -- detected by the elapsed clock jumping BACKWARD to ~0 (every new mission
    restarts it), which is reliable even when two missions share a display name. Call AFTER
    refresh_current_mission() so CURRENT_MISSION is fresh.

      * First START_BONUS_WINDOW seconds: every present player gets START_BONUS_PTS ('start of
        match bonus') exactly once, and a one-time chat line thanks them for kicking things off.
      * At each STAY_MARKS elapsed mark: a one-time reminder to stay for the next match's bonus.

    Adopting a mission already in progress (e.g. the bot reconnected mid-match) stays SILENT: the
    kickoff line + already-passed reminders are pre-suppressed and the bonus window has closed."""
    global _ms_mission, _ms_last_elapsed, _ms_cycle_at
    global _ms_start_done, _ms_start_said, _ms_stay_fired
    if not mtime or mtime[1] <= 0:
        return
    elapsed, now = mtime[0], mtime[2]
    mission = CURRENT_MISSION
    # New mission? The elapsed clock resets to ~0 at every mission start (a backward jump well past
    # poll jitter); first boot bootstraps via _ms_mission is None; a fresh name is a backup signal.
    is_new = (_ms_mission is None or elapsed + 30 < _ms_last_elapsed
              or (mission != _ms_mission and mission and mission != "(unknown)"))
    if is_new and now - _ms_cycle_at > 90:        # 90s cooldown collapses the name-lag/elapsed-lag double edge
        _ms_cycle_at = now
        _ms_start_done = False
        _ms_start_said = False
        _ms_stay_fired = set()
        if elapsed > START_BONUS_WINDOW + 120:    # adopted an IN-PROGRESS mission (past the grant window) -> don't backfire
            _ms_start_done = True
            _ms_start_said = True
            _ms_stay_fired = {m for m in STAY_MARKS if elapsed >= m}
        else:
            try:
                fire_event_messages(rc, "match_start")   # genuine fresh match -> owner match_start messages
            except Exception as e:                # noqa: BLE001
                print(f"[servermsg] match_start error: {e}")
    _ms_mission = mission
    _ms_last_elapsed = elapsed

    # --- start-of-match bonus: granted ONCE the match has been live for 1 minute (NOT immediately), to
    #     everyone present at that point. A quick restart/redeploy that doesn't reach 1 min => no grant,
    #     so back-to-back server restarts no longer hand out the bonus repeatedly. ---
    if not _ms_start_done and START_BONUS_WINDOW <= elapsed <= START_BONUS_WINDOW + 120:
        _ms_start_done = True
        # Persisted per-match dedupe: a bot restart mid-match clears _ms_start_done (in-memory) and re-enters
        # this window, but the on-disk granted-sid set survives, so nobody is awarded the +250 twice. (#1)
        grant_key = _match_grant_key(elapsed, now, mission)
        granted = _load_grant_set(grant_key)
        newly = []
        for sid, p in list(ROSTER_BY_SID.items()):
            if not sid or sid in granted:            # already credited for THIS match (even across a restart)
                continue
            name = p.get("displayName") or PLAYER_NAMES.get(sid) or sid
            _, _, total = award_points(sid, name, START_BONUS_PTS)
            ledger_award(sid, name, START_BONUS_PTS, "start_bonus",
                         "start of match bonus (present at the 1-minute mark)", total,
                         match=CUR_MATCH["match_id"] if CUR_MATCH else None)
            granted.add(sid)
            newly.append(name)
        _save_grant_set(grant_key, granted)          # persist BEFORE announce so a crash mid-grant still dedupes
        if newly:
            save_ranks()
            push_plugin_ranks()                   # refresh the in-chat [Name - RANK] tags right away
            shown = ", ".join(newly[:6]) + ("..." if len(newly) > 6 else "")
            activity(f"Start-of-match bonus: +{START_BONUS_PTS} to {len(newly)} player(s) ({shown})", "RANK")
            if not _ms_start_said:
                _ms_start_said = True
                rc.say(f"<color=#36FFD0>Thanks for being here for the start - "
                       f"you've all received +{START_BONUS_PTS} points!</color>")
                print(f"[start-bonus] +{START_BONUS_PTS} to {len(newly)} player(s) @ 1-min mark")

    # --- 'stay for the next match' reminders at 105 / 125 / 145 min in ---
    for mark in STAY_MARKS:
        if mark in _ms_stay_fired or elapsed < mark:
            continue
        _ms_stay_fired.add(mark)
        rc.say("<color=#FFC83D>** Make sure you stay for the next match for bonus rank points! **</color>")
        activity(f"Posted the 'stay for the next match' reminder ({mark // 60} min in)", "INFO")
        print(f"[stay] reminder fired at {mark // 60} min elapsed")


def load_schedule():
    """Read schedule.json (the web CC writes it; this bot executes due items)."""
    try:
        with open(SCHEDULE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_schedule(items):
    try:
        tmp = SCHEDULE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2)
        os.replace(tmp, SCHEDULE_FILE)
    except OSError as e:
        print(f"[sched] save failed: {e}")


def _sched_when_ts(when):
    """Parse a 'YYYY-MM-DD HH:MM' (local) schedule time to an epoch, or None."""
    try:
        return time.mktime(time.strptime(str(when)[:16].replace("T", " "), "%Y-%m-%d %H:%M"))
    except (ValueError, TypeError):
        return None


def check_schedule(rc):
    """Fire any due scheduled restarts/updates: warn players in-chat at SCHED_WARN thresholds,
    then at the target time run the guarded deploy (deploy.bat -> run.bat --deploy-plugin) as a
    DETACHED subprocess so this daemon keeps running (it just reconnects across the bounce, like
    the 05:00 task). An 'update' deploys whatever pending_plugin.dll is staged; a 'restart' is a
    plain bounce. Both go through the same relay-verified pipeline."""
    items = load_schedule()
    if not items:
        return
    now = time.time()
    dirty = False
    for it in items:
        if it.get("status") != "pending":
            continue
        ts = _sched_when_ts(it.get("when", ""))
        if ts is None:
            continue
        label = "update" if it.get("type") == "update" else "restart"
        note = f" - {it['desc']}" if it.get("desc") else ""
        remaining = ts - now
        if remaining > 0:                                  # not due yet: maybe warn
            warned = _sched_warned.setdefault(it["id"], set())
            for thr in SCHED_WARN:
                if remaining <= thr and thr not in warned:
                    warned.add(thr)
                    rc.say(f"<color=#FFAA00>** SCHEDULED {label.upper()} in {thr // 60} min{note} - wrap it up! **</color>")
                    activity(f"scheduled {label} in {thr // 60} min{note}", "!")
            continue
        # due -> fire
        it["fired"] = time.strftime("%Y-%m-%d %H:%M:%S")
        dirty = True
        rc.say(f"<color=#FF6A00>** SCHEDULED {label.upper()} NOW{note} - server back in ~1 min **</color>")
        activity(f"firing scheduled {label}{note}", "!")
        try:
            subprocess.Popen(["cmd", "/c", os.path.join(_BASE_DIR, "deploy.bat")], cwd=_BASE_DIR,
                             creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
            it["status"] = "done"
            print(f"[sched] fired {label}{note} -> deploy.bat launched")
        except OSError as e:
            it["status"] = "failed"
            it["result"] = str(e)
            print(f"[sched] fire failed: {e}")
    if dirty:
        save_schedule(items)


def _player_name_pool():
    """sid -> display name, merged from every source we know (ranks.json, the name cache,
    the live roster). Used by resolve_player + the grant 'did you mean' suggestions."""
    pool = {}
    for sid, rec in RANK_DATA.items():
        nm = (rec.get("name") or "").strip()
        if nm:
            pool[sid] = nm
    for sid, nm in PLAYER_NAMES.items():
        nm = (nm or "").strip()
        if nm and sid not in pool:
            pool[sid] = nm
    for sid, p in ROSTER_BY_SID.items():
        nm = (p.get("displayName") or "").strip()
        if nm and sid not in pool:
            pool[sid] = nm
    return pool


def resolve_player(query):
    """Resolve a SteamID or display name to a SteamID, else None. Used by the admin 'grant'
    command. Tries, in order: exact SteamID, raw SteamID, exact name, unique name-prefix, unique
    substring, then a unique FUZZY match -- so admins can grant by a partial or slightly-off name
    (game names are often truncated/odd, e.g. 'GoatseWithTheAwesomeSauc'). Every step requires a
    UNIQUE match, so it never silently grants the wrong player; admin_grant logs the resolved name."""
    q = str(query).strip()
    if not q:
        return None
    if q in RANK_DATA:                       # exact SteamID we already track
        return q
    if q.isdigit() and len(q) >= 15:         # looks like a raw SteamID (can grant to anyone)
        return q
    ql = q.lower()
    pool = _player_name_pool()
    exact = [sid for sid, nm in pool.items() if nm.lower() == ql]      # 1) exact (case-insensitive) name
    if exact:
        return exact[0] if len(exact) == 1 else None                  # ambiguous exact -> refuse
    pre = [sid for sid, nm in pool.items() if nm.lower().startswith(ql)]   # 2) unique prefix
    if len(pre) == 1:
        return pre[0]
    sub = [sid for sid, nm in pool.items() if ql in nm.lower()]        # 3) unique substring
    if len(sub) == 1:
        return sub[0]
    import difflib                                                     # 4) unique fuzzy (typo/truncation tolerant)
    scored = sorted(((difflib.SequenceMatcher(None, ql, nm.lower()).ratio(), sid)
                     for sid, nm in pool.items()), reverse=True)
    if scored and scored[0][0] >= 0.82 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.08):
        return scored[0][1]
    return None


_admin_cmd_offset = None     # byte offset into ADMIN_CMD_FILE; None until pre-existing lines are skipped


def process_admin_commands(rc):
    """Apply admin commands queued by the command centre (admin_commands.jsonl). The bot
    owns ranks.json, so all manual point changes MUST flow through here (the command centre
    is a separate process and must never write ranks.json directly)."""
    global _admin_cmd_offset
    if _admin_cmd_offset is None:            # not initialized yet (set once at startup in main)
        return
    try:
        size = os.path.getsize(ADMIN_CMD_FILE)
    except OSError:
        return                               # no queue file yet -> nothing to do
    if size < _admin_cmd_offset:             # truncated/rotated
        _admin_cmd_offset = 0
    if size == _admin_cmd_offset:
        return
    try:
        with open(ADMIN_CMD_FILE, "r", encoding="utf-8", errors="replace") as f:
            f.seek(_admin_cmd_offset)
            data = f.read()
            _admin_cmd_offset = f.tell()
    except OSError:
        return
    did_changemap = False
    for line in data.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
        except ValueError:
            continue
        if cmd.get("action") == "grant":
            admin_grant(rc, cmd)
        elif cmd.get("action") == "team":
            admin_team(rc, cmd)
        elif cmd.get("action") == "changemap":
            try:
                force_change_map(rc, cmd.get("name", ""))
                did_changemap = True              # tell main() to suppress the auto mission-end vote
            except Exception as e:                # noqa: BLE001
                print(f"[admin] changemap error: {e}")
        elif cmd.get("action") == "setcfg":       # webcc settings menu: change a plugin/bot/game setting
            try:
                set_cfg_dispatch(rc, cmd.get("key", ""), cmd.get("value", ""), cmd.get("owner", "plugin"))
            except Exception as e:                # noqa: BLE001
                print(f"[admin] setcfg error: {e}")
        elif cmd.get("action") == "dumpcfg":      # webcc settings menu: ask the plugin to re-emit its live config
            try:
                _drop_plugin_cmd("dumpcfg")
            except Exception as e:                # noqa: BLE001
                print(f"[admin] dumpcfg error: {e}")
        elif cmd.get("action") == "missionpool":  # webcc Mission Pool modal: toggle a mission in/out of the votemap pool
            try:
                if set_mission_enabled(cmd.get("mission", ""), bool(cmd.get("on", True))):
                    activity(f"Mission pool: {friendly_label(cmd.get('mission', ''))} -> "
                             f"{'on' if cmd.get('on', True) else 'off'}", "MAP")
            except Exception as e:                # noqa: BLE001
                print(f"[admin] missionpool error: {e}")
        elif cmd.get("action") == "servermsg":    # webcc Messages modal: CRUD an automated server message
            try:
                ok, info = server_msg_apply(cmd.get("op", ""), cmd.get("msg", {}))
                if ok:
                    activity(f"Server message {cmd.get('op', '')}: {info}", "BOT")
            except Exception as e:                # noqa: BLE001
                print(f"[admin] servermsg error: {e}")
        elif cmd.get("action") in ("ban_steamid", "unban_steamid"):   # webcc Reports tab: ban/unban a SteamID
            try:
                bsid = str(cmd.get("sid", "")).strip()
                if not re.fullmatch(r"\d{6,20}", bsid):   # mirror the cc_web guard: defend the plugin-cmd channel
                    activity(f"{cmd.get('action')}: invalid SteamID - not applied", "!")  # vs newline/pipe injection
                    bsid = ""
                if bsid:
                    ban = cmd.get("action") == "ban_steamid"
                    _drop_plugin_cmd(("ban|" if ban else "unban|") + bsid)   # plugin list (in-memory + plugin_bans.txt)
                    try:
                        rc.send("banlist-add" if ban else "banlist-remove", bsid)   # game-native list (immediate; no player needed)
                    except Exception:             # noqa: BLE001
                        pass
                    set_report_banned(bsid, ban)
                    try:
                        refresh_banned_players()
                    except Exception:             # noqa: BLE001
                        pass
                    activity(f"{'Banned' if ban else 'Unbanned'} {bsid} (plugin + game ban list)", "ADMIN")
            except Exception as e:                # noqa: BLE001
                print(f"[admin] {cmd.get('action')} error: {e}")
        elif cmd.get("action") == "logban":       # webcc Reports 'Log ban' button: record a ban in the persistent ban log
            try:
                n = log_ban(cmd.get("sid", ""), cmd.get("name", ""), cmd.get("reason", ""))
                if n:
                    activity(f"Ban logged: {cmd.get('name', '?')} (now {n}x in the ban log)", "ADMIN")
            except Exception as e:                # noqa: BLE001
                print(f"[admin] logban error: {e}")
        elif cmd.get("action") == "rmbanlog":     # webcc Ban log 🗑 button: delete one player's logged-ban history
            try:
                if remove_ban_log(cmd.get("sid", "")):
                    activity(f"Ban-log entry removed for {cmd.get('name', '') or cmd.get('sid', '?')}", "ADMIN")
            except Exception as e:                # noqa: BLE001
                print(f"[admin] rmbanlog error: {e}")
        elif cmd.get("action") in ("clear_report", "clear_reports"):   # webcc Reports tab: clear one / all reports
            try:
                if cmd.get("action") == "clear_reports":
                    n = clear_all_reports()
                    activity(f"Cleared all reports ({n})", "ADMIN")
                else:
                    if clear_report(int(cmd.get("seq", 0))):
                        activity(f"Cleared report #{cmd.get('seq')}", "ADMIN")
            except Exception as e:                # noqa: BLE001
                print(f"[admin] {cmd.get('action')} error: {e}")
        elif cmd.get("action") == "dumpserverconfig":   # webcc Server Settings tab: re-read DedicatedServerConfig.json
            try:
                refresh_server_config()
            except Exception as e:                # noqa: BLE001
                print(f"[admin] dumpserverconfig error: {e}")
        elif cmd.get("action") == "missionaudit":   # webcc Mission Pool: re-scan official/custom missions + integrity
            try:
                refresh_mission_audit()
            except Exception as e:                # noqa: BLE001
                print(f"[admin] missionaudit error: {e}")
        elif cmd.get("action") == "missiontoggle":   # webcc Mission Pool: enable/disable a mission in the live rotation
            try:
                r = mission_set_enabled(cmd.get("group", "User"), cmd.get("name", ""), bool(cmd.get("on")))
                activity(f"Mission {'enabled' if cmd.get('on') else 'disabled'}: {cmd.get('name')}"
                         + ("" if r.get("ok") else f" (FAILED: {r.get('error')})"), "MAP")
            except Exception as e:                # noqa: BLE001
                print(f"[admin] missiontoggle error: {e}")
        elif cmd.get("action") == "missionworkshop":   # webcc Mission Pool: add a Steam Workshop mission
            try:
                r = mission_add_workshop(cmd.get("id", ""))
                activity(f"Workshop mission added: {cmd.get('id')}"
                         + ("" if r.get("ok") else f" (FAILED: {r.get('error')})"), "MAP")
            except Exception as e:                # noqa: BLE001
                print(f"[admin] missionworkshop error: {e}")
        elif cmd.get("action") == "missionupload":   # webcc Mission Pool: upload a custom mission folder (added OFF)
            try:
                sp = os.path.join(_BASE_DIR, str(cmd.get("staging", "")))
                with open(sp, encoding="utf-8") as f:
                    up = json.load(f)
                r = mission_upload(up.get("name", ""), up.get("files", []))
                try:
                    os.remove(sp)
                except OSError:
                    pass
                activity(f"Mission uploaded: {up.get('name')}"
                         + ("" if r.get("ok") else f" (FAILED: {r.get('error')})"), "MAP")
            except Exception as e:                # noqa: BLE001
                print(f"[admin] missionupload error: {e}")
        elif cmd.get("action") == "setvotemap":   # webcc Votemap settings: ballot size / mode / includes
            try:
                if set_votemap_cfg(cmd.get("key", ""), cmd.get("value")):
                    activity(f"Votemap: {cmd.get('key')} = {cmd.get('value')}", "MAP")
            except Exception as e:                # noqa: BLE001
                print(f"[admin] setvotemap error: {e}")
        elif cmd.get("action") == "banaudit":     # webcc Moderation 'Banned' tab: re-read plugin_bans.txt
            try:
                refresh_banned_players()
            except Exception as e:                # noqa: BLE001
                print(f"[admin] banaudit error: {e}")
        elif cmd.get("action") == "setserverconfig":    # webcc Server Settings tab: edit one config field (+ gpanel mirror)
            try:
                r = set_server_config(cmd.get("key", ""), cmd.get("value", ""))
                if not r.get("ok"):
                    print(f"[admin] setserverconfig {cmd.get('key')}: {r.get('error')}")
            except Exception as e:                # noqa: BLE001
                print(f"[admin] setserverconfig error: {e}")
        elif cmd.get("action") == "sysmsg":             # webcc Messages tab: edit a built-in automated message
            try:
                if sysmsg_set(cmd.get("key", ""), cmd.get("fields", {}) or {}):
                    activity(f"System message '{cmd.get('key', '')}' updated", "BOT")
            except Exception as e:                # noqa: BLE001
                print(f"[admin] sysmsg error: {e}")
        elif cmd.get("action") == "helpcfg":            # webcc Help editor: show/hide a command in the !help list
            try:
                if set_help_gate(cmd.get("cmd", ""), bool(cmd.get("on", True))):
                    activity(f"!help: '{cmd.get('cmd', '')}' {'shown' if cmd.get('on') else 'hidden'}", "BOT")
            except Exception as e:                # noqa: BLE001
                print(f"[admin] helpcfg error: {e}")
        elif cmd.get("action") == "rankladder":         # webcc Ranks modal: replace the whole rank ladder + rank-up template
            try:
                res = rank_ladder_apply(cmd.get("payload", {}) or {})
                if res.get("ok"):
                    push_plugin_ranks()                  # refresh the in-chat [Name - RANK] tags + colours immediately
                    activity(f"Rank ladder updated ({len(RANKS)} ranks)", "BOT")
                else:
                    activity(f"Rank ladder NOT updated: {res.get('error', '?')}", "!")
            except Exception as e:                # noqa: BLE001
                print(f"[admin] rankladder error: {e}")
        elif cmd.get("action") == "sharedranks":        # webcc Shared Ranks card: enable/disable + set the shared directory
            try:
                set_shared_ranks(bool(cmd.get("enabled")), str(cmd.get("dir", "") or ""))
                activity(f"Shared ranks {'ON' if SHARED_RANKS_ENABLED else 'off'}"
                         + (f" -> {SHARED_RANKS_DIR}" if SHARED_RANKS_ENABLED else ""), "BOT")
            except Exception as e:                # noqa: BLE001
                print(f"[admin] sharedranks error: {e}")
    return did_changemap


def admin_grant(rc, cmd):
    """Manually add (or subtract) rank points for a player and do every follow-on update:
    persist ranks.json, refresh the in-chat rank tag, append the audit ledger, and
    announce + record a promotion if one is crossed."""
    query = str(cmd.get("query", "")).strip()
    try:
        pts = round(float(cmd.get("points", 0)), 1)
    except (TypeError, ValueError):
        return
    if not query or pts == 0:
        return
    sid = resolve_player(query)
    if not sid:
        import difflib
        pool = _player_name_pool()
        near = difflib.get_close_matches(query.lower(), [nm.lower() for nm in pool.values()], n=3, cutoff=0.5)
        # map the lowered suggestions back to their real display names
        seen, names = set(), []
        for sug in near:
            for nm in pool.values():
                if nm.lower() == sug and nm not in seen:
                    seen.add(nm); names.append(nm); break
        hint = (" - did you mean: " + ", ".join(names)) if names else " - no close match (try the exact name, the SteamID, or click the player)"
        activity(f"admin grant: '{query}' didn't match a player{hint} - not applied", "!")
        return
    name = PLAYER_NAMES.get(sid) or RANK_DATA.get(sid, {}).get("name") or sid
    old_idx, new_idx, total = award_points(sid, name, pts)
    ledger_award(sid, name, pts, "grant", "admin grant (command centre)", total, match=None)
    save_ranks()
    push_plugin_ranks()                      # refresh the in-chat [Name - RANK] tag immediately
    activity(f"ADMIN granted {pts:+.1f} pts to {name}  ->  now {total:.1f} pts", "RANK")
    crossed, ann_idx = combined_rankup(sid, total, pts)   # #4: combined-rank crossing
    if crossed:
        _, rname, abbr, color = RANKS[ann_idx]
        rc.say(rankup_line(name, rname, abbr, color))
        activity(f"{name} promoted to {rname} ({abbr})!", "RANK")


_plugin_cmd_id = 0


def admin_team(rc, cmd):
    """Relay a command-centre TEAM action (move / spec / join / balance) to the NukeStats
    plugin by dropping a per-command file 'plugin_cmd_<id>.txt' (content 'verb|steamId|faction')
    in the container game root. The plugin processes then DELETES each file (no dedup to get
    wrong). Takes effect once the v0.6.1 plugin is loaded."""
    global _plugin_cmd_id
    verb = str(cmd.get("verb", "")).strip().lower()
    if verb not in ("move", "team", "join", "spec", "spectate", "unteam", "balance",
                    "setrank", "setfunds", "addfunds"):
        return
    sid = str(cmd.get("sid", "")).strip().replace("|", "").replace("\n", "").replace("\r", "")
    faction = str(cmd.get("faction", "")).strip().replace("|", "").replace("\n", "").replace("\r", "")   # for set*rank/*funds this 3rd field carries the NUMBER; strip framing chars (defense-in-depth, ADMIN-1)
    if verb != "balance" and not sid:
        activity(f"admin {verb}: no SteamID - not applied", "!")
        return
    try:
        _drop_plugin_cmd(f"{verb}|{sid}|{faction}")   # pooled session (atomic .tmp+rename)
    except Exception as e:                          # noqa: BLE001
        activity(f"team command relay failed: {e}", "!")
        return
    name = PLAYER_NAMES.get(sid) or sid or "(n/a)"
    if verb == "balance":
        activity("ADMIN ran a team balance pass", "TEAM")
    elif verb in ("spec", "spectate", "unteam"):
        activity(f"ADMIN moved {name} to spectate", "TEAM")
    elif verb in ("setrank", "setfunds", "addfunds"):
        activity(f"ADMIN {verb} {name} -> {faction}", "TEAM")
    else:
        activity(f"ADMIN moved {name} -> {faction}", "TEAM")


# ===================== PUBLIC SERVER DIRECTORY (opt-in GitHub "advertise server publicly") =====================
# Owners opt in via the webcc settings (Global.ListServer + Global.Region — the plugin reports these in
# PLUGIN_CFG). While listed, the bot publishes this server's DIRECTORY entry (name + region + plugin version,
# NEVER IP/host/port/SteamIDs) to a shared public GitHub repo via the Contents API so players can find it by
# name. INERT unless NO_GH_TOKEN + NO_GH_REPO are set (in run.bat); HTTP runs off the poll thread. NO cross-
# server leaderboard, NO live online status. Contract: docs/GLOBAL_LEADERBOARD_CONTRACT.md (directory section).
GH_TOKEN  = os.environ.get("NO_GH_TOKEN", "").strip()
GH_REPO   = os.environ.get("NO_GH_REPO", "").strip()                 # "owner/name" of the shared public repo
GH_BRANCH = os.environ.get("NO_GH_BRANCH", "main").strip() or "main"
SERVER_NAME_OVERRIDE = os.environ.get("NO_SERVER_NAME", "").strip()  # TODO: read ServerName from DedicatedServerConfig (Part B)
GLOBAL_DIR_INTERVAL  = 10 * 60        # how often to CHECK whether the directory entry needs (re)publishing
GLOBAL_DIR_KEEPALIVE = 6 * 3600       # re-publish at least this often even if unchanged (keeps `updated` fresh)
_global_last_dir = 0.0
_global_busy = [False]
_global_status_result = [None, None]   # (ok, ts) of the last directory publish; ts None = not yet attempted (-> webcc shows "pending")
_last_dir_sig = [None, 0.0]            # (payload-sans-timestamp, last-PUT ts) -> commit only on change / keepalive
SERVER_ID_FILE = os.path.join(_BASE_DIR, "global_server_id.txt")
GLOBAL_OPTIN_FILE = os.path.join(_BASE_DIR, "global_optin.json")   # last-known opt-in -> directory survives a bot restart / empty server


_GLOBAL_REGIONS = frozenset({"OCE", "NA", "EU", "SA", "AS", "AF", "ME", "Other"})


def _utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _load_optin():
    try:
        with open(GLOBAL_OPTIN_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_optin(d):
    try:
        with open(GLOBAL_OPTIN_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except OSError:
        pass


def _global_cfg():
    """The public-listing opt-in (list + region). AUTHORITATIVE = global_optin.json — the operator's
    explicit webcc choice, persisted by set_cfg_dispatch the moment they toggle it. This is the fix for
    the listing 'keeps turning off': the plugin's Global.ListServer bind defaults to false and only
    applies when a player is online, so it must NOT be allowed to overwrite the operator's choice. We
    only SEED global_optin.json from the plugin's reported value on first run (when nothing is persisted)."""
    p = _load_optin()
    if not p and any(k in PLUGIN_CFG for k in ("Global.ListServer", "Global.Region")):
        def _b(k):
            v = PLUGIN_CFG.get(k)
            return v is True or str(v).lower() in ("1", "true", "on", "yes")
        region = str(PLUGIN_CFG.get("Global.Region", "") or "").strip()
        p = {"list": _b("Global.ListServer"), "region": region if region in _GLOBAL_REGIONS else "Other"}
        _save_optin(p)
    region = p.get("region", "Other")
    return {"list": bool(p.get("list")), "region": region if region in _GLOBAL_REGIONS else "Other",
            "gm": str(p.get("gm", "") or "").strip()}


def _dashboard_plugin_cfg():
    """PLUGIN_CFG as the webcc settings menu should SEE it: the live plugin dump, but with the two
    public-listing keys overlaid from the AUTHORITATIVE global_optin.json. The plugin's Global.ListServer
    bind defaults to false and only applies with a player online, so on an empty server its DumpCfg
    reports false -- which made the 'List Server Publicly' + Region toggles 'keep turning off' in the UI
    even though the operator's choice was persisted. Showing the persisted truth here stops the revert."""
    if not PLUGIN_CFG:
        return None
    cfg = dict(PLUGIN_CFG)
    try:
        gc = _global_cfg()
        cfg["Global.ListServer"]    = bool(gc["list"])
        cfg["Global.Region"]        = gc["region"]
        cfg["Global.Gamemonitoring"] = gc.get("gm", "")
    except Exception:                                    # noqa: BLE001
        pass
    return cfg


def _server_id():
    """Stable opaque id for THIS server's repo files (never the IP). Generated once, stored locally."""
    try:
        with open(SERVER_ID_FILE, encoding="utf-8") as f:
            sid = f.read().strip()
        if sid:
            return sid
    except OSError:
        pass
    import uuid
    sid = uuid.uuid4().hex
    try:
        with open(SERVER_ID_FILE, "w", encoding="utf-8") as f:
            f.write(sid)
    except OSError:
        pass
    return sid


def _server_name():
    """Public-directory name: the NO_SERVER_NAME override, else the live ServerName from
    DedicatedServerConfig.json (cached by the Server Settings tab; refreshed once if not yet
    loaded), else a generic default. So the listing matches what players see in the browser."""
    if SERVER_NAME_OVERRIDE:
        return SERVER_NAME_OVERRIDE
    try:
        nm = (_srvcfg_cache.get("values") or {}).get("ServerName")
        if not nm:
            refresh_server_config()                  # one SFTP read; then cached
            nm = (_srvcfg_cache.get("values") or {}).get("ServerName")
        if nm:
            return str(nm)
    except Exception:                                # noqa: BLE001
        pass
    return "Nuclear Option server"


def _plugin_version():
    try:
        with open(os.path.join(_BASE_DIR, "deployed_plugin.json"), encoding="utf-8") as f:
            return str(json.load(f).get("version", "") or "")
    except (OSError, ValueError):
        return ""


# ── gamemonitoring.net live-banner id ────────────────────────────────────────
# The public directory entry can carry this server's gamemonitoring.net listing id so the GitHub Pages /
# README renders its LIVE banner. No API key: match our node ip:port (the GAME port, not the query port)
# against Nuclear Option's public list, or use a manual id/URL the operator set in global_optin.json ("gm").
# We publish ONLY the resolved id -- never the ip/host/port itself (same privacy rule as the rest of the entry).
GM_GAME_ID = 2168680                  # Nuclear Option on gamemonitoring.net (= Steam app id)
_GM_CACHE = {"id": None, "at": 0.0}   # resolved id + when (re-resolve at most daily; last good id persists in global_optin "gm_id")


def resolve_gamemonitoring_id(my_ip, my_game_port, manual=None, timeout=15):
    """This server's gamemonitoring.net id, or None. 1) manual override = the trailing digits of a pasted
    URL/id; 2) auto = match our ip:port against the public, key-less Nuclear Option list. Reads a public
    endpoint only; never raises."""
    if manual:
        m = re.search(r"(\d{4,})", str(manual))
        if m:
            return m.group(1)
    if not (my_ip and my_game_port):
        return None
    try:
        import urllib.request
        req = urllib.request.Request(
            "https://api.gamemonitoring.net/servers?game=%d" % GM_GAME_ID,
            headers={"User-Agent": "nuke-toolkit", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            items = (json.load(r).get("response") or {}).get("items") or []
    except Exception:                          # noqa: BLE001
        return None
    want = "%s:%s" % (my_ip, my_game_port)     # game/player port, NOT the query port
    for it in items:
        if it.get("connect") == want or (str(it.get("ip")) == str(my_ip)
                                         and str(it.get("port")) == str(my_game_port)):
            return str(it["id"]) if it.get("id") is not None else None
    return None


def _gamemonitoring_id():
    """Cached gamemonitoring id for the public-directory entry. A manual override (global_optin 'gm' = a pasted
    URL/id) wins; else best-effort auto-match using our node host + the live game Port.Value. Re-resolved at
    most daily; the last good id persists in global_optin ('gm_id') so a restart / empty server keeps the
    banner. Returns '' when unknown (the directory then simply omits the banner)."""
    opt = _load_optin()
    manual = str(opt.get("gm", "") or "").strip()
    if manual:
        rid = resolve_gamemonitoring_id(None, None, manual=manual)
        if rid:
            return rid
    now = time.time()
    if _GM_CACHE["id"] and (now - _GM_CACHE["at"]) < 86400:
        return _GM_CACHE["id"]
    rid = ""
    try:
        ip = None
        if SFTP_HOST:
            import socket as _sock
            try:
                ip = _sock.gethostbyname(SFTP_HOST)
            except Exception:                  # noqa: BLE001 - hostname unresolved -> try the raw value
                ip = SFTP_HOST
        port = (_srvcfg_cache.get("values") or {}).get("Port.Value")
        rid = resolve_gamemonitoring_id(ip, port) or ""
    except Exception:                          # noqa: BLE001
        rid = ""
    if rid:
        _GM_CACHE["id"], _GM_CACHE["at"] = rid, now
        if opt.get("gm_id") != rid:
            opt["gm_id"] = rid
            _save_optin(opt)
        return rid
    return str(opt.get("gm_id") or "")         # keep the last known id if a one-off lookup failed


def _gh_api(method, path, body=None):
    import urllib.request
    import urllib.error
    import ssl
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request("https://api.github.com" + path, data=data, method=method, headers={
        "Authorization": "Bearer " + GH_TOKEN, "Accept": "application/vnd.github+json",
        "User-Agent": "nukeoption-bot", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, context=ssl.create_default_context(), timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:                          # noqa: BLE001
        return 0, None


def _gh_put_file(path, content_str, message):
    """Create/update a repo file via the Contents API. A true 404 means create; any OTHER GET failure aborts
    (so a transient blip never blind-creates over an existing file and 422s). Retries once on 409/422."""
    import base64
    st, cur = _gh_api("GET", "/repos/%s/contents/%s?ref=%s" % (GH_REPO, path, GH_BRANCH))
    if st not in (200, 404):
        return False
    sha = cur.get("sha") if (st == 200 and isinstance(cur, dict)) else None
    body = {"message": message, "branch": GH_BRANCH,
            "content": base64.b64encode(content_str.encode("utf-8")).decode("ascii")}
    if sha:
        body["sha"] = sha
    pst, _ = _gh_api("PUT", "/repos/%s/contents/%s" % (GH_REPO, path), body)
    if pst in (409, 422):                          # stale/missing sha -> refresh once and retry
        st2, cur2 = _gh_api("GET", "/repos/%s/contents/%s?ref=%s" % (GH_REPO, path, GH_BRANCH))
        if st2 == 200 and isinstance(cur2, dict):
            body["sha"] = cur2.get("sha")
            pst, _ = _gh_api("PUT", "/repos/%s/contents/%s" % (GH_REPO, path), body)
    return pst in (200, 201)


def _gh_delete(path):
    """Best-effort delete of a repo file (GET sha -> DELETE). Never raises; returns True on success.
    Used to remove this server's directory entry when it opts out of public listing."""
    st, cur = _gh_api("GET", "/repos/%s/contents/%s?ref=%s" % (GH_REPO, path, GH_BRANCH))
    if st != 200 or not isinstance(cur, dict) or not cur.get("sha"):
        return False
    dst, _ = _gh_api("DELETE", "/repos/%s/contents/%s" % (GH_REPO, path),
                     {"message": "remove %s @ %s" % (path, _utcnow()), "branch": GH_BRANCH, "sha": cur["sha"]})
    return dst in (200, 201)


def global_dir_tick():
    """Publish the public DIRECTORY entry servers/<id>.json while listed (name + region + plugin version;
    NEVER IP/host/port/SteamIDs, no live status). Commits only when something changed (or a keepalive is
    due) so the repo isn't churned. On opt-out it deletes the listing so the public page drops it (one-shot)."""
    if not (GH_TOKEN and GH_REPO):
        return True
    gc = _global_cfg()
    if not gc["list"]:
        if _last_dir_sig[0] is not None:          # was listed this session -> opted out: remove the public listing now (one-shot)
            if _gh_delete("servers/%s.json" % _server_id()):
                activity("Delisted from the public server directory", "BOT")
            _last_dir_sig[0] = None
        return True
    sid, region = _server_id(), gc["region"]
    directory = {
        "server_id":       sid,
        "name":            _server_name(),
        "region":          region,
        "uses_nukeoption": True,
        "plugin_version":  _plugin_version(),
        "updated":         _utcnow(),
    }
    gmid = _gamemonitoring_id()                # gamemonitoring.net listing id (manual override or auto ip:port match)
    if gmid:
        directory["gamemonitoring_id"] = gmid  # GitHub Pages / README renders this server's LIVE banner from it
    sig = json.dumps({k: v for k, v in directory.items() if k != "updated"}, sort_keys=True)
    now = time.time()
    if sig == _last_dir_sig[0] and (now - _last_dir_sig[1]) < GLOBAL_DIR_KEEPALIVE:
        return True                               # unchanged + keepalive not yet due -> skip the commit
    first = _last_dir_sig[0] is None
    ok = _gh_put_file("servers/%s.json" % sid, json.dumps(directory, indent=1),
                      "list %s (%s) @ %s" % (directory["name"], region, _utcnow()))
    _global_status_result[0], _global_status_result[1] = ok, now
    if ok:
        _last_dir_sig[0], _last_dir_sig[1] = sig, now
        if first:
            activity("Listed on the public server directory (%s)" % region, "BOT")
    else:
        activity("Public directory update FAILED - check NO_GH_TOKEN / repo permissions", "!")
    return ok


def global_tick():
    """Poll-loop hook: self-rate-limit + offload the (blocking) HTTP to a one-shot daemon thread.
    Publishes the public directory entry on a slow cadence (on change / keepalive)."""
    global _global_last_dir
    if not (GH_TOKEN and GH_REPO):
        return                                     # opted out entirely -> never touch throttles / spawn a thread
    if _global_busy[0]:
        return
    now = time.time()
    if (now - _global_last_dir) < GLOBAL_DIR_INTERVAL:
        return
    _global_last_dir = now
    _global_busy[0] = True

    def _work():
        global _global_last_dir
        try:
            if global_dir_tick() is False:
                _global_last_dir = time.time() - GLOBAL_DIR_INTERVAL + 120   # failed -> retry in ~2 min
        except Exception as e:                 # noqa: BLE001
            print("[global] tick error:", e)
        finally:
            _global_busy[0] = False
    import threading
    threading.Thread(target=_work, daemon=True).start()


def global_sync_state():
    """Read-only snapshot of the public-listing state for the webcc (NO secrets: the repo owner/name is
    public, the token is never exposed). `configured` is false until NO_GH_TOKEN+NO_GH_REPO are set."""
    gc = _global_cfg()
    configured = bool(GH_TOKEN and GH_REPO)
    return {
        "configured":  configured,
        "repo":        GH_REPO or None,            # public owner/name (never the token)
        "page":        ("https://github.com/%s" % GH_REPO) if GH_REPO else None,
        "branch":      GH_BRANCH,
        "list":        gc["list"],
        "region":      gc["region"],
        "server_id":   _server_id() if configured else None,
        "last_status": {"ok": _global_status_result[0], "ts": _global_status_result[1]},
    }


def set_cfg_dispatch(rc, key, value, owner):
    """webcc settings menu: route a setting change to the right owner.
       plugin -> drop a setcfg plugin_cmd (applies live on the next HQ tick, persisted to the cfg);
       bot    -> persist to bot_overrides.json + apply the runtime global (full effect on bot restart);
       game   -> run.bat --set-votekick (applies on the next server config reload / restart)."""
    key = str(key).strip()
    owner = str(owner).strip().lower()
    val = str(value).strip()
    try:
        if owner == "plugin":
            safek = key.replace("|", "").replace("\n", " ").replace("\r", " ")
            safev = val.replace("|", "").replace("\n", " ").replace("\r", " ")
            if key != "Global.Gamemonitoring":                # bot-side only (lives in global_optin, no plugin bind) -> don't setcfg it
                _drop_plugin_cmd("setcfg|" + safek + "|" + safev)
            if key in ("Global.ListServer", "Global.Region", "Global.Gamemonitoring"):   # public-listing opt-in: persist the
                p = _load_optin()                                 # operator's explicit choice to global_optin.json IMMEDIATELY
                if key == "Global.ListServer":                    # (bot-side, no online>=1 needed) so a plugin reset / empty
                    p["list"] = safev.lower() in ("1", "true", "on", "yes")   # server default can never silently delist it
                elif key == "Global.Region":
                    p["region"] = safev if safev in _GLOBAL_REGIONS else "Other"
                else:                                             # Global.Gamemonitoring: pasted URL/id (digits extracted at resolve time)
                    p["gm"] = str(safev or "").strip()
                _save_optin(p)
            activity(f"ADMIN set {key} = {val}", "CFG")
            return {"ok": True, "needs_restart": False}
        if owner == "bot":
            short = key.split(".")[-1].split(":")[-1]
            if short not in _BOT_OVERRIDE_KEYS:
                activity(f"settings: unknown bot setting {key}", "!")
                return {"ok": False, "error": "unknown bot setting"}
            try:
                num = float(val)
                num = int(num) if num.is_integer() else num
            except ValueError:
                return {"ok": False, "error": "must be a number"}
            ov = {}
            try:
                with open(os.path.join(_BASE_DIR, "bot_overrides.json"), "r", encoding="utf-8") as f:
                    ov = json.load(f)
            except (OSError, ValueError):
                ov = {}
            ov[short] = num
            tmp = os.path.join(_BASE_DIR, "bot_overrides.json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(ov, f, indent=1)
            os.replace(tmp, os.path.join(_BASE_DIR, "bot_overrides.json"))
            globals()[short] = num                # apply now where the bot re-reads at runtime
            activity(f"ADMIN set {short} = {num} (restart bot to fully apply)", "CFG")
            return {"ok": True, "needs_restart": True}
        if owner == "game":
            on = val.lower() in ("1", "true", "on", "yes")
            try:
                subprocess.Popen(["cmd", "/c", os.path.join(_BASE_DIR, "run.bat"),
                                  "--set-votekick", "on" if on else "off"], cwd=_BASE_DIR,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except OSError as e:
                return {"ok": False, "error": str(e)}
            activity(f"ADMIN set in-game VoteKick = {'on' if on else 'off'}", "CFG")
            return {"ok": True, "needs_restart": True}
    except Exception as e:                        # noqa: BLE001
        activity(f"settings change failed: {e}", "!")
        return {"ok": False, "error": str(e)}
    return {"ok": False, "error": "unknown owner"}


def _drop_plugin_cmd(body: str):
    """Atomically drop a plugin_cmd_<id>.txt for the NukeStats plugin to consume.
    Uses the persistent bot SFTP session (no fresh handshake per whisper/command)."""
    global _plugin_cmd_id
    cid = int(time.time() * 1000)
    if cid <= _plugin_cmd_id:
        cid = _plugin_cmd_id + 1
    _plugin_cmd_id = cid
    tmp, final = f"plugin_cmd_{cid}.tmp", f"plugin_cmd_{cid}.txt"
    payload = (body.rstrip("\n") + "\n").encode("utf-8")

    def _w(sftp):
        with sftp.open(tmp, "wb") as f:
            f.write(payload)
        try:
            sftp.rename(tmp, final)
        except OSError:
            try: sftp.remove(final)
            except OSError: pass
            sftp.rename(tmp, final)
    _sftp_op(_w)


# Whisper delivery. The plugin 'tell' command (private per-player message) is NOT
# delivering reliably on v0.7.4 (the bot drops it + the plugin logs "[cmd] recv: tell"
# with no error, but TellPlayer's RpcTargetServerMessage no-ops from the poll context -
# likely because the plugin enumerates players via FindObjectsOfType instead of the
# game's UnitRegistry.playerLookup, so p.Owner is null and the send is skipped). Until a
# plugin fix is built + verified (needs a server restart), replies go to ALL-CHAT, which
# is proven to work. These are on-demand command replies (only sent when a player types a
# command) so all-chat isn't spammy. Flip to True once the v0.7.5 'tell' fix is live.
WHISPER_VIA_TELL = False


def whisper(rc, sid, *lines):
    """Reply to a player's command. Private via the plugin 'tell' command when
    WHISPER_VIA_TELL is on (and it's verified working); otherwise all-chat (reliable)."""
    sid = str(sid or "").replace("|", "")
    parts = [str(l).replace("|", "/") for l in lines if l is not None]
    if not parts:
        return
    nm = PLAYER_NAMES.get(sid) or RANK_DATA.get(sid, {}).get("name") or sid or "?"
    summary = _strip_color(parts[0])[:50]
    extra = f"  (+{len(parts) - 1} lines)" if len(parts) > 1 else ""
    if WHISPER_VIA_TELL and sid:
        try:
            _drop_plugin_cmd("tell|" + sid + "|" + "\x1f".join(parts))
            activity(f"whispered {nm}: {summary}{extra}", "PM")
            return
        except Exception as e:                       # noqa: BLE001
            activity(f"whisper relay failed ({e}) - falling back to chat", "!")
    for l in parts:
        rc.send("send-chat-message", l)   # send directly (not rc.say) so it doesn't also log a
    activity(f"replied to {nm}: {summary}{extra}", "CHAT")   # [BOT] line - the reply logs ONCE, here


def broadcast(rc, lines, label):
    """Post several lines to ALL-CHAT as one logical message, logging a SINGLE compact activity
    summary ('<label> - sent +N lines to server') instead of one [BOT] line per line (keeps the
    webcc activity feed readable for big posts like the leaderboard / !help)."""
    parts = [str(l).replace("|", "/") for l in lines if l is not None]
    if not parts:
        return
    for l in parts:
        rc.send("send-chat-message", l)   # send directly (not rc.say) so each line doesn't log
    activity(f"{label} - sent +{len(parts)} lines to server", "BOT")


def tell_player(sid, *lines):
    """Send a PRIVATE (client-side) reply to ONE player via the plugin's TellPlayer (the 'tell' verb) --
    the same mechanism !spec / team-moves use, so only that player sees it. NO all-chat fallback: the whole
    point is to keep long/noisy replies (e.g. !help) out of public chat. The asker must be online (plugin
    commands need a player present), which they are when they just typed the command.
    Lines are joined with U+2028 (LINE SEPARATOR) into ONE message, NOT \\x1f-split into many: the plugin
    splits the body on \\x1f and sends one RpcTargetServerMessage per piece, and a rapid 12-message burst
    didn't render -- a single message does. U+2028 survives the file command-channel (File.ReadAllLines
    only breaks on \\r/\\n) and renders as a line break client-side, so the whole reply arrives as one
    multi-line message with the colours intact."""
    sid = str(sid or "").replace("|", "")
    parts = [str(l).replace("|", "/") for l in lines if l is not None]
    if not sid or not parts:
        return
    _drop_plugin_cmd("tell|" + sid + "|" + "\u2028".join(parts))
    nm = PLAYER_NAMES.get(sid) or RANK_DATA.get(sid, {}).get("name") or sid
    activity(f"private reply to {nm} ({len(parts)} lines, one message)", "PM")


def help_lines():
    # Public all-chat reply, built dynamically from _HELP_REGISTRY: each command's text is editable (webcc
    # Help editor) and a command is OMITTED when its gate/feature is OFF (e.g. !votemap drops out when the
    # votemap kill-switch is off). 2 commands/line separated by " || ", each "<cmd> - <what it does>";
    # group order stats(green)->teams(cyan)->match(amber)->info(grey).
    hcfg = _help_cfg()
    vm_enabled = _votemap_cfg()["enabled"]
    tokens = []
    for g in _HELP_GROUP_ORDER:
        for e in _HELP_REGISTRY:
            if e[1] == g and _help_gate_open(e, hcfg, vm_enabled):
                tokens.append(sysmsg_text("help_" + e[0], e[3]))
    lines = [sysmsg_text("help_header", _HELP_TEXT_DEFAULTS["help_header"])]
    for i in range(0, len(tokens), 2):
        lines.append(" || ".join(tokens[i:i + 2]))
    return lines


def balance_lines():
    return [
        "<color=#FFD200>=== TEAM BALANCING (PvP) ===</color>",
        "Teams are kept even. Join the FULLER side and you'll get a warning, then be moved to spectator.",
        "To switch sides yourself: type <color=#36FFD0>!swapteam</color> to move to the smaller team (you keep your points). It only works if the other team has fewer players.",
        "Mid-match the bot may move the newest pilot off the bigger side (you get a 10s warning first).",
    ]


def spectator_tip_lines(pvp=False):
    # PvE: no spectator tip at all. PvP: only the longer team-balance message.
    if not pvp:
        return []
    return ["On the bigger team? Type <color=#36FFD0>!swapteam</color> to switch to the smaller side instantly - you keep your points and progress."]


def leaderboard_lines(steamid=None):
    """Server-rank leaderboard as chat lines. With steamid (the !leaderboard asker) it LEADS with that
    player's own position + who's right above them (mirrors the !skill 'next up' format), then the
    Top-5 by points + Top-5 by skill. Without steamid (the 30-min auto-post) it's just the top lists."""
    out = []
    # With cross-server sharing ON, rank the COMBINED board (this server + the host's other servers)
    # so the in-game leaderboard AGREES with !rank and the baked name tag, and peer-only players show.
    # Consistent with the webcc leaderboard (both read the same aggregate). Skill stays per-server.
    src = RANK_DATA
    if SHARED_RANKS_ENABLED:
        try:
            agg = read_aggregate_ranks()               # {sid: {name, points, wins, losses}} summed across servers
            if agg:
                src = agg
        except Exception:                              # noqa: BLE001 - a leaderboard must never raise
            src = RANK_DATA
    pts_board = [(s, r) for s, r in src.items() if r.get("points", 0) > 0]
    pts_board.sort(key=lambda kv: kv[1].get("points", 0), reverse=True)
    table = skill_table()

    if steamid is not None:                            # personalized header for the asker
        rec = src.get(steamid)
        mypts = rec.get("points", 0) if rec else 0
        idx = next((i for i, (s, _) in enumerate(pts_board) if s == steamid), None)
        if idx is None or mypts <= 0:
            out.append("<color=#FFD200>Server rank:</color> you're unranked - score points to get on the leaderboard!")
        else:
            _, _, myabbr, mycolor = RANKS[rank_index_for(mypts)]
            line = (f"<color=#FFD200>Your server rank: #{idx + 1} of {len(pts_board)}</color> - "
                    f"<color={mycolor}>[{myabbr}]</color> {_pts(mypts)}.")
            if idx > 0:
                asid, arec = pts_board[idx - 1]
                apts = arec.get("points", 0)
                _, _, aabbr, acolor = RANKS[rank_index_for(apts)]
                line += (f"  Above you: <color={acolor}>[{aabbr}]</color> {arec.get('name', asid)} - "
                         f"{_pts(apts)} (+{_pts(apts - mypts)} to pass).")
            else:
                line += "  <color=#FFD200>You're #1 on the server!</color>"
            out.append(line)

    if not pts_board and not table:
        return out or ["<color=#FFD200>Leaderboard:</color> no ranked pilots yet - score points "
                       "and fly 5+ lives to get on the board!"]
    if pts_board:
        out.append("<color=#FFD200>=== TOP 5 BY POINTS (server rank) ===</color>")
        for i, (sid_b, rec) in enumerate(pts_board[:5], 1):
            bpts = rec.get("points", 0)
            _, _, babbr, bcolor = RANKS[rank_index_for(bpts)]
            out.append(f"  {i}. <color={bcolor}>[{babbr}]</color> {rec.get('name', sid_b)} - {_pts(bpts)}")
    if table:
        out.append("<color=#36FFD0>=== TOP 5 BY SKILL (avg pts/life · /10) ===</color>")
        for sid_b, rec, P in table[:5]:                # in order, no ranking number
            sr = skill_ranking(P, table)
            out.append(f"  {_skill_namecolour(rec)} - <color=#36FFD0>{P:.0f} pts/life · {sr:.1f}/10</color>")
    return out


def main():
    rc = RemoteCommand(RCMD_HOST, RCMD_PORT)
    if LOCAL_CONSOLE_PATH:
        print("[bot] local console mode: tailing " + LOCAL_CONSOLE_PATH + " ; commands -> %s:%d" % (RCMD_HOST, RCMD_PORT))
        console = ConsoleSource(LOCAL_CONSOLE_PATH)
    else:
        console = SFTPConsoleSource(SFTP_HOST, SFTP_PORT, SFTP_USER, SFTP_PASS, SFTP_LOG_PATH)

    state = "IDLE"               # IDLE -> APPROVAL (!votemap) or VOTING (map ballot)
    votes = {}                   # steamid -> map option key
    first_vote_at = {}           # option key -> time of its first vote (tie-breaker)
    vote_ends_at = 0
    vote_context = "mission_end" # what triggered the current map vote
    approvals = {}               # steamid -> bool (yes/no) during a !votemap poll
    approval_ends_at = 0
    approval_threshold = 0
    approval_players = 0
    cooldown_until = 0              # anti-spam gate for player-initiated !votemap only
    suppress_mission_end_until = 0  # swallow the self-induced "Mission complete" after a !votemap switch
    last_console_poll = 0
    last_side = None            # side that last gained a base (for capture awards)
    last_capture = {}           # base -> time, to ignore duplicate capture log lines
    last_result_at = 0          # to ignore the duplicate mission-result line
    last_mission_end_at = 0     # to ignore the duplicate "Mission complete" burst
    last_rank_shown = {}        # steamid -> time, to throttle per-chat rank lines
    last_namesync = 0           # last refresh of the player-name cache
    known_online = set()        # steamids seen online last poll (for join announces)
    seeded_online = False       # skip the first poll so we don't "welcome" everyone
    server_up = True            # connection health, for clean up/down activity lines
    last_thanks_at = time.time()  # last "thanks for playing" message (+10min)
    last_leaderboard_at = time.time()  # last auto leaderboard post (+30min during a match)
    last_spectip_at = time.time()      # last spectator/team-switch tip (+12min)
    last_rank_push = 0.0          # last push of plugin_ranks.txt to the container
    last_skill_push = 0.0         # last push of plugin_skill.txt to the container
    last_state_write = 0.0        # last dashboard_state.json write (command-centre feed)
    last_mtime_poll = 0.0         # last get-mission-time poll (for the dashboard header)
    last_mirror_trim = time.time()  # last console_mirror.log size check
    mtime = [0, 0, 0]             # cached (currentTime, maxTime, fetched_at) for the dashboard

    load_ranks()
    maybe_reset_skills()         # one-time: zero skill data for the corrected life-reason logic

    # seed the current mission name (best effort) so the first match record is labelled
    refresh_current_mission(rc)

    # start applying command-centre admin actions from NOW (skip any stale queued lines
    # left over from before this bot started; new grants written after this are processed)
    global _admin_cmd_offset
    try:
        _admin_cmd_offset = os.path.getsize(ADMIN_CMD_FILE)
    except OSError:
        _admin_cmd_offset = 0

    def open_map_vote(context):
        nonlocal votes, first_vote_at, vote_ends_at, vote_context, state
        votes = {}
        first_vote_at = {}
        open_vote(len(known_online))   # build a fresh ballot (force_pvp uses the live player count)
        announce_options(rc)
        vote_ends_at = time.time() + VOTE_DURATION
        vote_context = context
        state = "VOTING"
        activity(f"Map vote open for {VOTE_DURATION}s - {len(VOTE_OPTIONS)} maps on the ballot "
                 f"(players type !1-!{len(VOTE_OPTIONS)})", "VOTE")

    print("[bot] running. Ctrl-C to stop.")
    activity("====== Bot started - watching the server ======")
    while True:
        now = time.time()

        # --- drain delayed welcomes (deadline-based; runs every loop tick) ---
        if WELCOME_QUEUE:
            for sid_w in [s for s, (dl, _n) in list(WELCOME_QUEUE.items()) if now >= dl]:
                _dl, nm_w = WELCOME_QUEUE.pop(sid_w)
                if sid_w in ROSTER_BY_SID and sid_w not in WELCOMED:
                    say_welcome(rc, sid_w, nm_w)

        if now - last_console_poll >= CONSOLE_POLL_INTERVAL:
            last_console_poll = now
            lines = console.poll()
            # mirror the whole batch in one write so the command centre can show the
            # live server/BepInEx console. Done BEFORE parsing so an unhandled parse
            # error can't drop a cycle's mirror lines (best-effort; never affects parsing).
            mirror_console_batch(lines)
            for line in lines:
                # real per-player score from the NukeStats plugin (frequent; handle
                # first and skip). Inert until the plugin is emitting these lines.
                ns = NOSTATS_RE.search(line)
                if ns:
                    try:
                        handle_stats_line(rc, json.loads(ns.group(1)))
                    except (ValueError, TypeError):
                        pass
                    continue
                # anti-grief: a single connection storming the game's RPC rate
                # limiter (macro/exploit command-flood) -> auto-kick + Reports entry.
                rl = RATELIMIT_DROP_RE.search(line)
                if rl:
                    note_ratelimit_drop(rl.group(1), rl.group(2), now)
                    continue
                # remember which side just took a base (capturing side for the
                # TOTAL CAPTURE line that follows it)
                am = ADD_AIRBASE_RE.search(line)
                if am:
                    last_side = am.group(2)
                    continue
                # a base was captured -> rank up the capturing team. Consume
                # last_side so a stale value can never credit an unrelated capture,
                # and key the dedup by (base, side) so a genuine opposite-side
                # recapture within 20s still scores.
                cm = CAPTURE_RE.search(line)
                if cm:
                    base = cm.group(1).strip()
                    side, last_side = last_side, None
                    key = (base, (side or "").lower())
                    if side and now - last_capture.get(key, 0) > 20:
                        last_capture[key] = now
                        handle_capture(rc, side, base)
                    continue
                # mission result -> award a win (deduped); a result also ends the
                # mission, so clear the capturing-side carry-over.
                gm = GAME_RESULT_RE.search(line)
                if gm:
                    last_side = None
                    if now - last_result_at > 60:
                        # only mark the result handled if it actually processed; a
                        # roster-read blip returns False so a re-emitted result line
                        # can retry rather than locking in a false defeat.
                        if handle_result(rc, gm.group(1)):
                            last_result_at = now
                    continue
                # a mission ending -> finalize the just-ended match in ANY state, so a
                # mission that ends mid-vote still gets its own record and can't bleed
                # into the next one; then, only when idle, show ranks + open the vote.
                # Deduped so the duplicate "Mission complete" burst doesn't re-fire.
                if MISSION_END_RE.search(line):
                    if now - last_mission_end_at > 15:
                        last_mission_end_at = now
                        last_side = None       # don't carry a capture side across missions
                        roster = get_players(rc)
                        match_finalize(rc, roster)   # close + persist the match that just ended
                        try:
                            fire_event_messages(rc, "match_end")   # owner match_end messages
                        except Exception as e:        # noqa: BLE001
                            print(f"[servermsg] match_end error: {e}")
                        # A mission ending is the PRIMARY trigger for the next-map vote, so it
                        # must NOT be blocked by the !votemap anti-spam cooldown (that gates only
                        # player-initiated votes). The only mission-ends we skip are the forced cut
                        # we caused ourselves right after a !votemap switch, and one that arrives
                        # while a map vote is already running.
                        if state != "VOTING" and now >= suppress_mission_end_until:
                            announce_rank_roster(rc, roster,
                                                 "<color=#FFD200>== End of mission - current ranks ==</color>")
                            if _votemap_cfg()["enabled"]:
                                activity("Mission ended - showing ranks, opening the map vote", "MAP")
                                open_map_vote("mission_end")
                                print("[bot] mission complete detected -> roster + vote opened")
                            else:
                                activity("Mission ended - map voting is OFF; the server rotation picks the next map", "MAP")
                                print("[bot] mission complete -> votemap disabled; server rotation advances")
                        else:
                            why = ("a map vote is already in progress" if state == "VOTING"
                                   else "just switched via !votemap")
                            activity(f"Mission ended ({why}) - no new vote opened", "MAP")
                            print(f"[bot] mission complete detected -> vote skipped ({why})")
                    continue

                parsed = parse_chat_line(line)
                if not parsed:
                    continue
                steamid = parsed["steamid"]
                text = parsed["message"].strip()
                low = text.lower()

                # show what each player typed (messages, commands, votes); flag the admin
                if LOG_CONVERSATION and text:
                    who = (PLAYER_NAMES.get(steamid)
                           or RANK_DATA.get(steamid, {}).get("name") or steamid)
                    if steamid in ADMIN_SIDS:
                        activity(f"[ADMIN] {who}: {text}", "!")     # stands out in the activity feed
                    else:
                        activity(f"{who}: {text}", "CHAT")

                # a player checks their rank (detailed breakdown)
                if low == "!rank":
                    pts = player_points(steamid)
                    nm = (PLAYER_NAMES.get(steamid)
                          or RANK_DATA.get(steamid, {}).get("name") or "Pilot")
                    label, color, tail = rank_progress(pts)
                    whisper(rc, steamid, f"<color={color}>{label}</color> - {nm}: "
                            f"{_pts(pts)} ({tail})")
                    continue

                # team-kill (friendly fire) policy explainer (private)
                if low == "!notk":
                    whisper(rc, steamid,
                            "<color=#FF5555>=== NO TEAM KILLING ===</color>",
                            "Destroying a FRIENDLY player's aircraft, vehicle or building is friendly fire - it's detected and auto-punished.",
                            "<color=#FFD200>1st</color> time in a match: you're <color=#FF8C00>ejected</color> from your plane with a warning.",
                            "<color=#FFD200>2nd</color> time: <color=#FF8C00>kicked</color> - and if you rejoin, your in-game rank is reset to 0.",
                            "<color=#FFD200>3rd</color> time: <color=#FF0000>banned from the server</color>.",
                            "Counts reset each match. TKs are almost always avoidable - check your targets before firing.")
                    continue

                # how PvP team balancing works (private)
                if low == "!balance":
                    whisper(rc, steamid, *balance_lines())
                    continue

                # all-time leaderboard: top 5 by points + top 5 by skill (private to asker)
                if low == "!leaderboard":
                    whisper(rc, steamid, *leaderboard_lines(steamid))
                    continue

                # why do I have these points? (audit, private)
                if low == "!why":
                    rows = recent_ledger_for(steamid, 4)
                    if not rows:
                        whisper(rc, steamid, "<color=#FFD200>No points logged for you yet.</color>")
                    else:
                        whisper(rc, steamid, "<color=#FFD200>Your recent points:</color>",
                                *[(f"  +{e.get('pts')}  [{e.get('category','')}] {e.get('reason','')}"
                                   if e.get('pts') else
                                   f"  · [{e.get('category','')}] {e.get('reason','')}") for e in rows])
                    continue

                # skill rating + who's just above you
                if low == "!skill":
                    rec = RANK_DATA.get(steamid)
                    P = skill_rating(rec)
                    if P is None:
                        have = rec.get("lives", 0) if rec else 0
                        whisper(rc, steamid, f"<color=#FFD200>Skill: unranked - complete {max(1, SKILL_MIN_LIVES - have)} "
                                f"more life/lives (a life = spawn until you're shot down or eject) to qualify.</color>")
                    else:
                        table = skill_table()
                        sr = skill_ranking(P, table)
                        idx = next((i for i, (s, _, _) in enumerate(table) if s == steamid), None)
                        line = f"<color=#36FFD0>Your skill: {P:.0f} pts/life  ({sr:.1f}/10).</color>"
                        if idx is not None and idx > 0:
                            _asid, arec, aP = table[idx - 1]
                            line += f"  Next up: {_skill_namecolour(arec)} <color=#36FFD0>- {aP:.0f} pts/life.</color>"
                        elif idx == 0:
                            line += "  <color=#FFD200>You're the #1 skill pilot!</color>"
                        whisper(rc, steamid, line)
                    continue

                # points this life vs last life (private to asker)
                if low == "!points":
                    rec = RANK_DATA.get(steamid)
                    cur = round(rec.get("curLife", 0.0), 1) if rec else 0.0
                    last = rec.get("lastLife") if rec else None
                    last_str = f"{round(last, 1):g}" if last is not None else "-"
                    whisper(rc, steamid,
                            f"<color=#36FFD0>This life: {cur:g} pts</color>   "
                            f"<color=#9fd6b0>Last life: {last_str} pts</color>")
                    continue

                # command help. NOTE: ideally private/client-side (TODO: handle natively in the plugin like
                # !spec -- the bot-relayed plugin 'tell' verb logs "delivering" but doesn't render, while
                # !spec's TellPlayer does; pending a plugin redeploy). For now it goes to chat so it WORKS.
                if low == "!help":
                    broadcast(rc, help_lines(), "!help")
                    continue

                # after a normal chat message, post just the player's rank tag
                if (SHOW_RANK_ON_CHAT and state == "IDLE" and not low.startswith("!")
                        and now - last_rank_shown.get(steamid, 0) >= RANK_CHAT_THROTTLE):
                    last_rank_shown[steamid] = now
                    rc.say(rank_tag(player_points(steamid)))

                # a player calls a mid-mission map vote
                if state == "IDLE" and now >= cooldown_until and low == "!votemap":
                    if not _votemap_cfg()["enabled"]:
                        rc.say("<color=#FF5555>Map voting is currently disabled by the server.</color>")
                        continue
                    players = get_players(rc)
                    n = max(len(players), 1)
                    caller = next((p.get("displayName") for p in players
                                   if str(p.get("steamId")) == steamid), "A player")
                    activity(f"{caller} called a map-change vote", "VOTE")
                    if n <= 1:
                        rc.say(f"<color=#55FF55>{caller} called a map vote - only player, "
                               f"so it's on!</color> Pick the next map:")
                        print(f"[votemap] {steamid} solo -> auto-pass")
                        activity(f"{caller} is the only player - map vote opens automatically", "VOTE")
                        open_map_vote("votemap")
                    else:
                        approvals = {steamid: True}      # the caller counts as a Yes
                        approval_threshold = n // 2 + 1
                        approval_players = n
                        approval_ends_at = now + APPROVAL_DURATION
                        state = "APPROVAL"
                        rc.say(f"<color=#FFFF00>{caller} wants to change the map!</color> "
                               f"Type !y or !n ({APPROVAL_DURATION}s) - "
                               f"need {approval_threshold} of {n} to agree.")
                        print(f"[votemap] {steamid} -> approval poll, need {approval_threshold}/{n}")
                    continue

                # approval poll: tally !y / !n
                if state == "APPROVAL":
                    if low == "!y":
                        approvals[steamid] = True
                        print(f"[approval] {steamid} -> yes")
                    elif low == "!n":
                        approvals[steamid] = False
                        print(f"[approval] {steamid} -> no")
                    continue

                # map vote: tally the numbers
                if state == "VOTING":
                    opt = extract_vote(parsed["message"])
                    if opt:
                        votes[steamid] = opt
                        first_vote_at.setdefault(opt, now)
                        print(f"[vote] {steamid} -> {opt}")

        # refresh the player-name cache and welcome anyone who just joined.
        # Only act on a confident reading (a dict reply); a None means the command
        # errored -- skip it so a transient blip doesn't re-"welcome" everyone.
        if now - last_namesync >= JOIN_POLL_INTERVAL:
            last_namesync = now
            resp = rc.get_player_list()
            if isinstance(resp, dict):
                if not server_up:
                    server_up = True
                    activity("Reconnected to the server", "OK")
                players = [p for p in (resp.get("Players") or resp.get("players") or [])
                           if isinstance(p, dict)]
                # keep the per-sid roster (faction + name) fresh for the dashboard table
                ROSTER_BY_SID.clear()
                ROSTER_BY_SID.update({str(p.get("steamId")): p for p in players if p.get("steamId")})
                roster_changed = False
                for p in players:
                    sid_p = str(p.get("steamId") or "")
                    nm_p = _strip_rank_tag(p.get("displayName"))   # drop any [ABBR] rank tag
                    if nm_p is not None:
                        p["displayName"] = nm_p          # clean ROSTER_BY_SID's dict (same ref)
                    if sid_p and nm_p:
                        PLAYER_NAMES[sid_p] = nm_p
                        # log everyone seen online at rank 0, even if they never score
                        if ensure_player(sid_p, nm_p):
                            roster_changed = True
                if roster_changed:
                    save_ranks()
                current = {str(p.get("steamId")) for p in players if p.get("steamId")}
                if seeded_online:
                    # Welcome any not-yet-welcomed player whose NAME we actually know now.
                    # Iterating `current` (not just brand-new sids) means a player first seen
                    # before their name synced still gets welcomed on a later 5s poll once it
                    # does -> no more "A pilot". WELCOMED dedups; it's cleared when they leave.
                    for sid_j in current:
                        if sid_j in WELCOMED or sid_j in WELCOME_QUEUE:
                            continue
                        nm_j = PLAYER_NAMES.get(sid_j) or RANK_DATA.get(sid_j, {}).get("name")
                        if nm_j:
                            queue_welcome(sid_j, nm_j)   # delayed ~5s; sent from the loop drain
                    for sid_l in known_online - current:
                        nm_l = (PLAYER_NAMES.get(sid_l)
                                or RANK_DATA.get(sid_l, {}).get("name") or "A pilot")
                        WELCOMED.discard(sid_l)         # so a rejoin is welcomed again
                        WELCOME_QUEUE.pop(sid_l, None)  # left within the delay -> no welcome
                        activity(f"{nm_l} left   -  {len(current)} online", "LEFT")
                else:
                    seeded_online = True
                    activity(f"{len(current)} player(s) currently online", "INFO")
                known_online = current
            elif server_up:
                server_up = False
                activity("Lost connection to the server - retrying every few seconds...", "!")

        # every 10 min while players are on + idle: friendly reminder of the commands.
        # Only advance the timer when it actually sends, so it isn't "used up" during
        # a vote or an empty server.
        if (sysmsg_on("thanks") and now - last_thanks_at >= sysmsg_interval("thanks", THANKS_INTERVAL)
                and known_online and state == "IDLE"):
            last_thanks_at = now
            rc.say(sysmsg_text("thanks", "<color=#FFD200>Thanks for playing!</color> For a list of "
                                         "commands type <color=#55FF55>!help</color>"))

        # every 30 min during an active match: auto-post the leaderboard to chat
        if (sysmsg_on("leaderboard") and now - last_leaderboard_at >= sysmsg_interval("leaderboard", LEADERBOARD_INTERVAL)
                and known_online and state == "IDLE"):
            last_leaderboard_at = now
            broadcast(rc, leaderboard_lines(), "Leaderboard")

        # every 12 min while players are on: how to spectate / switch to the smaller team.
        # The team-switch line only shows in a PvP match (both factions have players).
        if (sysmsg_on("spectip") and now - last_spectip_at >= sysmsg_interval("spectip", SPECTIP_INTERVAL)
                and known_online and state == "IDLE"):
            last_spectip_at = now
            facs = {(p.get("faction") or "").lower() for p in ROSTER_BY_SID.values()}
            facs.discard(""); facs.discard("none"); facs.discard("null")
            for ln in spectator_tip_lines(pvp=len(facs) >= 2):
                rc.say(ln)

        # owner-defined automated messages (interval + daily clock triggers; event triggers fire
        # from the match start/end hooks). Never let a bad message break the main loop.
        try:
            check_server_messages(rc, now, known_online, state)
        except Exception as e:                # noqa: BLE001
            print(f"[servermsg] tick error: {e}")

        # keep the plugin's chat-rank lookup fresh (only needed when the plugin runs)
        if USE_PLUGIN_SCORE and (_RANK_PUSH_FLAG[0] or now - last_rank_push >= PLUGIN_RANK_PUSH_INTERVAL):
            last_rank_push = now
            _RANK_PUSH_FLAG[0] = False
            push_plugin_ranks()

        # keep the plugin's skill-rating lookup fresh (drives skill-based auto-balance)
        if USE_PLUGIN_SCORE and (_SKILL_PUSH_FLAG[0] or now - last_skill_push >= PLUGIN_RANK_PUSH_INTERVAL):
            last_skill_push = now
            _SKILL_PUSH_FLAG[0] = False
            push_plugin_skill()

        # --- command-centre feed: apply queued admin actions, refresh clock + state ---
        if process_admin_commands(rc):        # e.g. grant points; True => an admin 'Change map' just cut the match over
            suppress_mission_end_until = now + ROLLOVER_SECONDS + 25   # swallow the self-induced "Mission complete"
            cooldown_until = now + POST_VOTE_COOLDOWN                  # block a player !votemap right after
            state = "IDLE"                                            # cancel any vote in progress so the choice sticks
        if server_up and now - last_mtime_poll >= 15:   # skip 2 blocking rcmds during an outage
            last_mtime_poll = now
            mt = rc.get_mission_time()
            cur = find_number(mt, "current")
            mx = find_number(mt, "max")
            refresh_current_mission(rc)       # settle CURRENT_MISSION FIRST (also self-heals "(unknown)") so the
                                              # mission-time-warning dedupe key is the final value, never the
                                              # transient post-vote name -> no double "Mission time: X remaining"
            if cur is not None and mx is not None:
                mtime = [cur, mx, now]
                check_mission_time_warnings(rc, mtime, CURRENT_MISSION)
            if cur is not None and mx is not None:
                try:
                    check_match_milestones(rc, mtime)   # start-of-match bonus + 'stay for next match' reminders
                except Exception as e:        # never let a milestone hiccup break the main loop
                    print(f"[milestone] check error: {e}")
            try:
                check_schedule(rc)            # fire any due scheduled restarts/updates (warns players first)
                global_tick()                 # opt-in public server-directory publish (self-throttled; HTTP off-thread)
            except Exception as e:            # never let a schedule hiccup break the main loop
                print(f"[sched] check error: {e}")
        if now - last_state_write >= STATE_WRITE_INTERVAL:
            last_state_write = now
            approval_info = None
            if state == "APPROVAL":
                approval_info = {
                    "yes":     sum(1 for v in approvals.values() if v),
                    "need":    approval_threshold,
                    "players": approval_players,
                    "ends_in": max(0, int(approval_ends_at - now)),
                }
            write_dashboard_state(state=state, server_up=server_up, online=known_online,
                                  votes=votes, vote_ends_at=vote_ends_at,
                                  vote_context=vote_context, approval=approval_info, mtime=mtime)
        if now - last_mirror_trim >= 60:
            last_mirror_trim = now
            trim_console_mirror()

        # approval poll closes: pass -> open a map vote; fail -> nothing happens
        if state == "APPROVAL" and now >= approval_ends_at:
            yes = sum(1 for v in approvals.values() if v)
            if yes >= approval_threshold:
                rc.say(f"<color=#55FF55>Map change approved</color> ({yes}/{approval_players}) - "
                       f"vote for the next map:")
                print(f"[votemap] approved {yes}/{approval_players} -> map vote")
                activity(f"Map-change vote passed ({yes}/{approval_players} yes) - opening map vote", "VOTE")
                open_map_vote("votemap")
            else:
                rc.say(f"<color=#FF5555>Map change rejected</color> ({yes}/{approval_players} yes).")
                print(f"[votemap] rejected {yes}/{approval_players}")
                activity(f"Map-change vote rejected ({yes}/{approval_players} yes)", "VOTE")
                cooldown_until = now + POST_VOTE_COOLDOWN
                state = "IDLE"

        # map vote closes -> apply winner (force the cut-over only for a !votemap vote)
        if state == "VOTING" and now >= vote_ends_at:
            force = (vote_context == "votemap")
            apply_winner(rc, votes, first_vote_at, force_switch=force)
            cooldown_until = now + POST_VOTE_COOLDOWN
            if force:
                # the mid-mission cut logs its own "Mission complete" ~ROLLOVER_SECONDS later;
                # swallow that one so it doesn't immediately open a second (mission-end) vote.
                suppress_mission_end_until = now + ROLLOVER_SECONDS + 25
            state = "IDLE"

        time.sleep(0.3)


def test_conn():
    """Verify the remote-command channel and show the raw get-mission-time reply."""
    rc = RemoteCommand(RCMD_HOST, RCMD_PORT)
    print(f"[test] connecting to remote commands at {RCMD_HOST}:{RCMD_PORT} ...")
    resp = rc.get_mission_time()
    if resp is None:
        print("[test] FAILED - no response. Check RCMD_HOST/RCMD_PORT and that the")
        print("       TCP port is reachable from this machine (firewall / ask Legion).")
    else:
        print(f"[test] OK - got a reply: {resp}")
        cur = find_number(resp, "current")
        mx = find_number(resp, "max")
        if cur is not None and mx is not None:
            rem = mx - cur
            print(f"[test] Mission time: {int(cur)}s elapsed of {int(mx)}s -> "
                  f"~{int(rem)}s ({int(rem)//60}m{int(rem)%60:02d}s) remaining. Channel works!")
        else:
            print("[test] Channel works, but couldn't parse current/max from the reply above.")


def test_chat(seconds=20):
    """Verify the vote-reading channel: watch the log and print any chat it sees."""
    console = SFTPConsoleSource(SFTP_HOST, SFTP_PORT, SFTP_USER, SFTP_PASS, SFTP_LOG_PATH)
    print(f"[test] watching the console log for {seconds}s - go type in game chat now...")
    end = time.time() + seconds
    seen = 0
    while time.time() < end:
        for line in console.poll():
            parsed = parse_chat_line(line)
            if parsed:
                seen += 1
                print(f"[test] chat from {parsed['steamid']}: {parsed['message']!r}")
        time.sleep(1.5)
    if seen:
        print(f"[test] OK - read {seen} chat line(s). Vote-reading works.")
    else:
        print("[test] No chat parsed. Check NO_SFTP_LOGPATH points at the right file")
        print("       and that someone actually chatted during the window.")


def _open_sftp():
    """Open an SFTP session from the NO_SFTP_* env creds. Caller closes the ssh."""
    import paramiko
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(SFTP_HOST, port=SFTP_PORT, username=SFTP_USER, password=SFTP_PASS,
                timeout=15, look_for_keys=False, allow_agent=False)
    return ssh, ssh.open_sftp()


# Persistent SFTP session for the RUNNING bot's hot paths (rank/skill pushes, whispers,
# team commands). Reuses one SSH connection instead of a ~100-300ms handshake per op.
# CLI one-shots (--get/--put/--ls) keep using _open_sftp (their process exits anyway).
_BOT_SFTP = {"ssh": None, "sftp": None}


def _bot_sftp():
    s = _BOT_SFTP
    tr = s["ssh"].get_transport() if s["ssh"] else None
    if s["sftp"] is None or tr is None or not tr.is_active():
        try:
            if s["ssh"]:
                s["ssh"].close()
        except Exception:                            # noqa: BLE001
            pass
        s["ssh"], s["sftp"] = _open_sftp()
    return s["sftp"]


def _sftp_op(fn):
    """Run fn(sftp) on the persistent session; reconnect + retry once if it dropped."""
    try:
        return fn(_bot_sftp())
    except Exception:                                # noqa: BLE001 - stale/dropped conn
        try:
            if _BOT_SFTP["ssh"]:
                _BOT_SFTP["ssh"].close()
        except Exception:                            # noqa: BLE001
            pass
        _BOT_SFTP["ssh"] = _BOT_SFTP["sftp"] = None
        return fn(_bot_sftp())


def remote_ls():
    """run.bat --ls [path]: list a remote directory (default = SFTP root)."""
    import stat as statmod
    path = "."
    rest = sys.argv[sys.argv.index("--ls") + 1:]
    if rest and not rest[0].startswith("--"):
        path = rest[0]
    ssh, sftp = _open_sftp()
    try:
        print(f"[ls] {path}")
        for e in sorted(sftp.listdir_attr(path),
                        key=lambda a: (not statmod.S_ISDIR(a.st_mode), a.filename.lower())):
            kind = "d" if statmod.S_ISDIR(e.st_mode) else "-"
            print(f"  {kind} {e.st_size:>12,}  {e.filename}")
    finally:
        ssh.close()


def remote_cat():
    """run.bat --cat <path> [maxbytes]: print a remote text file (default 200 KB)."""
    rest = sys.argv[sys.argv.index("--cat") + 1:]
    if not rest:
        print("usage: run.bat --cat <remote_path> [maxbytes]")
        return
    path = rest[0]
    maxb = int(rest[1]) if len(rest) > 1 and rest[1].isdigit() else 200_000
    ssh, sftp = _open_sftp()
    try:
        with sftp.open(path, "rb") as f:
            data = f.read(maxb).decode("utf-8", "replace")
        print(f"[cat] {path} ({len(data)} chars shown)\n" + "-" * 60)
        print(data)
    finally:
        ssh.close()


def probe_missions():
    """run.bat --probe-missions: discover the Group/Name of the built-in (stock)
    Escalation / Terminal Control missions. set-next-mission always replies 2000
    but only actually changes the override for a VALID mission, so we set a known
    baseline, try a candidate, and read back the override to see if it 'took'."""
    rc = RemoteCommand(RCMD_HOST, RCMD_PORT)
    baseline = ("User", "Escalation Co-op as BDF - Dawn")

    def current_override():
        r = rc.send("get-mission-rotation")
        if isinstance(r, dict) and r.get("hasNextOverride"):
            k = r.get("nextOverride", {}).get("Key", {})
            return (k.get("Group"), k.get("Name"))
        return None

    groups = ["Built-in", "Built-In", "BuiltIn", "Builtin", "",
              "Official", "Base", "Stock", "Default", "Campaign"]
    names = ["Escalation", "Terminal Control"]
    candidates = [(g, n) for n in names for g in groups]
    accepted = []
    print(f"[probe] testing {len(candidates)} candidate(s) ...")
    for g, n in candidates:
        rc.send("set-next-mission", baseline[0], baseline[1], 10800)  # reset baseline
        rc.send("set-next-mission", g, n, 10800)                     # try candidate
        ov = current_override()
        ok = ov == (g, n)
        if ok:
            accepted.append((g, n))
        print(f"  {'ACCEPTED' if ok else 'rejected':>8}  Group={g!r:14} Name={n!r}")
    rc.send("set-next-mission", baseline[0], baseline[1], 10800)      # leave sane
    print(f"\n[probe] accepted: {accepted or 'NONE'}")
    print("[probe] override left at baseline; a server restart clears it entirely.")


def remote_get():
    """run.bat --get <remote> <local>: download a remote file to inspect locally."""
    rest = sys.argv[sys.argv.index("--get") + 1:]
    if len(rest) < 2:
        print("usage: run.bat --get <remote_path> <local_path>")
        return
    remote, local = rest[0], rest[1]
    ssh, sftp = _open_sftp()
    try:
        sftp.get(remote, local)
        print(f"[get] {remote} -> {local} ({os.path.getsize(local):,} bytes)")
    finally:
        ssh.close()


def remote_put():
    """run.bat --put <local> <remote>: upload a local file to a remote path."""
    rest = sys.argv[sys.argv.index("--put") + 1:]
    if len(rest) < 2:
        print("usage: run.bat --put <local_path> <remote_path>")
        return
    local, remote = rest[0], rest[1]
    if not os.path.exists(local):
        print(f"[put] local file not found: {local}")
        return
    ssh, sftp = _open_sftp()
    try:
        sftp.put(local, remote)
        print(f"[put] {local} -> {remote} ({os.path.getsize(local):,} bytes)")
    finally:
        ssh.close()


def remote_put_atomic():
    """run.bat --put-atomic <local> <remote>: upload to <remote>.deploytmp then
    atomically rename over <remote>. SAFE to replace a DLL the RUNNING server has
    mmap'd (e.g. BepInEx/plugins/NukeStats.dll): the live process keeps its old
    inode, so it does NOT corrupt (no BadImageFormatException); the new file loads
    on the next server restart. Use this instead of --put for live plugin deploys."""
    rest = sys.argv[sys.argv.index("--put-atomic") + 1:]
    if len(rest) < 2:
        print("usage: run.bat --put-atomic <local_path> <remote_path>")
        return
    local, remote = rest[0], rest[1]
    if not os.path.exists(local):
        print(f"[put-atomic] local file not found: {local}")
        return
    tmp = remote + ".deploytmp"
    ssh, sftp = _open_sftp()
    try:
        sftp.put(local, tmp)
        try:
            sftp.posix_rename(tmp, remote)           # openssh ext: atomic overwrite
        except Exception:                            # noqa: BLE001 - no posix-rename
            # Linux fallback: unlinking the dir entry is safe while the process holds
            # the inode via its mapping; the new file then takes the path.
            try:
                sftp.remove(remote)
            except Exception:                        # noqa: BLE001
                pass
            sftp.rename(tmp, remote)
        print(f"[put-atomic] {local} -> {remote} ({os.path.getsize(local):,} bytes, atomic)")
    finally:
        ssh.close()


def remote_chmod_exec():
    """run.bat --chmod-exec <remote>: chmod 0755 a remote file. Use after a --put round-trip
    on an EXECUTABLE launch wrapper/script (a plain SFTP create can land 0644 -> the server
    won't start). NOTE: for launch SCRIPTS use plain --put (truncates in place, preserves the
    inode+mode) then this; never --put-atomic (its temp file lands non-executable)."""
    rest = sys.argv[sys.argv.index("--chmod-exec") + 1:]
    if not rest:
        print("usage: run.bat --chmod-exec <remote_path>")
        return
    remote = rest[0]
    ssh, sftp = _open_sftp()
    try:
        sftp.chmod(remote, 0o755)
        print(f"[chmod-exec] {remote} -> 0755")
    finally:
        ssh.close()


# ── Automated plugin deploy (scheduled ~05:00 via deploy.bat -> run.bat --deploy-plugin) ──────
# Owns the daily restart: atomically stages a new plugin DLL (if one is pending), then stops &
# starts the game server via the Pterodactyl client API, verifying the server is actually serving
# through the RELAY (the panel's "running" state is unreliable for this egg - it flaps to "starting"
# on mission reloads). GUARDRAIL: from the stop onward, any failure forces a START so the server is
# never knowingly left offline. Run via run.bat so the SFTP env (NO_SFTP_*) is set for the upload.
_PT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
PENDING_DLL   = os.path.join(_BASE_DIR, "pending_plugin.dll")
DEPLOY_HASH   = os.path.join(_BASE_DIR, "deployed_plugin.sha256")
DEPLOY_LOG    = os.path.join(_BASE_DIR, "deploy_plugin.log")
DEPLOY_LOCK   = os.path.join(_BASE_DIR, "pending_plugin.dll.lock")
REMOTE_PLUGIN = "BepInEx/plugins/NukeStats.dll"


def _deploy_log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line)
    try:
        with open(DEPLOY_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


_PANEL_SCHEME_RE = re.compile(r'^[a-z][a-z0-9+.-]*://', re.I)


def normalize_panel_url(url):
    """Forgiving Pterodactyl panel BASE. Adds https:// when there's no scheme, replaces a wrong
    scheme (sftp://, ws://, ...), drops a pasted /server/... path and a trailing /api/client.
    A CORRECT base is returned byte-identical (strict superset) so existing setups are unchanged."""
    u = (url or "").strip()
    if not u:
        return ""
    m = _PANEL_SCHEME_RE.match(u)
    if m:
        if m.group(0).lower() not in ("http://", "https://"):
            u = "https://" + u[m.end():]          # someone pasted sftp://… etc.
    else:
        u = "https://" + u
    i = u.lower().find("/server/")                 # full server URL pasted -> keep the base
    if i != -1:
        u = u[:i]
    u = u.rstrip("/")
    if u.lower().endswith("/api/client"):          # only the well-known client-API path (NOT a bare /api)
        u = u[:-len("/api/client")].rstrip("/")
    return u


def _pt_friendly_json(raw, ctype):
    """json.loads with a clear error when the panel returns an HTML page (wrong URL) not JSON,
    so the cryptic 'Expecting value: line 1 column 1' never surfaces on the power button."""
    body = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else (raw or "")
    if not body:
        return {}
    if "json" not in (ctype or "").lower() and body.lstrip()[:1] not in ("{", "["):
        raise ValueError("the panel URL returned a web page, not the API — check panel.txt is your "
                         "panel's base address (e.g. https://panel.host.net), with no /server/... path")
    return json.loads(body)


def _pt_cfg():
    """Load Pterodactyl client-API config from apiKey.txt + panel.txt (mirrors cc_web._pt_load)."""
    cfg = {"key": None, "base": None, "server": None, "err": None}
    try:
        cfg["key"] = open(os.path.join(_BASE_DIR, "apiKey.txt")).read().strip() or None
    except OSError:
        cfg["key"] = None
    try:
        rows = [l.strip() for l in open(os.path.join(_BASE_DIR, "panel.txt")) if l.strip()]
    except OSError:
        rows = []
    raw = (rows[0] if rows else "")
    want = rows[1] if len(rows) > 1 else None
    if "/server/" in raw and not want:
        want = raw.partition("/server/")[2].split("/")[0] or None
    cfg["base"], cfg["server"] = (normalize_panel_url(raw) or None), want
    if not cfg["key"]:
        cfg["err"] = "no apiKey.txt"
    elif not cfg["base"]:
        cfg["err"] = "no panel.txt"
    elif not cfg["server"]:
        try:
            s = _pt_api(cfg, "GET", "/api/client", None).get("data", [])
            cfg["server"] = s[0]["attributes"]["identifier"] if s else None
            if not cfg["server"]:
                cfg["err"] = "API key sees no servers"
        except Exception as e:                       # noqa: BLE001
            cfg["err"] = f"discover failed: {e}"
    return cfg


def _pt_api(cfg, method, path, body):
    import ssl
    import urllib.request
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(cfg["base"] + path, data=data, method=method, headers={
        "Authorization": "Bearer " + cfg["key"], "Accept": "application/json",
        "Content-Type": "application/json", "User-Agent": _PT_UA})
    with urllib.request.urlopen(req, context=ssl.create_default_context(), timeout=15) as r:
        ctype = r.headers.get("Content-Type", "")
        raw = r.read()
    return _pt_friendly_json(raw, ctype)


def _pt_power_signal(cfg, signal):
    _pt_api(cfg, "POST", f"/api/client/servers/{cfg['server']}/power", {"signal": signal})


def _pt_state(cfg):
    a = _pt_api(cfg, "GET", f"/api/client/servers/{cfg['server']}/resources", None).get("attributes", {})
    return a.get("current_state")


def _relay_alive():
    """Authoritative 'the game is actually serving' check via the relay (panel state is unreliable)."""
    try:
        get_players(RemoteCommand(RCMD_HOST, RCMD_PORT))   # raises on a dead relay; a list (even []) = up
        return True
    except Exception:                                # noqa: BLE001
        return False


def _sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def deploy_plugin_job(dry=False):
    """Daily ~05:00 job (run via run.bat --deploy-plugin so the SFTP env is set). Stages a pending
    plugin DLL (atomic, mmap-safe) if it differs from the last deployed one, then RESTARTS the game
    server (stop -> offline -> start -> relay-verified) so the new DLL loads. GUARDRAIL: from the
    stop onward, any failure forces a START. --deploy-plugin-dry does pre-flight + reports state and
    what WOULD happen, with NO power/upload actions (safe to run against the live server)."""
    tag = "DRY-RUN" if dry else "DEPLOY"
    _deploy_log(f"=== {tag} start ===")

    if not dry:
        try:
            if os.path.exists(DEPLOY_LOCK) and (time.time() - os.path.getmtime(DEPLOY_LOCK)) < 900:
                _deploy_log("ABORT: another deploy appears to be running (fresh lock)."); return
            fd = os.open(DEPLOY_LOCK, os.O_CREAT | os.O_WRONLY | os.O_TRUNC)
            os.write(fd, str(time.time()).encode()); os.close(fd)
        except OSError as e:
            _deploy_log(f"ABORT: cannot take lock: {e}"); return

    try:
        cfg = _pt_cfg()
        if cfg.get("err") or not cfg.get("server"):
            _deploy_log(f"ABORT: Pterodactyl not configured ({cfg.get('err')}). No power action taken."); return
        try:
            _deploy_log(f"server reachable; current panel state={_pt_state(cfg)}")
        except Exception as e:                       # noqa: BLE001
            _deploy_log(f"WARN: could not read power state: {e}")

        have_update = False
        if os.path.exists(PENDING_DLL):
            new_hash = _sha256(PENDING_DLL)
            try:
                old_hash = open(DEPLOY_HASH).read().strip()
            except OSError:
                old_hash = ""
            have_update = (new_hash != old_hash)
            _deploy_log(f"pending_plugin.dll present ({os.path.getsize(PENDING_DLL):,} B); "
                        f"{'NEW -> will upload' if have_update else 'unchanged -> skip upload'}")
        else:
            _deploy_log("no pending_plugin.dll -> restart only (no plugin change)")

        if dry:
            _deploy_log(f"DRY-RUN: would {'UPLOAD then ' if have_update else ''}restart (stop->start). "
                        f"Relay alive now: {_relay_alive()}. No action taken."); return

        # upload the new DLL FIRST, while the server is still up (atomic rename is mmap-safe).
        if have_update:
            try:
                ssh, sftp = _open_sftp()
                try:
                    tmp = REMOTE_PLUGIN + ".deploytmp"
                    sftp.put(PENDING_DLL, tmp)
                    try:
                        sftp.posix_rename(tmp, REMOTE_PLUGIN)
                    except Exception:                # noqa: BLE001
                        try: sftp.remove(REMOTE_PLUGIN)
                        except Exception: pass        # noqa: BLE001
                        sftp.rename(tmp, REMOTE_PLUGIN)
                finally:
                    ssh.close()
                _deploy_log(f"uploaded plugin atomically -> {REMOTE_PLUGIN}")
            except Exception as e:                   # noqa: BLE001
                _deploy_log(f"ABORT: upload FAILED ({e}). Server untouched (still up). Retry next run."); return

        # restart: stop -> wait offline -> start -> verify via relay.
        try:
            _deploy_log("sending STOP ...")
            _pt_power_signal(cfg, "stop")
            stopped = False
            for _ in range(30):                      # up to ~90s
                time.sleep(3)
                try:
                    if _pt_state(cfg) == "offline":
                        stopped = True; break
                except Exception:                    # noqa: BLE001
                    pass
            _deploy_log(f"server {'reached offline' if stopped else 'did NOT reach offline in 90s'}")
            if not stopped:
                _deploy_log("CRIT: stop timed out -> forcing START to avoid downtime")
            _deploy_log("sending START ...")
            _pt_power_signal(cfg, "start")
            for _ in range(20):                      # up to ~60s for the container to leave offline
                time.sleep(3)
                try:
                    if _pt_state(cfg) != "offline":
                        break
                except Exception:                    # noqa: BLE001
                    pass
            alive = False
            for _ in range(24):                      # up to ~120s for the relay to answer
                time.sleep(5)
                if _relay_alive():
                    alive = True; break
            if alive:
                _deploy_log("OK: server is back and serving (relay verified)")
                if have_update:
                    try:
                        new_sha = _sha256(PENDING_DLL)
                        with open(DEPLOY_HASH, "w") as f:
                            f.write(new_sha)
                        # record the DEPLOYED version (from the staged sidecar) so the web CC can show which
                        # plugin build is actually LIVE, not just what's staged. Atomic (tmp + os.replace).
                        ver = ""
                        pj = os.path.join(_BASE_DIR, "pending_plugin.json")
                        try:
                            if os.path.exists(pj):
                                with open(pj, encoding="utf-8") as pf:
                                    ver = (json.load(pf) or {}).get("version", "")
                        except (OSError, ValueError):
                            ver = ""
                        dj = os.path.join(_BASE_DIR, "deployed_plugin.json")
                        tmpj = dj + ".tmp"
                        with open(tmpj, "w", encoding="utf-8") as df:
                            json.dump({"version": ver, "sha": new_sha[:12],
                                       "deployed_at": time.strftime("%Y-%m-%d %H:%M")}, df)
                        os.replace(tmpj, dj)
                        os.replace(PENDING_DLL, PENDING_DLL + ".deployed-" + time.strftime("%Y%m%d-%H%M"))
                    except OSError as e:
                        _deploy_log(f"WARN: post-deploy bookkeeping failed: {e}")
            else:
                _deploy_log("CRIT: server did not answer the relay within ~3min after start - "
                            "re-sending START and leaving it; CHECK MANUALLY.")
                try: _pt_power_signal(cfg, "start")
                except Exception: pass                # noqa: BLE001
        except Exception as e:                       # noqa: BLE001
            _deploy_log(f"CRIT: exception during restart ({e}) -> forcing START")
            try: _pt_power_signal(cfg, "start")
            except Exception: pass                    # noqa: BLE001
    finally:
        if not dry:
            try: os.remove(DEPLOY_LOCK)
            except OSError: pass                      # noqa: BLE001
        _deploy_log(f"=== {tag} end ===")
        try:
            lines = open(DEPLOY_LOG, encoding="utf-8").read().splitlines()
            if len(lines) > 400:
                with open(DEPLOY_LOG, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines[-400:]) + "\n")
        except OSError:
            pass


def disable_panel_restart():
    """One-shot: disable the Pterodactyl panel 'Restart' schedule so --deploy-plugin owns the daily
    05:00 restart (avoids a double restart). Reversible: re-enable it in the panel UI any time."""
    cfg = _pt_cfg()
    if cfg.get("err") or not cfg.get("server"):
        print(f"[sched] pterodactyl not configured: {cfg.get('err')}"); return
    d = _pt_api(cfg, "GET", f"/api/client/servers/{cfg['server']}/schedules", None)
    for s in d.get("data", []):
        a = s.get("attributes", {})
        if str(a.get("name", "")).strip().lower() == "restart" and a.get("is_active"):
            c = a.get("cron", {})
            _pt_api(cfg, "POST", f"/api/client/servers/{cfg['server']}/schedules/{a.get('id')}",
                    {"name": a.get("name"), "minute": c.get("minute"), "hour": c.get("hour"),
                     "day_of_month": c.get("day_of_month"), "month": c.get("month"),
                     "day_of_week": c.get("day_of_week"), "is_active": False})
            print(f"[sched] disabled panel schedule '{a.get('name')}' (id {a.get('id')}); "
                  f"the --deploy-plugin job now owns the 05:00 restart")
            return
    print("[sched] no active 'Restart' schedule found (already disabled?)")


BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_server_backup")
NEW_SERVER_NAME = "[ANZ | PvE & PvP | Persistent !rank | !votemap | !help]"
AI_OPP_LIMIT    = 8       # opposing (AI, preventJoin) team AIAircraftLimit (start count)
AI_OPP_ADDAI    = 0.75    # opposing team addAIPerEnemyPlayer (+per enemy player)
AI_PLR_LIMIT    = 6       # player (preventJoin==false) team AIAircraftLimit (AI allies)


def set_server_name():
    """run.bat --set-server-name: change ServerName in DedicatedServerConfig.json.
    Surgical value replace (rest of the file untouched); local backup first."""
    path = "DedicatedServerConfig.json"
    ssh, sftp = _open_sftp()
    try:
        try:
            with sftp.open(path, "rb") as f:
                text = f.read().decode("utf-8")
        except UnicodeDecodeError:
            print("[name] ABORT: DedicatedServerConfig.json is not valid UTF-8 "
                  "(refusing to round-trip it and risk corruption)")
            return
        cfg = json.loads(text)
        old = cfg.get("ServerName")
        os.makedirs(BACKUP_DIR, exist_ok=True)
        with open(os.path.join(BACKUP_DIR, "DedicatedServerConfig.json.bak"),
                  "w", encoding="utf-8") as bf:
            bf.write(text)
        marker = f'"ServerName": {json.dumps(old)}'
        if text.count(marker) != 1:
            print(f"[name] ABORT: found {text.count(marker)} matches for {marker!r}")
            return
        new_text = text.replace(marker, f'"ServerName": {json.dumps(NEW_SERVER_NAME)}')
        json.loads(new_text)        # verify still valid JSON
        with sftp.open(path, "wb") as f:
            f.write(new_text.encode("utf-8"))
        print(f"[name] ServerName {old!r}\n           ->  {NEW_SERVER_NAME!r}")
        print("[name] takes effect on the next FULL server restart.")
    finally:
        ssh.close()


def _edit_faction_values(text, faction_name, next_faction_name, repls):
    """Surgically replace numeric values for given keys INSIDE one faction object's
    text span (from its "factionName" anchor up to the next faction's anchor, or
    EOF), so an edit can't bleed into another team. repls = [(key, value_regex,
    new_value), ...]. Returns (new_text, error_or_None)."""
    anchor = f'"factionName": "{faction_name}"'
    if text.count(anchor) != 1:
        return text, f"factionName {faction_name!r} x{text.count(anchor)}"
    start = text.index(anchor)
    end = len(text)
    if next_faction_name:
        nanchor = f'"factionName": "{next_faction_name}"'
        if nanchor in text:
            end = text.index(nanchor)
    if end <= start:
        return text, "faction span ordering"
    region = text[start:end]
    for key, valpat, newval in repls:
        region, n = re.subn(rf'("{key}":\s*){valpat}', rf'\g<1>{newval}', region, count=1)
        if n != 1:
            return text, f"{key} replaced x{n} in {faction_name}"
    return text[:start] + region + text[end:], None


def set_ai_limits():
    """run.bat --set-ai-limits [--dry-run]: in every PvE CO-OP mission, set the
    OPPOSING (AI, preventJoin==true) team's AIAircraftLimit -> AI_OPP_LIMIT (8) and
    addAIPerEnemyPlayer -> AI_OPP_ADDAI (0.75), AND the PLAYER (preventJoin==false)
    team's AIAircraftLimit -> AI_PLR_LIMIT (6). PvP missions (no preventJoin==true
    team, e.g. 'Escalation') are skipped automatically. Surgical: only those three
    numbers change, verified by a full deep-diff of the re-parsed JSON. Local backup
    of each original; --dry-run previews without uploading."""
    dry = "--dry-run" in sys.argv
    MISSIONS_DIR = "NuclearOption-Missions"
    ssh, sftp = _open_sftp()
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        folders = sorted(f for f in sftp.listdir(MISSIONS_DIR) if not f.startswith("."))
        changed = skipped = 0
        print(f"[ai] {'DRY-RUN: ' if dry else ''}{len(folders)} mission folder(s)\n")
        for folder in folders:
            remote_json = f"{MISSIONS_DIR}/{folder}/{folder}.json"
            try:
                with sftp.open(remote_json, "rb") as f:
                    text = f.read().decode("utf-8")
                d = json.loads(text)
            except Exception as e:                       # noqa: BLE001
                print(f"  SKIP  {folder:42} (read/parse: {e})"); skipped += 1; continue
            factions = d.get("factions")
            if not isinstance(factions, list):
                print(f"  SKIP  {folder:42} (no factions[])"); skipped += 1; continue
            opp = [fa for fa in factions
                   if fa.get("preventJoin") is True and "AIAircraftLimit" in fa]
            plr = [fa for fa in factions
                   if fa.get("preventJoin") is False and "AIAircraftLimit" in fa]
            if len(opp) != 1 or len(plr) != 1:
                print(f"  SKIP  {folder:42} (opp={len(opp)} player={len(plr)} - not a co-op layout)")
                skipped += 1; continue
            # order the named factions by their position in the text so each edit is
            # bounded to a single faction object (anchor .. next factionName)
            named = [fa for fa in factions if fa.get("factionName")]
            order = sorted(named, key=lambda fa: text.find(f'"factionName": "{fa["factionName"]}"'))
            def _next_name(fa):
                i = order.index(fa)
                return order[i + 1]["factionName"] if i + 1 < len(order) else None

            new_text, err = text, None
            for fa, repls in ((opp[0], [("AIAircraftLimit", r"-?\d+", AI_OPP_LIMIT),
                                        ("addAIPerEnemyPlayer", r"-?[\d.eE+]+", AI_OPP_ADDAI)]),
                              (plr[0], [("AIAircraftLimit", r"-?\d+", AI_PLR_LIMIT)])):
                new_text, err = _edit_faction_values(new_text, fa["factionName"], _next_name(fa), repls)
                if err:
                    break
            if err:
                print(f"  SKIP  {folder:42} ({err})"); skipped += 1; continue

            # verify ONLY the three intended numbers changed (full deep-diff)
            expected = json.loads(text)
            for fa in expected["factions"]:
                if fa.get("preventJoin") is True and "AIAircraftLimit" in fa:
                    fa["AIAircraftLimit"] = AI_OPP_LIMIT
                    fa["addAIPerEnemyPlayer"] = AI_OPP_ADDAI
                elif fa.get("preventJoin") is False and "AIAircraftLimit" in fa:
                    fa["AIAircraftLimit"] = AI_PLR_LIMIT
            try:
                got = json.loads(new_text)
            except json.JSONDecodeError as e:
                print(f"  FAIL  {folder:42} (result not valid JSON: {e})"); skipped += 1; continue
            if got != expected:
                print(f"  FAIL  {folder:42} (deep-diff: unintended change - NOT uploaded)")
                skipped += 1; continue

            print(f"  OK    {folder:42} "
                  f"{opp[0].get('factionName'):8} AI {opp[0]['AIAircraftLimit']}->{AI_OPP_LIMIT} "
                  f"addAI {opp[0].get('addAIPerEnemyPlayer'):.3g}->{AI_OPP_ADDAI} | "
                  f"{plr[0].get('factionName'):8} AI {plr[0]['AIAircraftLimit']}->{AI_PLR_LIMIT}")
            if not dry:
                with open(os.path.join(BACKUP_DIR, f"{folder}.json"), "w",
                          encoding="utf-8") as bf:
                    bf.write(text)
                with sftp.open(remote_json, "wb") as f:
                    f.write(new_text.encode("utf-8"))
            changed += 1
        print(f"\n[ai] {'would change' if dry else 'changed'} {changed}, skipped {skipped}.")
        if not dry and changed:
            print("[ai] backups in _server_backup/. Takes effect as each mission loads "
                  "(or on restart).")
    finally:
        ssh.close()


def apply_map_changes():
    """run.bat --apply-map-changes [--dry-run]: on every PvE CO-OP mission (one with a
    preventJoin==true AI team; the PvP 'Escalation' has none, so it's skipped) set the
    EW1 + FastBomber1 factories' productionTime -> 600 (Medusa/Alkyon EW planes + the fast
    bomber) and wrecksMaxNumber -> 1000, wrecksDecayTime -> 5.0. ALSO set DedicatedServerConfig
    PostMissionDelay -> 80.0 so the end-of-match map vote has time to apply before the
    rotation. ONLY those values change: targeted text edits, then a re-parse + full
    deep-diff guard (won't upload anything else). Idempotent; local backups in _server_backup/."""
    import re as _re
    dry = "--dry-run" in sys.argv
    MISSIONS_DIR = "NuclearOption-Missions"
    THROTTLE_CODES = ("EW1", "FastBomber1")          # Medusa/Alkyon (EW) + the fast bomber -> 600s
    _codes_re = "|".join(_re.escape(c) for c in THROTTLE_CODES)

    def _expected(obj):                              # logical version of the edits, for the diff guard
        if isinstance(obj, dict):
            if "wrecksMaxNumber" in obj:
                obj["wrecksMaxNumber"] = 1000
            if "wrecksDecayTime" in obj:
                obj["wrecksDecayTime"] = 5.0
            fo = obj.get("factoryOptions")
            if isinstance(fo, dict) and fo.get("productionType") in THROTTLE_CODES:
                fo["productionTime"] = 600.0
            for v in obj.values():
                _expected(v)
        elif isinstance(obj, list):
            for v in obj:
                _expected(v)

    def _edit(text):
        new = _re.sub(r'"wrecksMaxNumber": \d+', '"wrecksMaxNumber": 1000', text)
        new = _re.sub(r'"wrecksDecayTime": [\d.]+', '"wrecksDecayTime": 5.0', new)
        new, n_fac = _re.subn(
            r'("productionType": "(?:' + _codes_re + r')",\s+"productionTime": )[\d.]+',
            r'\g<1>600.0', new)
        return new, n_fac

    ssh, sftp = _open_sftp()
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        folders = sorted(f for f in sftp.listdir(MISSIONS_DIR) if not f.startswith("."))
        changed = skipped = 0
        print(f"[map] {'DRY-RUN: ' if dry else ''}{len(folders)} mission folder(s)\n")
        for folder in folders:
            remote_json = f"{MISSIONS_DIR}/{folder}/{folder}.json"
            try:
                with sftp.open(remote_json, "rb") as f:
                    text = f.read().decode("utf-8")
                d = json.loads(text)
            except Exception as e:                   # noqa: BLE001
                print(f"  SKIP  {folder:42} (read/parse: {e})"); skipped += 1; continue
            factions = d.get("factions")
            if not (isinstance(factions, list) and any(fa.get("preventJoin") is True for fa in factions)):
                print(f"  SKIP  {folder:42} (PvP / no AI team)"); skipped += 1; continue
            new_text, n_fac = _edit(text)
            # GUARD: re-parse + deep-diff that ONLY the intended values changed
            try:
                got = json.loads(new_text)
            except json.JSONDecodeError as e:
                print(f"  FAIL  {folder:42} (result not valid JSON: {e})"); skipped += 1; continue
            expected = json.loads(text)
            _expected(expected)
            if got != expected:
                print(f"  FAIL  {folder:42} (deep-diff: unintended change - NOT uploaded)")
                skipped += 1; continue
            if new_text == text:
                print(f"  ok    {folder:42} (already set; throttled factories={n_fac})"); continue
            if not dry:
                bpath = os.path.join(BACKUP_DIR, f"{folder}.json")
                if not os.path.exists(bpath):        # keep the earliest (pre-throttle) backup
                    with open(bpath, "w", encoding="utf-8") as bf:
                        bf.write(text)
                with sftp.open(remote_json, "wb") as f:
                    f.write(new_text.encode("utf-8"))
            print(f"  OK    {folder:42} throttled factories->600: {n_fac}; wrecks 1000/5")
            changed += 1

        # --- DedicatedServerConfig: PostMissionDelay -> 80 ---
        cfg = "DedicatedServerConfig.json"
        with sftp.open(cfg, "rb") as f:
            ctext = f.read().decode("utf-8")
        cnew, ncfg = _re.subn(r'"PostMissionDelay": [\d.]+', '"PostMissionDelay": 80.0', ctext)
        exp_cfg = json.loads(ctext); exp_cfg["PostMissionDelay"] = 80.0
        if ncfg and cnew != ctext and json.loads(cnew) == exp_cfg:
            if not dry:
                with open(os.path.join(BACKUP_DIR, "DedicatedServerConfig.json.bak"), "w", encoding="utf-8") as bf:
                    bf.write(ctext)
                with sftp.open(cfg, "wb") as f:
                    f.write(cnew.encode("utf-8"))
            print(f"  OK    DedicatedServerConfig PostMissionDelay -> 80.0")
        else:
            print(f"  ok    DedicatedServerConfig PostMissionDelay unchanged (matches={ncfg})")

        print(f"\n[map] {'would change' if dry else 'changed'} {changed} mission(s), skipped {skipped}.")
        if not dry:
            print("[map] missions apply as each next loads; PostMissionDelay needs reload-config or a restart.")
    finally:
        ssh.close()


def fix_starting_ranks():
    """run.bat --check-ranks | --fix-ranks: ensure each PvE CO-OP mission's
    playerStartingRank is correct -- Escalation co-ops -> 3, Terminal Control co-ops -> 4
    (raised 2026-06-26 from the shipped 2/3 at the user's request; only this rank field
    changes, money/everything else untouched). PvP missions (no
    preventJoin AI team) are left untouched. Surgical regex on that ONE top-level field,
    then a re-parse + full deep-diff guard (won't upload if anything else moved). A
    separate '.rankbak.json' backup is kept so the pre-throttle backup isn't clobbered.
    --check-ranks is read-only; --fix-ranks uploads. Applies as each mission NEXT loads."""
    import re as _re
    fix = "--fix-ranks" in sys.argv
    MISSIONS_DIR = "NuclearOption-Missions"
    ssh, sftp = _open_sftp()
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        folders = sorted(f for f in sftp.listdir(MISSIONS_DIR) if not f.startswith("."))
        wrong = fixed = skipped = 0
        print(f"[rank] {'FIX' if fix else 'CHECK (read-only)'}: {len(folders)} mission folder(s)\n")
        for folder in folders:
            remote_json = f"{MISSIONS_DIR}/{folder}/{folder}.json"
            try:
                with sftp.open(remote_json, "rb") as f:
                    text = f.read().decode("utf-8")
                d = json.loads(text)
            except Exception as e:                       # noqa: BLE001
                print(f"  SKIP  {folder:44} (read/parse: {e})"); skipped += 1; continue
            factions = d.get("factions")
            if not (isinstance(factions, list) and any(fa.get("preventJoin") is True for fa in factions)):
                print(f"  skip  {folder:44} (PvP / no AI team)"); skipped += 1; continue
            low = folder.lower()
            if "terminal" in low:
                want = 4
            elif "escalation" in low:
                want = 3
            else:
                print(f"  SKIP  {folder:44} (unknown type - not touching)"); skipped += 1; continue
            ms = d.get("missionSettings")
            if not isinstance(ms, dict):
                print(f"  SKIP  {folder:44} (no missionSettings block)"); skipped += 1; continue
            cur = ms.get("playerStartingRank")        # the field lives in missionSettings
            if cur == want:
                print(f"  ok    {folder:44} rank {cur}"); continue
            wrong += 1
            print(f"  WRONG {folder:44} rank {cur} -> {want}")
            if not fix:
                continue
            if "playerStartingRank" in ms:            # present but wrong value -> replace
                new_text, n = _re.subn(r'"playerStartingRank": \d+',
                                       f'"playerStartingRank": {want}', text, count=1)
            else:                                     # missing -> insert after its sibling allowRespawn
                new_text, n = _re.subn(
                    r'(\n(\s*)"allowRespawn":\s*(?:true|false),)',
                    r'\1\n\g<2>"playerStartingRank": ' + str(want) + ',', text, count=1)
            if n != 1:
                print(f"        FAIL  anchor matched {n}x (expected 1) - skipped"); skipped += 1; continue
            try:
                got = json.loads(new_text)
            except json.JSONDecodeError as e:
                print(f"        FAIL  result not valid JSON: {e} - skipped"); skipped += 1; continue
            expected = json.loads(text); expected["missionSettings"]["playerStartingRank"] = want
            if got != expected:
                print(f"        FAIL  deep-diff: unintended change - NOT uploaded"); skipped += 1; continue
            bpath = os.path.join(BACKUP_DIR, f"{folder}.rankbak.json")
            if not os.path.exists(bpath):
                with open(bpath, "w", encoding="utf-8") as bf:
                    bf.write(text)
            with sftp.open(remote_json, "wb") as f:
                f.write(new_text.encode("utf-8"))
            fixed += 1
            print(f"        OK    uploaded ({cur} -> {want})")
        verb = "fixed" if fix else "would fix"
        print(f"\n[rank] {verb} {fixed if fix else wrong} mission(s); skipped {skipped}.")
        if fix and fixed:
            print("[rank] takes effect as each corrected mission NEXT loads "
                  "(restart or wait for rotation).")
    finally:
        ssh.close()


def set_balance_diff():
    """run.bat --set-balance-diff <n>: set the NukeStats plugin's [Balance] MaxDifference in the
    LIVE server config (BepInEx/config/anz.nukestats.cfg). Team balancing only triggers when a side
    is MORE than n ahead (n=2 => a 2-player gap is tolerated, only a 3+ gap acts; higher = fewer,
    less-twitchy moves). Surgical line-anchored single-line edit + re-read verify. BepInEx watches
    the config file so a running plugin can pick this up live; it's also what the plugin reads on its
    next load/deploy (so the staged v0.9.0 inherits it)."""
    import re as _re
    rest = [a for a in sys.argv[sys.argv.index("--set-balance-diff") + 1:] if not a.startswith("--")]
    if not rest or not rest[0].isdigit():
        print("usage: run.bat --set-balance-diff <n>   (whole number 0..10)"); return
    n = int(rest[0])
    if n > 10:
        print("[balance] refusing a MaxDifference > 10 (sanity guard)"); return
    CFG = "BepInEx/config/anz.nukestats.cfg"
    ssh, sftp = _open_sftp()
    try:
        with sftp.open(CFG, "rb") as f:
            text = f.read().decode("utf-8")
        cur = _re.search(r'(?m)^MaxDifference\s*=\s*(\d+)\s*$', text)
        if not cur:
            print("[balance] ABORT: no '^MaxDifference = <n>' line in the config"); return
        new, c = _re.subn(r'(?m)^(MaxDifference\s*=\s*)\d+\s*$', r'\g<1>' + str(n), text, count=1)
        if c != 1:
            print(f"[balance] ABORT: expected exactly 1 MaxDifference line, found {c}"); return
        if new == text:
            print(f"[balance] MaxDifference already {n} - nothing to do"); return
        tmp = CFG + ".tmp"
        with sftp.open(tmp, "wb") as f:
            f.write(new.encode("utf-8"))
        try:
            sftp.remove(CFG)
        except Exception:        # noqa: BLE001
            pass
        sftp.posix_rename(tmp, CFG)
        with sftp.open(CFG, "rb") as f:
            back = f.read().decode("utf-8")
        ok = _re.search(r'(?m)^MaxDifference\s*=\s*' + str(n) + r'\s*$', back) is not None
        print(f"[balance] MaxDifference {cur.group(1)} -> {n}: {'OK' if ok else 'VERIFY FAILED'}")
        print("[balance] BepInEx watches the cfg (can take effect live); fully applies with the v0.9.0 leave-only autobalance.")
    finally:
        ssh.close()


def set_votekick():
    """run.bat --set-votekick <on|off>: enable/disable the game's built-in VoteKick (player vote-to-kick)
    in DedicatedServerConfig.json -- the only player-facing kick feature. Surgical single-token edit on
    VoteKick.Enabled + a JSON round-trip + full deep-diff guard (won't upload if anything else moved),
    a local backup, then a reload-config so it applies without a full restart (also applies on the next
    mission load / restart). NOTE: this is SEPARATE from the send-buffer-overflow mass-disconnect."""
    import re as _re
    rest = [a for a in sys.argv[sys.argv.index("--set-votekick") + 1:] if not a.startswith("--")]
    val = rest[0].lower() if rest else ""
    if val not in ("on", "off", "true", "false", "enable", "disable"):
        print("usage: run.bat --set-votekick <on|off>"); return
    want = val in ("on", "true", "enable")
    path = "DedicatedServerConfig.json"
    ssh, sftp = _open_sftp()
    try:
        with sftp.open(path, "rb") as f:
            text = f.read().decode("utf-8")
        cfg = json.loads(text)
        vk = cfg.get("VoteKick")
        if not isinstance(vk, dict) or "Enabled" not in vk:
            print("[votekick] ABORT: no VoteKick.Enabled block in config"); return
        if bool(vk.get("Enabled")) == want:
            print(f"[votekick] already {'ENABLED' if want else 'DISABLED'} - nothing to do"); return
        new_text, n = _re.subn(r'("Enabled"\s*:\s*)(?:true|false)',
                               r'\g<1>' + ("true" if want else "false"), text, count=1)
        if n != 1:
            print(f"[votekick] ABORT: expected exactly 1 'Enabled' key, found {n} - not touching"); return
        try:
            got = json.loads(new_text)
        except json.JSONDecodeError as e:
            print(f"[votekick] ABORT: result not valid JSON: {e}"); return
        expected = json.loads(text); expected["VoteKick"]["Enabled"] = want
        if got != expected:
            print("[votekick] ABORT: deep-diff shows an unintended change - NOT uploaded"); return
        os.makedirs(BACKUP_DIR, exist_ok=True)
        bpath = os.path.join(BACKUP_DIR, "DedicatedServerConfig.votekickbak.json")
        if not os.path.exists(bpath):
            with open(bpath, "w", encoding="utf-8") as bf:
                bf.write(text)
        with sftp.open(path, "wb") as f:
            f.write(new_text.encode("utf-8"))
        print(f"[votekick] VoteKick.Enabled {vk.get('Enabled')} -> {want}: uploaded (backup in _server_backup/)")
    finally:
        ssh.close()
    try:
        rc = RemoteCommand(RCMD_HOST, RCMD_PORT)
        resp = rc.send("reload-config")
        print(f"[votekick] reload-config -> {resp!r}")
    except Exception as e:        # noqa: BLE001
        print(f"[votekick] reload-config failed ({e}); applies on the next mission load / restart anyway")
    print(f"[votekick] VoteKick is now {'ON' if want else 'OFF'} (full effect on reload-config / next mission / restart).")


# ============ Server Settings tab: edit DedicatedServerConfig.json (remote/SFTP) + mirror to gpanel ============
# cc_web has no SFTP, so the webcc routes BOTH the read (dumpserverconfig) and write (setserverconfig) through
# the bot. We read the config, set ONE dotted-path field on the parsed object, re-serialize (a json round-trip
# is game-safe), back up the original, write it back, reload-config (best-effort), and mirror the change to the
# Pterodactyl panel startup variables so a re-templating boot doesn't revert it.
SRVCFG_PATH = "DedicatedServerConfig.json"
_SRVCFG_UNSET = object()
# (dotted-key, label, type, mask, needs_restart, note)
_SRVCFG_SCHEMA = [
    ("ServerName",            "Server name",            "str",     False, True,  "Shown in the public server browser."),
    ("Password",              "Join password",          "str",     True,  True,  "Blank = open server. Masked here."),
    ("MaxPlayers",            "Max players",            "int",     False, True,  "Player cap."),
    ("Port.Value",            "Game port",              "int",     False, True,  "UDP game port. On a panel, the port must also be allocated in gpanel."),
    ("QueryPort.Value",       "Query port",             "int",     False, True,  "Steam query port. Panel-allocated."),
    ("Hidden",                "Hidden from browser",    "bool",    False, True,  "Hide from the public server list."),
    ("ModdedServer",          "Modded server",          "strbool", False, True,  "Whether mods are enabled."),
    ("DisableErrorKick",      "Disable error-kick",     "bool",    False, False, "Don't kick a client on a desync error."),
    ("PostMissionDelay",      "Post-mission delay (s)", "float",   False, False, "Seconds between mission end and the next load (the bot tunes this so the end-of-match map vote can run)."),
    ("NoPlayerStopTime",      "Empty-stop time (s)",    "float",   False, False, "Seconds with no players before the match stops."),
    ("VoteKick.Enabled",      "Vote-kick enabled",      "bool",    False, False, "Players can vote to kick (the game's built-in feature)."),
    ("VoteKick.PassRatio",    "Vote-kick pass ratio",   "float",   False, False, "Fraction of yes-votes needed (0-1)."),
    ("VoteKick.MinVotes",     "Vote-kick min votes",    "int",     False, False, "Minimum votes to start one."),
    ("VoteKick.VoteDuration", "Vote-kick duration (s)", "float",   False, False, "How long a vote runs."),
]
_SRVCFG_MAP = {k: (lbl, typ, mask, nr, note) for (k, lbl, typ, mask, nr, note) in _SRVCFG_SCHEMA}
_srvcfg_cache = {"ok": False, "err": "not loaded yet", "ts": 0, "values": {}, "last_set": None}


def _srvcfg_walk(d, dotted, set_to=_SRVCFG_UNSET):
    parts = dotted.split(".")
    cur = d
    for p in parts[:-1]:
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    last = parts[-1]
    if not isinstance(cur, dict) or last not in cur:
        return None
    if set_to is not _SRVCFG_UNSET:
        cur[last] = set_to
    return cur[last]


def _srvcfg_coerce(typ, value):
    if typ == "bool":
        return value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "on", "yes")
    if typ == "strbool":
        b = value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "on", "yes")
        return "true" if b else "false"
    if typ == "int":
        return int(float(value))
    if typ == "float":
        return float(value)
    return str(value)


def _srvcfg_read():
    try:
        ssh, sftp = _open_sftp()
    except Exception as e:                       # noqa: BLE001
        return None, f"sftp connect: {e}"
    try:
        with sftp.open(SRVCFG_PATH, "rb") as f:
            return json.loads(f.read().decode("utf-8")), None
    except Exception as e:                        # noqa: BLE001
        return None, str(e)
    finally:
        try:
            ssh.close()
        except Exception:                         # noqa: BLE001
            pass


def refresh_server_config():
    cfg, err = _srvcfg_read()
    if err:
        _srvcfg_cache.update({"ok": False, "err": err, "ts": time.time()})
        return
    values = {}
    for (k, lbl, typ, mask, nr, note) in _SRVCFG_SCHEMA:
        v = _srvcfg_walk(cfg, k)
        if typ == "strbool":
            v = str(v).strip().lower() == "true"
        values[k] = ("********" if (mask and v) else v)
    _srvcfg_cache.update({"ok": True, "err": None, "ts": time.time(), "values": values})


def server_config_state():
    vals = _srvcfg_cache.get("values", {})
    fields = [{"key": k, "label": lbl, "type": typ, "mask": mask, "needs_restart": nr,
               "note": note, "value": vals.get(k)}
              for (k, lbl, typ, mask, nr, note) in _SRVCFG_SCHEMA]
    return {"ok": _srvcfg_cache.get("ok"), "err": _srvcfg_cache.get("err"),
            "ts": _srvcfg_cache.get("ts"), "fields": fields, "last_set": _srvcfg_cache.get("last_set")}


def _srvcfg_panel_mirror(key, old, new):
    """Best-effort: push the change to the matching Pterodactyl startup VARIABLE so gpanel matches and a
    re-templating boot won't revert it. Matched by the var's current server_value == the OLD config value
    (env-var names are egg-specific). Never fails the config write."""
    cfg = _pt_cfg()
    if cfg.get("err"):
        return {"mirrored": False, "reason": cfg["err"]}
    try:
        d = _pt_api(cfg, "GET", f"/api/client/servers/{cfg['server']}/startup", None)
        attrs = [v.get("attributes", {}) for v in d.get("data", [])]
    except Exception as e:                         # noqa: BLE001
        return {"mirrored": False, "reason": f"list: {e}"}
    olds = str(old)
    target = next((a for a in attrs if a.get("is_editable") and str(a.get("server_value")) == olds), None)
    if target is None:
        return {"mirrored": False, "reason": "no editable panel variable matched (config-file only)"}
    try:
        _pt_api(cfg, "PUT", f"/api/client/servers/{cfg['server']}/startup/variable",
                {"key": target.get("env_variable"), "value": str(new)})
        return {"mirrored": True, "var": target.get("env_variable")}
    except Exception as e:                         # noqa: BLE001
        return {"mirrored": False, "reason": f"put failed (key may be read-only): {e}"}


# ── Mission audit: official vs custom/workshop missions + integrity (pool-divergence status) ──
# Missions live in DedicatedServerConfig.MissionDirectory as <name>/<name>.json (Group "User"), plus any
# {Group:"Workshop",Name:<id>} rotation entries. OFFICIAL_MISSIONS = the curated pool this server ships;
# anything else present/enabled = unofficial. Official mission JSONs are hashed vs a trust-on-first-use
# baseline (mission_baseline.json) to detect edits. ALL READ-ONLY over SFTP (never writes mission files).
MISSION_BASELINE_FILE = os.path.join(_BASE_DIR, "mission_baseline.json")
_mission_audit_cache = {"ts": 0.0, "data": {"loaded": False}}


def refresh_mission_audit():
    """SFTP-read the mission layout, hash official mission files vs the baseline, classify official vs
    unofficial, and compute pool status (`eligible` = all-official & unedited). Cached. Read-only."""
    import hashlib
    d = {"loaded": True, "official": [], "unofficial": [], "edited": [], "missing": [],
         "mission_dir": "", "eligible": True, "reasons": [], "error": None}
    cfg, err = _srvcfg_read()
    if err or not isinstance(cfg, dict):
        d["error"] = err or "could not read DedicatedServerConfig.json"
        _mission_audit_cache.update({"ts": time.time(), "data": d})
        return d
    mdir = str(cfg.get("MissionDirectory", "") or "").rstrip("/")
    d["mission_dir"] = mdir
    rot = []
    for e in (cfg.get("MissionRotation", []) or []):
        k = e.get("Key", {}) if isinstance(e, dict) else {}
        rot.append((str(k.get("Group", "")), str(k.get("Name", ""))))
    rot_names = {n for _, n in rot}
    base = {}
    try:
        with open(MISSION_BASELINE_FILE, encoding="utf-8") as f:
            base = json.load(f)
    except (OSError, ValueError):
        base = {}
    newbase = dict(base)

    # The SFTP session is rooted at the container home (the bot reads DedicatedServerConfig.json by a
    # RELATIVE path), but MissionDirectory is an absolute /home/<user>/... path -> resolve to candidates.
    cands = [mdir, mdir.lstrip("/")]
    _mp = mdir.lstrip("/").split("/")
    if len(_mp) >= 2 and _mp[0] == "home":
        cands.append("/".join(_mp[2:]))                         # drop /home/<user>/ -> relative to the SFTP root
    cands = [c for i, c in enumerate(cands) if c and c not in cands[:i]]

    def _op(sftp):
        mb = None
        on_disk = set()
        for c in cands:
            try:
                on_disk = set(sftp.listdir(c)); mb = c; break
            except Exception:                                   # noqa: BLE001
                continue
        if mb is None:
            d["error"] = "mission dir not accessible via SFTP (tried: " + ", ".join(cands) + ")"
            return
        d["mission_dir"] = mb
        d["dirlist"] = sorted(on_disk)[:50]
        for grp, name in rot:
            official = (grp != "Workshop") and (name in OFFICIAL_MISSIONS)
            row = {"name": name, "group": grp, "enabled": True, "official": official}
            if official:
                try:
                    with sftp.open(mb + "/" + name + "/" + name + ".json", "rb") as f:
                        h = hashlib.sha256(f.read()).hexdigest()
                    newbase.setdefault(name, h)                 # trust-on-first-use baseline
                    if newbase[name] != h:
                        d["edited"].append(name); row["edited"] = True
                except IOError:
                    d["missing"].append(name); row["missing"] = True
                d["official"].append(row)
            else:
                d["unofficial"].append(row)
        for fold in sorted(on_disk):                            # uploaded-but-not-rotated folders
            if fold in OFFICIAL_MISSIONS or any(u["name"] == fold for u in d["unofficial"]):
                continue
            d["unofficial"].append({"name": fold, "group": "User", "enabled": fold in rot_names, "official": False})

    try:
        _sftp_op(_op)
    except Exception as e:                                      # noqa: BLE001
        d["error"] = str(e)
    if newbase != base:
        try:
            tmp = MISSION_BASELINE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(newbase, f, indent=1)
            os.replace(tmp, MISSION_BASELINE_FILE)
        except OSError:
            pass
    if any(u.get("enabled") for u in d["unofficial"]):
        d["eligible"] = False
        d["reasons"].append("an unofficial / workshop mission is enabled")
    if d["edited"]:
        d["eligible"] = False
        d["reasons"].append("official mission edited: " + ", ".join(d["edited"][:6]))
    _mission_audit_cache.update({"ts": time.time(), "data": d})
    return d


def mission_audit_state():
    return _mission_audit_cache["data"]


def _mission_dir_candidates(mdir):
    """SFTP-relative candidates for an absolute MissionDirectory (session is rooted at the container home)."""
    c = [mdir, mdir.lstrip("/")]
    mp = mdir.lstrip("/").split("/")
    if len(mp) >= 2 and mp[0] == "home":
        c.append("/".join(mp[2:]))
    return [x for i, x in enumerate(c) if x and x not in c[:i]]


def _mission_rotation_mutate(mutate):
    """Open SFTP, read DedicatedServerConfig, run mutate(cfg) (True if it changed), back up + write +
    reload-config + re-audit. Reuses the set_server_config write pattern. Returns {ok, error?}."""
    try:
        ssh, sftp = _open_sftp()
    except Exception as e:                              # noqa: BLE001
        return {"ok": False, "error": f"sftp: {e}"}
    try:
        with sftp.open(SRVCFG_PATH, "rb") as f:
            orig = f.read().decode("utf-8")
        cfg = json.loads(orig)
        if not mutate(cfg):
            return {"ok": True, "nochange": True}
        os.makedirs(BACKUP_DIR, exist_ok=True)
        with open(os.path.join(BACKUP_DIR, "DedicatedServerConfig.beforeedit.json"), "w", encoding="utf-8") as bf:
            bf.write(orig)
        with sftp.open(SRVCFG_PATH, "wb") as f:
            f.write(json.dumps(cfg, indent=2).encode("utf-8"))
    except Exception as e:                             # noqa: BLE001
        return {"ok": False, "error": f"write: {e}"}
    finally:
        try:
            ssh.close()
        except Exception:                              # noqa: BLE001
            pass
    try:
        RemoteCommand(RCMD_HOST, RCMD_PORT).send("reload-config")
    except Exception:                                  # noqa: BLE001
        pass
    try:
        refresh_mission_audit()
    except Exception:                                  # noqa: BLE001
        pass
    return {"ok": True}


def mission_set_enabled(group, name, on, max_time=10800.0):
    """Add (on) or remove (off) a mission from the live MissionRotation. Enabling an unofficial mission
    makes the pool diverge from stock (surfaced by the next mission audit)."""
    group = str(group or "User"); name = str(name or "")
    if not name:
        return {"ok": False, "error": "no mission name"}

    def _match(e):
        k = e.get("Key", {}) if isinstance(e, dict) else {}
        return k.get("Name") == name and k.get("Group") == group

    def _m(cfg):
        rot = cfg.setdefault("MissionRotation", [])
        if on:
            if any(_match(e) for e in rot):
                return False
            rot.append({"Key": {"Group": group, "Name": name}, "MaxTime": float(max_time)})
            return True
        before = len(rot)
        cfg["MissionRotation"] = [e for e in rot if not _match(e)]
        return len(cfg["MissionRotation"]) != before
    return _mission_rotation_mutate(_m)


def mission_add_workshop(workshop_id, max_time=10800.0):
    """Add a Steam Workshop mission ({Group:Workshop,Name:<id>}) to the rotation -- the server
    auto-downloads it on the next start. This enables it, so the pool diverges from stock."""
    wid = str(workshop_id or "").strip()
    if not re.fullmatch(r"\d{5,20}", wid):
        return {"ok": False, "error": "workshop id must be numeric"}
    return mission_set_enabled("Workshop", wid, True, max_time)


def mission_upload(name, files):
    """SFTP-write an uploaded mission folder into MissionDirectory/<name>/. Adds it OFF (not in the
    rotation) until the owner enables it. files=[{path, b64}]. Read of the
    config is SFTP; writes are confined to MissionDirectory/<sanitized name>/."""
    import base64
    name = re.sub(r"[^A-Za-z0-9 ._-]", "", (name or "").strip())
    if not name:
        return {"ok": False, "error": "bad mission name"}
    cfg, err = _srvcfg_read()
    if err or not isinstance(cfg, dict):
        return {"ok": False, "error": err or "config read failed"}
    mdir = str(cfg.get("MissionDirectory", "") or "").rstrip("/")
    cands = _mission_dir_candidates(mdir)
    res = {"ok": False, "error": "upload failed"}

    def _op(sftp):
        base_dir = None
        for c in cands:
            try:
                sftp.listdir(c); base_dir = c; break
            except Exception:                          # noqa: BLE001
                continue
        if base_dir is None:
            res.update({"ok": False, "error": "mission dir not accessible via SFTP"}); return
        dest = base_dir + "/" + name
        try:
            sftp.mkdir(dest)
        except Exception:                              # noqa: BLE001
            pass                                       # already exists
        n = 0
        for fobj in (files or []):
            rel = re.sub(r"[^A-Za-z0-9 ._-]", "", str(fobj.get("path", "")).split("/")[-1])  # flat: filename only
            if not rel:
                continue
            try:
                data = base64.b64decode(fobj.get("b64", "") or "")
            except Exception:                          # noqa: BLE001
                continue
            with sftp.open(dest + "/" + rel, "wb") as f:
                f.write(data)
            n += 1
        res.update({"ok": n > 0, "files": n, "name": name, "error": None if n else "no valid files"})
    try:
        _sftp_op(_op)
    except Exception as e:                             # noqa: BLE001
        return {"ok": False, "error": str(e)}
    try:
        refresh_mission_audit()
    except Exception:                                  # noqa: BLE001
        pass
    return res


def set_server_config(key, value):
    meta = _SRVCFG_MAP.get(key)
    if not meta:
        return {"ok": False, "error": f"unknown field {key}"}
    lbl, typ, mask, nr, note = meta
    if mask and str(value) in ("", "********"):
        return {"ok": False, "error": "no change (password left masked)"}
    try:
        coerced = _srvcfg_coerce(typ, value)
    except (ValueError, TypeError) as e:
        return {"ok": False, "error": f"bad value: {e}"}
    try:
        ssh, sftp = _open_sftp()
    except Exception as e:                         # noqa: BLE001
        return {"ok": False, "error": f"sftp: {e}"}
    old = None
    try:
        with sftp.open(SRVCFG_PATH, "rb") as f:
            orig_text = f.read().decode("utf-8")
        cfg = json.loads(orig_text)
        old = _srvcfg_walk(cfg, key)
        if _srvcfg_walk(cfg, key, set_to=coerced) is None:
            return {"ok": False, "error": f"field {key} not present in config"}
        os.makedirs(BACKUP_DIR, exist_ok=True)
        with open(os.path.join(BACKUP_DIR, "DedicatedServerConfig.beforeedit.json"), "w", encoding="utf-8") as bf:
            bf.write(orig_text)
        with sftp.open(SRVCFG_PATH, "wb") as f:
            f.write(json.dumps(cfg, indent=2).encode("utf-8"))
    except Exception as e:                         # noqa: BLE001
        return {"ok": False, "error": f"write: {e}"}
    finally:
        try:
            ssh.close()
        except Exception:                          # noqa: BLE001
            pass
    try:
        RemoteCommand(RCMD_HOST, RCMD_PORT).send("reload-config")
    except Exception:                              # noqa: BLE001
        pass
    panel = _srvcfg_panel_mirror(key, old, coerced)
    refresh_server_config()
    res = {"ok": True, "key": key, "needs_restart": nr, "panel": panel, "ts": time.time()}
    _srvcfg_cache["last_set"] = res
    activity(f"Server config: {lbl} -> {'********' if mask else coerced}"
             + (f" (synced to gpanel: {panel.get('var')})" if panel.get("mirrored") else ""), "ADMIN")
    return res


def add_rotation_mission():
    """run.bat --add-rotation <Name> [Group] [MaxTime]: append a mission to
    MissionRotation in DedicatedServerConfig.json (Group defaults to 'User',
    MaxTime to 10800.0). Idempotent; surgical insert before the array's closing
    bracket; local backup first; verified by a JSON round-trip."""
    rest = [a for a in sys.argv[sys.argv.index("--add-rotation") + 1:] if not a.startswith("--")]
    if not rest:
        print("usage: run.bat --add-rotation <Name> [Group] [MaxTime]")
        return
    name = rest[0]
    group = rest[1] if len(rest) > 1 else "User"
    try:
        max_time = float(rest[2]) if len(rest) > 2 else 10800.0
    except ValueError:
        print("[rot] MaxTime must be a number"); return
    path = "DedicatedServerConfig.json"
    ssh, sftp = _open_sftp()
    try:
        try:
            with sftp.open(path, "rb") as f:
                text = f.read().decode("utf-8")
        except UnicodeDecodeError:
            print("[rot] ABORT: config is not valid UTF-8"); return
        cfg = json.loads(text)
        rot = cfg.get("MissionRotation")
        if not isinstance(rot, list):
            print("[rot] ABORT: no MissionRotation array"); return
        if any(isinstance(e, dict) and e.get("Key", {}).get("Group") == group
               and e.get("Key", {}).get("Name") == name for e in rot):
            print(f"[rot] '{group}/{name}' already in the rotation ({len(rot)} entries) - nothing to do.")
            return
        # locate the MissionRotation array's closing ']' (entries contain no '[')
        mr = text.index('"MissionRotation"')
        bopen = text.index("[", mr)
        bclose = text.index("]", bopen)
        insert_at = text.rindex("}", bopen, bclose) + 1
        entry = ("    {\n"
                 '      "Key": {\n'
                 f'        "Group": {json.dumps(group)},\n'
                 f'        "Name": {json.dumps(name)}\n'
                 "      },\n"
                 f'      "MaxTime": {max_time}\n'
                 "    }")
        new_text = text[:insert_at] + ",\n" + entry + text[insert_at:]
        new_cfg = json.loads(new_text)               # verify still valid JSON
        want = rot + [{"Key": {"Group": group, "Name": name}, "MaxTime": max_time}]
        if new_cfg.get("MissionRotation") != want:
            print("[rot] ABORT: post-insert rotation didn't match expected - not uploaded")
            return
        os.makedirs(BACKUP_DIR, exist_ok=True)
        with open(os.path.join(BACKUP_DIR, "DedicatedServerConfig.json.bak"),
                  "w", encoding="utf-8") as bf:
            bf.write(text)
        with sftp.open(path, "wb") as f:
            f.write(new_text.encode("utf-8"))
        print(f"[rot] added {group}/{name} (MaxTime {max_time}); rotation now "
              f"{len(new_cfg['MissionRotation'])} entries.")
        print("[rot] takes effect on the next FULL server restart.")
    finally:
        ssh.close()


def upload_bepinex():
    """run.bat --upload-bepinex: push the local NukeStats/bepinex_pack tree to the
    container root and the built NukeStats.dll to BepInEx/plugins/. RUN ONLY WITH THE
    SERVER STOPPED (it writes into the live game install). Reuses the SFTP creds."""
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "NukeStats")
    pack = os.path.join(base, "bepinex_pack")
    if not os.path.isdir(pack):
        print(f"[up] no BepInEx pack at {pack}")
        return
    ssh, sftp = _open_sftp()
    try:
        def mkremote(rpath):
            cur = ""
            for part in rpath.strip("/").split("/"):
                cur = f"{cur}/{part}" if cur else part
                try:
                    sftp.stat(cur)
                except IOError:
                    try:
                        sftp.mkdir(cur)
                    except IOError:
                        pass
        count = 0
        for root, _dirs, files in os.walk(pack):
            rel = os.path.relpath(root, pack).replace("\\", "/")
            rdir = "" if rel == "." else rel
            if rdir:
                mkremote(rdir)
            for fn in files:
                rp = f"{rdir}/{fn}" if rdir else fn
                sftp.put(os.path.join(root, fn), rp)
                count += 1
                print(f"  put {rp}")
        mkremote("BepInEx/plugins")
        dll = next((c for c in (os.path.join(base, "bin", "Release", "NukeStats.dll"),
                                os.path.join(base, "bin", "Debug", "NukeStats.dll"))
                    if os.path.exists(c)), None)
        if dll:
            sftp.put(dll, "BepInEx/plugins/NukeStats.dll")
            print(f"  put BepInEx/plugins/NukeStats.dll  (from {dll})")
            count += 1
        else:
            print("  [warn] NukeStats.dll not built yet - build it, then re-run, or upload it later.")
        print(f"[up] uploaded {count} file(s). Now set the GPanel Doorstop startup command "
              "and start the server; check console.log for 'NukeStats loaded'.")
    finally:
        ssh.close()


def _read_tick_rate():
    """Clamp the configured engine tick rate to a safe 30-120 Hz (default 60); never raises.
    Read at wrapper-build time so --setup-server / --rewrite-wrapper always emit the live value."""
    try:
        v = int(TICK_RATE)
    except (TypeError, ValueError):
        v = 60
    return max(30, min(120, v))


def setup_server():
    """One-off admin helper (run via:  run.bat --setup-server).

    The panel's startup command launches ./NuclearOptionServer.x86_64 with no flags
    and can't be edited, so we install a wrapper at that name. Unity derives its
    data folder from the executable name minus extension, so we rename the real
    launcher by just DROPPING the .x86_64 extension (NuclearOptionServer.x86_64 ->
    NuclearOptionServer) -- that still maps to NuclearOptionServer_Data, no symlink
    needed. The wrapper then execs ./NuclearOptionServer WITH the flags the bot
    needs. Idempotent and reversible (delete the wrapper, rename NuclearOptionServer
    back to *.x86_64). Reuses the NO_SFTP_* credentials.
    """
    import paramiko
    LAUNCH = "NuclearOptionServer.x86_64"   # what the panel runs; becomes the wrapper
    REAL   = "NuclearOptionServer"          # real ELF, ext dropped -> same _Data folder
    DATA   = "NuclearOptionServer_Data"
    tick   = _read_tick_rate()              # engine frame/tick rate (Hz), 30-120, default 60 (was hardcoded 30 -> live regression)
    wrapper = (
        "#!/bin/sh\n"
        "# Launch wrapper (map-vote bot). Exposes the localhost-only remote-command\n"
        "# port (127.0.0.1:5504) on 0.0.0.0:5550 via whatever relay tool the container\n"
        "# has, adds the remote-command flag + a stable console log the bot tails,\n"
        "# mirrors that log to the panel, and execs the game so it stays PID 1.\n"
        "# Undo: run.bat --revert-server\n"
        'export LD_LIBRARY_PATH="$(pwd)/linux64:$LD_LIBRARY_PATH"\n'
        "mkdir -p ./logs\n"
        ": > ./logs/console.log\n"
        ": > ./logs/relay.log\n"
        '{ for t in python3 python perl ncat socat nc busybox bash awk node php; do '
        'p=$(command -v "$t" 2>/dev/null) && echo "[probe] FOUND $t -> $p" '
        '|| echo "[probe] no $t"; done; } >> ./logs/relay.log 2>&1\n'
        "if command -v python3 >/dev/null 2>&1; then\n"
        "  echo '[relay] using python3' >> ./logs/relay.log\n"
        "  python3 ./no_relay.py 0.0.0.0:5550 127.0.0.1:5504 >> ./logs/relay.log 2>&1 &\n"
        "elif command -v perl >/dev/null 2>&1; then\n"
        "  echo '[relay] using perl' >> ./logs/relay.log\n"
        "  perl ./no_relay.pl 0.0.0.0:5550 127.0.0.1:5504 >> ./logs/relay.log 2>&1 &\n"
        "elif command -v ncat >/dev/null 2>&1; then\n"
        "  echo '[relay] using ncat' >> ./logs/relay.log\n"
        "  ncat -l 0.0.0.0 5550 -k -c 'ncat 127.0.0.1 5504' >> ./logs/relay.log 2>&1 &\n"
        "elif command -v socat >/dev/null 2>&1; then\n"
        "  echo '[relay] using socat' >> ./logs/relay.log\n"
        "  socat TCP-LISTEN:5550,fork,reuseaddr TCP:127.0.0.1:5504 >> ./logs/relay.log 2>&1 &\n"
        "else\n"
        "  echo '[relay] NO RELAY TOOL found in container' >> ./logs/relay.log\n"
        "fi\n"
        "tail -n +1 -F ./logs/console.log 2>/dev/null &\n"
        "exec ./NuclearOptionServer"
        f' -logFile ./logs/console.log -limitframerate {tick} -ServerRemoteCommands 5504 "$@"\n'
    )

    if not (SFTP_HOST and SFTP_USER and SFTP_PASS):
        print("[setup] Missing SFTP creds. Run this through run.bat:  run.bat --setup-server")
        return

    print(f"[setup] connecting to {SFTP_HOST}:{SFTP_PORT} as {SFTP_USER} ...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(SFTP_HOST, port=SFTP_PORT, username=SFTP_USER, password=SFTP_PASS,
                timeout=15, look_for_keys=False, allow_agent=False)
    sftp = ssh.open_sftp()
    try:
        names = set(sftp.listdir("."))
        if LAUNCH not in names:
            print(f"[setup] ERROR: {LAUNCH} not found in SFTP root. Entries: {sorted(names)[:10]}")
            return
        if DATA not in names:
            print(f"[setup] ERROR: {DATA} not found beside the binary; aborting to be safe.")
            return

        with sftp.open(LAUNCH, "rb") as f:
            magic = f.read(4)
        is_elf = magic == b"\x7fELF"
        kind = "ELF" if is_elf else ("script" if magic[:2] == b"#!" else "unknown")
        print(f"[setup] {LAUNCH} magic={magic!r} ({kind})")

        if REAL in names:
            if is_elf:
                print(f"[setup] ABORT: {REAL} exists but {LAUNCH} is still an ELF -- "
                      f"unclear state. Inspect manually, not touching anything.")
                return
            print(f"[setup] {REAL} already present; rewriting the wrapper only.")
        else:
            if not is_elf:
                print(f"[setup] ABORT: {LAUNCH} is not an ELF and {REAL} missing -- "
                      f"unexpected, not touching anything.")
                return
            print(f"[setup] renaming real launcher {LAUNCH} -> {REAL} (keeps {DATA} valid)")
            try:
                sftp.posix_rename(LAUNCH, REAL)
            except (IOError, OSError):
                sftp.rename(LAUNCH, REAL)

        with sftp.open(LAUNCH, "wb") as f:
            f.write(wrapper.encode("utf-8"))
        sftp.chmod(LAUNCH, 0o755)
        sftp.chmod(REAL, 0o755)

        # upload the relay helpers next to the binary so the wrapper can launch one
        here = os.path.dirname(os.path.abspath(__file__))
        for helper in ("no_relay.py", "no_relay.pl"):
            local = os.path.join(here, helper)
            try:
                with open(local, "r", encoding="utf-8") as rf:
                    src = rf.read()
                with sftp.open(helper, "wb") as f:
                    f.write(src.encode("utf-8"))
                sftp.chmod(helper, 0o755)
                print(f"[setup] uploaded {helper} ({len(src)} bytes)")
            except FileNotFoundError:
                print(f"[setup] WARNING: local {helper} not found next to the bot; skipping.")

        with sftp.open(LAUNCH, "rb") as f:
            head = f.read(32)
        with sftp.open(REAL, "rb") as f:
            rmagic = f.read(4)
        wmode = oct(sftp.stat(LAUNCH).st_mode & 0o777)
        rmode = oct(sftp.stat(REAL).st_mode & 0o777)
        print(f"[setup] wrapper({LAUNCH}) mode={wmode} head={head[:18]!r}")
        print(f"[setup] real({REAL}) mode={rmode} magic={rmagic!r}")
        if head.startswith(b"#!/bin/sh") and rmagic == b"\x7fELF":
            print("[setup] DONE. Now fully RESTART the server in the panel, then tell me.")
        else:
            print("[setup] WARNING: verification looks off -- do NOT restart; ping me.")
    finally:
        sftp.close()
        ssh.close()


def revert_server():
    """Undo setup_server(): remove the wrapper and restore NuclearOptionServer.x86_64.
    Run via:  run.bat --revert-server
    """
    import paramiko
    LAUNCH = "NuclearOptionServer.x86_64"
    REAL   = "NuclearOptionServer"
    if not (SFTP_HOST and SFTP_USER and SFTP_PASS):
        print("[revert] Missing SFTP creds. Run through run.bat:  run.bat --revert-server")
        return
    print(f"[revert] connecting to {SFTP_HOST}:{SFTP_PORT} as {SFTP_USER} ...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(SFTP_HOST, port=SFTP_PORT, username=SFTP_USER, password=SFTP_PASS,
                timeout=15, look_for_keys=False, allow_agent=False)
    sftp = ssh.open_sftp()
    try:
        names = set(sftp.listdir("."))
        if REAL not in names:
            print(f"[revert] Nothing to do: {REAL} not present (already reverted?).")
            return
        if LAUNCH in names:
            with sftp.open(LAUNCH, "rb") as f:
                magic = f.read(4)
            if magic == b"\x7fELF":
                print(f"[revert] ABORT: {LAUNCH} is already the real ELF; not touching.")
                return
            sftp.remove(LAUNCH)
            print(f"[revert] removed wrapper {LAUNCH}")
        try:
            sftp.posix_rename(REAL, LAUNCH)
        except (IOError, OSError):
            sftp.rename(REAL, LAUNCH)
        sftp.chmod(LAUNCH, 0o755)
        with sftp.open(LAUNCH, "rb") as f:
            magic = f.read(4)
        ok = magic == b"\x7fELF"
        print(f"[revert] restored {LAUNCH} magic={magic!r} ({'OK' if ok else 'WARNING: not ELF'})")
        print("[revert] DONE. Restart the server to return to the original (flag-less) launch.")
    finally:
        sftp.close()
        ssh.close()


def check_server():
    """Diagnostic (run via: run.bat --check-server): is the wrapper running and is
    the console log being written? Prints file state and the tail of the log."""
    import paramiko
    if not (SFTP_HOST and SFTP_USER and SFTP_PASS):
        print("[check] Missing SFTP creds. Run through run.bat:  run.bat --check-server")
        return
    print(f"[check] connecting to {SFTP_HOST}:{SFTP_PORT} as {SFTP_USER} ...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(SFTP_HOST, port=SFTP_PORT, username=SFTP_USER, password=SFTP_PASS,
                timeout=15, look_for_keys=False, allow_agent=False)
    sftp = ssh.open_sftp()
    try:
        now = time.time()
        names = set(sftp.listdir("."))
        for n in ("NuclearOptionServer", "NuclearOptionServer.x86_64",
                  "NuclearOptionServer_Data", "logs"):
            if n in names:
                st = sftp.stat(n)
                print(f"[check] {n}: size={st.st_size:,} age={int(now - st.st_mtime)}s")
            else:
                print(f"[check] {n}: MISSING")
        logpath = SFTP_LOG_PATH or "/logs/console.log"
        try:
            st = sftp.stat(logpath)
        except FileNotFoundError:
            print(f"[check] {logpath}: NOT FOUND -> the wrapper hasn't run. Do a FULL "
                  f"stop+start (not reconnect) so the new launch command executes.")
            return
        age = int(now - st.st_mtime)
        print(f"[check] {logpath}: size={st.st_size:,} age={age}s "
              f"({'fresh' if age < 180 else 'STALE - not being written'})")
        with sftp.open(logpath, "rb") as f:
            data = f.read(2_000_000).decode("utf-8", "replace")
        lines = data.splitlines()
        print(f"[check] read {len(lines)} lines from console.log")
        print("[check] ---- first 25 lines (startup + args echo) ----")
        for line in lines[:25]:
            print("   " + line)
        KEYS = ("5504", "remotecommand", "remote command", "command line", "commandline",
                "argument", "listen", "bind", "unknown option", "unrecognized",
                "exception", "invalid", "socket")
        NOISE = ("transport", "allocating", "[aihelo]", "warhead", "airbase")
        hits = [ln for ln in lines
                if any(k in ln.lower() for k in KEYS)
                and not any(n in ln.lower() for n in NOISE)]
        print("[check] ---- lines mentioning 5504 / remotecommand / args / errors ----")
        for ln in hits[:40]:
            print("   >> " + ln)
        if not hits:
            print("   (no relevant lines found anywhere in the log)")
        try:
            rst = sftp.stat("/logs/relay.log")
            with sftp.open("/logs/relay.log", "rb") as f:
                rlog = f.read(8000).decode("utf-8", "replace")
            print(f"[check] ---- /logs/relay.log (size={rst.st_size}) ----")
            for ln in rlog.splitlines()[-20:]:
                print("   " + ln)
        except FileNotFoundError:
            print("[check] /logs/relay.log: not present (no relay configured/started yet)")
    finally:
        sftp.close()
        ssh.close()


def test_tunnel():
    """Probe: can we reach the localhost-bound remote-command port by tunnelling
    through the SFTP host's SSH (paramiko direct-tcpip)? If yes, the bot can stay
    on this PC and drive the server over that tunnel."""
    import paramiko
    print(f"[tunnel] SSH to {SFTP_HOST}:{SFTP_PORT} as {SFTP_USER} ...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(SFTP_HOST, port=SFTP_PORT, username=SFTP_USER, password=SFTP_PASS,
                timeout=15, look_for_keys=False, allow_agent=False)
    try:
        transport = ssh.get_transport()
        try:
            chan = transport.open_channel("direct-tcpip",
                                          ("127.0.0.1", RCMD_PORT), ("127.0.0.1", 0))
        except Exception as e:  # noqa: BLE001
            print(f"[tunnel] FAILED to open a forward channel: {e!r}")
            print("[tunnel] -> this host's SFTP/SSH does not allow port forwarding.")
            return
        print(f"[tunnel] channel open to 127.0.0.1:{RCMD_PORT}; sending get-mission-time ...")
        payload = json.dumps({"name": "get-mission-time", "arguments": []}).encode("utf-8")
        chan.sendall(len(payload).to_bytes(4, "little") + payload)
        chan.settimeout(8)
        try:
            hdr = b""
            while len(hdr) < 4:
                b = chan.recv(4 - len(hdr))
                if not b:
                    print("[tunnel] channel closed before any reply.")
                    return
                hdr += b
            length = int.from_bytes(hdr, "little")
            body = b""
            while len(body) < length:
                b = chan.recv(length - len(body))
                if not b:
                    break
                body += b
            print(f"[tunnel] OK! reply: {body.decode('utf-8', 'replace')}")
            print("[tunnel] SUCCESS -- the bot can drive the server through an SSH tunnel.")
        except Exception as e:  # noqa: BLE001
            print(f"[tunnel] channel opened but no usable reply: {e!r}")
    finally:
        ssh.close()


def find_chat():
    """Diagnostic (run.bat --findchat): pull the console log and show chat-ish lines
    plus whether the parser matches them, so we can confirm/fix the chat regex."""
    import paramiko
    if not (SFTP_HOST and SFTP_USER and SFTP_PASS):
        print("[findchat] Missing SFTP creds. Run via run.bat --findchat")
        return
    print(f"[findchat] connecting to {SFTP_HOST}:{SFTP_PORT} ...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(SFTP_HOST, port=SFTP_PORT, username=SFTP_USER, password=SFTP_PASS,
                timeout=15, look_for_keys=False, allow_agent=False)
    sftp = ssh.open_sftp()
    try:
        logpath = SFTP_LOG_PATH or "/logs/console.log"
        with sftp.open(logpath, "rb") as f:
            data = f.read(4_000_000).decode("utf-8", "replace")
        lines = data.splitlines()
        hits = [ln for ln in lines if ("chat" in ln.lower()) or ("CmdSendChatMessage" in ln)]
        print(f"[findchat] {len(hits)} chat-ish line(s) in {logpath} (showing last 30):")
        for ln in hits[-30:]:
            parsed = parse_chat_line(ln)
            print(f"  [{'PARSED  ' if parsed else 'NO-MATCH'}] {ln}")
            if parsed:
                print(f"             -> {parsed}")
        if not hits:
            print("[findchat] No chat-ish lines found. Type in game chat, then re-run.")
    finally:
        sftp.close()
        ssh.close()


def show_ranks():
    """run.bat --ranks: print the full saved standings from ranks.json -- ALL
    players incl. rank-0 Officer Cadets, sorted by points (then name)."""
    load_ranks()
    if not RANK_DATA:
        print(f"[ranks] no records yet in {RANK_FILE}")
        return
    board = sorted(RANK_DATA.items(),
                   key=lambda kv: (-kv[1].get("points", 0), kv[1].get("name", "").lower()))
    print(f"[ranks] {len(board)} player(s) in {RANK_FILE}:")
    for i, (sid, rec) in enumerate(board, 1):
        pts = rec.get("points", 0)
        nm = rec.get("name", sid)
        _, rname, abbr, _ = RANKS[rank_index_for(pts)]
        nxt = points_to_next(pts)
        tail = f"{nxt:.1f} to next" if nxt is not None else "max rank"
        print(f"  {i:>3}. {nm:<28.28} {pts:>9.1f} pts  [{abbr:<7}] {rname:<18} ({tail})")


# ----------------------------------------------------------------------------
# Bot command centre  (run.bat --centre / centre.bat): one coloured, interactive
# console to send server commands + bot helpers. Stays open between commands.
# ----------------------------------------------------------------------------
STATUS_CODES = {
    2000: "Success", 4000: "BadRequest", 4001: "BadHeader", 4002: "BadLength",
    4003: "JsonError", 4004: "UnknownCommand", 4005: "BadArguments",
    5000: "InternalServerError", 5001: "CommandError", 5002: "ConfigError",
}

# (alias, wire-name, args-hint, description, destructive?) -- the 19 Shockfront
# ServerCommands, exposed through friendly aliases.
CENTRE_SERVER_CMDS = [
    ("players",     "get-player-list",      "",                         "list connected players + their ranks", False),
    ("time",        "get-mission-time",     "",                         "current / max mission time", False),
    ("mission",     "get-mission",          "",                         "current + next mission", False),
    ("rotation",    "get-mission-rotation", "",                         "mission rotation + next override", False),
    ("serverid",    "get-server-id",        "",                         "the server's Steam ID", False),
    ("say",         "send-chat-message",    "<message>",                "send a message into in-game chat", False),
    ("settime",     "set-time-remaining",   "<seconds>",                "set the remaining mission time", False),
    ("nextmap",     "set-next-mission",     "<group> <name> <maxTime>", "queue the next mission (quote the name)", False),
    ("clearnext",   "clear-next-mission",   "",                         "cancel a queued next mission", False),
    ("reloadcfg",   "reload-config",        "[filepath]",               "reload the server config", True),
    ("setrotation", "set-mission-rotation", "<json>",                   "replace the mission rotation (JSON)", True),
    ("kick",        "kick-player",          "<steamId>",                "kick a player (until restart)", True),
    ("unkick",      "unkick-player",        "<steamId>",                "un-kick a player", False),
    ("clearkicks",  "clear-kicked-players", "",                         "clear the whole kick list", True),
    ("ban",         "banlist-add",          "<steamId> [reason]",       "ban a SteamID (writes to file)", True),
    ("unban",       "banlist-remove",       "<steamId>",                "remove a ban", True),
    ("banreload",   "banlist-reload",       "",                         "reload the ban list from file", False),
    ("banclear",    "banlist-clear",        "",                         "clear the in-memory ban list", True),
    ("updateready", "update-ready",         "",                         "signal a component ready", False),
]
CENTRE_BOT_CMDS = [
    ("ranks",       "show ALL saved player ranks, best first (nice table)"),
    ("rankpreview", "post the rank ladder into in-game chat"),
    ("endmission",  "force the current mission to end now"),
    ("help",        "show this command list again"),
    ("cls",         "clear the screen"),
    ("quit",        "close the command centre (the bot keeps running)"),
]


def command_centre():
    """run.bat --centre : interactive coloured console for driving the server."""
    global DEBUG
    DEBUG = False                       # we print our own tidy output instead
    try:                                # enable ANSI colours on Windows 10+
        import ctypes
        k = ctypes.windll.kernel32
        k.SetConsoleMode(k.GetStdHandle(-11), 7)
    except Exception:                   # noqa: BLE001
        pass

    R, B, DIM = "\033[0m", "\033[1m", "\033[90m"
    RED, GRN, YEL = "\033[91m", "\033[92m", "\033[93m"
    CYN, MAG, WHT = "\033[96m", "\033[95m", "\033[97m"

    def hexc(hx):
        hx = hx.lstrip("#")
        return f"\033[38;2;{int(hx[0:2],16)};{int(hx[2:4],16)};{int(hx[4:6],16)}m"

    rc = RemoteCommand(RCMD_HOST, RCMD_PORT)
    load_ranks()

    def banner():
        print(f"{CYN}{B}")
        print("  ================================================================")
        print("         NUCLEAR OPTION  -  BOT COMMAND CENTRE")
        print("  ================================================================" + R)
        print(f"{DIM}  server {RCMD_HOST}:{RCMD_PORT}   |   type a command + Enter   |"
              f"   'help' lists everything, 'quit' exits{R}\n")

    def show_help():
        print(f"\n{B}{WHT}  SERVER COMMANDS{R} {DIM}(sent live to the game server){R}")
        for alias, wire, ahint, desc, danger in CENTRE_SERVER_CMDS:
            mark = f"{RED}!{R}" if danger else " "
            print(f"   {mark} {GRN}{alias:<11}{R}{DIM}{ahint:<27}{R}{desc}")
        print(f"\n{B}{WHT}  BOT COMMANDS{R} {DIM}(local helpers){R}")
        for alias, desc in CENTRE_BOT_CMDS:
            print(f"     {CYN}{alias:<11}{R}{'':<27}{desc}")
        print(f"\n   {DIM}{RED}!{DIM} = changes the server/players -> you'll be asked to confirm."
              f"   raw <name> <args...> sends any command directly.{R}\n")

    def confirm(what):
        try:
            return input(f"{YEL}   really do '{what}'? type yes: {R}").strip().lower() == "yes"
        except (EOFError, KeyboardInterrupt):
            return False

    def show_response(code, resp):
        if code is None:
            print(f"   {RED}no response - server/relay unreachable{R}")
            return
        name = STATUS_CODES.get(code, "?")
        col = GRN if code == 2000 else RED
        print(f"   {col}{'OK' if code == 2000 else 'ERROR'} ({code} {name}){R}")
        if isinstance(resp, dict):
            print(DIM + json.dumps(resp, indent=2)[:4000] + R)
        elif isinstance(resp, str) and resp.strip():
            print(DIM + resp[:2000] + R)

    def show_players():
        code, resp = rc.send("get-player-list", return_code=True)
        if code is None:
            print(f"   {RED}no response - server/relay unreachable{R}")
            return
        if code != 2000:
            print(f"   {RED}error {code} {STATUS_CODES.get(code,'?')}{R}")
            return
        players = (resp.get("Players") or resp.get("players")) if isinstance(resp, dict) else None
        if not players:
            print(f"   {DIM}(no players online){R}")
            return
        print(f"   {B}{len(players)} player(s) online:{R}")
        for i, p in enumerate(players, 1):
            sid = str(p.get("steamId")); nm = p.get("displayName") or sid
            fac = p.get("faction") or "-"
            pts = player_points(sid)
            _, _, abbr, color = RANKS[rank_index_for(pts)]
            print(f"     {i:>2}. {hexc(color)}[{abbr}]{R} {nm:<22.22} {DIM}{fac:<8} {pts:.1f} pts   {sid}{R}")

    def show_ranks_table():
        load_ranks()
        if not RANK_DATA:
            print(f"   {DIM}no ranks saved yet{R}")
            return
        board = sorted(RANK_DATA.items(),
                       key=lambda kv: (-kv[1].get("points", 0), kv[1].get("name", "").lower()))
        print(f"\n   {B}{WHT}SERVER RANKS - {len(board)} pilots (best first){R}")
        print(f"   {DIM}{'#':>3}  {'pilot':<24}{'pts':>5}   rank{R}")
        for i, (sid, rec) in enumerate(board, 1):
            pts = rec.get("points", 0); nm = rec.get("name", sid)
            _, rname, abbr, color = RANKS[rank_index_for(pts)]
            print(f"   {i:>3}. {nm:<24.24}{pts:>9.1f}   {hexc(color)}[{abbr}] {rname}{R}")
        print()

    def post_rank_ladder():
        rc.say("<color=#FFFF00>=== SERVER RANKS (points needed) ===</color>")
        row = []
        for i, (thr, name, abbr, color) in enumerate(RANKS, 1):
            row.append(f"<color={color}>{i}. {name} [{abbr}] {thr}</color>")
            if len(row) == 4:
                rc.say("   ".join(row)); row = []
        if row:
            rc.say("   ".join(row))
        for line in skill_tier_info():
            rc.say(line)
        print(f"   {GRN}posted the rank ladder + skill tiers to in-game chat{R}")

    banner()
    show_help()
    while True:
        try:
            raw = input(f"{B}{CYN}command>{R} ").lstrip("﻿").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n   {DIM}closing the command centre (the bot keeps running){R}")
            return
        if not raw:
            continue
        head, _, rest = raw.partition(" ")
        cmd, rest = head.lower(), rest.strip()

        if cmd in ("quit", "exit", "q"):
            print(f"   {DIM}closing the command centre (the bot keeps running){R}")
            return
        if cmd in ("help", "?", "commands"):
            show_help(); continue
        if cmd in ("cls", "clear"):
            os.system("cls"); banner(); continue
        if cmd == "ranks":
            show_ranks_table(); continue
        if cmd == "players":
            show_players(); continue
        if cmd == "rankpreview":
            post_rank_ladder(); continue
        if cmd == "endmission":
            if confirm("force-end the current mission"):
                show_response(*rc.send("set-time-remaining", "5", return_code=True))
            continue
        if cmd == "say":
            if not rest:
                print(f"   {DIM}usage: say <message>{R}"); continue
            show_response(*rc.send("send-chat-message", rest, return_code=True)); continue
        if cmd == "raw":
            try:
                toks = shlex.split(rest)
            except ValueError as e:
                print(f"   {RED}{e}{R}"); continue
            if not toks:
                print(f"   {DIM}usage: raw <command-name> <arg> <arg> ...{R}"); continue
            # honour the same confirmation gate the aliases use for known destructive commands
            if any(e[1] == toks[0] and e[4] for e in CENTRE_SERVER_CMDS) and not confirm(rest):
                print(f"   {DIM}cancelled{R}"); continue
            show_response(*rc.send(toks[0], *toks[1:], return_code=True)); continue

        entry = (next((e for e in CENTRE_SERVER_CMDS if e[0] == cmd), None)
                 or next((e for e in CENTRE_SERVER_CMDS if e[1] == cmd), None))
        if not entry:
            print(f"   {RED}unknown command '{cmd}'{R} {DIM}- type 'help'{R}"); continue
        alias, wire, ahint, desc, danger = entry
        try:
            toks = shlex.split(rest)
        except ValueError as e:
            print(f"   {RED}{e}{R}"); continue
        if danger and not confirm(f"{alias} {rest}".strip()):
            print(f"   {DIM}cancelled{R}"); continue
        # set-time-remaining with a small value cuts the round short for everyone
        if wire == "set-time-remaining" and toks:
            try:
                if float(toks[0]) < 60 and not confirm(f"set remaining time to {toks[0]}s (ends the round soon)"):
                    print(f"   {DIM}cancelled{R}"); continue
            except ValueError:
                pass
        show_response(*rc.send(wire, *toks, return_code=True))


def match_selftest():
    """run.bat --matchtest: exercise the per-match lifecycle OFFLINE (temp files,
    no server) and print the resulting history/ledger/derived stats."""
    global MATCH_HISTORY_FILE, LEDGER_FILE, RANK_DATA, CURRENT_MISSION, CUR_MATCH
    import tempfile
    d = tempfile.mkdtemp()
    MATCH_HISTORY_FILE = os.path.join(d, "match_history.json")
    LEDGER_FILE = os.path.join(d, "points_ledger.jsonl")
    RANK_DATA, CUR_MATCH = {}, None

    class _Stub:
        def say(self, m):
            print("   say>", _plain(m))

    rc = _Stub()

    def _award(sid, nm, fac, pts, reason, kind):
        award_points(sid, nm, pts)
        match_award(sid, nm, fac, pts, reason, kind, local_points(sid))   # ledger balance snapshot = LOCAL (per-server audit)

    print("[matchtest] MATCH 1: Tomo + Shirley capture & win, Jerms only present")
    CURRENT_MISSION = "Escalation BDF - Dawn"
    for sid, nm in (("1", "Tomo"), ("2", "Shirley")):
        _award(sid, nm, "Boscali", 1, "capture: Riven Beach (Boscali)", "capture")
    match_set_result("Victory (Boscali)")
    for sid, nm in (("1", "Tomo"), ("2", "Shirley")):
        _award(sid, nm, "Boscali", 2, "win (Boscali)", "win")
    match_finalize(rc, [{"steamId": "1", "displayName": "Tomo", "faction": "Boscali"},
                        {"steamId": "2", "displayName": "Shirley", "faction": "Boscali"},
                        {"steamId": "3", "displayName": "Jerms", "faction": "Boscali"}])
    print("[matchtest] finalize again -> must be a no-op:")
    match_finalize(rc, [])

    print("\n[matchtest] MATCH 2: Tomo plays a loss")
    CURRENT_MISSION = "Terminal Control PALA - Day"
    _award("1", "Tomo", "Primeva", 1, "capture: Feldspar (Primeva)", "capture")
    match_set_result("Defeat (Boscali won)")
    match_finalize(rc, [{"steamId": "1", "displayName": "Tomo", "faction": "Primeva"}])

    print("\n[matchtest] MATCH 3: Mission complete, players present but NOTHING scored")
    print("            -> must NOT create a phantom record / count a match")
    CUR_MATCH = None
    with open(MATCH_HISTORY_FILE, encoding="utf-8") as f:
        before = len(json.load(f))
    match_finalize(rc, [{"steamId": "9", "displayName": "Lurker", "faction": "Boscali"}])
    with open(MATCH_HISTORY_FILE, encoding="utf-8") as f:
        after = len(json.load(f))
    print(f"[matchtest] history records before={before} after={after} "
          f"-> phantom guard: {'PASS' if after == before else 'FAIL'}")
    print("[matchtest] Lurker detail (must be 0 matches):", player_match_detail("9"))

    print("\n[matchtest] ranks.json totals:", RANK_DATA)
    print("[matchtest] fold_match_stats:", fold_match_stats())
    print("[matchtest] Tomo detail:", player_match_detail("1"))
    print("[matchtest] Jerms detail (present, never scored):", player_match_detail("3"))
    print("[matchtest] Tomo ledger:", recent_ledger_for("1", 9))
    # invariant: ledger sum == ranks for a fresh run
    led = {}
    with open(LEDGER_FILE, encoding="utf-8") as f:
        for line in f:
            e = json.loads(line)
            led[e["steamid"]] = led.get(e["steamid"], 0) + e["pts"]
    ok = all(led.get(sid, 0) == rec["points"] for sid, rec in RANK_DATA.items())
    print(f"\n[matchtest] ledger-sum == ranks invariant: {'PASS' if ok else 'FAIL'}")


def audit_ledger():
    """run.bat --audit [name]: sum points_ledger.jsonl per SteamID vs ranks.json, and break
    the awards down by category (score / kill / win / place_* / capture / grant / score-spike).
    Ledger may be LESS than ranks for players with pre-ledger points (normal); ledger GREATER
    than ranks would indicate a double-award bug. Informational lines (capture, score-spike)
    carry pts:0 so they never inflate the per-player total. Pass a name to drill into one player."""
    load_ranks()
    totals = {}                      # sid -> summed pts (real awards only; info lines are 0)
    bycat = {}                       # category -> summed pts (server-wide)
    by_sid_cat = {}                  # sid -> {category -> [count, pts]}
    spikes = []                      # (ts, name, reason) for live exploit review
    try:
        with open(LEDGER_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sid = str(e.get("steamid"))
                pts = e.get("pts", 0) or 0
                cat = e.get("category", "?")
                totals[sid] = totals.get(sid, 0) + pts
                bycat[cat] = bycat.get(cat, 0) + pts
                cc = by_sid_cat.setdefault(sid, {}).setdefault(cat, [0, 0.0])
                cc[0] += 1; cc[1] = round(cc[1] + pts, 1)
                if cat == "score-spike":
                    spikes.append((e.get("ts", ""), e.get("name", sid), e.get("reason", "")))
    except FileNotFoundError:
        print(f"[audit] no ledger yet at {LEDGER_FILE}")
    # Optional drill-down: run.bat --audit <name|sid>
    rest = sys.argv[sys.argv.index("--audit") + 1:] if "--audit" in sys.argv else []
    query = " ".join(a for a in rest if not a.startswith("--")).strip()
    if query:
        ql = query.lower()
        hits = [(sid, rec) for sid, rec in RANK_DATA.items()
                if sid == query or ql in str(rec.get("name", "")).lower()]
        if not hits:
            print(f"[audit] no player matching '{query}'")
        for sid, rec in hits:
            print(f"\n[audit] {rec.get('name', sid)} ({sid}) - {rec.get('points', 0)} pts, "
                  f"ledger {round(totals.get(sid, 0), 1)}")
            for cat, (cnt, pv) in sorted(by_sid_cat.get(sid, {}).items(), key=lambda kv: -kv[1][1]):
                print(f"    {cat:12} x{cnt:<4} {pv:+.1f}")
        return
    print(f"[audit] {len(RANK_DATA)} ranked players; ledger covers {len(totals)} of them")
    print("[audit] points by category (server-wide):")
    for cat, pv in sorted(bycat.items(), key=lambda kv: -kv[1]):
        print(f"    {cat:12} {pv:+.1f}")
    if spikes:
        print(f"[audit] {len(spikes)} score-spike flag(s) logged (review for exploits):")
        for ts, nm, why in spikes[-10:]:
            print(f"    {ts}  {nm}  {why}")
    overs = 0
    for sid, rec in sorted(RANK_DATA.items(), key=lambda kv: -kv[1].get("points", 0)):
        rp, lp = rec.get("points", 0), totals.get(sid, 0)
        if lp > rp:
            overs += 1
            print(f"  !! {rec.get('name', sid):24} ledger {lp} > ranks {rp}  (possible double-award)")
    print(f"[audit] {'OK - no over-credits' if overs == 0 else f'{overs} over-credit(s)!'}; "
          f"(ledger < ranks is expected for points earned before the ledger existed)")


def ctx_log():
    """run.bat --ctxlog <term> [lines]: show each match of <term> with N context
    lines above/below (default 3), so we can see what IDs sit next to an event."""
    import paramiko
    args = sys.argv[sys.argv.index("--ctxlog") + 1:]
    if not args:
        print("usage: run.bat --ctxlog <term> [context_lines]")
        return
    term = args[0].lower()
    ctx = int(args[1]) if len(args) > 1 and args[1].isdigit() else 3
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(SFTP_HOST, port=SFTP_PORT, username=SFTP_USER, password=SFTP_PASS,
                timeout=15, look_for_keys=False, allow_agent=False)
    sftp = ssh.open_sftp()
    try:
        logpath = SFTP_LOG_PATH or "/logs/console.log"
        with sftp.open(logpath, "rb") as f:
            data = f.read(16_000_000).decode("utf-8", "replace")
        lines = data.splitlines()
        shown = 0
        for i, ln in enumerate(lines):
            if term in ln.lower():
                print(f"  --- match @ line {i} ---")
                for j in range(max(0, i - ctx), min(len(lines), i + ctx + 1)):
                    print(f"  {'>>' if j == i else '  '} {lines[j].strip()}")
                shown += 1
                if shown >= 12:
                    print("  ... (stopped at 12 matches)")
                    break
        if not shown:
            print(f"[ctxlog] no matches for {term!r}")
    finally:
        sftp.close()
        ssh.close()


def scan_log():
    """Diagnostic (run.bat --scanlog [terms...]): pull the console log and surface
    lines that look like player actions (rank, score, kills, captures, ...), so we
    can see what data exists and whether it ties to a SteamID."""
    import paramiko
    if not (SFTP_HOST and SFTP_USER and SFTP_PASS):
        print("[scanlog] Missing SFTP creds. Run via run.bat --scanlog")
        return
    extra = [a.lower() for a in sys.argv[sys.argv.index("--scanlog") + 1:]]
    terms = extra or [
        "rank", "promot", "score", "kill", "destroy", "shot down", "captur",
        "objective", "credit", "reward", "experience", "eliminat", "[player]",
        "steamconnection", "death", "respawn", "landed", "takeoff",
    ]
    print(f"[scanlog] connecting to {SFTP_HOST}:{SFTP_PORT} ...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(SFTP_HOST, port=SFTP_PORT, username=SFTP_USER, password=SFTP_PASS,
                timeout=15, look_for_keys=False, allow_agent=False)
    sftp = ssh.open_sftp()
    try:
        logpath = SFTP_LOG_PATH or "/logs/console.log"
        with sftp.open(logpath, "rb") as f:
            data = f.read(16_000_000).decode("utf-8", "replace")
        lines = data.splitlines()
        print(f"[scanlog] {len(lines)} lines in {logpath}; searching: {terms}")
        counts, samples = {}, {}
        for ln in lines:
            low = ln.lower()
            for t in terms:
                if t in low:
                    counts[t] = counts.get(t, 0) + 1
                    samples.setdefault(t, [])
                    if len(samples[t]) < 5:
                        samples[t].append(ln.strip())
        if not counts:
            print("[scanlog] no matches. Try custom terms, e.g.: run.bat --scanlog elo wins")
            return
        print("[scanlog] hit counts:")
        for t in sorted(counts, key=lambda k: -counts[k]):
            print(f"  {t!r}: {counts[t]}")
        print("[scanlog] samples:")
        for t in sorted(counts, key=lambda k: -counts[k]):
            print(f"  --- {t!r} ---")
            for ln in samples[t]:
                print(f"    {ln}")
    finally:
        sftp.close()
        ssh.close()


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sample = ("81587.130: [ChatManager] CmdSendChatMessage allChat:True "
                  "connection(SteamConnection(7656119xxxxxxxxxx)) Player(Clone) 2")
        parsed = parse_chat_line(sample)
        print("parsed:", parsed)
        sample_end = ("100.0: [DedicatedServerManager] Mission complete. "
                      "Waiting 60 seconds before closing...")
        print("mission-end match:", bool(MISSION_END_RE.search(sample_end)))
        print("!votemap thresholds (players: yes-needed):",
              {n: n // 2 + 1 for n in (1, 2, 3, 4, 5, 6)})

        # ballot generation + constraints, six votes in a row ('*' = dark map)
        print("\n[selftest] six ballots in a row ('*' = dark map):")
        ok = True
        prev_esc = prev_tc = None
        for r in range(6):
            ballot = open_vote()
            names = [ballot[k][1] for k in sorted(ballot)]
            esc = frozenset(n for n in names if n in ESCALATION_MISSIONS)
            tc = frozenset(n for n in names if n in TERMINAL_CONTROL_MISSIONS)
            dark = sum(is_dark(n) for n in names)
            shown = "   ".join(
                f"{k}={ballot[k][3]}{'*' if is_dark(ballot[k][1]) else ''}" for k in ballot
            )
            print(f"  vote {r + 1}: {shown}")
            print(f"          (esc={len(esc)} tc={len(tc)} dark={dark})")
            if len(esc) != 2 or len(tc) != 2:
                ok = False; print("          !! wrong family counts")
            if dark > MAX_DARK_PER_VOTE:
                ok = False; print("          !! too many dark maps")
            if prev_esc is not None and esc == prev_esc:
                ok = False; print("          !! repeated Escalation pair")
            if prev_tc is not None and tc == prev_tc:
                ok = False; print("          !! repeated Terminal Control pair")
            prev_esc, prev_tc = esc, tc

        # vote extraction against the most recent ballot
        print("\n[selftest] extract_vote (against the last ballot above):")
        for msg in ["!1", "!vote 2", "!3 go", "1", "4", "!9", "hi"]:
            print(f"  {msg!r:>9} -> {extract_vote(msg)}")

        print("\n[selftest] rank thresholds (points -> rank, next):")
        for pts in (0, 1, 2, 6, 24, 25, 150, 999):
            i = rank_index_for(pts)
            print(f"  {pts:>4} -> {RANKS[i][1]} ({RANKS[i][2]}); to next: {points_to_next(pts)}")
        print("[selftest] event-line parsing (capture side + result):")
        for s in ("Adding airbase riven_beach to PrimevaHQ",
                  "AIRBASE Riven Beach TOTAL CAPTURE 36",
                  "1888.994: [GameResolution] FinishGame Defeat"):
            a = ADD_AIRBASE_RE.search(s)
            c = CAPTURE_RE.search(s)
            g = GAME_RESULT_RE.search(s)
            winner = RESULT_WINNER.get(g.group(1).lower()) if g else None
            print(f"  capturing-side={a.group(2) if a else None}  "
                  f"capture={c.group(1) if c else None}  "
                  f"result={g.group(1) if g else None}  winner={winner}")
        print("[selftest] result->winner mapping:",
              {k: v for k, v in RESULT_WINNER.items()},
              "(a PALA/Primeva player win logs as 'Defeat' -> Primeva wins)")

        print("\n[selftest] PASS" if ok else "\n[selftest] FAIL -- see !! lines above")
    elif "--testconn" in sys.argv:
        test_conn()
    elif "--testchat" in sys.argv:
        test_chat()
    elif "--setup-server" in sys.argv:
        setup_server()
    elif "--revert-server" in sys.argv:
        revert_server()
    elif "--check-server" in sys.argv:
        check_server()
    elif "--testtunnel" in sys.argv:
        test_tunnel()
    elif "--findchat" in sys.argv:
        find_chat()
    elif "--say" in sys.argv:
        i = sys.argv.index("--say")
        msg = " ".join(sys.argv[i + 1:]).strip() or "hello"
        rc = RemoteCommand(RCMD_HOST, RCMD_PORT)
        print(f"[say] sending to game chat: {msg!r}")
        print(f"[say] response: {rc.say(msg)}")
    elif "--endmission" in sys.argv:
        rc = RemoteCommand(RCMD_HOST, RCMD_PORT)
        secs = 5
        print(f"[endmission] forcing the current mission to end in ~{secs}s ...")
        print(f"[endmission] set-time-remaining -> {rc.set_time_remaining(secs)}")
        print("[endmission] If the running bot is watching, it should soon log:")
        print('             "[bot] mission complete detected -> vote opened".')
    elif "--cmd" in sys.argv:
        i = sys.argv.index("--cmd")
        rest = sys.argv[i + 1:]
        name = rest[0] if rest else ""
        cmdargs = rest[1:]
        rc = RemoteCommand(RCMD_HOST, RCMD_PORT)
        print(f"[cmd] sending {name!r} args={cmdargs} ...")
        print(f"[cmd] response: {rc.send(name, *cmdargs)!r}")
    elif "--players" in sys.argv:
        rc = RemoteCommand(RCMD_HOST, RCMD_PORT)
        print("[players] calling get-player-list ...")
        resp = rc.send("get-player-list")
        print(f"[players] raw response -> {resp!r}")
    elif "--colortest" in sys.argv:
        rc = RemoteCommand(RCMD_HOST, RCMD_PORT)
        msg = ("<color=#55FF55>GREEN ok</color>  "
               "<color=#FFFF00>YELLOW ok</color>  "
               "<color=#FF5555>RED ok</color>")
        print(f"[colortest] sending: {msg}")
        print(f"[colortest] response: {rc.say(msg)}")
    elif "--ls" in sys.argv:
        remote_ls()
    elif "--cat" in sys.argv:
        remote_cat()
    elif "--get" in sys.argv:
        remote_get()
    elif "--put-atomic" in sys.argv:
        remote_put_atomic()
    elif "--chmod-exec" in sys.argv:
        remote_chmod_exec()
    elif "--deploy-plugin-dry" in sys.argv:
        deploy_plugin_job(dry=True)
    elif "--deploy-plugin" in sys.argv:
        deploy_plugin_job(dry=False)
    elif "--disable-panel-restart" in sys.argv:
        disable_panel_restart()
    elif "--put" in sys.argv:
        remote_put()
    elif "--probe-missions" in sys.argv:
        probe_missions()
    elif "--set-server-name" in sys.argv:
        set_server_name()
    elif "--set-ai-limits" in sys.argv:
        set_ai_limits()
    elif "--set-balance-diff" in sys.argv:
        set_balance_diff()
    elif "--set-votekick" in sys.argv:
        set_votekick()
    elif "--apply-map-changes" in sys.argv:
        apply_map_changes()
    elif "--check-ranks" in sys.argv or "--fix-ranks" in sys.argv:
        fix_starting_ranks()
    elif "--add-rotation" in sys.argv:
        add_rotation_mission()
    elif "--upload-bepinex" in sys.argv:
        upload_bepinex()
    elif "--centre" in sys.argv or "--center" in sys.argv:
        command_centre()
    elif "--scanlog" in sys.argv:
        scan_log()
    elif "--ranks" in sys.argv:
        show_ranks()
    elif "--matchtest" in sys.argv:
        match_selftest()
    elif "--audit" in sys.argv:
        audit_ledger()
    elif "--ctxlog" in sys.argv:
        ctx_log()
    elif "--rankpreview" in sys.argv:
        rc = RemoteCommand(RCMD_HOST, RCMD_PORT)
        online = len(get_players(rc))
        print(f"[rankpreview] {online} player(s) online; sending 11-rank preview...")
        rc.say("<color=#FFFF00>=== SERVER RANKS (points needed) ===</color>")
        row = []
        for i, (thr, name, abbr, color) in enumerate(RANKS, 1):
            row.append(f"<color={color}>{i}. {name} [{abbr}] {thr}</color>")
            if len(row) == 4:
                rc.say("   ".join(row))
                row = []
        if row:
            rc.say("   ".join(row))
        for line in skill_tier_info():
            rc.say(line)
        print("[rankpreview] done")
    else:
        # Self-healing: if main() ever throws an unexpected error, log it and
        # restart the loop rather than dying. Ctrl-C still stops cleanly. (An
        # external keep-alive wrapper, run_keepalive.bat, covers hard process
        # death -- killed / OOM / reboot -- that Python can't catch.)
        while True:
            try:
                main()
            except KeyboardInterrupt:
                print("\n[bot] stopped.")
                break
            except Exception:                       # noqa: BLE001 - never die on a bug
                print("[bot] main() crashed; restarting in 5s:")
                traceback.print_exc()
                sys.stdout.flush()
                activity("Bot hit an error and is auto-restarting in 5s "
                         "(details in bot_output.log)", "!")
                time.sleep(5)
