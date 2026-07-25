#!/usr/bin/env bash
# Wee-Orchestrator Linux installer
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/leprachuan/Wee-Orchestrator/main/scripts/install-linux.sh | bash
# Optional:
#   WEE_INSTALL_DIR=/srv/wee-orchestrator bash install-linux.sh
#   WEE_INSTALL_METHOD=git WEE_REF=v1.2.3 bash install-linux.sh   # source checkout
#
# Installs a published, checksum-verified release by default, so the result can
# self-update with scripts/update-api-package.sh and needs no git.

set -Eeuo pipefail

REPOSITORY_URL="https://github.com/leprachuan/Wee-Orchestrator.git"
INSTALL_DIR="${WEE_INSTALL_DIR:-$HOME/.local/share/wee-orchestrator}"
REF="${WEE_REF:-main}"

die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' is required. Install it, then rerun this installer."
}

generate_shared_key() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    python3 -c 'import secrets; print(secrets.token_hex(32))'
  fi
}

require_command git
require_command python3

if ! python3 -m venv --help >/dev/null 2>&1; then
  die "Python venv support is required (for Debian/Ubuntu: sudo apt install python3-venv)."
fi

# Issues #406/#411: default to installing a published, checksum-verified
# package so the result can self-update without git. Set WEE_INSTALL_METHOD=git
# for a developer checkout instead.
INSTALL_METHOD="${WEE_INSTALL_METHOD:-package}"

install_from_package() {
  require_command curl
  require_command tar

  local work release version tag archive_url checksum_url helper
  work="$(mktemp -d)"
  trap 'rm -rf "$work"' RETURN

  # Bootstrap the resolver from the repository, since we have no install yet.
  helper="$work/wee_release.py"
  curl -fsSL "https://raw.githubusercontent.com/${REPOSITORY_SLUG}/main/wee_release.py" -o "$helper" \
    || die "could not fetch the release resolver"

  release="$(python3 "$helper" latest "$REPOSITORY_SLUG" || true)"
  [[ -n "$release" ]] || die "no published api-v* release found in $REPOSITORY_SLUG. Use WEE_INSTALL_METHOD=git for a source install."
  read -r version tag archive_url checksum_url <<<"$release"
  printf 'Installing Wee Orchestrator API v%s\n' "$version"

  curl -fsSL "$archive_url"  -o "$work/api.tar.gz" || die "download failed"
  curl -fsSL "$checksum_url" -o "$work/api.sha256" || die "checksum download failed"
  python3 "$helper" verify "$work/api.tar.gz" "$work/api.sha256" \
    || die "refusing to install: checksum verification failed"

  mkdir -p "$work/unpacked" "$INSTALL_DIR"
  tar -xzf "$work/api.tar.gz" -C "$work/unpacked"
  local root
  root="$(find "$work/unpacked" -mindepth 1 -maxdepth 1 -type d | head -1)"
  [[ -f "$root/agent_manager.py" ]] || die "archive does not look like the API"
  (cd "$root" && tar -cf - .) | (cd "$INSTALL_DIR" && tar -xf -)

  # The updater compares against this on every check.
  printf '%s\n' "$version" > "$INSTALL_DIR/VERSION"
}

REPOSITORY_SLUG="${WEE_RELEASE_REPOSITORY:-leprachuan/Wee-Orchestrator}"

if [[ "$INSTALL_METHOD" == "package" ]] && [[ ! -d "$INSTALL_DIR/.git" ]]; then
  if [[ -e "$INSTALL_DIR" ]] && [[ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]] && [[ ! -f "$INSTALL_DIR/VERSION" ]]; then
    die "Install directory exists and is not a Wee-Orchestrator install: $INSTALL_DIR"
  fi
  install_from_package
elif [[ -d "$INSTALL_DIR/.git" ]]; then
  printf 'Updating existing checkout in %s\n' "$INSTALL_DIR"
  git -C "$INSTALL_DIR" fetch --tags origin
  git -C "$INSTALL_DIR" checkout "$REF"
  git -C "$INSTALL_DIR" pull --ff-only origin "$REF" 2>/dev/null || true
else
  if [[ -e "$INSTALL_DIR" ]] && [[ -n "$(ls -A "$INSTALL_DIR")" ]]; then
    die "Install directory exists and is not a Wee-Orchestrator checkout: $INSTALL_DIR"
  fi
  mkdir -p "$(dirname "$INSTALL_DIR")"
  printf 'Cloning Wee-Orchestrator into %s\n' "$INSTALL_DIR"
  git clone --branch "$REF" --depth 1 "$REPOSITORY_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

printf 'Creating Python environment and installing API dependencies…\n'
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

if [[ ! -f agents.json ]]; then
  printf 'Creating a minimal local agent configuration.\n'
  printf '{\n  "agents": [\n    {\n      "name": "orchestrator",\n      "description": "Local Wee Orchestrator agent",\n      "path": "%s"\n    }\n  ]\n}\n' "$INSTALL_DIR" > agents.json
fi

if [[ ! -f .env ]]; then
  printf 'Creating localhost-only API configuration.\n'
  shared_key="$(generate_shared_key)"
  printf '%s\n' \
    'APP_ENV=LOCAL' \
    'API_HOST=127.0.0.1' \
    'API_PORT=8000' \
    "API_SHARED_KEY=$shared_key" \
    "AGENT_CONFIG_FILE=$INSTALL_DIR/agents.json" \
    'SCHEDULER_ENABLED=true' \
    > .env
  chmod 600 .env
fi

printf '\nInstalled Wee-Orchestrator API in %s\n' "$INSTALL_DIR"
printf 'Start it with:\n  cd %s && .venv/bin/python agent_manager.py --api\n' "$INSTALL_DIR"
printf 'Then open: http://127.0.0.1:8000/ui\n'
printf 'Keep .env private. Configure Telegram, WebEx, runtime credentials, or a remote client after installation.\n'
