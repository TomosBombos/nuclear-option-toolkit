# Global Leaderboard + Server Directory — data contract

**Status:** authored by the OPS side (2026-06-28). The OPS runtime (bot/plugin/webcc) PRODUCES the
files below; the GitHub/productization side CONSUMES them to render the public page. This file is the
hand-off so both sides build to the same shape. Nothing here requires a restart to author.

This covers two opt-in features that share one public GitHub repo:
1. **Global leaderboard** — a server publishes its rankings; an aggregated cross-server board is shown
   in-game (`!global`) and on the GitHub page.
2. **Server directory** — a server opts in to be *listed* (name + region + "runs the NukeOption plugins"),
   so players can discover servers and find them **by name** in the in-game browser. **No IP / no
   direct-connect** is published (Nuclear Option has no direct connect), so region + name is the locator.

---

## Opt-in settings (webcc settings menu → "Global Leaderboard" group; plugin-owned ConfigEntries)

| Key | Type | Meaning |
|---|---|---|
| `Global.Region` | enum (`OCE/NA/EU/SA/AS/AF/ME/Other`) | The server's region. Used by BOTH the directory and the leaderboard. Required when either is on. |
| `Global.ListServer` | toggle | **Opt in to the public server directory.** Publishes name + region + plugin version. Does NOT require the leaderboard. |
| `Global.Enabled` | toggle | **Opt in to the global leaderboard.** Publishes rankings AND locks gameplay settings (good-faith). Implies listing. |

The webcc already renders these via the existing settings menu (toggle + enum controls + the gameplay
lock). A server may List without Enable (appears in the directory, free to change gameplay settings, but
contributes no rankings). Enabling the leaderboard implies listing.

---

## Public repo layout (the central repo, e.g. `<owner>/nuclear-option-leaderboard`)

```
servers/<server_id>.json                 # directory entry (one per listed server)
leaderboards/<region>/<server_id>.json    # this server's board (only when Global.Enabled)
global.json                               # AGGREGATED board — built by a scheduled GitHub Action
index.html / README.md                    # the public page (GitHub Pages) — built by the GitHub side
```

`server_id` = a stable, **non-reversible** id the toolkit generates once and stores in its config
(a random UUID, or a salted hash of the server name — NEVER the IP/host). It only namespaces files.

---

## `servers/<server_id>.json` — directory entry (NEW)

```json
{
  "server_id": "5f3c…",
  "name": "ANZ Nuclear Option Community",
  "region": "OCE",
  "uses_nukeoption": true,
  "plugin_version": "0.9.8",
  "global_leaderboard": true,
  "max_players": 32,
  "updated": "2026-06-28T05:00:00Z"
}
```
Written only when `Global.ListServer` (or `Global.Enabled`) is on. No IP, host, port, or SteamIDs.

## `leaderboards/<region>/<server_id>.json` — per-server board

```json
{
  "server_id": "5f3c…", "name": "ANZ …", "region": "OCE", "updated": "2026-06-28T05:00:00Z",
  "top_points": [ { "name": "Brick", "points": 21850, "rank": "ACM" }, … up to ~25 ],
  "top_skill":  [ { "name": "Mull",  "pts_per_life": 120, "skill": 8.5 }, … up to ~25 ]
}
```
Written only when `Global.Enabled`. In-game names only (already public); no SteamIDs.

---

## Publish flow (OPS bot — feature #4, every 4h, needs `NO_GH_TOKEN`)

1. If `Global.ListServer` or `Global.Enabled`: build `servers/<server_id>.json`.
2. If `Global.Enabled`: build `leaderboards/<region>/<server_id>.json` from `ranks.json` (top N by
   points + by skill).
3. Commit changed files to the repo via the GitHub **Contents API** with a fine-grained token
   (Contents: read/write, that repo only). One commit per 4h cycle. Skip the commit if unchanged.
4. (Optional) fetch `global.json` periodically → write a local file the plugin reads for `!global`.

Good-faith model: the gameplay-settings lock + "changed-from-default ⇒ not eligible" warning are the
only anti-cheat for v1; submissions are not cryptographically verified.

## Aggregation + public page (GitHub side)

- A scheduled **GitHub Action** reads all `servers/*.json` + `leaderboards/**/*.json` and writes
  `global.json` (merged, de-duped by `server_id`, sorted).
- `index.html` (GitHub Pages) renders **two tables**: a **Server Directory** (name · region · plugin
  version · leaderboard yes/no · max players) so players can browse by region and look the server up by
  name in-game; and the **Global Leaderboard** (top players across all opted-in servers).
- Stale entries (no `updated` in N days) are hidden.

## Privacy / security

- NEVER published: IP, host, port, SFTP/panel creds, SteamIDs. Only the public server name, region,
  plugin version, and in-game player names.
- `server_id` is opaque and non-reversible.
- The push token is the owner's fine-grained PAT, scoped to the single repo, kept in `run.bat` as
  `NO_GH_TOKEN` (never echoed).
