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


def _notes(channel, version, date, signed):
    lines = ["Automated %s release of the Nuclear Option community toolkit." % channel,
             "", "- Plugin + bot version: **%s**" % version.lstrip("v")]
    if channel == "nightly":
        lines.append("- Nightly build %s — pre-release; use the **nightly** update channel to receive it." % date)
    lines += ["- Bundles: Pterodactyl / Local / Manual (each a full self-contained install).",
              "- Updater assets: NukeStats.dll, no_mapvote_bot.py.",
              "- %s" % ("All assets are **minisign-signed**; the public key ships as `installer/trusted.pub`."
                        if signed else "**Unsigned build** (testing).")]
    return "\n".join(lines)


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

    # 2. assemble the updater assets (plugin DLL + the scrubbed bot)
    assets = [os.path.join(out, "nuclear-option-toolkit-%s.zip" % t)
              for t in ("pterodactyl", "local", "manual")]
    dll = os.path.join(out, "NukeStats.dll")
    shutil.copy2(bb.SRC_DLL, dll)
    bot_src = os.path.join(out, "_clean", "no_mapvote_bot.py")     # the scrubbed bot
    bot = os.path.join(out, "no_mapvote_bot.py")
    shutil.copy2(bot_src, bot)
    assets += [dll, bot]

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
    rel = pb.get_or_create(token, tag, name, _notes(a.channel, version, date, sign), prerelease)
    for f in final:
        pb.upload_asset(token, rel, f)
    print("DONE. https://github.com/%s/releases/tag/%s" % (pb.REPO, tag))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
