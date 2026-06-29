#!/usr/bin/env python3
"""Nuke Option Server Toolkit — local setup wizard (offline-first).

Run this once to set up the toolkit for YOUR server. It starts a tiny web server
bound to localhost only, opens your browser to a guided wizard, and writes a clean
config + a separate secrets file (your credentials never leave this machine and are
never committed). Nothing here phones home — the only optional network calls are the
"Test connection" buttons you click and (later, opt-in) the GitHub updater.

    python setup.py            # opens the wizard in your browser

Design notes:
  * Localhost-only bind + a random per-run token in the URL, so nothing else on the
    LAN can reach the setup API.
  * Secrets (SFTP password, Pterodactyl key, panel URL) are written to secrets.json
    in the user-data dir with 0600 perms where the OS supports it, and are NEVER put
    in config.json (which is the safe-to-share file).
  * The plugin feature catalogue is read from ../settings_catalogue.json — the SAME
    catalogue the web command centre's Settings menu uses (one source of truth).
"""
import http.server
import json
import os
import secrets as _secrets
import socket
import sys
import threading
import webbrowser

for _s in (sys.stdout, sys.stderr):                   # never crash printing on a cp1252 console
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                      # the toolkit root (where the bot/web CC live)
CATALOGUE = os.path.join(ROOT, "settings_catalogue.json")
# User data dir: where the generated config + secrets live. Kept OUT of the repo.
USER_DIR = os.environ.get("NOST_DATA_DIR") or os.path.join(
    os.path.expanduser("~"), ".nuke-option-toolkit")
CONFIG = os.path.join(USER_DIR, "config.json")
SECRETS = os.path.join(USER_DIR, "secrets.json")
TOKEN = _secrets.token_urlsafe(16)                # guards the setup API for this run
_SERVER = None                                    # set in main(); used by /api/shutdown

sys.path.insert(0, HERE)
try:
    import fetcher as _fetcher        # manifest-driven source resolver/downloader
    import detect as _detect          # autodetect scenario + connectivity
    import steamcmd as _steamcmd      # install/locate the dedicated server (SteamCMD)
    import serverconfig as _serverconfig  # read/write DedicatedServerConfig.json (ports etc.)
except Exception:                     # noqa: BLE001
    _fetcher = _detect = _steamcmd = _serverconfig = None

try:
    import paramiko                               # optional: only needed for SFTP scenarios
except Exception:                                 # noqa: BLE001
    paramiko = None
try:
    import urllib.request
    import urllib.error
except Exception:                                 # noqa: BLE001
    urllib = None


