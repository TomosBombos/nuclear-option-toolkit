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

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                      # the toolkit root (where the bot/web CC live)
CATALOGUE = os.path.join(ROOT, "settings_catalogue.json")
# User data dir: where the generated config + secrets live. Kept OUT of the repo.
USER_DIR = os.environ.get("NOST_DATA_DIR") or os.path.join(
    os.path.expanduser("~"), ".nuke-option-toolkit")
CONFIG = os.path.join(USER_DIR, "config.json")
SECRETS = os.path.join(USER_DIR, "secrets.json")
TOKEN = _secrets.token_urlsafe(16)                # guards the setup API for this run

sys.path.insert(0, HERE)
try:
    import fetcher as _fetcher        # manifest-driven source resolver/downloader
    import detect as _detect          # autodetect scenario + connectivity
except Exception:                     # noqa: BLE001
    _fetcher = _detect = None

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


def _save(payload):
    scenario = payload.get("scenario", "external_linux")
    conn = payload.get("connection", {})
    features = payload.get("features", {})
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
    return {"ok": True, "config_path": CONFIG, "secrets_path": SECRETS, "cfg_path": cfg_path}


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
        return self._send(404, json.dumps({"error": "not found"}))


def main():
    port = _free_port()
    url = "http://127.0.0.1:%d/?t=%s" % (port, TOKEN)
    httpd = http.server.HTTPServer(("127.0.0.1", port), Handler)
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
