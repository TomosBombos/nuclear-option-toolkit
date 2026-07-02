#!/usr/bin/env python3
"""Nuke Option — Web Command Centre (backend).

A modern browser dashboard replacing the Textual TUI. Serves webcc.html + a JSON
API that reuses the bot's RemoteCommand relay, the baked map atlas, ranks.json,
the admin_commands.jsonl queue (so grant/team flow through the running bot, which
owns ranks + SFTP), and the Pterodactyl client API for real power control.

Run:  python cc_web.py   then open  http://127.0.0.1:8770
Config: apiKey.txt (Pterodactyl client key) + panel.txt (panel URL).
"""
import json
import math
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.request

from flask import Flask, jsonify, request, send_from_directory

import no_mapvote_bot as bot
try:
    from map_atlas import ATLAS as _ATLAS
except Exception:                                        # noqa: BLE001
    _ATLAS = {}

HERE = os.path.dirname(os.path.abspath(__file__))
DASHBOARD = os.path.join(HERE, "dashboard_state.json")
ACTIVITY = os.path.join(HERE, "activity.log")
CONSOLE = os.path.join(HERE, "console_mirror.log")
RANK_FILE = getattr(bot, "RANK_FILE", os.path.join(HERE, "ranks.json"))
SCHEDULE_FILE = os.path.join(HERE, "schedule.json")   # scheduled restarts/updates (UI here, executed by the bot)
PENDING_DLL  = os.path.join(HERE, "pending_plugin.dll")     # a plugin update waiting for the next deploy
PENDING_META = os.path.join(HERE, "pending_plugin.json")    # sidecar: {version, note, sha256, staged_at}
DEPLOYED_SHA = os.path.join(HERE, "deployed_plugin.sha256") # sha of the plugin currently deployed/live
DEPLOYED_META = os.path.join(HERE, "deployed_plugin.json")  # {version, sha, deployed_at} written by the deploy job
PORT = int(((getattr(bot, "_TK_CFG", {}) or {}).get("web", {}) or {}).get("port") or os.environ.get("PORT") or os.environ.get("NOCC_PORT") or 8770)  # config web.port -> env -> 8770
# Bind interface: default 0.0.0.0 so the dashboard is reachable from other devices on the LAN
# (phone/laptop), matching the LAN-over-HTTP use the clipboard fallback was built for. Lock it back
# to loopback with web.host="127.0.0.1" (config) or NOCC_HOST=127.0.0.1 (env) if you want host-only.
HOST = (((getattr(bot, "_TK_CFG", {}) or {}).get("web", {}) or {}).get("host")) or os.environ.get("NOCC_HOST") or "0.0.0.0"
SETTINGS_CATALOGUE = os.path.join(HERE, "settings_catalogue.json")  # static metadata for the settings menu
BOT_OVERRIDES = os.path.join(HERE, "bot_overrides.json")            # bot-owned setting overrides (current values)
_last_dump_nudge = 0.0                                              # throttle the "ask the plugin to dump" nudge


def _load_catalogue():
    """The shipped settings catalogue (friendly names / groups / types / defaults / ranges)."""
    try:
        with open(SETTINGS_CATALOGUE, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("settings", []) if isinstance(d, dict) else (d if isinstance(d, list) else [])
    except (OSError, ValueError):
        return []


def _deploy_status():
    """Describe the plugin update (if any) STAGED for the next deploy, so the web CC can show
    'update good to go' at a glance. Reads pending_plugin.dll (+ its .json sidecar) and compares
    its sha to deployed_plugin.sha256. `new` => something genuinely different from what's live."""
    out = {"staged": False}
    try:                                                  # the LIVE deployed version (recorded at deploy time)
        with open(DEPLOYED_META, encoding="utf-8") as f:
            dm = json.load(f)
        out["deployed_version"] = dm.get("version")
        out["deployed_at"] = dm.get("deployed_at")
    except Exception:                                     # noqa: BLE001 - not recorded yet / no deploy
        pass
    try:
        if not os.path.exists(PENDING_DLL):
            return out                                    # no pending update -> still reports deployed_version
        import hashlib
        h = hashlib.sha256()
        with open(PENDING_DLL, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        sha = h.hexdigest()
        out.update({"staged": True, "size": os.path.getsize(PENDING_DLL), "sha": sha[:12]})
        try:                                              # optional human-readable metadata
            with open(PENDING_META, encoding="utf-8") as f:
                meta = json.load(f)
            out["version"] = meta.get("version")
            out["note"] = meta.get("note")
            out["staged_at"] = meta.get("staged_at")
            out["meta_ok"] = (str(meta.get("sha256", ""))[:12] == sha[:12])   # sidecar matches the real DLL?
        except Exception:                                 # noqa: BLE001
            out["meta_ok"] = None
        deployed = ""
        try:
            with open(DEPLOYED_SHA, encoding="utf-8") as f:
                deployed = f.read().strip()
        except Exception:                                 # noqa: BLE001
            pass
        out["deployed_sha"] = deployed[:12]
        out["new"] = (not deployed) or deployed[:12] != sha[:12]   # differs from live -> a real update
    except Exception as e:                                # noqa: BLE001
        out["error"] = str(e)
    return out

app = Flask(__name__, static_folder=None)

# ── game remote-command relay (reuse the bot's client; serialise access) ──────
_rc = bot.RemoteCommand(bot.RCMD_HOST, bot.RCMD_PORT)
_rc_lock = threading.Lock()
_STATUS = getattr(bot, "STATUS_CODES", {})


def _send_cmd(name, args):
    with _rc_lock:
        return _rc.send(name, *args)


def _tail(path, n):
    """Last n non-empty lines. Reads only the file's last 256KB — activity.log is never trimmed and
    console_mirror.log can be 2MB, and this runs on EVERY ~1s /api/state poll per open tab."""
    try:
        window = 262144
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - window))
            data = f.read().decode("utf-8", errors="replace")
        lines = data.splitlines()
        if size > window and lines:
            lines = lines[1:]                            # drop the first line (likely cut mid-way by the seek)
        return [ln for ln in lines if ln.strip()][-n:]
    except Exception:                                    # noqa: BLE001
        return []


# ── user console filters ("filter messages like this") ───────────────────────
CONSOLE_FILTERS = os.path.join(HERE, "console_filters.json")   # user-added patterns (normalised)


def _norm_console(s):
    """Normalise a console line so 'messages like this' match despite varying numbers:
    drop digit runs (timestamps, netIds, counts) and lowercase."""
    return re.sub(r"\d+", "#", str(s)).strip().lower()


def _load_console_filters():
    try:
        with open(CONSOLE_FILTERS, encoding="utf-8") as f:
            d = json.load(f)
        return [p for p in d if p] if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def _save_console_filters(lst):
    tmp = CONSOLE_FILTERS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(lst, f, indent=1)
    os.replace(tmp, CONSOLE_FILTERS)


# ── console noise filter (ported from the TUI) ────────────────────────────────
_ERR_TOKENS = ("Exception", "NullReference", "Traceback", "stack trace")
_ERR_LOW = ("error", "failed", "fatal", " denied", "could not patch")
NOISE_LABELS = {"remote": "remote-cmd", "weapon": "weapon-mgr", "ai": "AI-units",
                "nostats": "NOSTATS", "blast": "blast", "kinematic": "kinematic-vel",
                "engine": "engine-warn", "steam": "Steam-net"}
_ENGINE_NOISE = ("linear velocity of a kinematic", "boxcollider does not support negative",
                 "the effective box size has been forced", "if you absolutely need to use negative s",
                 "did you use #pragma only_renderers", "if subshaders removal was intentional",
                 "fallback handler could not load library", "particle system is trying to spawn")


def _is_err(line):
    low = line.lower()
    return any(k in line for k in _ERR_TOKENS) or any(k in low for k in _ERR_LOW)


