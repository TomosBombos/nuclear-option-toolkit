#!/usr/bin/env python3
"""Nuke Option Server Toolkit — opt-in GitHub updater (plugin + bot, stable/nightly).

The install itself is fully local/offline. THIS tool is the separate, by-choice way a
server owner connects to GitHub to pull fixes when they're released. It never
auto-applies: `check` only reports; `update` downloads + VERIFIES + stages the new
component for the next deploy; the owner decides when to apply.

    python updater.py check                         # what's available on your channel?
    python updater.py update                         # plugin: download+verify+STAGE (no deploy)
    python updater.py update --component bot          # bot:   download+verify+STAGE
    python updater.py update --component all          # both
    python updater.py update --deploy                 # plugin: ...and run the guarded deploy
    python updater.py update --component bot --apply   # bot: ...and replace no_mapvote_bot.py (backs up first)

Channels (config update.channel, or --channel): `stable` = latest full release;
`nightly` = latest release INCLUDING pre-releases. Opt in once; it sticks.

Verify-before-apply (mandatory, identical for plugin AND bot):
  * SHA-256 of the download is checked against the release's published <asset>.sha256.
  * If a minisign <asset>.minisig + the bundled trusted.pub are present, the Ed25519
    signature is verified (minisign CLI, else pynacl/cryptography). If NO verifier is
    available it REFUSES to stage unless run with --i-understand-unsigned.
Plugin stages pending_plugin.dll (+ .json) for run.bat --deploy-plugin. Bot stages
pending_bot.py (+ .json); --apply backs up + replaces no_mapvote_bot.py (no auto-restart).
"""
import hashlib
import json
import os
import shutil
import ssl
import subprocess
import sys
import urllib.request
import urllib.error

for _s in (sys.stdout, sys.stderr):                   # never crash printing on a cp1252 console
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
USER_DIR = os.environ.get("NOST_DATA_DIR") or os.path.join(os.path.expanduser("~"), ".nuke-option-toolkit")
CONFIG = os.path.join(USER_DIR, "config.json")
PUBKEY = os.path.join(HERE, "trusted.pub")          # bundled minisign public key (ships with the toolkit)

# A release ships a matched set; both components carry the release tag as their version.
COMPONENTS = {
    "plugin": {
        "asset": "NukeStats.dll",
        "pending": os.path.join(ROOT, "pending_plugin.dll"),
        "meta": os.path.join(ROOT, "pending_plugin.json"),
        "deployed": os.path.join(ROOT, "deployed_plugin.json"),
        "apply": "deploy",          # applied via run.bat --deploy-plugin
    },
    "bot": {
        "asset": "no_mapvote_bot.py",
        "pending": os.path.join(ROOT, "pending_bot.py"),
        "meta": os.path.join(ROOT, "pending_bot.json"),
        "deployed": os.path.join(ROOT, "deployed_bot.json"),
        "target": os.path.join(ROOT, "no_mapvote_bot.py"),
        "apply": "replace",         # applied by backing up + replacing the file
    },
    "webcc": {                      # the web command centre (dashboard) — cc_web.py + webcc.html + deps
        "asset": "command-centre.zip",
        "pending": os.path.join(ROOT, "pending_webcc.zip"),
        "meta": os.path.join(ROOT, "pending_webcc.json"),
        "deployed": os.path.join(ROOT, "deployed_webcc.json"),
        "apply": "extract",         # applied by backing up + extracting the files into ROOT
    },
}


def _cfg():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _deployed_version(comp):
    try:
        with open(COMPONENTS[comp]["deployed"], encoding="utf-8") as f:
            return (json.load(f) or {}).get("version", "")
    except (OSError, ValueError):
        return ""


TOOLKIT_DEPLOYED = os.path.join(ROOT, "deployed_toolkit.json")


