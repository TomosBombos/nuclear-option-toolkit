# Security

## Plugin update signing (minisign)

Plugin updates pulled through the opt-in GitHub updater are **verified before they are applied**. So when you auto-stage a `NukeStats.dll`, you can trust it came from the maintainer and wasn't tampered with in transit or on the release page.

**Trust model**
- The maintainer holds a **minisign secret key** (Ed25519). It never leaves their machine or a secured signer.
- The matching **public key ships with the toolkit** as `installer/trusted.pub`, and is baked into the frozen launcher. This is the trust root.
- Each release publishes three assets: `NukeStats.dll`, `NukeStats.dll.sha256`, and `NukeStats.dll.minisig`.
- `installer/updater.py` checks **both**. It checks the SHA-256 against the published hash for integrity, and the minisign signature against `trusted.pub` for authenticity. If no signature verifier is available it **refuses to stage**, unless you explicitly override with `--i-understand-unsigned`.

**One-time key generation (maintainer)**
```bash
minisign -G -p installer/trusted.pub -s ~/.minisign/nukeoption.key
#  -> commit installer/trusted.pub ; keep the .key secret (never commit)
```

**Cutting a signed release (maintainer)**
```bash
export MINISIGN_SECRET_KEY=~/.minisign/nukeoption.key
python scripts/release.py 0.9.7 --notes "What changed"
#  builds the DLL (needs the game libs locally), signs it, and publishes the GitHub release
```

**Key rotation**
If the signing key is lost or rotated, ship a new toolkit and launcher version carrying the new `trusted.pub`. Then publish one transitional release signed with **both** the old and new keys, so in-flight users can verify with either.

## Credentials & secrets

- The shipped package contains **zero** maintainer secrets. The setup wizard collects the owner's own SFTP password and Pterodactyl API key **at run time** and stores them in `~/.nuke-option-toolkit/secrets.json` (written `0600`). They are **never** in `config.json` and **never** in the repo.
- A pre-commit and CI secret scan (gitleaks plus a SteamID64, host, and key sweep) must report **zero** before any publish. See `docs/PRE_UPLOAD_CHECKLIST.md`.

## Reporting a vulnerability

Open a private security advisory on the repository, or email the maintainer. Please don't file public issues for exploitable bugs (score and economy exploits, RCE, credential leaks) until they're fixed.
