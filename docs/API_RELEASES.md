# API releases

The API and the macOS application have independent release streams. API tags
use `api-vMAJOR.MINOR.PATCH`; macOS tags use `macos-vMAJOR.MINOR.PATCH`.

Each API release publishes two assets:

- `Wee-Orchestrator-API-vMAJOR.MINOR.PATCH.tar.gz` — source package from the
  tagged Git revision.
- `Wee-Orchestrator-API-vMAJOR.MINOR.PATCH.tar.gz.sha256` — SHA-256 checksum
  for the package.

The archive is created with `git archive`, so it includes only files committed
to the release revision. Local `.env`, `agents.json`, virtual environments,
session data, and uncommitted changes are not packaged.

## Install or upgrade

On macOS or Linux, install the latest published API release with:

```bash
curl -fsSL https://raw.githubusercontent.com/leprachuan/Wee-Orchestrator/main/scripts/install-api.sh | bash
```

The installer downloads the latest stable `api-v*` release, verifies its
checksum, creates an isolated virtual environment, and creates a localhost
configuration only when one does not already exist. Existing `.env` and
`agents.json` files are retained when upgrading.

To install a specific release or location:

```bash
curl -fsSL https://raw.githubusercontent.com/leprachuan/Wee-Orchestrator/main/scripts/install-api.sh | WEE_VERSION=api-v1.0.0 WEE_INSTALL_DIR=/srv/wee-orchestrator bash
```

Start the installed API with:

```bash
cd ~/.local/share/wee-orchestrator
.venv/bin/python agent_manager.py --api
```

The installer intentionally binds its generated configuration to `127.0.0.1`.
Review [network access guidance](dev-access.md) before exposing an API over a
network.

## Maintainer release procedure

1. Run the relevant tests and ensure the release commit is on `main`.
2. Choose the next semantic version and build an archive from that commit:

   ```bash
   scripts/package-api-release.sh 1.0.0 main
   ```

3. Inspect the archive and checksum, then create a GitHub release tagged
   `api-v1.0.0`, attaching both files from `dist/`.
4. Run the one-line installer into a fresh temporary directory using
   `WEE_VERSION=api-v1.0.0` and verify the API can start locally.

Do not attach a working tree archive or include host configuration, credentials,
or runtime data in a release.
