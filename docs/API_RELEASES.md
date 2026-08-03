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

## Development and release policy

Development may happen on any branch, including `main`. Direct commits to
`main` are permitted; feature branches and pull requests are optional tools for
collaboration, not release gates. A branch is never itself a supported
distribution channel. Users receive only the versioned API artifacts published
on GitHub Releases.

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

1. Run the relevant tests and identify the exact committed revision to release.
2. Choose the next semantic version and build an archive from that commit:

   ```bash
   scripts/package-api-release.sh 1.0.0 <committed-ref>
   ```

3. Inspect the archive and checksum, then create a GitHub release tagged
   `api-v1.0.0`, attaching both files from `dist/`.
4. Run the one-line installer into a fresh temporary directory using
   `WEE_VERSION=api-v1.0.0` and verify the API can start locally.

Do not attach a working tree archive or include host configuration, credentials,
or runtime data in a release.

## Publishing a release

`scripts/publish-api-release.sh MAJOR.MINOR.PATCH [ref]` packages from a
committed ref and creates the `api-vMAJOR.MINOR.PATCH` release with both the
archive and its `.sha256`. It refuses to publish an archive that fails its own
checksum, so a release can never be published in a state the installer would
reject.

## Updating an existing install

`/update` (or `update_orchestrator.sh`) runs `git pull` when the install is a
checkout. A release install has no `.git`, so it hands off to
`scripts/update-api-package.sh`, which resolves the newest `api-v*` release,
verifies the published SHA-256 **before** touching anything, backs up the current
install, swaps in the new tree, and restarts the services.

`.env`, `.task-scheduler` and `.canvas-sessions` are preserved. The previous
install is kept as `<install-dir>.backup-<timestamp>`. Set
`WEE_UPDATE_SERVICES=""` to skip the restarts, or `WEE_UPDATE_FORCE=1` to
reinstall the current version.

An unverifiable download is refused rather than installed — a missing checksum is
treated as a failure, not as permission to skip the check.
