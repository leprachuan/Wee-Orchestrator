#!/usr/bin/env bash
# Wee-Orchestrator API installer for macOS and Linux.
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/leprachuan/Wee-Orchestrator/main/scripts/install-api.sh | bash
# Optional:
#   curl -fsSL https://raw.githubusercontent.com/leprachuan/Wee-Orchestrator/main/scripts/install-api.sh | WEE_VERSION=api-v1.0.0 WEE_INSTALL_DIR=/srv/wee bash

set -Eeuo pipefail

REPOSITORY="leprachuan/Wee-Orchestrator"
RELEASE_BASE_URL="${WEE_RELEASE_BASE_URL:-https://github.com/${REPOSITORY}/releases/download}"
INSTALL_DIR="${WEE_INSTALL_DIR:-$HOME/.local/share/wee-orchestrator}"
VERSION="${WEE_VERSION:-}"

die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' is required. Install it, then rerun this installer."
}

sha256() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    die "shasum or sha256sum is required to verify the download."
  fi
}

generate_shared_key() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    python3 -c 'import secrets; print(secrets.token_hex(32))'
  fi
}

latest_api_version() {
  curl --fail --location --silent --show-error \
    "https://api.github.com/repos/${REPOSITORY}/releases?per_page=100" |
    python3 -c '
import json
import re
import sys

for release in json.load(sys.stdin):
    tag = release.get("tag_name", "")
    if not release.get("draft") and not release.get("prerelease") and re.fullmatch(r"api-v\d+\.\d+\.\d+", tag):
        print(tag)
        break
else:
    raise SystemExit("No published Wee-Orchestrator API release was found.")
'
}

case "$(uname -s)" in
  Darwin|Linux) ;;
  *) die "This installer supports macOS and Linux only." ;;
esac

require_command curl
require_command tar
require_command python3

python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' || \
  die "Python 3.10 or newer is required."
python3 -m venv --help >/dev/null 2>&1 || \
  die "Python venv support is required (on Debian/Ubuntu: sudo apt install python3-venv)."

if [[ -z "$VERSION" ]]; then
  VERSION="$(latest_api_version)"
fi
[[ "$VERSION" =~ ^api-v[0-9]+\.[0-9]+\.[0-9]+$ ]] || \
  die "WEE_VERSION must use the api-vMAJOR.MINOR.PATCH format."

BASE_VERSION="${VERSION#api-v}"
ARCHIVE_NAME="Wee-Orchestrator-API-v${BASE_VERSION}.tar.gz"
CHECKSUM_NAME="${ARCHIVE_NAME}.sha256"
TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/wee-orchestrator.XXXXXX")"
trap 'rm -rf "$TEMP_DIR"' EXIT

download() {
  curl --fail --location --silent --show-error "$1" --output "$2"
}

printf 'Downloading Wee-Orchestrator API %s…\n' "$VERSION"
download "${RELEASE_BASE_URL%/}/${VERSION}/${ARCHIVE_NAME}" "${TEMP_DIR}/${ARCHIVE_NAME}"
download "${RELEASE_BASE_URL%/}/${VERSION}/${CHECKSUM_NAME}" "${TEMP_DIR}/${CHECKSUM_NAME}"

EXPECTED_CHECKSUM="$(awk 'NR == 1 { print $1 }' "${TEMP_DIR}/${CHECKSUM_NAME}")"
ACTUAL_CHECKSUM="$(sha256 "${TEMP_DIR}/${ARCHIVE_NAME}")"
[[ -n "$EXPECTED_CHECKSUM" && "$EXPECTED_CHECKSUM" == "$ACTUAL_CHECKSUM" ]] || \
  die "Checksum verification failed; the download was not installed."

if [[ -e "$INSTALL_DIR" && ! -d "$INSTALL_DIR" ]]; then
  die "Install location exists but is not a directory: $INSTALL_DIR"
fi
mkdir -p "$INSTALL_DIR"

printf 'Installing API files in %s…\n' "$INSTALL_DIR"
tar -xzf "${TEMP_DIR}/${ARCHIVE_NAME}" -C "$INSTALL_DIR" --strip-components=1

printf 'Creating Python environment and installing API dependencies…\n'
if [[ ! -x "$INSTALL_DIR/.venv/bin/python" ]]; then
  python3 -m venv "$INSTALL_DIR/.venv"
fi
"$INSTALL_DIR/.venv/bin/python" -m pip install --upgrade pip
"$INSTALL_DIR/.venv/bin/python" -m pip install -r "$INSTALL_DIR/requirements.txt"

if [[ ! -f "$INSTALL_DIR/agents.json" ]]; then
  printf 'Creating a minimal local agent configuration.\n'
  printf '{\n  "agents": [\n    {\n      "name": "orchestrator",\n      "description": "Local Wee Orchestrator agent",\n      "path": "%s"\n    }\n  ]\n}\n' "$INSTALL_DIR" > "$INSTALL_DIR/agents.json"
fi

if [[ ! -f "$INSTALL_DIR/.env" ]]; then
  printf 'Creating localhost-only API configuration.\n'
  shared_key="$(generate_shared_key)"
  printf '%s\n' \
    'APP_ENV=LOCAL' \
    'API_HOST=127.0.0.1' \
    'API_PORT=8000' \
    "API_SHARED_KEY=$shared_key" \
    "AGENT_CONFIG_FILE=$INSTALL_DIR/agents.json" \
    'SCHEDULER_ENABLED=true' \
    > "$INSTALL_DIR/.env"
  chmod 600 "$INSTALL_DIR/.env"
fi

printf '\nInstalled Wee-Orchestrator API %s in %s\n' "$VERSION" "$INSTALL_DIR"
printf 'Start it with:\n  cd %s && .venv/bin/python agent_manager.py --api\n' "$INSTALL_DIR"
printf 'Then open: http://127.0.0.1:8000/ui\n'
printf 'Existing .env and agents.json files are preserved during upgrades. Keep .env private.\n'
