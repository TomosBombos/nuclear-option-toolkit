#!/usr/bin/env python3
"""Render the public community-server list into README.md between the
<!-- COMMUNITY-SERVERS:START --> / <!-- COMMUNITY-SERVERS:END --> markers.

Run by .github/workflows/community-servers.yml (hourly). Reads servers/*.json from the public
directory repo via the GitHub contents API (no secrets; uses the Action's GITHUB_TOKEN only for
rate limits). On a transient API error it exits WITHOUT changing the file, so a blip never wipes
the list. Usage: python render_servers.py [README.md]
"""
import json
import os
import re
import sys
import urllib.request

REPO = os.environ.get("SERVERS_REPO", "TomosBombos/nuclear-option-servers")
README = sys.argv[1] if len(sys.argv) > 1 else "README.md"
START = "<!-- COMMUNITY-SERVERS:START -->"
END = "<!-- COMMUNITY-SERVERS:END -->"
OWNER, NAME = REPO.split("/", 1)
DIR_URL = "https://%s.github.io/%s/" % (OWNER.lower(), NAME)
REGION_NAMES = {"OCE": "Oceania", "NA": "North America", "EU": "Europe", "SA": "South America",
                "AS": "Asia", "AF": "Africa", "ME": "Middle East", "Other": "Other"}
REGION_ORDER = ["OCE", "NA", "EU", "SA", "AS", "AF", "ME", "Other"]


def gh(url):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "nuke-readme-render"})
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def fetch_servers():
    try:
        lst = json.loads(gh("https://api.github.com/repos/%s/contents/servers" % REPO))
    except Exception as e:                            # noqa: BLE001
        if "404" in str(e):
            return []                                  # no servers/ dir yet -> empty (valid)
        print("fetch failed (%s) — leaving the list unchanged" % e)
        sys.exit(0)                                    # transient error: don't overwrite
    out = []
    for f in (lst if isinstance(lst, list) else []):
        if not (f.get("type") == "file" and str(f.get("name", "")).endswith(".json")):
            continue
        try:
            j = json.loads(gh(f["download_url"]))
            if j.get("name"):
                out.append(j)
        except Exception:                             # noqa: BLE001
            pass
    return out


def region_of(s):
    r = str(s.get("region", "")).strip().upper()
    for k in REGION_ORDER:
        if k.upper() == r:
            return k
    return "Other"


def build_block(servers):
    if not servers:
        return "_No servers are listed yet — be the first._ &nbsp; ([directory ↗](%s))" % DIR_URL
    servers.sort(key=lambda s: (REGION_ORDER.index(region_of(s)), str(s.get("name", "")).lower()))
    n = len(servers)
    head = "**%d server%s** running the toolkit &nbsp; ([full directory ↗](%s))\n\n" % (
        n, "" if n == 1 else "s", DIR_URL)
    rows = ["| Server | Region | Plugin |", "|---|---|---|"]
    for s in servers:
        rows.append("| %s | %s | %s |" % (
            str(s.get("name", "")).replace("|", "\\|"),
            REGION_NAMES.get(region_of(s), region_of(s)),
            ("v" + str(s["plugin_version"])) if s.get("plugin_version") else "—"))
    return head + "\n".join(rows)


def main():
    block = build_block(fetch_servers())
    with open(README, encoding="utf-8") as f:
        txt = f.read()
    if START not in txt or END not in txt:
        print("markers not found in %s — nothing to do" % README)
        return 0
    new = re.sub(re.escape(START) + r".*?" + re.escape(END),
                 START + "\n" + block + "\n" + END, txt, flags=re.S)
    if new != txt:
        with open(README, "w", encoding="utf-8", newline="\n") as f:
            f.write(new)
        print("README server list updated")
    else:
        print("no change")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