# ----------------------------- helpers -----------------------------
def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _load_catalogue():
    try:
        with open(CATALOGUE, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("settings", []) if isinstance(d, dict) else (d if isinstance(d, list) else [])
    except (OSError, ValueError):
        return []


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_json_secure(path, data, secret=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)
    if secret:
        try:
            os.chmod(path, 0o600)                 # no-op semantics on Windows, real on *nix
        except OSError:
            pass


def _preflight():
    """Green/amber/red checks the wizard shows on the welcome step."""
    out = []
    import sys
    out.append({"name": "Python runtime",
                "ok": sys.version_info >= (3, 8),
                "detail": "Python %d.%d" % sys.version_info[:2]})
    out.append({"name": "paramiko (SFTP)",
                "ok": paramiko is not None,
                "detail": "available" if paramiko else "missing — needed only for external (SFTP) servers; pip install paramiko"})
    out.append({"name": "Settings catalogue",
                "ok": os.path.exists(CATALOGUE),
                "detail": "%d settings" % len(_load_catalogue()) if os.path.exists(CATALOGUE) else "settings_catalogue.json not found next to the toolkit"})
    out.append({"name": "Internet (optional)",
                "ok": True,
                "detail": "the installer runs fully offline; internet is only used for the opt-in GitHub updater later"})
    return out


def _test_sftp(p):
    if paramiko is None:
        return {"ok": False, "error": "paramiko not installed (pip install paramiko)"}
    host = (p.get("sftp_host") or "").strip()
    port = int(p.get("sftp_port") or 22)
    user = (p.get("sftp_user") or "").strip()
    pw = p.get("sftp_pass") or ""
    if not host or not user:
        return {"ok": False, "error": "host and user are required"}
    try:
        t = paramiko.Transport((host, port))
        t.connect(username=user, password=pw)
        sftp = paramiko.SFTPClient.from_transport(t)
        listing = sftp.listdir(".")[:5]
        t.close()
        return {"ok": True, "info": "connected; sample of remote files: " + ", ".join(listing) if listing else "connected (empty home)"}
    except Exception as e:                         # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _test_panel(p):
    if urllib is None:
        return {"ok": False, "error": "urllib unavailable"}
    panel = (p.get("panel_url") or "").strip().rstrip("/")
    key = (p.get("api_key") or "").strip()
    if not panel or not key:
        return {"ok": False, "error": "panel URL and API key are required"}
    req = urllib.request.Request(panel + "/api/client",
                                 headers={"Authorization": "Bearer " + key,
                                          "Accept": "application/json",
                                          "User-Agent": "Mozilla/5.0 NukeOptionToolkit"})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        servers = [s.get("attributes", {}).get("identifier") for s in (data.get("data") or [])]
        return {"ok": True, "info": "panel reachable; %d server(s): %s" % (len(servers), ", ".join(filter(None, servers)) or "(none)"),
                "servers": [{"id": s.get("attributes", {}).get("identifier"),
                             "name": s.get("attributes", {}).get("name")} for s in (data.get("data") or [])]}
    except urllib.error.HTTPError as e:            # noqa: BLE001
        return {"ok": False, "error": "HTTP %s — check the panel URL + that this is a CLIENT (account) API key" % e.code}
    except Exception as e:                         # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _render_plugin_cfg(features):
    """Generate a BepInEx anz.nukestats.cfg body from the selected feature toggles +
    the catalogue defaults. Only ON/OFF feature master-keys are written here; the full
    per-value tuning happens later in the web CC's Settings menu."""
    cat = {s["key"]: s for s in _load_catalogue()}
    lines = ["## anz.nukestats.cfg — generated by the Nuke Option setup wizard.",
             "## Feature on/off below; fine-tune everything live in the web command centre.\n"]
    # group by section
    sections = {}
    for key, on in features.items():
        meta = cat.get(key)
        if not meta or "." not in key:
            continue
        sec, name = key.split(".", 1)
        sections.setdefault(sec, []).append((name, meta, on))
    for sec in sorted(sections):
        lines.append("[%s]" % sec)
        for name, meta, on in sections[sec]:
            val = "true" if on else "false"
            lines.append("## %s" % (meta.get("adminDescription") or ""))
            lines.append("%s = %s\n" % (name, val))
        lines.append("")
    return "\n".join(lines)


def _generate_launch(game_dir, platform, rcmd_port):
    """Own-PC install: create logs/console.log + a StartServer launcher in the game folder so
    the server boots modded (BepInEx) and writes the log the bot reads. Returns (script, log)."""
    logs = os.path.join(game_dir, "logs")
    os.makedirs(logs, exist_ok=True)
    logp = os.path.join(logs, "console.log")
    if not os.path.exists(logp):
        open(logp, "a").close()                          # so the path exists immediately
    if platform == "windows":
        script = os.path.join(game_dir, "StartServer.bat")
        body = ("@echo off\r\n"
                "cd /d \"%~dp0\"\r\n"
                "if not exist logs mkdir logs\r\n"
                "echo Starting Nuclear Option (modded) - logs\\console.log\r\n"
                "NuclearOptionServer.exe -batchmode -nographics -logFile logs\\console.log -ServerRemoteCommands " + str(rcmd_port) + "\r\n"
                "pause\r\n")
    else:
        script = os.path.join(game_dir, "start_server.sh")
        body = ("#!/usr/bin/env bash\n"
                "cd \"$(dirname \"$0\")\"\n"
                "mkdir -p logs\n"
                "chmod +x ./NuclearOptionServer.x86_64 ./libdoorstop.so 2>/dev/null || true\n"
                "export LD_LIBRARY_PATH=\"$(pwd):$(pwd)/linux64:$LD_LIBRARY_PATH\"\n"
                "export DOORSTOP_ENABLED=1\n"
                "export DOORSTOP_TARGET_ASSEMBLY=\"$(pwd)/BepInEx/core/BepInEx.Preloader.dll\"\n"
                "export LD_PRELOAD=\"$(pwd)/libdoorstop.so:$LD_PRELOAD\"\n"
                "./NuclearOptionServer.x86_64 -batchmode -nographics -logFile logs/console.log -ServerRemoteCommands " + str(rcmd_port) + "\n")
    with open(script, "w", encoding="utf-8", newline="") as f:
        f.write(body)
    if platform != "windows":
        try:
            os.chmod(script, 0o755)
        except OSError:
            pass
    return script, logp


def _generate_start_everything(game_dir, platform, web_port):
    """Own-PC: ONE launcher that (re)starts the server + bot + web CC and opens the dashboard,
    exactly like the live server's START EVERYTHING (which starts bot + web CC)."""
    if platform == "windows":
        path = os.path.join(game_dir, "START EVERYTHING.bat")
        body = ("@echo off\r\n"
                "cd /d \"%~dp0\"\r\n"
                "echo Starting your Nuclear Option community server (server + bot + web CC)...\r\n"
                "powershell -NoProfile -Command \"Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'no_mapvote_bot.py|cc_web.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }\" 2>nul\r\n"
                "tasklist /FI \"IMAGENAME eq NuclearOptionServer.exe\" | find /I \"NuclearOptionServer.exe\" >nul || start \"Nuclear Option - Server\" \"%~dp0StartServer.bat\"\r\n"
                "timeout /t 5 >nul\r\n"
                "start \"Nuke Option - Bot\" cmd /k python -u no_mapvote_bot.py\r\n"
                "timeout /t 2 >nul\r\n"
                "start \"Nuke Option - Web CC\" cmd /k python -u cc_web.py\r\n"
                "timeout /t 3 >nul\r\n"
                "start \"\" http://localhost:" + str(web_port) + "\r\n")
    else:
        path = os.path.join(game_dir, "start_everything.sh")
        body = ("#!/usr/bin/env bash\n"
                "cd \"$(dirname \"$0\")\"\n"
                "pkill -f 'no_mapvote_bot.py|cc_web.py' 2>/dev/null || true\n"
                "pgrep -f NuclearOptionServer >/dev/null || (bash ./start_server.sh &)\n"
                "sleep 5\n"
                "(python3 -u no_mapvote_bot.py &)\n"
                "sleep 2\n"
                "(python3 -u cc_web.py &)\n"
                "sleep 3\n"
                "(xdg-open http://localhost:" + str(web_port) + " 2>/dev/null || true)\n")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(body)
    if platform != "windows":
        try:
            os.chmod(path, 0o755)
        except OSError:
            pass
    return path


def _save(payload):
    scenario = payload.get("scenario", "external_linux")
    conn = payload.get("connection", {})
    features = payload.get("features", {})
    srv = payload.get("server", {}) or {}          # the Server step (install/ports/name)
    # config.json — SAFE TO SHARE: no secrets.
    config = {
        "version": 1,
        "scenario": scenario,                      # own_pc | external_linux | external_windows
        "server": {
            "sftp_host": conn.get("sftp_host", ""),
            "sftp_port": int(conn.get("sftp_port") or 22),
            "sftp_user": conn.get("sftp_user", ""),
            "log_path": conn.get("log_path", "console.log"),
            "rcmd_host": conn.get("rcmd_host", ""),
            "rcmd_port": int(conn.get("rcmd_port") or 5550),
            "panel_url": conn.get("panel_url", ""),
            "server_id": conn.get("server_id", ""),
            "local_game_dir": conn.get("local_game_dir", ""),
            "power": payload.get("power", "pterodactyl"),
        },
        "web": {"port": int(payload.get("web_port") or 8770)},
        "features": features,
        "update": {"github_repo": payload.get("github_repo", ""),
                   "channel": payload.get("channel", "stable"),
                   "auto_check": bool(payload.get("auto_check", False))},
    }
    # fold in the Server step (ports / install location / name)
    game_dir = (srv.get("dir") or conn.get("local_game_dir") or "").strip()
    gp = int(srv.get("game_port") or 7777)
    qp = int(srv.get("query_port") or 7778)
    config["server"].update({
        "game_port": gp, "query_port": qp,
        "server_name": srv.get("server_name", ""),
        "max_players": int(srv.get("max_players") or 16),
        "install_mode": srv.get("mode", ""),       # install | existing
        "game_dir": game_dir,
    })
    # own-PC: create the log + a StartServer launcher in the game folder, so the server
    # boots modded and writes the log the bot reads — and the log path exists immediately.
    launch = None
    if scenario == "own_pc" and game_dir and os.path.isdir(game_dir):
        rcmd = int(conn.get("rcmd_port") or 5504)
        plat = "windows" if _platform().startswith("win") else "linux"
        try:
            script, logp = _generate_launch(game_dir, plat, rcmd)
            se = _generate_start_everything(game_dir, plat, int(payload.get("web_port") or 8770))
            launch = {"script": script, "log": logp, "start_everything": se}
            config["server"]["log_path"] = logp
            config["server"]["local_console_path"] = logp     # the bot tails this locally
            config["server"]["rcmd_host"] = "127.0.0.1"
            config["server"]["rcmd_port"] = rcmd
            config["server"]["power"] = "local"               # web CC controls the local process
            _sid = (srv.get("admin_sid") or "").strip()
            if _sid:
                config["server"]["admin_sids"] = [_sid]
        except OSError:
            launch = None
    # secrets.json — NEVER shared/committed (0600).
    secret = {
        "sftp_pass": conn.get("sftp_pass", ""),
        "api_key": conn.get("api_key", ""),
    }
    _write_json_secure(CONFIG, config, secret=False)
    _write_json_secure(SECRETS, secret, secret=True)
    # also drop a ready-to-upload BepInEx cfg next to the config
    cfg_path = os.path.join(USER_DIR, "anz.nukestats.cfg")
    try:
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write(_render_plugin_cfg(features))
    except OSError:
        cfg_path = None
    # write DedicatedServerConfig.json (ports/name/players) — always a ready-to-upload copy
    # in the user dir, and directly into the game dir too when it's a local install.
    dsc = []
    if _serverconfig:
        try:
            p, _b = _serverconfig.write_config(USER_DIR, gp, qp, srv.get("server_name", ""),
                                               srv.get("max_players") or 0, srv.get("password", ""))
            dsc.append(p)
            if game_dir and os.path.isdir(game_dir):
                p2, _b2 = _serverconfig.write_config(game_dir, gp, qp, srv.get("server_name", ""),
                                                     srv.get("max_players") or 0, srv.get("password", ""))
                dsc.append(p2)
        except ValueError as e:
            dsc.append("PORTS INVALID: %s" % e)
    return {"ok": True, "config_path": CONFIG, "secrets_path": SECRETS, "cfg_path": cfg_path,
            "dedicated_config": dsc, "launch": launch}


def _api_plan(option):
    if not _fetcher:
        return {"error": "fetcher unavailable"}
    m = _fetcher.load_manifest()
    cfg = _read_json(CONFIG)
    out = []
    for dep_id in m["options"].get(option, []):
        dep = m["dependencies"][dep_id]
        if option in (dep.get("provided_by_host") or []):
            out.append({"id": dep_id, "name": dep.get("name"), "method": "host",
                        "version": "host-provided", "url": "", "note": "the panel/egg installs it server-side"})
        else:
            r = _fetcher.resolve(dep, cfg)
            r["id"] = dep_id
            r["name"] = dep.get("name")
            out.append(r)
    return {"option": option, "deps": out}


def _api_offline_list(option):
    if not _fetcher:
        return {"error": "fetcher unavailable"}
    m = _fetcher.load_manifest()
    out = []
    for dep_id in m["options"].get(option, []):
        dep = m["dependencies"][dep_id]
        off = dep.get("offline", {})
        manual = not (option in (dep.get("provided_by_host") or []) or dep["fetch"]["method"] in ("steamcmd", "bundled"))
        out.append({"id": dep_id, "name": dep.get("name"), "filename": off.get("filename"),
                    "url": off.get("official_url"), "instructions": off.get("instructions"), "manual": manual})
    return {"option": option, "items": out}


def _api_fetch(payload):
    if not _fetcher:
        return {"error": "fetcher unavailable"}
    option = payload.get("option", "")
    dest = payload.get("dest") or os.path.join(USER_DIR, "server")
    offline_dir = payload.get("offline_dir") or None
    m = _fetcher.load_manifest()
    results = []
    for dep_id in m["options"].get(option, []):
        try:
            results.append(_fetcher.fetch_one(dep_id, dest, offline_dir))
        except Exception as e:                           # noqa: BLE001
            results.append({"id": dep_id, "ok": False, "note": str(e)})
    return {"option": option, "dest": dest, "results": results}


def _platform():
    if _detect:
        try:
            return _detect.platform_target()
        except Exception:                                # noqa: BLE001
            pass
    return "win_x64" if sys.platform.startswith("win") else (
        "linux_x64" if sys.platform.startswith("linux") else "unknown")


def _pick_folder(initial=""):
    """Open a NATIVE folder chooser on the user's machine (the wizard runs locally).
    Returns {ok, path}. Falls back gracefully if tkinter isn't available (e.g. headless)."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:                                    # noqa: BLE001
        return {"ok": False, "error": "no native folder picker on this machine — type the path"}
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askdirectory(initialdir=(initial or os.path.expanduser("~")),
                                       title="Choose a folder")
        try:
            root.destroy()
        except Exception:                                # noqa: BLE001
            pass
        return {"ok": True, "path": path or ""}
    except Exception as e:                               # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _api_install_server(payload):
    """Install the official dedicated server via SteamCMD into the chosen folder.
    Downloads SteamCMD FIRST if the user doesn't already have it, then launches the
    multi-GB game install detached (new console / logfile) so the wizard never blocks."""
    if not (_steamcmd and _fetcher):
        return {"ok": False, "error": "installer modules unavailable"}
    install_dir = (payload.get("dir") or "").strip()
    if not install_dir:
        return {"ok": False, "error": "choose an install folder first (use Browse...)"}
    plat = str(payload.get("platform") or _platform())
    platform = "windows" if plat.startswith("win") else "linux"
    sc_base = os.path.join(USER_DIR, "steamcmd")
    try:
        import shutil
        sc = (_steamcmd.find_steamcmd(sc_base, platform)
              or shutil.which("steamcmd") or shutil.which("steamcmd.exe") or "")
        had = bool(sc)
        if not sc:
            sc = _steamcmd.ensure_steamcmd(sc_base, platform, _fetcher)   # safety net: auto-download
        if not sc:
            return {"ok": False, "error": "SteamCMD isn't installed yet - click 'Install SteamCMD' first"}
        cmd, where = _steamcmd.launch_install(sc, install_dir)
        steam_note = ("Using SteamCMD. " if had else "Downloaded SteamCMD. ")
        launch_note = ("The game server is now downloading in a new console window. " if where == "console"
                       else "The game server is downloading (log: %s). " % where)
        return {"ok": True, "cmd": " ".join(cmd), "where": where, "downloaded_steamcmd": not had,
                "note": steam_note + launch_note + "It's several GB — when it finishes, click 'Check install'."}
    except Exception as e:                               # noqa: BLE001
        return {"ok": False, "error": "install failed: %s" % e}


def _pick_file(initial=""):
    """Native FILE chooser on the user's machine (the wizard runs locally). Returns {ok, path}."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:                                    # noqa: BLE001
        return {"ok": False, "error": "no native file picker on this machine — type the path"}
    try:
        if initial and os.path.isdir(initial):
            initdir = initial
        elif initial:
            initdir = os.path.dirname(initial) or os.path.expanduser("~")
        else:
            initdir = os.path.expanduser("~")
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askopenfilename(initialdir=initdir, title="Choose a file")
        try:
            root.destroy()
        except Exception:                                # noqa: BLE001
            pass
        return {"ok": True, "path": path or ""}
    except Exception as e:                               # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _api_install_steamcmd(payload):
    """Step 1 of the own-PC install: download SteamCMD if missing and run it once so it
    self-updates BEFORE the (separate) server download. Detached, so it never blocks."""
    if not (_steamcmd and _fetcher):
        return {"ok": False, "error": "installer modules unavailable"}
    plat = str(payload.get("platform") or _platform())
    platform = "windows" if plat.startswith("win") else "linux"
    sc_base = os.path.join(USER_DIR, "steamcmd")
    try:
        had = bool(_steamcmd.find_steamcmd(sc_base, platform))
        sc = _steamcmd.ensure_steamcmd(sc_base, platform, _fetcher)
        if not sc:
            return {"ok": False, "error": "could not download SteamCMD (check your internet connection)"}
        cmd, where = _steamcmd.launch_selfupdate(sc)
        note = ("Downloaded SteamCMD; " if not had else "SteamCMD already present; ")
        note += ("it's self-updating in a new window — give it up to a minute, then click 'Check SteamCMD'."
                 if where == "console" else "self-updating (log: %s)." % where)
        return {"ok": True, "note": note}
    except Exception as e:                               # noqa: BLE001
        return {"ok": False, "error": "SteamCMD install failed: %s" % e}


def _api_open_folder(payload):
    """Open a folder in the OS file explorer (the wizard runs locally)."""
    path = (payload.get("path") or "").strip()
    if not path or not os.path.isdir(path):
        return {"ok": False, "error": "folder not found: %s" % (path or "(empty)")}
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)                           # noqa
        else:
            import subprocess
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])
        return {"ok": True}
    except Exception as e:                               # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _api_run_launcher(payload):
    """Run a generated launcher (e.g. START EVERYTHING) on the user's machine."""
    path = (payload.get("path") or "").strip()
    if not path or not os.path.exists(path):
        return {"ok": False, "error": "launcher not found: %s" % (path or "(empty)")}
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)                           # runs the .bat in its own window(s)
        else:
            import subprocess
            subprocess.Popen(["bash", path], cwd=os.path.dirname(path))
        return {"ok": True}
    except Exception as e:                               # noqa: BLE001
        return {"ok": False, "error": str(e)}