def _classify(line):
    low = line.lower()
    err = _is_err(line)
    if "[serverremotecommands]" in low:
        return "error" if (err or ("response:" in low and "success" not in low)) else "remote"
    if "[weaponmanager]" in low:
        return "error" if err else "weapon"
    if "[aihelo]" in low or "[aiplane]" in low or "[aiground]" in low or "aipilot" in low:
        return "error" if err else "ai"
    if "[nostats]" in low:
        return "error" if err else "nostats"
    if "[blastmanager]" in low or "blast manager" in low:
        return "error" if err else "blast"
    if "[steammanager]" in low:
        return "error" if (err or "unable to communicate with any" in low or "no route" in low) else "steam"
    if any(p in low for p in _ENGINE_NOISE):
        return "engine"
    return "error" if err else "show"


def _console_view(lines, raw):
    if raw:
        return [{"t": ln, "k": "err" if _classify(ln) == "error" else "show"} for ln in lines]
    user = _load_console_filters()
    out, supp, ucount = [], {}, 0
    for ln in lines:
        if user:
            nl = _norm_console(ln)
            if any(p in nl for p in user):       # user "filter messages like this"
                ucount += 1
                continue
        c = _classify(ln)
        if c in ("show", "error"):
            out.append({"t": ln, "k": "err" if c == "error" else "show"})
        else:
            supp[c] = supp.get(c, 0) + 1
    if supp or ucount:
        parts = [f"{supp[k]} {NOISE_LABELS[k]}" for k in NOISE_LABELS if supp.get(k)]
        if ucount:
            parts.append(f"{ucount} custom")
        out.append({"t": f"— filtered  {'  ·  '.join(parts)} —", "k": "sum"})
    return out


# ── command catalog (server aliases + bot/local) for the palette + autocomplete ─
_LOCAL_CMDS = [
    ("say",         "<message>",            "broadcast an [Admin] message to chat",       False, "message"),
    ("nextmap",     "<mission>",            "queue the next mission",                     False, "mission"),
    ("changemap",   "<mission>",            "END the current match + switch to a chosen map NOW", False, "mission"),
    ("endmission",  "",                     "force the current mission to end now",       True,  ""),
    ("leaderboard", "",                     "top pilots by points + skill",               False, ""),
    ("ranks",       "",                     "all saved ranks, best first",                False, ""),
    ("rankpreview", "",                     "post the rank ladder into in-game chat",     False, ""),
    ("grant",       "<player> <points>",    "add / remove rank points (use -N to remove)", False, "pn"),
    ("move",        "<player> <faction>",   "move a player to a team",                    False, "pf"),
    ("join",        "<player> <faction>",   "join a player to a team",                    False, "pf"),
    ("spec",        "<player>",             "move a player to spectator",                 False, "player"),
    ("setrank",     "<player> <rank>",      "set a player's IN-GAME rank (number)",       False, "pn"),
    ("setfunds",    "<player> <amount>",    "set a player's IN-GAME funds",               False, "pn"),
    ("addfunds",    "<player> <amount>",    "add/remove IN-GAME funds (use -N to remove)", False, "pn"),
    ("balance",     "",                     "run a PvP team-balance pass",                False, ""),
]


_HIDDEN_VERBS = {"updateready", "update-ready", "banreload", "banlist-reload", "banclear",
                 "banlist-clear", "clearkicks", "clear-kicked-players"}   # raw ops verbs: hidden from the palette AND rejected by /api/cmd


def _catalog():
    out = []
    for alias, wire, args, desc, danger in getattr(bot, "CENTRE_SERVER_CMDS", []):
        if wire == "send-chat-message":   # drop the raw server 'say' - the local 'say' below
            continue                      # covers it (adds the [Admin] prefix + mirrors to activity)
        if alias in _HIDDEN_VERBS or wire in _HIDDEN_VERBS:   # public ship: don't surface raw operational verbs
            continue
        ac = ("message" if wire == "send-chat-message" else
              "steamid" if wire in ("kick-player", "unkick-player", "banlist-add", "banlist-remove") else "")
        out.append({"name": alias, "wire": wire, "args": args, "desc": desc,
                    "danger": danger, "ac": ac, "group": "server"})
    for name, args, desc, danger, ac in _LOCAL_CMDS:
        out.append({"name": name, "wire": name, "args": args, "desc": desc,
                    "danger": danger, "ac": ac, "group": "bot"})
    return out


def _missions():
    base = (list(getattr(bot, "PVP_MISSIONS", [])) + list(getattr(bot, "BUILTIN_COOP_MISSIONS", []))
            + list(getattr(bot, "ESCALATION_MISSIONS", []))
            + list(getattr(bot, "TERMINAL_CONTROL_MISSIONS", [])))
    # + the bot's live votable universe (enabled custom/uploaded USER missions) from the dashboard,
    # so the Change-map picker and nextmap autocomplete can reach missions the static lists can't know
    try:
        with open(DASHBOARD, encoding="utf-8") as f:
            votable = (json.load(f).get("votemap") or {}).get("votable") or []
        for v in votable:
            n = v.get("name") if isinstance(v, dict) else None
            if n and n not in base:
                base.append(n)
    except Exception:                                    # noqa: BLE001
        pass
    return base


def _resolve_mission(q):
    q = (q or "").strip().lower()
    if not q:
        return None
    ms = _missions()
    for m in ms:
        if m.lower() == q:
            return m
    for m in ms:
        if m.lower().startswith(q):
            return m
    for m in ms:
        if q in m.lower():
            return m
    return None


def _players():
    try:
        with open(DASHBOARD, encoding="utf-8") as f:
            return json.load(f).get("players", [])
    except Exception:                                    # noqa: BLE001
        return []


def _resolve_player(query):
    """name/partial/sid -> sid, using the live roster. Returns (sid, label) or (None, msg)."""
    q = (query or "").strip()
    if not q:
        return None, "no player given"
    ps = _players()
    if q.isdigit():
        for p in ps:
            if str(p.get("sid")) == q:
                return q, p.get("name", q)
        return q, q                                      # trust a raw SteamID
    ql = q.lower()
    hits = [p for p in ps if ql in (p.get("name", "").lower())]
    exact = [p for p in ps if p.get("name", "").lower() == ql]
    if exact:
        hits = exact
    if not hits:
        return None, f"no online player matches '{q}'"
    if len(hits) > 1:
        return None, f"'{q}' matches {len(hits)} players - be more specific"
    return str(hits[0].get("sid")), hits[0].get("name", q)


