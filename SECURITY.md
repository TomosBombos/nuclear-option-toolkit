# Security

## Plugin update signing (minisign)

Plugin updates pulled through the opt-in GitHub updater are **verified before they are
applied**, so a server owner can trust that a `NukeStats.dll` they auto-stage really came
from the maintainer and wasn't tampered with in transit or on the release page.

**Trust model**
- The maintainer holds a **minisign secret key** (Ed25519). It never leaves their machine / a secured signer.
- The matching **public key ships with the toolkit** as `installer/trusted.pub` **from the first signed release onward** (and is baked into the frozen launcher). This is the trust root. *(Until that first signed release `trusted.pub` is absent, and the updater refuses to stage a binary unless you pass `--i-understand-unsigned`.)*
- Each release publishes three assets: `NukeStats.dll`, `NukeStats.dll.sha256`, and `NukeStats.dll.minisig`.
- `installer/updater.py` checks **both**: the SHA-256 against the published hash (integrity) **and** the minisign signature against `trusted.pub` (authenticity). If no signature verifier is available it **refuses to stage** unless explicitly overridden with `--i-understand-unsigned`.

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
If the signing key is lost or rotated: ship a new toolkit/launcher version carrying the
new `trusted.pub`, and publish one transitional release signed with **both** the old and
new keys so in-flight users can verify with either.

## Credentials & secrets

- The shipped package contains **zero** maintainer secrets. The setup wizard collects the
  owner's own SFTP password + Pterodactyl API key **at run time** and stores them in
  `~/.nuke-option-toolkit/secrets.json` (written `0600`), **never** in `config.json` and
  **never** in the repo.
- A pre-commit/CI secret scan (gitleaks + a SteamID64/host/key sweep) must report **zero**
  before any publish. See `docs/PRE_UPLOAD_CHECKLIST.md`.

## Reporting a vulnerability

Open a private security advisory on the repository, or email the maintainer. Please don't
file public issues for exploitable bugs (score/economy exploits, RCE, credential leaks)
until they're fixed.
