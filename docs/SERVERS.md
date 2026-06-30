# Public server directory

Opt in and your server appears in a public list other players can browse. It publishes a tiny
JSON for your server — **name, region, and plugin version only**. Never IPs, SteamIDs, player
names, or live status. Fully opt-in and reversible.

Because Nuclear Option has no direct-connect, the directory is a *discovery* tool: players find
your server by **name** (and region) in the in-game server browser.

---

## Enable it for your server

**1. Create the repo.** A new **public** repo, e.g. `YourUser/nuclear-option-servers`.

**2. Make a fine-grained PAT.** GitHub → *Settings → Developer settings → Fine-grained personal
access tokens → Generate new token*:
- **Repository access:** *Only select repositories* → the repo from step 1.
- **Permissions → Contents:** *Read and write* (everything else off).
- Pick an expiry you're OK with (regenerate + re-paste when it lapses).
- Generate, then copy the token (starts with `github_pat_…`).

**3. Add it to `run.bat`** (same place as your SFTP password — it stays on your machine):
```bat
REM -- Public server directory (opt-in) --
set "NO_GH_REPO=YourUser/nuclear-option-servers"
set "NO_GH_TOKEN=github_pat_xxxxxxxxxxxxxxxxxxxxxxxx"
REM Optional friendly public name (defaults to the in-game server name)
set "NO_SERVER_NAME=ANZ Nuclear Option Community"
```
The token only grants write to that one repo. Keep it in `run.bat` (gitignored) — **never commit it**.

**4. Restart the bot.** It publishes `servers/<your-server-id>.json` on its next tick. (Listing
stays inert until **both** `NO_GH_REPO` and `NO_GH_TOKEN` are set.)

**5. Confirm the toggle.** web command centre → *Game Settings → Public Listing*: check
**List Server Publicly** is ON and **Server Region** is set. (This is a plugin setting, so the
toggle only applies while a player is online.)

**6. Stand up the directory page.** Copy [`index.html`](../src/server_directory/index.html) (in
this toolkit, under `src/server_directory/`) into the **root** of your servers repo, then enable
Pages: *Settings → Pages → Deploy from branch → `main` / `/ (root)`*. Your directory goes live at
`https://<youruser>.github.io/<repo>/` and fills in as servers publish.

The page is a single self-contained file (vanilla JS, no dependencies). It auto-detects your
repo from the Pages URL; only set the `REPO_OVERRIDE` constant at the top if you use a custom
domain. It reads only the public JSON — no tokens, nothing secret.

---

## Live status banner (optional)

If your server is also listed on **[gamemonitoring.net](https://gamemonitoring.net/nuclear-option/servers/)**,
the directory and README can show its live **560×95 status banner** (player count, map, online state).

- **Automatic:** nothing to do beyond being listed there — the bot finds your gamemonitoring entry by
  matching your server's address against the public Nuclear Option list and publishes only its numeric
  banner id (`gamemonitoring_id`). Your IP is still never written to the directory.
- **Manual override:** paste your gamemonitoring server URL (e.g.
  `https://gamemonitoring.net/nuclear-option/servers/11798637`) in the command centre under
  *Game Settings → Public Listing* to pin a specific listing.

One-time prerequisite: add your server on gamemonitoring.net first (their *Add server* flow) so it has a
banner to show. Only the public banner image and a link to your gamemonitoring page are displayed; on the
GitHub README the banner is a near-daily snapshot (GitHub proxies images), while the live directory page
shows it in real time.

## To delist
Turn the **List Server Publicly** toggle off (or clear `NO_GH_REPO` / `NO_GH_TOKEN` and restart) —
the bot removes your entry from the directory.

## What's published (and what isn't)
- **Published:** server name, region, plugin version, and an opaque random server id.
- **Never published:** IP / host / port, SFTP or panel credentials, SteamIDs, player names,
  or any live/online status.