def _queue_admin(rec):
    rec["ts"] = time.time()
    with open(bot.ADMIN_CMD_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def _read_schedule():
    try:
        with open(SCHEDULE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _write_schedule(items):
    tmp = SCHEDULE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2)
    os.replace(tmp, SCHEDULE_FILE)


def _faction_norm(f):
    f = (f or "").lower()
    if f in ("boscali", "bdf", "bosc", "blue"):
        return "boscali"
    if f in ("primeva", "pala", "prim", "red"):
        return "primeva"
    return None


# ── ranks / leaderboard (read-only from ranks.json) ───────────────────────────
def _read_ranks():
    try:
        with open(RANK_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:                                    # noqa: BLE001
        return {}


def _rank_tier(pts):
    RANKS = getattr(bot, "RANKS", [])
    try:
        _, name, abbr, color = RANKS[bot.rank_index_for(pts)]
        return abbr, color
    except Exception:                                    # noqa: BLE001
        return "", "#aaa"


def _leaderboard():
    d = _read_ranks()
    # Points board: when cross-server sharing is ON, use the COMBINED board the bot writes into the
    # dashboard (authoritative across the host's servers) so a server with few LOCAL players still
    # shows everyone's carried-over ranks -- fixes the "leaderboard had no ranks" case on a fresh
    # server. Falls back to local ranks.json when sharing is off or the board isn't ready. Skill
    # stays per-server (skill = local sorties).
    pboard = None
    try:
        with open(DASHBOARD, encoding="utf-8") as f:
            sr = (json.load(f) or {}).get("shared_ranks", {}) or {}
        if sr.get("enabled") and sr.get("board"):
            pboard = []
            for r in sr["board"][:8]:
                pv = r.get("points", 0) or 0
                ab, co = _rank_tier(pv)
                pboard.append({"name": r.get("name", ""), "pts": round(pv, 1), "abbr": ab, "color": co})
    except Exception:                                    # noqa: BLE001
        pboard = None
    if pboard is None:
        pts = sorted(((s, r) for s, r in d.items() if r.get("points", 0) > 0),
                     key=lambda kv: -kv[1].get("points", 0))[:8]
        pboard = []
        for sid, r in pts:
            ab, co = _rank_tier(r.get("points", 0))
            pboard.append({"name": r.get("name", sid), "pts": round(r.get("points", 0), 1),
                           "abbr": ab, "color": co})
    ml = getattr(bot, "SKILL_MIN_LIVES", 5)
    sk = [(r.get("name", s), r.get("skillPoints", 0.0) / max(1, r.get("lives", 1)), r.get("lives", 0))
          for s, r in d.items() if r.get("lives", 0) >= ml]
    sk.sort(key=lambda t: -t[1])
    sboard = [{"name": n, "rating": round(v, 1)} for n, v, _ in sk[:8]]
    return {"points": pboard, "skill": sboard}


def _ranks_table():
    d = _read_ranks()
    rows = sorted(d.items(), key=lambda kv: -kv[1].get("points", 0))
    out = []
    for sid, r in rows:
        ab, co = _rank_tier(r.get("points", 0))
        out.append({"name": r.get("name", sid), "pts": round(r.get("points", 0), 1),
                    "abbr": ab, "color": co, "wins": r.get("wins", 0), "losses": r.get("losses", 0)})
    return out


# ── Pterodactyl client API (Cloudflare-aware) ─────────────────────────────────
_PT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_pt = {"key": None, "base": None, "server": None, "err": None, "loaded": 0.0}
_pt_lock = threading.Lock()


def _pt_load():
    with _pt_lock:
        if time.time() - _pt["loaded"] < 30 and _pt["server"]:
            return _pt
        _pt["loaded"] = time.time()
        try:
            _pt["key"] = open(os.path.join(HERE, "apiKey.txt")).read().strip() or None
        except Exception:                                # noqa: BLE001
            _pt["key"] = None
        cfg = _tail(os.path.join(HERE, "panel.txt"), 2)
        raw = (cfg[0].strip() if cfg else "") or ""
        want = cfg[1].strip() if len(cfg) > 1 else None
        if "/server/" in raw and not want:               # accept the full browser URL form
            want = raw.partition("/server/")[2].split("/")[0] or None
        _pt["base"] = bot.normalize_panel_url(raw) or None
        _pt["err"] = None
        if not _pt["key"]:
            _pt["err"] = "no apiKey.txt"
        elif not _pt["base"]:
            _pt["err"] = "no panel.txt"
        elif want:
            _pt["server"] = want
        else:
            try:
                d = _pt_call("GET", "/api/client", None)
                s = d.get("data", [])
                _pt["server"] = s[0]["attributes"]["identifier"] if s else None
                if not _pt["server"]:
                    _pt["err"] = "API key sees no servers"
            except Exception as e:                       # noqa: BLE001
                _pt["err"] = f"discover failed: {e}"
        return _pt


def _pt_call(method, path, body):
    ctx = ssl.create_default_context()
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(_pt["base"] + path, data=data, method=method, headers={
        "Authorization": "Bearer " + _pt["key"], "Accept": "application/json",
        "Content-Type": "application/json", "User-Agent": _PT_UA})
    with urllib.request.urlopen(req, context=ctx, timeout=12) as r:
        ctype = r.headers.get("Content-Type", "")
        raw = r.read()
    return bot._pt_friendly_json(raw, ctype)


def _pt_power(signal):
    _pt_load()
    if not _pt.get("server"):
        return False, _pt.get("err") or "pterodactyl not configured"
    if signal not in ("start", "stop", "restart", "kill"):
        return False, "bad signal"
    try:
        _pt_call("POST", f"/api/client/servers/{_pt['server']}/power", {"signal": signal})
        return True, f"sent {signal}"
    except Exception as e:                               # noqa: BLE001
        return False, str(e)


def _pt_resources():
    _pt_load()
    if not _pt.get("server"):
        return {"configured": False, "err": _pt.get("err")}
    try:
        a = _pt_call("GET", f"/api/client/servers/{_pt['server']}/resources", None).get("attributes", {})
        u = a.get("resources", {})
        return {"configured": True, "state": a.get("current_state"),
                "cpu": round(u.get("cpu_absolute", 0), 1),
                "mem_mb": round(u.get("memory_bytes", 0) / 1048576),
                "uptime_s": round(u.get("uptime", 0) / 1000)}
    except Exception as e:                               # noqa: BLE001
        return {"configured": True, "err": str(e)}


# ── local (own-PC) power: start/stop the dedicated server process ───────────────
_local_proc = {"p": None}


def _is_local_power():
    return (((getattr(bot, "_TK_CFG", {}) or {}).get("server", {}) or {}).get("power") == "local")


def _local_game_dir():
    sv = (getattr(bot, "_TK_CFG", {}) or {}).get("server", {}) or {}
    return sv.get("game_dir") or sv.get("local_game_dir") or ""


def _server_alive():
    import subprocess
    import sys
    try:
        if sys.platform.startswith("win"):
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq NuclearOptionServer.exe"],
                                 capture_output=True, text=True, timeout=8).stdout
            return "NuclearOptionServer.exe" in out
        return subprocess.run(["pgrep", "-f", "NuclearOptionServer"], capture_output=True, timeout=8).returncode == 0
    except Exception:                                    # noqa: BLE001
        p = _local_proc["p"]
        return bool(p and p.poll() is None)


def _local_power(signal):
    import subprocess
    import sys
    if signal not in ("start", "stop", "restart", "kill"):   # unlike _pt_power this had NO guard; an unknown signal skipped the kill branch and launched a 2nd server
        return False, "bad signal"
    gd = _local_game_dir()
    if not gd or not os.path.isdir(gd):
        return False, "no local game dir configured"
    if signal in ("stop", "kill", "restart"):
        try:
            if sys.platform.startswith("win"):
                subprocess.run(["taskkill", "/F", "/IM", "NuclearOptionServer.exe"], capture_output=True, timeout=10)
            else:
                subprocess.run(["pkill", "-f", "NuclearOptionServer"], capture_output=True, timeout=10)
        except Exception as e:                           # noqa: BLE001
            if signal != "restart":
                return False, str(e)
        if signal != "restart":
            return True, "server stopped"
        time.sleep(2)
    starter = os.path.join(gd, "StartServer.bat" if sys.platform.startswith("win") else "start_server.sh")
    try:
        if os.path.exists(starter):
            _local_proc["p"] = (subprocess.Popen([starter], cwd=gd, creationflags=0x00000010)
                                if sys.platform.startswith("win") else
                                subprocess.Popen(["bash", starter], cwd=gd))
        else:
            exe = ""
            for n in ("NuclearOptionServer.exe", "NuclearOptionServer.x86_64"):
                if os.path.exists(os.path.join(gd, n)):
                    exe = os.path.join(gd, n)
                    break
            if not exe:
                return False, "server executable not found in " + gd
            _local_proc["p"] = subprocess.Popen([exe, "-batchmode", "-nographics"], cwd=gd)
        return True, "server started"
    except Exception as e:                               # noqa: BLE001
        return False, str(e)


def _local_resources():
    return {"configured": True, "local": True, "state": "running" if _server_alive() else "offline"}


# ── Toolkit version + GitHub updater (github/productization fork's installer/updater.py) ──────
# We READ deployed_toolkit.json + the toolkit config and CALL the fork's updater (never edit installer/).
# Inert in a dev checkout (no deployed_toolkit.json / no ~/.nuke-option-toolkit/config.json -> "not configured").
TOOLKIT_META = os.path.join(HERE, "deployed_toolkit.json")


def _toolkit_user_dir():
    """Folder-safe config dir, matching installer/updater.py: env pin > this folder's
    .nost-data > legacy shared dir. The legacy-first fallback silently read the WRONG
    config (wrong channel) on per-folder installs when launched without the wrapper."""
    env = os.environ.get("NOST_DATA_DIR")
    if env:
        return env
    local = os.path.join(HERE, ".nost-data")
    if os.path.isdir(local):
        return local
    return os.path.join(os.path.expanduser("~"), ".nuke-option-toolkit")


_USER_DIR    = _toolkit_user_dir()
_TOOLKIT_CFG = os.path.join(_USER_DIR, "config.json")
_toolkit_chk = {"ts": 0.0, "data": None}   # cached result of the last (network) update check


def _json_version(path):
    try:
        with open(path, encoding="utf-8") as f:
            return str((json.load(f) or {}).get("version", "") or "")
    except (OSError, ValueError):
        return ""


def _toolkit_cfg():
    try:
        with open(_TOOLKIT_CFG, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _updater_mod():
    import importlib
    import sys as _sys
    idir = os.path.join(HERE, "installer")
    if idir not in _sys.path:
        _sys.path.insert(0, idir)
    import updater
    # reload each call (it's on-demand only): a self-updated installer/updater.py must take
    # effect without restarting the web CC
    return importlib.reload(updater)


# ── routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(HERE, "webcc.html")


@app.route("/api/toolkit")
def api_toolkit():
    """Fast/local: installed toolkit + plugin versions, the configured channel, and the last cached check."""
    upd = (_toolkit_cfg().get("update") or {})
    return jsonify({
        "toolkit_version": _json_version(TOOLKIT_META) or None,
        "plugin_version":  _json_version(DEPLOYED_META) or None,
        "channel":         upd.get("channel") or "stable",
        "configured":      bool((upd.get("github_repo") or "").strip()),
        "check":           _toolkit_chk["data"],
        "checked_age":     (round(time.time() - _toolkit_chk["ts"], 1) if _toolkit_chk["ts"] else None),
    })


@app.route("/api/toolkit/check", methods=["POST"])
def api_toolkit_check():
    """On-demand: ask GitHub (via the fork's updater.check) whether a newer release exists on the channel."""
    upd = (_toolkit_cfg().get("update") or {})
    try:
        mod = _updater_mod()
        comps = getattr(mod, "ALL_COMPONENTS", ("plugin", "bot", "webcc", "installer"))
        info = mod.check(comps, verbose=False)           # ALL components — a web-CC-only update must show
    except Exception as e:                               # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)})
    if not info:                                         # no repo configured or GitHub unreachable
        d = {"configured": bool((upd.get("github_repo") or "").strip()),
             "installed": _json_version(TOOLKIT_META) or None, "latest": None, "newer": None,
             "channel": upd.get("channel") or "stable", "note": "updater not configured or GitHub unreachable"}
    else:
        rel = info.get("release") or {}
        d = {"configured": True, "installed": info.get("installed") or None, "latest": info.get("latest") or None,
             "newer": bool(info.get("newer")), "channel": info.get("channel"), "repo": info.get("repo"),
             "url": rel.get("html_url"), "components": info.get("components")}
    _toolkit_chk.update(ts=time.time(), data=d)
    return jsonify({"ok": True, **d})


@app.route("/api/toolkit/channel", methods=["POST"])
def api_toolkit_channel():
    """Set the update channel (stable/nightly) in ~/.nuke-option-toolkit/config.json."""
    ch = (request.get_json(silent=True) or {}).get("channel")
    if ch not in ("stable", "nightly"):
        return jsonify({"ok": False, "error": "channel must be 'stable' or 'nightly'"})
    cfg = _toolkit_cfg()
    cfg.setdefault("update", {})["channel"] = ch
    try:
        os.makedirs(_USER_DIR, exist_ok=True)
        with open(_TOOLKIT_CFG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except OSError as e:
        return jsonify({"ok": False, "error": str(e)})
    _toolkit_chk["data"] = None                          # channel changed -> stale check
    return jsonify({"ok": True, "channel": ch})


@app.route("/api/toolkit/update", methods=["POST"])
def api_toolkit_update():
    """Download + VERIFY + INSTALL the latest. Bot / web CC / installer are applied immediately
    (every replaced file is backed up; a bot / web-CC restart loads them). The PLUGIN is only
    STAGED — it deploys via the normal Schedule / --deploy-plugin flow, so clicking Update can
    never surprise-restart the match."""
    import subprocess
    import sys as _sys
    upy = os.path.join(HERE, "installer", "updater.py")
    if not os.path.exists(upy):
        return jsonify({"ok": False, "error": "installer/updater.py not present"})
    try:
        env = dict(os.environ)
        env.setdefault("NOST_DATA_DIR", _USER_DIR)       # same config the web CC itself resolved
        r = subprocess.run([_sys.executable, upy, "update", "--component", "all"],
                           cwd=HERE, capture_output=True, text=True, timeout=300, env=env)
        out = r.stdout or ""
        summary = out.split("================ UPDATE SUMMARY ================")[-1].strip() \
            if "UPDATE SUMMARY" in out else None
        return jsonify({"ok": r.returncode == 0,
                        "applied": "APPLIED" in out,      # bot/webcc/installer installed now
                        "staged": "STAGED" in out,        # plugin downloaded, awaiting its deploy step
                        "summary": summary,
                        "output": out[-4000:], "error": ((r.stderr or "").strip()[-1000:] or None)})
    except Exception as e:                               # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/state")
def api_state():
    try:
        with open(DASHBOARD, encoding="utf-8") as f:
            st = json.load(f)
    except Exception:                                    # noqa: BLE001
        st = {}
    raw = request.args.get("raw") == "1"
    st["activity"] = _tail(ACTIVITY, 80)
    st["console"] = _console_view(_tail(CONSOLE, 400), raw)
    m = (st.get("mission") or "").lower()
    # mission -> atlas terrain. Every stock Large Operation runs on Heartland EXCEPT Terminal
    # Control (Ignus Archipelago); Carrier Duel is on Ignus; scenario 13. Reprisal is on
    # Heartland (wiki + owner-confirmed 2026-07-02). Ignus keywords are checked FIRST so
    # "Terminal Control ..." never falls through to a heartland keyword. Unknown missions stay
    # None (no map is better than the wrong map).
    if any(k in m for k in ("ignus", "terminal", "carrier duel")):
        st["map_key"] = "ignus"
    elif any(k in m for k in ("heartland", "escalation", "altercation", "confrontation",
                              "domination", "breakout", "reprisal")):
        st["map_key"] = "heartland"
    else:
        st["map_key"] = None
    st["server_age"] = round(time.time() - st.get("ts", 0), 1) if st.get("ts") else None
    st["deploy"] = _deploy_status()
    st["toolkit_version"] = _json_version(TOOLKIT_META) or None   # header chip; None in a dev checkout
    return jsonify(st)


@app.route("/api/missionpool", methods=["POST"])
def api_missionpool():
    """webcc Mission Pool modal: toggle a mission in/out of the votemap pool (routed to the bot)."""
    b = request.get_json(force=True, silent=True) or {}
    mission = str(b.get("mission", "")).strip()
    if not mission:
        return jsonify({"ok": False, "error": "no mission"})
    _queue_admin({"action": "missionpool", "mission": mission, "on": bool(b.get("on", True))})
    return jsonify({"ok": True})


_SID_RE = re.compile(r"^\d{6,20}$")


def _finite(s):
    """Parse a user-supplied number; None unless finite ('nan'/'inf' pass float() but would
    corrupt ranks/funds downstream)."""
    try:
        v = float(s)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


@app.route("/api/reports/ban", methods=["POST"])
def api_reports_ban():
    """webcc Reports tab: ban (default) or unban a SteamID (routed to the bot -> plugin)."""
    b = request.get_json(force=True, silent=True) or {}
    sid = str(b.get("sid", "")).strip()
    if not _SID_RE.match(sid):
        return jsonify({"ok": False, "error": "bad steamid"})
    action = "unban_steamid" if b.get("unban") else "ban_steamid"
    _queue_admin({"action": action, "sid": sid})
    return jsonify({"ok": True, "banned": action == "ban_steamid"})


@app.route("/api/reports/clear", methods=["POST"])
def api_reports_clear():
    """webcc Reports tab: clear ONE report (by unique seq) or ALL. Routed to the bot, the single
    writer of plugin_reports.json, so cleared reports don't reappear on the next /api/state push."""
    b = request.get_json(force=True, silent=True) or {}
    if b.get("all"):
        _queue_admin({"action": "clear_reports"})
        return jsonify({"ok": True})
    try:
        seq = int(b.get("seq"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad seq"})
    if seq <= 0:
        return jsonify({"ok": False, "error": "bad seq"})
    _queue_admin({"action": "clear_report", "seq": seq})
    return jsonify({"ok": True})


@app.route("/api/serverconfig/refresh", methods=["POST"])
def api_serverconfig_refresh():
    """webcc Server Settings tab: ask the bot to re-read DedicatedServerConfig.json (data arrives via /api/state)."""
    _queue_admin({"action": "dumpserverconfig"})
    return jsonify({"ok": True})


@app.route("/api/missionaudit", methods=["POST"])
def api_missionaudit():
    """webcc Mission Pool: ask the bot to re-scan official/custom missions + integrity (data via /api/state)."""
    _queue_admin({"action": "missionaudit"})
    return jsonify({"ok": True})


@app.route("/api/mission/toggle", methods=["POST"])
def api_mission_toggle():
    """webcc Mission Pool: enable/disable a mission in the live MissionRotation (routed to the bot)."""
    b = request.get_json(force=True, silent=True) or {}
    name = str(b.get("name", "")).strip()
    if not name:
        return jsonify({"ok": False, "error": "no mission name"})
    _queue_admin({"action": "missiontoggle", "group": str(b.get("group", "User")), "name": name, "on": bool(b.get("on"))})
    return jsonify({"ok": True})


@app.route("/api/mission/workshop", methods=["POST"])
def api_mission_workshop():
    """webcc Mission Pool: add a Steam Workshop mission by published-file id (auto-downloads on restart)."""
    b = request.get_json(force=True, silent=True) or {}
    wid = str(b.get("id", "")).strip()
    if not re.fullmatch(r"\d{5,20}", wid):
        return jsonify({"ok": False, "error": "workshop id must be numeric"})
    _queue_admin({"action": "missionworkshop", "id": wid})
    return jsonify({"ok": True})


@app.route("/api/mission/upload", methods=["POST"])
def api_mission_upload():
    """webcc Mission Pool: upload a custom mission folder (staged locally, then SFTP'd by the bot, added OFF)."""
    b = request.get_json(force=True, silent=True) or {}
    name = str(b.get("name", "")).strip()
    files = b.get("files") or []
    if not name or not isinstance(files, list) or not files:
        return jsonify({"ok": False, "error": "need a mission name + at least one file"})
    if len(files) > 30:
        return jsonify({"ok": False, "error": "too many files (max 30)"})
    try:
        sdir = os.path.join(HERE, "mission_uploads")
        os.makedirs(sdir, exist_ok=True)
        sid = str(int(time.time() * 1000))
        with open(os.path.join(sdir, sid + ".json"), "w", encoding="utf-8") as f:
            json.dump({"name": name, "files": files}, f)
    except OSError as e:
        return jsonify({"ok": False, "error": f"stage failed: {e}"})
    _queue_admin({"action": "missionupload", "staging": os.path.join("mission_uploads", sid + ".json")})
    return jsonify({"ok": True})


_VOTEMAP_KEYS = {
    "enabled", "coop_count", "pvp_count", "coop_mode", "pvp_mode", "include_pvp", "include_custom",
    "coop_weights", "pvp_weights", "guaranteed", "avoid_recent",
    "force_pvp_enabled", "force_pvp_players", "force_pvp_coop", "force_pvp_pvp",
    "ballot_size", "mode",                       # legacy aliases (bot maps them); harmless to keep
}


@app.route("/api/votemap", methods=["POST"])
def api_votemap():
    """webcc Votemap settings: set one vote-pool config key. The bot is the sole validator/writer; the
    weight keys carry a {name: number} object as their value."""
    b = request.get_json(force=True, silent=True) or {}
    key = str(b.get("key", "")).strip()
    if key not in _VOTEMAP_KEYS:
        return jsonify({"ok": False, "error": "unknown key"})
    _queue_admin({"action": "setvotemap", "key": key, "value": b.get("value")})
    return jsonify({"ok": True})


@app.route("/api/banaudit", methods=["POST"])
def api_banaudit():
    """webcc Moderation 'Banned' tab: ask the bot to re-read plugin_bans.txt (data via /api/state)."""
    _queue_admin({"action": "banaudit"})
    return jsonify({"ok": True})


@app.route("/api/logban", methods=["POST"])
def api_logban():
    """webcc Reports 'Log ban' button: record a ban in the persistent ban-log (repeat-offender tracking)."""
    b = request.get_json(force=True, silent=True) or {}
    sid = str(b.get("sid", "")).strip()
    if not re.fullmatch(r"\d{6,20}", sid):
        return jsonify({"ok": False, "error": "bad steamid"})
    _queue_admin({"action": "logban", "sid": sid, "name": str(b.get("name", ""))[:64], "reason": str(b.get("reason", ""))[:200]})
    return jsonify({"ok": True})


@app.route("/api/banlog/remove", methods=["POST"])
def api_banlog_remove():
    """webcc Ban log 🗑 button: delete one player's logged-ban history. Separate from clearing reports."""
    b = request.get_json(force=True, silent=True) or {}
    sid = str(b.get("sid", "")).strip()
    if not re.fullmatch(r"\d{6,20}", sid):
        return jsonify({"ok": False, "error": "bad steamid"})
    _queue_admin({"action": "rmbanlog", "sid": sid, "name": str(b.get("name", ""))[:64]})
    return jsonify({"ok": True})


@app.route("/api/serverconfig", methods=["POST"])
def api_serverconfig_set():
    """webcc Server Settings tab: edit one config field (routed to the bot -> SFTP + gpanel mirror).
    Rejects unknown fields and empty numeric values HERE so obvious mistakes fail fast; a true 'saved'
    is only ever reported by the bot after its verify-after-write (queued != applied)."""
    b = request.get_json(force=True, silent=True) or {}
    key = str(b.get("key", "")).strip()
    if not key:
        return jsonify({"ok": False, "error": "no key"})
    srv_map = getattr(bot, "_SRVCFG_MAP", None)
    if isinstance(srv_map, dict) and srv_map and key not in srv_map:
        return jsonify({"ok": False, "error": f"unknown field {key}"})
    if isinstance(srv_map, dict) and key in srv_map:
        typ = srv_map[key][1]
        if typ in ("int", "float") and str(b.get("value", "")).strip() == "":
            return jsonify({"ok": False, "error": "enter a value"})
    _queue_admin({"action": "setserverconfig", "key": key, "value": b.get("value")})
    return jsonify({"ok": True, "queued": True})


@app.route("/api/serverconfig/restart", methods=["POST"])
def api_serverconfig_restart():
    """webcc Server Settings tab: restart the game server to apply restart-only config changes."""
    try:
        ok, msg = _pt_power("restart")                     # _pt_power returns (ok, msg) -> surface the real result
        return jsonify({"ok": bool(ok), "error": None if ok else msg})
    except Exception as e:                                  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/sysmessages", methods=["POST"])
def api_sysmessages():
    """webcc Messages tab: edit a built-in automated message (enable / text / interval / delay)."""
    b = request.get_json(force=True, silent=True) or {}
    key = str(b.get("key", "")).strip()
    if not key:
        return jsonify({"ok": False, "error": "no key"})
    fields = {}
    if "enabled" in b:
        fields["enabled"] = bool(b.get("enabled"))
    if "text" in b:
        fields["text"] = str(b.get("text", ""))[:240]
    for nk in ("interval", "delay"):
        if nk in b:
            try:
                fields[nk] = float(b.get(nk))
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": f"{nk} must be a number"})
    _queue_admin({"action": "sysmsg", "key": key, "fields": fields})
    return jsonify({"ok": True})


@app.route("/api/helpcfg", methods=["POST"])
def api_helpcfg():
    """webcc Help editor: show/hide a command in the dynamic !help list. The bot owns help_config.json;
    command TEXT edits reuse /api/sysmessages (key 'help_<cmd>')."""
    b = request.get_json(force=True, silent=True) or {}
    cmd = str(b.get("cmd", "")).strip()
    if not re.fullmatch(r"[a-z]{2,16}", cmd):
        return jsonify({"ok": False, "error": "bad cmd"})
    _queue_admin({"action": "helpcfg", "cmd": cmd, "on": bool(b.get("on", True))})
    return jsonify({"ok": True})


@app.route("/api/rankladder", methods=["POST"])
def api_rankladder():
    """webcc Ranks modal: replace the whole rank ladder + rank-up template. The bot owns
    rank_ladder.json and is the SOLE validator; this does cheap shape checks and queues."""
    b = request.get_json(force=True, silent=True) or {}
    if str(b.get("op", "save")).strip().lower() != "save":
        return jsonify({"ok": False, "error": "bad op"})
    ranks = b.get("ranks")
    if not isinstance(ranks, list) or not ranks:
        return jsonify({"ok": False, "error": "need at least one rank"})
    if len(ranks) > 40:
        return jsonify({"ok": False, "error": "too many ranks (max 40)"})
    clean = []
    for r in ranks:
        if not isinstance(r, dict):
            return jsonify({"ok": False, "error": "bad rank row"})
        try:
            th = int(float(r.get("threshold", 0)))
        except (TypeError, ValueError, OverflowError):
            return jsonify({"ok": False, "error": "threshold must be a number"})
        clean.append({"threshold": th,
                      "name": str(r.get("name", ""))[:40],
                      "abbr": str(r.get("abbr", ""))[:12],
                      "color": str(r.get("color", ""))[:7]})
    tmpl = str(b.get("rankup_template", ""))[:240]
    _queue_admin({"action": "rankladder", "payload": {"ranks": clean, "rankup_template": tmpl}})
    return jsonify({"ok": True})


@app.route("/api/sharedranks", methods=["POST"])
def api_sharedranks():
    """webcc Shared Ranks card: enable/disable cross-server rank sharing + set the shared dir.
    The bot owns shared_ranks.json and does the publish/read; this just queues."""
    b = request.get_json(force=True, silent=True) or {}
    enabled = bool(b.get("enabled"))
    dir_ = str(b.get("dir", "") or "").strip()[:500]
    if enabled and not dir_:
        return jsonify({"ok": False, "error": "enter the shared folder path"})
    _queue_admin({"action": "sharedranks", "enabled": enabled, "dir": dir_})
    return jsonify({"ok": True})


@app.route("/api/sharedranks/validate", methods=["POST"])
def api_sharedranks_validate():
    """Advisory server-side path check for the Shared Ranks card. NOTE: cc_web writability is
    not the bot's writability (separate processes) - the bot publisher's success is the real signal."""
    b = request.get_json(force=True, silent=True) or {}
    dir_ = str(b.get("dir", "") or "").strip()
    if not dir_:
        return jsonify({"ok": False, "error": "no path"})
    import glob as _glob
    exists = os.path.isdir(dir_)
    writable = bool(exists and os.access(dir_, os.W_OK))
    network = dir_.startswith("\\\\") or dir_.startswith("//")
    peers = len(_glob.glob(os.path.join(dir_, "rankshare_*.json"))) if exists else 0
    return jsonify({"ok": True, "exists": exists, "writable": writable,
                    "network": bool(network), "peer_files": peers})


_MSG_TRIGGERS = ("interval", "clock", "match_start", "match_end")
_MSG_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")
_MSG_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


@app.route("/api/messages", methods=["POST"])
def api_messages():
    """webcc Messages modal: CRUD automated server messages (routed to the bot, which owns the file
    and re-validates). op = add | update | delete | toggle."""
    b = request.get_json(force=True, silent=True) or {}
    op = str(b.get("op", "")).strip().lower()
    if op not in ("add", "update", "delete", "toggle"):
        return jsonify({"ok": False, "error": "bad op"})
    if op in ("delete", "toggle"):
        mid = str(b.get("id", "")).strip()
        if not mid:
            return jsonify({"ok": False, "error": "no id"})
        rec = {"action": "servermsg", "op": op, "msg": {"id": mid}}
        if op == "toggle":
            rec["msg"]["on"] = bool(b.get("on", True))
        _queue_admin(rec)
        return jsonify({"ok": True})
    # add / update -> validate the message fields
    text = str(b.get("text", "")).strip()
    if op == "add" and not text:
        return jsonify({"ok": False, "error": "message text is required"})
    trig = str(b.get("trigger", "interval")).strip()
    if trig not in _MSG_TRIGGERS:
        return jsonify({"ok": False, "error": "trigger must be one of: " + ", ".join(_MSG_TRIGGERS)})
    msg = {"text": text[:240], "trigger": trig}
    try:
        msg["interval_min"] = max(1, min(1440, int(float(b.get("interval_min", 30)))))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "interval must be a whole number of minutes"})
    at = str(b.get("at", "")).strip()
    if trig == "clock" and not _MSG_HHMM_RE.match(at):
        return jsonify({"ok": False, "error": "time must be HH:MM (24-hour)"})
    msg["at"] = at
    color = str(b.get("color", "")).strip()
    if color and not _MSG_HEX_RE.match(color):
        return jsonify({"ok": False, "error": "colour must be a #RRGGBB hex value"})
    msg["color"] = color
    if "enabled" in b:
        msg["enabled"] = bool(b.get("enabled"))
    if op == "update":
        mid = str(b.get("id", "")).strip()
        if not mid:
            return jsonify({"ok": False, "error": "no id"})
        msg["id"] = mid
    _queue_admin({"action": "servermsg", "op": op, "msg": msg})
    return jsonify({"ok": True})


@app.route("/api/settings")
def api_settings():
    """Merge the static catalogue with LIVE values (plugin cfg from the dashboard; bot overrides
    from bot_overrides.json) so the settings menu shows real current values."""
    cat = _load_catalogue()
    try:
        with open(DASHBOARD, encoding="utf-8") as f:
            live = (json.load(f) or {}).get("plugin_cfg") or {}
    except Exception:                                    # noqa: BLE001
        live = {}
    try:
        with open(BOT_OVERRIDES, encoding="utf-8") as f:
            bov = json.load(f) or {}
    except (OSError, ValueError):
        bov = {}
    have_live = bool(live)
    # Public-listing opt-in is AUTHORITATIVE in global_optin.json (the bot persists the operator's choice
    # there the moment they toggle it). The plugin's Global.ListServer bind defaults FALSE and only reports
    # via dumpcfg when a player is online, so on an empty/just-restarted server the menu would fall back to
    # the catalogue default and "keep turning off". Always overlay these two from the opt-in file. (fixes the recurring revert)
    try:
        gc = bot._global_cfg()
    except Exception:                                    # noqa: BLE001
        gc = None
    out, groups = [], []
    for s in cat:
        key = s.get("key", "")
        owner = s.get("owner", "plugin")
        val = s.get("default")
        if gc is not None and key == "Global.ListServer":
            val = bool(gc["list"])
        elif gc is not None and key == "Global.Region":
            val = gc["region"]
        elif gc is not None and key == "Global.Gamemonitoring":
            val = gc.get("gm", "")
        elif owner == "plugin" and key in live:
            val = live[key]
        elif owner == "bot":
            short = key.split(".")[-1].split(":")[-1]
            if short in bov:
                val = bov[short]
        row = dict(s)
        row["value"] = val
        out.append(row)
        g = s.get("group", "Other")
        if g not in groups:
            groups.append(g)
    if not have_live:                                    # nudge the bot to ask the plugin for a fresh dump (throttled)
        global _last_dump_nudge
        if time.time() - _last_dump_nudge > 10:
            _last_dump_nudge = time.time()
            try:
                _queue_admin({"action": "dumpcfg"})
            except Exception:                            # noqa: BLE001
                pass
    simple = [s["key"] for s in out if s.get("commonlyChanged")]
    return jsonify({"settings": out, "groups": groups, "simpleKeys": simple, "live": have_live})


@app.route("/api/settings", methods=["POST"])
def api_settings_set():
    """Validate a single setting change against the catalogue, then queue it to the bot."""
    b = request.get_json(force=True, silent=True) or {}
    key = str(b.get("key", "")).strip()
    if not key:
        return jsonify({"ok": False, "error": "no key"})
    meta = {s.get("key"): s for s in _load_catalogue()}.get(key)
    if not meta:
        return jsonify({"ok": False, "error": f"unknown setting {key}"})
    owner = meta.get("owner", "plugin")
    typ = meta.get("type", "string")
    val = b.get("value")
    if typ == "toggle":
        sval = "true" if (val is True or str(val).lower() in ("1", "true", "on", "yes")) else "false"
    elif typ in ("int", "float"):
        try:
            num = float(val)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "must be a number"})
        if num != num or num in (float("inf"), float("-inf")):   # reject NaN / Infinity (else int() 500s)
            return jsonify({"ok": False, "error": "must be a finite number"})
        try:
            if meta.get("min") not in (None, ""):
                num = max(num, float(meta["min"]))
            if meta.get("max") not in (None, ""):
                num = min(num, float(meta["max"]))
        except (TypeError, ValueError):
            pass
        sval = str(int(num) if typ == "int" else num)
    elif typ == "enum":
        opts = [str(o) for o in (meta.get("options") or [])]
        if str(val) not in opts:
            return jsonify({"ok": False, "error": "must be one of: " + ", ".join(opts)})
        sval = str(val)
    else:
        sval = str(val)
    _queue_admin({"action": "setcfg", "key": key, "value": sval, "owner": owner})
    return jsonify({"ok": True, "queued": sval, "owner": owner,
                    "needs_restart": meta.get("live") == "restart"})


@app.route("/api/console-filter", methods=["GET", "POST"])
def api_console_filter():
    """The webcc's 'filter messages like this' list. POST {action:add, pattern:<a console line>}
    normalises the line (digits -> #) and adds it; lines matching any pattern are hidden."""
    if request.method == "GET":
        return jsonify({"filters": _load_console_filters()})
    b = request.get_json(force=True, silent=True) or {}
    action = b.get("action", "add")
    lst = _load_console_filters()
    if action == "add":
        pat = _norm_console(b.get("pattern", ""))
        if pat and pat not in lst:
            lst.append(pat)
    elif action == "remove":
        pat = str(b.get("pattern", "")).strip().lower()
        lst = [p for p in lst if p != pat]
    elif action == "clear":
        lst = []
    _save_console_filters(lst)
    return jsonify({"ok": True, "filters": lst})


@app.route("/api/commands")
def api_commands():
    return jsonify({"commands": _catalog(), "missions": _missions(),
                    "factions": ["boscali", "primeva"]})


@app.route("/api/map")
def api_map():
    key = request.args.get("key", "")
    d = _ATLAS.get(key)
    if not d:
        return jsonify({"error": "no atlas"}), 404
    has_img = os.path.exists(os.path.join(HERE, key + "_map.png"))
    out = {k: d[k] for k in ("name", "cols", "rows", "x0", "x1", "z0", "z1",
                             "xmin", "cell", "znorth", "bases")}
    out["gcols"] = d.get("gcols", round((d["x1"] - d["x0"]) / d["cell"]) + 6)
    out["img"] = key + "_map.png" if has_img else None
    return jsonify(out)


@app.route("/api/mapimg")
def api_mapimg():
    key = request.args.get("key", "")
    fn = key + "_map.png"
    if key in _ATLAS and os.path.exists(os.path.join(HERE, fn)):
        return send_from_directory(HERE, fn, mimetype="image/png")
    return ("", 404)


@app.route("/api/sharedleaderboard")
def api_sharedleaderboard():
    """Full COMBINED cross-server board (ALL players) for the webcc Leaderboard 'Shared' column.
    Reads the shared dir from the dashboard (authoritative — cc_web's in-process bot copy can be stale
    because sharing may have been toggled AFTER cc_web started) and aggregates every rankshare_*.json.
    Read-only; tolerant of a peer file mid-write."""
    import glob
    out = {"enabled": False, "rows": [], "peers": 0, "server_id": None}
    try:
        with open(DASHBOARD, encoding="utf-8") as f:
            sr = (json.load(f) or {}).get("shared_ranks", {}) or {}
    except Exception:                                    # noqa: BLE001
        sr = {}
    out["enabled"] = bool(sr.get("enabled"))
    out["server_id"] = sr.get("server_id")
    sdir = sr.get("dir") or ""
    if not (out["enabled"] and sdir and os.path.isdir(sdir)):
        return jsonify(out)
    agg = {}
    try:
        files = glob.glob(os.path.join(sdir, "rankshare_*.json"))
        out["peers"] = len(files)
        for path in files:
            try:
                with open(path, encoding="utf-8") as f:
                    d = json.load(f)
            except Exception:                            # noqa: BLE001 - tolerate a file mid-write
                continue
            ranks = d.get("ranks", {}) if isinstance(d, dict) else {}
            for psid, rec in (ranks.items() if isinstance(ranks, dict) else []):
                if not isinstance(rec, dict):
                    continue
                a = agg.setdefault(psid, {"name": "", "points": 0.0, "wins": 0, "losses": 0})
                try:
                    a["points"] += float(rec.get("points", 0) or 0)
                    a["wins"] += int(rec.get("wins", 0) or 0)
                    a["losses"] += int(rec.get("losses", 0) or 0)
                except (TypeError, ValueError):
                    pass
                if rec.get("name"):
                    a["name"] = rec["name"]
    except OSError:
        pass
    rows = sorted(agg.items(), key=lambda kv: -kv[1]["points"])
    board = []
    for psid, v in rows:                                 # ALL players across every server (the owner wants the full shared board)
        ab, co = _rank_tier(v["points"])
        board.append({"name": v["name"] or psid, "pts": round(v["points"], 1),
                      "abbr": ab, "color": co, "w": v["wins"], "l": v["losses"]})
    out["rows"] = board
    return jsonify(out)


@app.route("/api/cmd", methods=["POST"])
def api_cmd():
    b = request.get_json(force=True, silent=True) or {}
    name = (b.get("name") or "").strip()
    args = [str(a) for a in b.get("args", [])]
    sid = str(b.get("sid", "")).strip()                  # set by the player popup
    if sid and not _SID_RE.match(sid):                   # sid reaches pipe-framed plugin_cmd files -- digits only
        return jsonify({"ok": False, "error": "bad SteamID"})
    text = " ".join(args).strip()
    try:
        if name in ("leaderboard", "lb", "top"):
            return jsonify({"ok": True, "board": _leaderboard()})
        if name == "ranks":
            return jsonify({"ok": True, "ranks": _ranks_table()})
        if name == "say":
            if not text:
                return jsonify({"ok": False, "error": "usage: say <message>"})
            res = _send_cmd("send-chat-message", [f"<color=#FF8C00>[Admin] {text}</color>"])
            try:    # mirror to the activity feed: admin broadcasts are server RPCs the bot can't parse as chat
                with open(ACTIVITY, "a", encoding="utf-8") as f:
                    f.write(f"{time.strftime('%I:%M:%S %p')}  [ADMIN] {text}\n")
            except OSError:
                pass
            return jsonify({"ok": True, "result": res, "info": f"said: {text}"})
        if name == "rankpreview":
            d = _read_ranks()
            top = sorted(((s, r) for s, r in d.items() if r.get("points", 0) > 0),
                         key=lambda kv: -kv[1].get("points", 0))[:5]
            _send_cmd("send-chat-message", ["<color=#FFD200>== TOP PILOTS ==</color>"])
            for i, (s, r) in enumerate(top, 1):
                ab, co = _rank_tier(r.get("points", 0))
                _send_cmd("send-chat-message",
                          [f"{i}. <color={co}>[{ab}]</color> {r.get('name', s)} - {r.get('points', 0):.0f} pts"])
            return jsonify({"ok": True, "info": f"posted top {len(top)} to chat"})
        if name == "nextmap":
            full = _resolve_mission(text)
            if not full:
                return jsonify({"ok": False, "error": f"no mission matches '{text}'"})
            grp = bot.mission_group(full)      # was hardcoded "User": stock BuiltIn missions silently no-opped with a success toast
            res = _send_cmd("set-next-mission", [grp, full, "7200"])
            return jsonify({"ok": True, "result": res, "info": f"next map -> {full}"})
        if name == "endmission":
            res = _send_cmd("set-time-remaining", ["5"])
            return jsonify({"ok": True, "result": res, "info": "mission ending in 5s"})
        if name == "changemap":                               # END current match + cut over to a chosen map NOW
            full = _resolve_mission(text)
            if not full:
                return jsonify({"ok": False, "error": f"no mission matches '{text}'"})
            # relay through the BOT (not _send_cmd) so it owns the cut-over + suppresses the auto map-vote
            _queue_admin({"action": "changemap", "name": full})
            return jsonify({"ok": True, "info": f"changing map -> {full} now"})
        if name == "grant":
            if sid:
                who, pts_s = sid, str(b.get("points", "")).strip()
            else:
                who, _, pts_s = text.rpartition(" ")
                who, pts_s = who.strip(), pts_s.strip()
            if not who or not pts_s:
                return jsonify({"ok": False, "error": "usage: grant <player> <points>"})
            try:
                pts = float(pts_s)
            except ValueError:
                return jsonify({"ok": False, "error": f"'{pts_s}' is not a number"})
            _queue_admin({"action": "grant", "query": who, "points": pts})
            return jsonify({"ok": True, "info": f"queued grant {pts:+g} -> {who}"})
        if name == "balance":
            _queue_admin({"action": "team", "verb": "balance", "sid": "", "faction": ""})
            return jsonify({"ok": True, "info": "queued team-balance pass"})
        if name in ("setrank", "setfunds", "addfunds"):       # in-game rank / funds (relayed to the plugin)
            if sid:
                rsid, label = sid, b.get("name", sid)
                num_s = str(b.get("amount", b.get("points", ""))).strip()
            else:
                who, _, num_s = text.rpartition(" ")
                rsid, label = _resolve_player(who.strip())
                num_s = num_s.strip()
            if not rsid:
                return jsonify({"ok": False, "error": label if isinstance(label, str) else "no such player"})
            if not num_s:
                return jsonify({"ok": False, "error": f"usage: {name} <player> <number>"})
            try:
                float(num_s)
            except ValueError:
                return jsonify({"ok": False, "error": f"'{num_s}' is not a number"})
            _queue_admin({"action": "team", "verb": name, "sid": rsid, "faction": num_s})   # plugin reads the number from field 3
            return jsonify({"ok": True, "info": f"queued {name} {label} -> {num_s}"})
        if name in ("move", "join", "spec", "spectate", "team"):
            if name in ("spec", "spectate"):
                rsid, label = (sid, b.get("name", sid)) if sid else _resolve_player(text)
                if not rsid:
                    return jsonify({"ok": False, "error": label})
                _queue_admin({"action": "team", "verb": "spec", "sid": rsid, "faction": ""})
                return jsonify({"ok": True, "info": f"queued: {label} -> spectate"})
            if sid:
                fac = _faction_norm(b.get("faction", "")) or _faction_norm(text)
                rsid, label = sid, b.get("name", sid)
            else:
                toks = text.split()
                if len(toks) < 2:
                    return jsonify({"ok": False, "error": f"usage: {name} <player> <boscali|primeva>"})
                fac = _faction_norm(toks[-1])
                rsid, label = _resolve_player(" ".join(toks[:-1]))
            if not fac:
                return jsonify({"ok": False, "error": "faction must be boscali or primeva"})
            if not rsid:
                return jsonify({"ok": False, "error": label})
            _queue_admin({"action": "team", "verb": "move" if name in ("move", "team") else "join",
                          "sid": rsid, "faction": fac})
            return jsonify({"ok": True, "info": f"queued: {label} -> {fac}"})
        if name == "copysid":
            return jsonify({"ok": True, "sid": sid})
        # server wire command: WHITELIST to known CENTRE_SERVER_CMDS verbs (no raw passthrough).
        # Everything else is either handled by an explicit branch above or is rejected here.
        entry = next((e for e in bot.CENTRE_SERVER_CMDS if e[0] == name or e[1] == name), None)
        if not entry:
            return jsonify({"ok": False, "error": f"unknown command '{name}'"})
        # basic arg sanity: reject control/newline/null chars that could corrupt the [code][len][body] relay framing
        if any(any(ord(ch) < 0x20 for ch in a) for a in args):
            return jsonify({"ok": False, "error": "command arguments contain invalid characters"})
        wire = entry[1]
        res = _send_cmd(wire, args)
        ok = True
        info = None
        if isinstance(res, dict) and "code" in res:
            ok = res.get("code") == 2000
        return jsonify({"ok": ok, "result": res, "info": info})
    except Exception as e:                               # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/power", methods=["POST"])
def api_power():
    sig = (request.get_json(force=True, silent=True) or {}).get("signal", "").strip()
    if sig not in ("start", "stop", "restart", "kill"):   # gate BOTH power paths; an unknown sig fell through _local_power -> launched a duplicate server
        return jsonify({"ok": False, "message": "bad signal"})
    ok, msg = (_local_power(sig) if _is_local_power() else _pt_power(sig))
    return jsonify({"ok": ok, "message": msg})


@app.route("/api/resources")
def api_resources():
    return jsonify(_local_resources() if _is_local_power() else _pt_resources())


@app.route("/api/schedule")
def api_schedule_get():
    return jsonify({"items": sorted(_read_schedule(), key=lambda i: i.get("when", ""))})


@app.route("/api/schedule", methods=["POST"])
def api_schedule_add():
    """Add a scheduled restart/update. The BOT polls schedule.json and executes at `when`
    (a server restart via the guarded deploy pipeline), warning players beforehand."""
    b = request.get_json(force=True, silent=True) or {}
    typ = (b.get("type") or "").strip().lower()
    when = (b.get("when") or "").strip().replace("T", " ")
    desc = (b.get("desc") or "").strip()[:200]
    if typ not in ("restart", "update"):
        return jsonify({"ok": False, "error": "type must be 'restart' or 'update'"})
    try:
        t = time.strptime(when[:16], "%Y-%m-%d %H:%M")
        when = time.strftime("%Y-%m-%d %H:%M", t)
    except ValueError:
        return jsonify({"ok": False, "error": "pick a valid date & time"})
    if time.mktime(t) < time.time() - 60:
        return jsonify({"ok": False, "error": "that time is in the past"})
    if typ == "update" and not desc:
        return jsonify({"ok": False, "error": "add a note of what's being updated"})
    items = _read_schedule()
    item = {"id": "sch_" + format(int(time.time() * 1000), "x"), "type": typ,
            "when": when, "desc": desc, "status": "pending",
            "created": time.strftime("%Y-%m-%d %H:%M")}
    items.append(item)
    _write_schedule(items)
    return jsonify({"ok": True, "item": item})


@app.route("/api/schedule/delete", methods=["POST"])
def api_schedule_del():
    iid = ((request.get_json(force=True, silent=True) or {}).get("id") or "").strip()
    _write_schedule([i for i in _read_schedule() if i.get("id") != iid])
    return jsonify({"ok": True})


if __name__ == "__main__":
    _shown = "127.0.0.1" if HOST in ("127.0.0.1", "localhost") else "<this-machine-LAN-IP>"
    print(f"[webcc] Nuke Option web command centre -> http://127.0.0.1:{PORT}"
          + (f"  (LAN: http://{_shown}:{PORT})" if HOST == "0.0.0.0" else ""))
    _pt_load()
    print(f"[webcc] pterodactyl: {'ready (' + (_pt.get('server') or '') + ')' if _pt.get('server') else 'NOT configured - ' + str(_pt.get('err'))}")
    app.run(host=HOST, port=PORT, threaded=True)