# ----------------------------- HTTP server -----------------------------
class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _guard(self):
        # token must be present in the query (?t=) or X-Setup-Token header
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(self.path).query)
        tok = (q.get("t") or [None])[0] or self.headers.get("X-Setup-Token")
        return tok == TOKEN

    def log_message(self, *a):                     # quiet
        pass

    def do_GET(self):
        from urllib.parse import urlparse
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            try:
                with open(os.path.join(HERE, "wizard.html"), encoding="utf-8") as f:
                    html = f.read().replace("__TOKEN__", TOKEN)
                return self._send(200, html, "text/html; charset=utf-8")
            except OSError:
                return self._send(500, "wizard.html missing", "text/plain")
        if not self._guard():
            return self._send(403, json.dumps({"error": "bad token"}))
        if path == "/api/preflight":
            return self._send(200, json.dumps({"checks": _preflight()}))
        if path == "/api/catalogue":
            return self._send(200, json.dumps({"settings": _load_catalogue()}))
        if path == "/api/current":
            return self._send(200, json.dumps({"config": _read_json(CONFIG),
                                                "has_secrets": os.path.exists(SECRETS),
                                                "user_dir": USER_DIR}))
        if path == "/api/detect":
            return self._send(200, json.dumps(_detect.suggest(_read_json(CONFIG)) if _detect else {"error": "detect unavailable"}))
        if path == "/api/plan":
            from urllib.parse import urlparse, parse_qs
            opt = (parse_qs(urlparse(self.path).query).get("option") or [""])[0]
            return self._send(200, json.dumps(_api_plan(opt)))
        if path == "/api/offline-list":
            from urllib.parse import urlparse, parse_qs
            opt = (parse_qs(urlparse(self.path).query).get("option") or [""])[0]
            return self._send(200, json.dumps(_api_offline_list(opt)))
        if path == "/api/server-status":
            from urllib.parse import urlparse, parse_qs
            d = (parse_qs(urlparse(self.path).query).get("dir") or [""])[0]
            exe = _steamcmd.server_exe(d) if _steamcmd else ""
            return self._send(200, json.dumps({"dir": d, "present": bool(exe), "exe": exe}))
        if path == "/api/pick-folder":
            from urllib.parse import urlparse, parse_qs
            ini = (parse_qs(urlparse(self.path).query).get("initial") or [""])[0]
            return self._send(200, json.dumps(_pick_folder(ini)))
        if path == "/api/pick-file":
            from urllib.parse import urlparse, parse_qs
            ini = (parse_qs(urlparse(self.path).query).get("initial") or [""])[0]
            return self._send(200, json.dumps(_pick_file(ini)))
        if path == "/api/steamcmd-status":
            from urllib.parse import urlparse, parse_qs
            plat = (parse_qs(urlparse(self.path).query).get("platform") or [""])[0] or _platform()
            platform = "windows" if str(plat).startswith("win") else "linux"
            present = bool(_steamcmd.find_steamcmd(os.path.join(USER_DIR, "steamcmd"), platform)) if _steamcmd else False
            return self._send(200, json.dumps({"present": present}))
        return self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        from urllib.parse import urlparse
        path = urlparse(self.path).path
        if not self._guard():
            return self._send(403, json.dumps({"error": "bad token"}))
        try:
            n = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except (ValueError, OSError):
            payload = {}
        if path == "/api/test-sftp":
            return self._send(200, json.dumps(_test_sftp(payload)))
        if path == "/api/test-panel":
            return self._send(200, json.dumps(_test_panel(payload)))
        if path == "/api/save":
            return self._send(200, json.dumps(_save(payload)))
        if path == "/api/fetch":
            return self._send(200, json.dumps(_api_fetch(payload)))
        if path == "/api/install-server":
            return self._send(200, json.dumps(_api_install_server(payload)))
        if path == "/api/install-steamcmd":
            return self._send(200, json.dumps(_api_install_steamcmd(payload)))
        if path == "/api/open-folder":
            return self._send(200, json.dumps(_api_open_folder(payload)))
        if path == "/api/run-launcher":
            return self._send(200, json.dumps(_api_run_launcher(payload)))
        if path == "/api/shutdown":
            self._send(200, json.dumps({"ok": True}))
            if _SERVER is not None:
                threading.Thread(target=_SERVER.shutdown, daemon=True).start()
            return
        return self._send(404, json.dumps({"error": "not found"}))


def main():
    port = _free_port()
    url = "http://127.0.0.1:%d/?t=%s" % (port, TOKEN)
    httpd = http.server.HTTPServer(("127.0.0.1", port), Handler)
    global _SERVER
    _SERVER = httpd
    print("Nuke Option setup wizard — open this in your browser if it doesn't pop up:")
    print("   " + url)
    print("(Ctrl+C to stop.)  User data dir: " + USER_DIR)
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nsetup wizard stopped.")


if __name__ == "__main__":
    main()