def _toolkit_installed():
    """The installed TOOLKIT version — stamped at install + bumped on apply. The single version the
    updater compares against the latest release tag (releases are tagged by the toolkit version, e.g.
    v1.0). Separate from deployed_plugin.json, which the bot owns for the plugin's own version."""
    try:
        with open(TOOLKIT_DEPLOYED, encoding="utf-8") as f:
            return str((json.load(f) or {}).get("version", "") or "")
    except (OSError, ValueError):
        return ""


def _set_toolkit_installed(version):
    try:
        with open(TOOLKIT_DEPLOYED, "w", encoding="utf-8") as f:
            json.dump({"version": str(version).lstrip("v")}, f, indent=2)
    except OSError:
        pass


def _vt(v):
    """Loose semver tuple for comparison; non-numeric parts ignored."""
    out = []
    for part in str(v).lstrip("v").split("."):
        n = "".join(ch for ch in part if ch.isdigit())
        out.append(int(n) if n else 0)
    return tuple(out) or (0,)


def _get(url, token=None, raw=False):
    headers = {"User-Agent": "NukeOptionToolkit-Updater", "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, headers=headers)
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
        data = r.read()
    return data if raw else json.loads(data.decode("utf-8", "replace"))


def _latest_release(repo, channel="stable", token=None):
    rels = _get("https://api.github.com/repos/%s/releases" % repo, token=token)
    if not isinstance(rels, list):
        return None
    for rel in rels:                                  # releases are newest-first
        if rel.get("draft"):
            continue
        if channel == "stable" and rel.get("prerelease"):
            continue                                   # stable skips pre-releases; nightly takes them
        return rel
    return None


def _asset(rel, name):
    for a in rel.get("assets", []):
        if a.get("name") == name:
            return a.get("browser_download_url")
    return None


def _repo_and_channel(channel_override=None):
    cfg = _cfg()
    upd = cfg.get("update", {}) or {}
    repo = (upd.get("github_repo") or "").strip()
    channel = channel_override or upd.get("channel", "stable")
    return repo, channel


def check(components=("plugin", "bot"), channel_override=None, verbose=True):
    repo, channel = _repo_and_channel(channel_override)
    if not repo:
        print("No GitHub repo configured. Re-run setup (or set update.github_repo in %s)." % CONFIG)
        return None
    token = os.environ.get("GITHUB_TOKEN")            # optional, for private repos / rate limits
    try:
        rel = _latest_release(repo, channel, token)
    except urllib.error.HTTPError as e:
        print("GitHub error %s for repo '%s' (private repo? set GITHUB_TOKEN)." % (e.code, repo))
        return None
    except Exception as e:                            # noqa: BLE001
        print("Could not reach GitHub: %s" % e)
        return None
    if not rel:
        print("No %s release found for %s." % (channel, repo))
        return None
    latest = rel.get("tag_name") or rel.get("name") or ""
    installed = _toolkit_installed()
    newer = _vt(latest) > _vt(installed)
    out = {"repo": repo, "channel": channel, "release": rel, "latest": latest,
           "installed": installed, "newer": newer, "components": {}}
    for comp in components:
        out["components"][comp] = {"in_release": _asset(rel, COMPONENTS[comp]["asset"]) is not None}
    if verbose:
        print("Repo:      %s  (%s channel)" % (repo, channel))
        print("Installed: %s" % (installed or "(unknown)"))
        print("Latest:    %s   %s" % (latest, "<-- UPDATE AVAILABLE" if newer else "(up to date)"))
        if newer and rel.get("body"):
            print("\nRelease notes:\n" + "\n".join("  " + ln for ln in rel["body"].splitlines()[:25]))
            print("\nRun `python updater.py update --component all` to download + verify + stage.")
    return out


def _verify(asset, data, rel, allow_unsigned):
    """SHA-256 + minisign verify-before-apply. Returns True to proceed, False to refuse."""
    sha = hashlib.sha256(data).hexdigest()
    sha_url = _asset(rel, asset + ".sha256")
    published = None
    if sha_url:
        try:
            published = _get(sha_url, raw=True).decode().split()[0].strip()
        except Exception:                            # noqa: BLE001
            published = None
    if published and published.lower() != sha.lower():
        print("  [FAIL] SHA-256 MISMATCH — refusing (download corrupt or tampered).")
        print("    expected %s\n    got      %s" % (published, sha))
        return False, sha
    print("  [ok] SHA-256 %s... (%s)" % (sha[:16], "matches published" if published else "no published hash"))

    sig_url = _asset(rel, asset + ".minisig")
    verified, how = (None, "no .minisig asset in the release")
    if sig_url:
        try:
            verified, how = _verify_minisig(data, _get(sig_url, raw=True))
        except Exception as e:                       # noqa: BLE001
            verified, how = (False, "could not fetch/verify signature: %s" % e)
    if verified is True:
        print("  [ok] signature %s" % how)
    elif verified is False:
        print("  [FAIL] signature verification FAILED (%s) — refusing." % how)
        return False, sha
    else:
        print("  [warn] signature NOT verified: %s" % how)
        if not allow_unsigned:
            print("    Refusing an unverified download. Re-run with --i-understand-unsigned to override,")
            print("    or install a verifier (`minisign` CLI, or `pip install pynacl`).")
            return False, sha
        print("    --i-understand-unsigned given: proceeding WITHOUT signature verification.")
    return True, sha


def _verify_minisig(data, sig_bytes):
    """Best-effort Ed25519 minisign verification of arbitrary bytes. Returns (verified, how)."""
    if not os.path.exists(PUBKEY):
        return (None, "no bundled public key (trusted.pub) — signature check skipped")
    try:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            dp = os.path.join(td, "asset.bin")
            sp = dp + ".minisig"
            open(dp, "wb").write(data)
            open(sp, "wb").write(sig_bytes)
            r = subprocess.run(["minisign", "-V", "-p", PUBKEY, "-m", dp],
                               capture_output=True, timeout=30)
            if r.returncode == 0:
                return (True, "verified with minisign CLI")
            return (False, "minisign CLI rejected the signature")
    except FileNotFoundError:
        pass
    except Exception as e:                            # noqa: BLE001
        return (False, "minisign CLI error: %s" % e)
    try:
        import base64
        pub_lines = [l for l in open(PUBKEY).read().splitlines() if l and not l.startswith("untrusted")]
        pub_raw = base64.b64decode(pub_lines[0])      # 2-byte alg + 8-byte keyid + 32-byte key
        pubkey = pub_raw[10:42]
        sig_lines = [l for l in sig_bytes.decode("utf-8", "replace").splitlines() if l and not l.startswith("untrusted")]
        sig_raw = base64.b64decode(sig_lines[0])
        alg, sig = sig_raw[:2], sig_raw[10:74]
        signed = data if alg == b"Ed" else hashlib.blake2b(data).digest()
        try:
            from nacl.signing import VerifyKey
            VerifyKey(pubkey).verify(signed, sig)
            return (True, "verified with pynacl (Ed25519)")
        except ImportError:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            Ed25519PublicKey.from_public_bytes(pubkey).verify(sig, signed)
            return (True, "verified with cryptography (Ed25519)")
    except Exception as e:                            # noqa: BLE001
        return (False, "no signature verifier available / verify failed (%s)" % e)


def _stage(comp, rel, latest, data, sha, repo):
    c = COMPONENTS[comp]
    with open(c["pending"], "wb") as f:
        f.write(data)
    meta = {"version": str(latest).lstrip("v"), "size": len(data), "sha256": sha,
            "component": comp,
            "note": "Fetched from GitHub %s release %s by the opt-in updater." % (repo, latest)}
    with open(c["meta"], "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    print("  [ok] staged %s %s (%d bytes) -> %s" % (comp, latest, len(data), os.path.basename(c["pending"])))


def _apply_bot(latest):
    c = COMPONENTS["bot"]
    if not os.path.exists(c["pending"]):
        print("  nothing staged to apply.")
        return
    target = c["target"]
    if os.path.exists(target):
        bak = target + ".bak-" + str(latest).lstrip("v")
        shutil.copy2(target, bak)
        print("  backed up current bot -> %s" % os.path.basename(bak))
    shutil.copy2(c["pending"], target)
    with open(c["deployed"], "w", encoding="utf-8") as f:
        json.dump({"version": str(latest).lstrip("v")}, f, indent=2)
    print("  [ok] applied bot %s -> %s   (restart the bot to load it)" % (latest, os.path.basename(target)))


def _apply_webcc(latest):
    c = COMPONENTS["webcc"]
    if not os.path.exists(c["pending"]):
        print("  nothing staged to apply.")
        return
    import zipfile
    applied = []
    with zipfile.ZipFile(c["pending"]) as z:
        for name in z.namelist():
            base = os.path.basename(name)
            if name.endswith("/") or not base:
                continue
            target = os.path.join(ROOT, base)
            if os.path.exists(target):
                shutil.copy2(target, target + ".bak-" + str(latest).lstrip("v"))
            with z.open(name) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            applied.append(base)
    with open(c["deployed"], "w", encoding="utf-8") as f:
        json.dump({"version": str(latest).lstrip("v")}, f, indent=2)
    print("  [ok] applied web command centre %s: %s   (restart the web CC to load it)"
          % (latest, ", ".join(applied) or "(empty)"))


def update(components=("plugin",), channel_override=None, do_deploy=False, do_apply=False, allow_unsigned=False):
    info = check(components, channel_override, verbose=False)
    if not info:
        return
    rel, latest, repo = info["release"], info["latest"], info["repo"]
    if not info.get("newer"):
        print("Already up to date — installed %s, latest %s (%s channel)."
              % (info.get("installed") or "?", latest, info["channel"]))
        return
    did = []
    for comp in components:
        st = info["components"].get(comp, {})
        if not st.get("in_release"):
            print("- %s: not in the latest %s release — skipping." % (comp, info["channel"]))
            continue
        asset = COMPONENTS[comp]["asset"]
        url = _asset(rel, asset)
        print("%s: downloading %s ..." % (comp, asset))
        data = _get(url, raw=True)
        ok, sha = _verify(asset, data, rel, allow_unsigned)
        if not ok:
            continue
        _stage(comp, rel, latest, data, sha, repo)
        did.append(comp)

    if "plugin" in did and do_deploy:
        runbat = os.path.join(ROOT, "run.bat")
        if os.path.exists(runbat):
            subprocess.Popen(["cmd", "/c", runbat, "--deploy-plugin"], cwd=ROOT)
            print("plugin: run.bat --deploy-plugin launched.")
        else:
            print("plugin: run.bat not found — run your deploy command to apply it.")
    if "bot" in did and do_apply:
        _apply_bot(latest)
    if "webcc" in did and do_apply:
        _apply_webcc(latest)
    if did and (do_deploy or do_apply):
        _set_toolkit_installed(latest)
        print("Toolkit now marked as %s (what 'up to date' checks against)." % latest)
    if did and not (do_deploy or do_apply):
        print("\nStaged only. Apply when ready: plugin -> run.bat --deploy-plugin ; "
              "bot + web command centre -> update --component all --apply")


def _parse(argv):
    comp = "plugin"
    channel = None
    for i, a in enumerate(argv):
        if a == "--component" and i + 1 < len(argv):
            comp = argv[i + 1]
        elif a == "--channel" and i + 1 < len(argv):
            channel = argv[i + 1]
    comps = ("plugin", "bot", "webcc") if comp == "all" else (comp,)
    return comps, channel


if __name__ == "__main__":
    args = sys.argv[1:]
    cmd = args[0] if args else "check"
    comps, channel = _parse(args)
    if cmd == "check":
        check(("plugin", "bot", "webcc"), channel)
    elif cmd == "update":
        update(comps, channel,
               do_deploy=("--deploy" in args),
               do_apply=("--apply" in args),
               allow_unsigned=("--i-understand-unsigned" in args))
    else:
        print(__doc__)
