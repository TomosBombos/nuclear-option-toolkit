#!/usr/bin/env python3
"""Build + sign + publish a plugin release (maintainer-side, run locally).

This is the PUBLISH end of the opt-in GitHub update loop (installer/updater.py is the
consume end). It runs locally because building NukeStats.dll needs the game's managed
assemblies (NukeStats/libs/*.dll) which are copyrighted and can't live in CI / the repo.

    python scripts/release.py 0.9.7 --notes "Fix the X exploit"

Steps:
  1. dotnet build -c Release  ->  NukeStats.dll
  2. sha256  ->  NukeStats.dll.sha256
  3. minisign sign  ->  NukeStats.dll.minisig   (needs a minisign secret key; see SECURITY.md)
  4. gh release create v<ver>  with the 3 assets + notes   (needs the `gh` CLI, authenticated)

Flags:
  --notes "..."   release notes (else a stub)
  --prerelease    mark as a pre-release (beta channel)
  --no-publish    build + sign locally only; skip the GitHub release (dry run)
  --repo owner/x  override the target repo (else taken from `gh` default / git remote)
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJ = os.path.join(ROOT, "NukeStats")
DLL = os.path.join(PROJ, "bin", "Release", "NukeStats.dll")
OUT = os.path.join(ROOT, "release_out")
MINISIGN_KEY = os.environ.get("MINISIGN_SECRET_KEY")   # path to the minisign secret key


def run(cmd, **kw):
    print("  $ " + " ".join(cmd))
    return subprocess.run(cmd, check=True, **kw)


def _make_bot_asset(out_dir, ver, sign):
    """SECRET-SCRUB the bot via the clean-room scrubber, prove zero secrets survive, then
    emit no_mapvote_bot.py (+ .sha256 [+ .minisig]) for the release. The live bot holds the
    real IP/SteamID, so it is NEVER published raw."""
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import build_public_repo as b
    b.REAL_SID, b.REAL_IP, b.REAL_HOST = b._load_targets()
    with open(os.path.join(ROOT, "no_mapvote_bot.py"), encoding="utf-8") as f:
        text = b.scrub_bot(f.read())
    for kind, rx in b._hard_patterns():
        if rx.search(text):
            sys.exit("ABORT: scrubbed bot still matches %s — refusing to publish a leak." % kind)
    out = os.path.join(out_dir, "no_mapvote_bot.py")
    with open(out, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    bsha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    open(out + ".sha256", "w").write("%s  no_mapvote_bot.py\n" % bsha)
    print("3b) scrubbed bot asset: no_mapvote_bot.py  sha256=%s… (CLEAN)" % bsha[:16])
    assets = [out, out + ".sha256"]
    if sign and shutil.which("minisign") and MINISIGN_KEY and os.path.exists(MINISIGN_KEY):
        try:
            run(["minisign", "-S", "-s", MINISIGN_KEY, "-m", out,
                 "-t", "NukeStats bot %s sha256:%s" % (ver, bsha), "-c", "NukeStats bot %s" % ver])
            if os.path.exists(out + ".minisig"):
                assets.append(out + ".minisig")
        except subprocess.CalledProcessError as e:
            print("   bot minisign failed: %s" % e)
    return assets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("version", help="plugin version, e.g. 0.9.7")
    ap.add_argument("--notes", default="")
    ap.add_argument("--prerelease", action="store_true", help="(alias of --channel nightly)")
    ap.add_argument("--channel", choices=["stable", "nightly"], default="stable",
                    help="nightly => published as a pre-release (the opt-in nightly channel)")
    ap.add_argument("--with-bot", action="store_true",
                    help="also publish the SCRUBBED bot (no_mapvote_bot.py) as a release asset")
    ap.add_argument("--no-publish", action="store_true")
    ap.add_argument("--repo", default="")
    a = ap.parse_args()
    ver = a.version.lstrip("v")
    tag = "v" + ver
    prerelease = a.prerelease or a.channel == "nightly"
    os.makedirs(OUT, exist_ok=True)

    print("1) building the plugin (dotnet -c Release)…")
    run(["dotnet", "build", "-c", "Release"], cwd=PROJ)
    if not os.path.exists(DLL):
        sys.exit("build did not produce " + DLL)

    print("2) computing SHA-256…")
    data = open(DLL, "rb").read()
    sha = hashlib.sha256(data).hexdigest()
    dll_out = os.path.join(OUT, "NukeStats.dll")
    shutil.copy2(DLL, dll_out)
    open(dll_out + ".sha256", "w").write("%s  NukeStats.dll\n" % sha)
    print("   sha256 = %s  (%d bytes)" % (sha, len(data)))

    print("3) signing with minisign…")
    sig_ok = False
    if shutil.which("minisign") and MINISIGN_KEY and os.path.exists(MINISIGN_KEY):
        trusted_comment = "NukeStats %s sha256:%s" % (ver, sha)
        try:
            run(["minisign", "-S", "-s", MINISIGN_KEY, "-m", dll_out,
                 "-t", trusted_comment, "-c", "NukeStats plugin %s" % ver])
            sig_ok = os.path.exists(dll_out + ".minisig")
        except subprocess.CalledProcessError as e:
            print("   minisign failed: %s" % e)
    else:
        print("   SKIPPED — need the `minisign` CLI on PATH and MINISIGN_SECRET_KEY pointing at your secret key.")
        print("   (Unsigned releases force `updater.py` to demand --i-understand-unsigned. See SECURITY.md.)")

    bot_assets = _make_bot_asset(OUT, ver, sig_ok) if a.with_bot else []

    print("\nbuilt assets in %s:" % OUT)
    for fn in sorted(os.listdir(OUT)):
        print("   - " + fn)

    if a.no_publish:
        print("\n--no-publish: stopping before the GitHub release.")
        return
    if not shutil.which("gh"):
        sys.exit("the GitHub CLI `gh` is not installed/authenticated — install it or use --no-publish.")

    print("\n4) creating the GitHub release %s…" % tag)
    notes = a.notes or ("NukeStats plugin %s." % ver)
    assets = [dll_out, dll_out + ".sha256"] + ([dll_out + ".minisig"] if sig_ok else []) + bot_assets
    title = "NukeStats %s%s" % (ver, " (nightly)" if prerelease else "")
    cmd = ["gh", "release", "create", tag, *assets, "--title", title, "--notes", notes]
    if prerelease:
        cmd.append("--prerelease")
    if a.repo:
        cmd += ["--repo", a.repo]
    run(cmd)
    print("\n[ok] published %s%s. Server owners can now `python installer/updater.py check`." %
          (tag, "" if sig_ok else "  (UNSIGNED — sign before a real public release)"))


if __name__ == "__main__":
    main()
