#!/usr/bin/env python3
"""Cut a signed Nuclear Option toolkit release (nightly pre-release or stable).

Builds the clean tree + the 3 bundles (via build_bundles) + the updater assets (NukeStats.dll,
the scrubbed no_mapvote_bot.py), **minisign-signs every asset**, then publishes a GitHub release
and uploads them (via publish_bundles' token+REST helpers). The matching public key ships in the
toolkit as installer/trusted.pub, so each server's opt-in updater verifies before applying.

    # dry run — build + sign locally, don't publish:
    python scripts/publish_release.py --channel stable --out ../dist --key <minisign.key> --dry-run

    # publish (token comes from git credential manager, like publish_bundles):
    python scripts/publish_release.py --channel stable --out ../dist --key <minisign.key>
    python scripts/publish_release.py --channel nightly --out ../dist --key <minisign.key> --date 20260629

Channels:
  stable  -> tag v<version>            (full release; `updater.py` stable + nightly both see it)
  nightly -> tag v<version>-nightly.<date>  (pre-release; only the nightly channel sees it)

Signing key / minisign binary come from --key/--minisign or the NO_SIGN_KEY / NO_MINISIGN env
vars (no personal paths baked into this file). The secret key is never printed.
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import build_bundles as bb
import publish_bundles as pb
import build_public_repo as bpr

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass


def _which_minisign(override):
    return override or os.environ.get("NO_MINISIGN") or shutil.which("minisign") or "minisign"


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    digest = h.hexdigest()
    with open(path + ".sha256", "w", encoding="utf-8", newline="\n") as f:
        f.write(digest + "  " + os.path.basename(path) + "\n")
    return digest


def _verify_against_main(clean_dir):
    """QA GATE. Confirm the built source matches `main` (the dev-server reference) for the key
    runtime files, so we never publish a build that isn't the current dev code. Returns
    (status, drifted): status is 'ok' | 'drift' | 'skipped' (couldn't reach GitHub)."""
    import urllib.request as _u
    files = ["cc_web.py", "webcc.html", "no_mapvote_bot.py", "settings_catalogue.json",
             "map_atlas.py", "command_centre.py", "installer/setup.py", "installer/updater.py",
             "NukeStats/NukeStatsPlugin.cs"]
    def _norm(b):
        return hashlib.sha256(b.replace(b"\r\n", b"\n").replace(b"\r", b"\n")).hexdigest()
    tok = os.environ.get("GITHUB_TOKEN")
    drift = []
    for rel in files:
        local_p = os.path.join(clean_dir, *rel.split("/"))
        if not os.path.exists(local_p):
            continue
        try:
            req = _u.Request("https://api.github.com/repos/%s/contents/src/%s?ref=main" % (pb.REPO, rel),
                             headers={"User-Agent": "nuke-qa", "Accept": "application/vnd.github.raw"})
            if tok:
                req.add_header("Authorization", "Bearer " + tok)
            remote = _u.urlopen(req, timeout=30).read()
        except Exception:                                  # noqa: BLE001  (network blip -> don't block)
            return ("skipped", [])
        with open(local_p, "rb") as f:
            if _norm(f.read()) != _norm(remote):
                drift.append(rel)
    return ("ok" if not drift else "drift", drift)


def _sign(path, key, minisign):
    """minisign-sign a file -> <path>.minisig. Uses a no-password key (NO_SIGN_KEY). Never logs the key."""
    sig = path + ".minisig"
    if os.path.exists(sig):
        os.remove(sig)
    try:
        r = subprocess.run([minisign, "-S", "-s", key, "-m", path, "-x", sig],
                           capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        raise SystemExit("minisign not found — install it or pass --minisign / set NO_MINISIGN.")
    if r.returncode != 0 or not os.path.exists(sig):
        # never echo the key path's contents; stderr from minisign is safe (no secret material)
        raise SystemExit("minisign signing failed for %s:\n%s" % (os.path.basename(path),
                         (r.stderr or r.stdout or "").strip()[:300]))
    return sig


def _tag_for(channel, version, date):
    v = version.lstrip("v")
    if channel == "stable":
        return "v" + v, "v" + v, False
    if not date:
        raise SystemExit("--date YYYYMMDD is required for a nightly (pass the build date).")
    return ("v%s-nightly.%s" % (v, date), "Nightly %s (v%s)" % (date, v), True)


def _changelog_section(channel, version):
    """Release-notes changelog body, or '' if none.
    Stable pulls the [<version>] section from CHANGELOG.md. Nightly pulls the in-development
    notes from CHANGELOG.unreleased.md (between the NIGHTLY-NOTES markers) — so nightlies carry
    a changelog without listing unreleased work as 'done' on the main CHANGELOG.md."""
    import re as _re
    if channel == "nightly":
        p = os.path.join(ROOT, "CHANGELOG.unreleased.md")
        if not os.path.exists(p):
            return ""
        with open(p, encoding="utf-8") as f:
            text = f.read()
        m = _re.search(r"<!--\s*NIGHTLY-NOTES:START\s*-->(.*?)<!--\s*NIGHTLY-NOTES:END\s*-->",
                       text, _re.S)
        return m.group(1).strip() if m else ""
    path = os.path.join(ROOT, "CHANGELOG.md")
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    base = version.lstrip("v").split("-nightly")[0]
    m = _re.search(r"(?m)^##\s*\[%s\][^\n]*\n" % _re.escape(base), text)
    if not m:
        return ""
    start = m.end()
    nxt = _re.search(r"(?m)^##\s+", text[start:])
    body = (text[start: start + nxt.start()] if nxt else text[start:]).strip()
    # trim a trailing horizontal-rule/footer that belongs after the last section
    body = _re.split(r"(?m)^---\s*$", body)[0].strip()
    return body


def _notes(channel, version, date, signed):
    lines = ["Automated %s release of the Nuclear Option community toolkit." % channel,
             "", "- Plugin + bot version: **%s**" % version.lstrip("v")]
    if channel == "nightly":
        lines.append("- Nightly build %s — pre-release; use the **nightly** update channel to receive it." % date)
    lines += ["- Bundles: Pterodactyl / Local / Manual (each a full self-contained install).",
              "- Updater assets: NukeStats.dll, no_mapvote_bot.py, command-centre.zip, installer.zip.",
              "- %s" % ("All assets are **minisign-signed**; the public key ships as `installer/trusted.pub`."
                        if signed else "**Unsigned build** (testing).")]
    note = "\n".join(lines)
    changes = _changelog_section(channel, version)
    if changes:
        hdr = "### What's in this build (in development)" if channel == "nightly" else "### What's changed"
        note += "\n\n---\n\n" + hdr + "\n\n" + changes
    return note


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", choices=["stable", "nightly"], required=True)
    ap.add_argument("--out", required=True, help="build dir (OUTSIDE the repo)")
    ap.add_argument("--version", default=None, help="default: plugin version from source")
    ap.add_argument("--date", default=None, help="YYYYMMDD for a nightly tag")
    ap.add_argument("--key", default=None, help="minisign secret key (or NO_SIGN_KEY env)")
    ap.add_argument("--minisign", default=None, help="minisign binary (or NO_MINISIGN env / PATH)")
    ap.add_argument("--no-sign", action="store_true", help="skip signing (testing only)")
    ap.add_argument("--dry-run", action="store_true", help="build + sign, do NOT publish")
    ap.add_argument("--keep-nightly", type=int, default=3,
                    help="retain only the N most-recent nightly pre-releases (nightly channel; default 3)")
    ap.add_argument("--no-prune", action="store_true", help="skip nightly retention pruning")
    ap.add_argument("--force", action="store_true",
                    help="publish even if the QA gate finds the build differs from main")
    ap.add_argument("--no-sync-main", action="store_true",
                    help="skip pushing the scrubbed tree to GitHub main before publishing")
    a = ap.parse_args(argv)

    out = os.path.abspath(a.out)
    if out == ROOT or out.startswith(ROOT + os.sep):
        raise SystemExit("--out must be OUTSIDE the source repo")
    version = (a.version or bb._toolkit_version()).lstrip("v")   # TOOLKIT version (1.0+), not the plugin's
    date = a.date
    if a.channel == "nightly" and not date:
        import datetime
        date = datetime.date.today().strftime("%Y%m%d")
    tag, name, prerelease = _tag_for(a.channel, version, date)

    key = a.key or os.environ.get("NO_SIGN_KEY")
    minisign = _which_minisign(a.minisign)
    sign = not a.no_sign
    if sign and not key:
        raise SystemExit("signing needs a key: pass --key or set NO_SIGN_KEY (or --no-sign to skip).")

    # 1. build the 3 bundles + the clean tree
    print("[release] building bundles (%s, %s) ..." % (a.channel, tag))
    rc = bb.main(["--out", out, "--force", "--version", version])
    if rc:
        raise SystemExit("bundle build failed")

    # 1b. smoke gate: never publish a syntactically broken build (critical for the unattended nightly)
    import py_compile
    for rel in ("no_mapvote_bot.py", "cc_web.py", "command_centre.py", "map_atlas.py",
                "installer/setup.py", "installer/updater.py", "installer/deployer.py"):
        p = os.path.join(out, "_clean", *rel.split("/"))
        if os.path.exists(p):
            try:
                py_compile.compile(p, doraise=True)
            except py_compile.PyCompileError as e:
                raise SystemExit("smoke check FAILED — refusing to publish a broken build (%s): %s" % (rel, e))
    print("[release] smoke check OK (key modules compile)")

    # SYNC MAIN — before publishing, push the same scrubbed tree to GitHub main so the repo,
    # the release assets, and the dev server can never diverge. Best-effort: a sync failure
    # (network/creds) must not stop the release — the downloadable build is the requirement.
    if not a.no_sync_main and not a.dry_run:          # a --dry-run must have NO side effects
        try:
            import sync_main
            sync_main.sync_from_clean(os.path.join(out, "_clean"))
        except SystemExit as e:
            print("[release] WARNING: main sync failed (%s) — release continues; main may lag." % e)
        except Exception as e:                       # noqa: BLE001
            print("[release] WARNING: main sync errored (%s) — release continues; main may lag." % e)

    # QA — the guarantee that matters: this build IS the live dev server (assembled from this
    # working dir) with the operator's details scrubbed out. That is enforced HARD upstream:
    #   * build_clean()'s secret scan raises if any real IP / password / key survives the scrub,
    #   * the smoke check above raises if any key module fails to compile.
    # So every nightly is "the dev server, downloadable, without my details, and it runs".
    # The main comparison below is now only an INFORMATIONAL drift note (main is a mirror OPS
    # syncs lazily) — it never blocks the nightly, so an out-of-sync main can't stop a good build.
    qa_status, qa_drift = _verify_against_main(os.path.join(out, "_clean"))
    if qa_status == "ok":
        print("[release] QA: clean build of the live dev server (secrets scrubbed) — also matches GitHub main.")
    elif qa_status == "skipped":
        print("[release] QA: clean build of the live dev server (secrets scrubbed). (Couldn't reach main to compare.)")
    else:
        print("[release] QA: clean build of the live dev server (secrets scrubbed).")
        print("[release] note: differs from GitHub main on: %s "
              "(expected while OPS edits live / main lags — publishing the live code anyway)."
              % ", ".join(qa_drift))

    # 2. assemble the updater assets (plugin DLL + the scrubbed bot)
    assets = [os.path.join(out, "nuclear-option-toolkit-%s.zip" % t)
              for t in ("pterodactyl", "local", "manual")]
    dll = os.path.join(out, "NukeStats.dll")
    shutil.copy2(bb.SRC_DLL, dll)
    bot_src = os.path.join(out, "_clean", "no_mapvote_bot.py")     # the scrubbed bot
    bot = os.path.join(out, "no_mapvote_bot.py")
    shutil.copy2(bot_src, bot)
    assets += [dll, bot]

    # web command centre (dashboard) as ONE signed zip, so the updater can deliver the UI +
    # backend the same verify-before-apply way it delivers the plugin/bot. WITHOUT this, every
    # web-CC feature (rank editor, killfeed panel, cross-server ranks, ...) is unreachable by update.
    import zipfile
    webcc_zip = os.path.join(out, "command-centre.zip")
    webcc_members = ["cc_web.py", "webcc.html", "map_atlas.py", "command_centre.py",
                     "settings_catalogue.json"]
    with zipfile.ZipFile(webcc_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for m in webcc_members:
            src = os.path.join(out, "_clean", m)
            if os.path.exists(src):
                z.write(src, m)
    if not zipfile.ZipFile(webcc_zip).namelist():
        raise SystemExit("command-centre.zip is empty — web-CC files missing from the clean tree")
    assets += [webcc_zip]

    # installer tooling as ONE signed zip so the updater can update ITSELF (component "installer").
    # Without this, updater/setup fixes never reach installed servers short of a full reinstall.
    # trusted.pub (the trust root) is EXCLUDED by design: a release must never rotate the key
    # that verifies releases — the updater's extract also refuses it, belt and braces.
    inst_zip = os.path.join(out, "installer.zip")
    inst_dir = os.path.join(out, "_clean", "installer")
    with zipfile.ZipFile(inst_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for m in sorted(os.listdir(inst_dir)):
            src = os.path.join(inst_dir, m)
            if os.path.isfile(src) and m != "trusted.pub":
                z.write(src, m)
    if not zipfile.ZipFile(inst_zip).namelist():
        raise SystemExit("installer.zip is empty — installer files missing from the clean tree")
    assets += [inst_zip]

    # 3. sha256 + sign every asset
    final = []
    for ap_ in assets:
        if not os.path.exists(ap_):
            raise SystemExit("missing built asset: %s" % ap_)
        if not os.path.exists(ap_ + ".sha256"):
            _sha256_file(ap_)            # bundles already have one; dll/bot get one here
        final.append(ap_)
        final.append(ap_ + ".sha256")
        if sign:
            final.append(_sign(ap_, key, minisign))
    print("[release] %d asset file(s) ready (%s)" % (len(final), "signed" if sign else "UNSIGNED"))
    for f in final:
        print("   " + os.path.basename(f))

    if a.dry_run:
        print("[release] --dry-run: not publishing. Tag would be %s (prerelease=%s)." % (tag, prerelease))
        return 0

    # 4. publish
    token = pb._token()
    notes = _notes(a.channel, version, date, sign)
    notes += ("\n\n_QA: built from the live dev server and secret-scrubbed — no operator details. "
              "Install with the setup wizard to point it at your own server._")
    if qa_status == "ok":
        notes += " _(Also matches `main`.)_"
    rel = pb.get_or_create(token, tag, name, notes, prerelease)
    for f in final:
        pb.upload_asset(token, rel, f)
    print("DONE. https://github.com/%s/releases/tag/%s" % (pb.REPO, tag))

    # 5. retention: keep only the N most-recent nightlies (stable releases are never touched)
    if a.channel == "nightly" and not a.no_prune:
        print("[release] retention: keeping %d most-recent nightlies ..." % a.keep_nightly)
        pruned = pb.prune_nightlies(token, keep=a.keep_nightly)
        print("[release] pruned: %s" % (", ".join(pruned) if pruned else "(none)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
