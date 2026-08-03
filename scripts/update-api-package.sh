#!/usr/bin/env bash
# Package-based self-update for a Linux API install (issues #406 / #411).
#
# The git path (update_orchestrator.sh) needs a checkout. A downloaded package
# has no .git, so this mirrors what the macOS updater does: read GitHub
# releases, verify the published sha256, swap the tree, restart the services.
#
# Usage: scripts/update-api-package.sh [install-dir]
# Env:   WEE_RELEASE_REPOSITORY, WEE_UPDATE_SERVICES, WEE_UPDATE_FORCE=1

set -Eeuo pipefail

INSTALL_DIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
REPOSITORY="${WEE_RELEASE_REPOSITORY:-leprachuan/Wee-Orchestrator}"
# Note ${VAR-default}, not ${VAR:-default}: an explicitly empty
# WEE_UPDATE_SERVICES must mean "restart nothing", which is what makes it safe
# to exercise this script against a throwaway directory on a live host.
SERVICES="${WEE_UPDATE_SERVICES-agent-manager-api task-scheduler-executor webex-connector telegram-bot-listener}"
PYTHON="${WEE_PYTHON:-python3}"
HELPER="$INSTALL_DIR/wee_release.py"

log()  { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
die()  { printf 'Error: %s\n' "$*" >&2; exit 1; }

[[ -d "$INSTALL_DIR" ]] || die "install dir not found: $INSTALL_DIR"
[[ -f "$HELPER" ]] || die "wee_release.py not found in $INSTALL_DIR"
command -v curl >/dev/null 2>&1 || die "curl is required"
command -v tar  >/dev/null 2>&1 || die "tar is required"

log "install dir: $INSTALL_DIR"

CURRENT="$("$PYTHON" "$HELPER" installed "$INSTALL_DIR" || true)"
log "installed version: ${CURRENT:-<unknown>}"

LATEST_LINE="$("$PYTHON" "$HELPER" latest "$REPOSITORY" || true)"
[[ -n "$LATEST_LINE" ]] || die "could not determine the latest release from $REPOSITORY"
read -r VERSION TAG ARCHIVE_URL CHECKSUM_URL <<<"$LATEST_LINE"
log "latest release: $VERSION ($TAG)"

if [[ "${WEE_UPDATE_FORCE:-0}" != "1" ]]; then
  if ! "$PYTHON" "$HELPER" newer "$VERSION" "${CURRENT:-}"; then
    log "already up to date — nothing to do"
    exit 0
  fi
fi

WORK="$(mktemp -d)"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

log "downloading $ARCHIVE_URL"
curl -fsSL "$ARCHIVE_URL"  -o "$WORK/api.tar.gz" || die "download failed"
curl -fsSL "$CHECKSUM_URL" -o "$WORK/api.sha256" || die "checksum download failed"

# Verify BEFORE touching the install. An unverifiable download is refused.
"$PYTHON" "$HELPER" verify "$WORK/api.tar.gz" "$WORK/api.sha256" \
  || die "refusing to install: checksum verification failed"
log "checksum verified"

mkdir -p "$WORK/unpacked"
tar -xzf "$WORK/api.tar.gz" -C "$WORK/unpacked"
NEW_ROOT="$(find "$WORK/unpacked" -mindepth 1 -maxdepth 1 -type d | head -1)"
[[ -n "$NEW_ROOT" ]] || die "archive did not contain a top-level directory"
[[ -f "$NEW_ROOT/agent_manager.py" ]] || die "archive does not look like the API (agent_manager.py missing)"

# Preserve anything that is deployment state rather than code.
log "preserving local state"
# Keep credentials, TLS material, and connector configuration outside the
# release archive. rsync runs with --delete, so every state path must also be
# excluded below.
for keep in .env agents.json telegram_config.json certs .task-scheduler .canvas-sessions VERSION; do
  [[ -e "$INSTALL_DIR/$keep" ]] && cp -a "$INSTALL_DIR/$keep" "$WORK/unpacked/.keep-$(basename "$keep")" || true
done

BACKUP="${INSTALL_DIR}.backup-$(date -u +%Y%m%d-%H%M%S)"
log "backing up current install to $BACKUP"
cp -a "$INSTALL_DIR" "$BACKUP"

log "installing $VERSION"
# Copy in place rather than swapping the directory: the path is referenced by
# systemd units and, on some hosts, by symlinks and git worktrees.
rsync -a --delete \
  --exclude '.env' \
  --exclude 'agents.json' \
  --exclude 'telegram_config.json' \
  --exclude 'certs/' \
  --exclude '.task-scheduler' \
  --exclude '.canvas-sessions' \
  --exclude '.venv' --exclude 'venv' \
  "$NEW_ROOT"/ "$INSTALL_DIR"/ 2>/dev/null || {
    # rsync is not guaranteed present; fall back to tar piping.
    (cd "$NEW_ROOT" && tar -cf - .) | (cd "$INSTALL_DIR" && tar -xf -)
  }

printf '%s\n' "$VERSION" > "$INSTALL_DIR/VERSION"

if [[ -f "$INSTALL_DIR/requirements.txt" ]]; then
  VENV_PY="$INSTALL_DIR/.venv/bin/python"
  if [[ -x "$VENV_PY" ]]; then
    log "updating dependencies"
    "$VENV_PY" -m pip install -q -r "$INSTALL_DIR/requirements.txt" || \
      log "WARNING: dependency install failed; services may not start"
    if "$VENV_PY" -c 'import playwright' >/dev/null 2>&1; then
      log "installing Playwright Chromium"
      PLAYWRIGHT_BROWSERS_PATH="$INSTALL_DIR/.playwright-browsers" \
        "$VENV_PY" -m playwright install chromium || \
        log "WARNING: Playwright Chromium install failed; browser fallback may be unavailable"
    fi
  fi
fi

log "restarting services"
for svc in $SERVICES; do
  if systemctl list-unit-files "${svc}.service" >/dev/null 2>&1; then
    sudo systemctl restart "${svc}.service" 2>/dev/null || log "WARNING: could not restart ${svc}"
    if sudo systemctl is-active --quiet "${svc}.service"; then
      log "  ${svc}: active"
    else
      log "  ${svc}: NOT active after restart"
    fi
  fi
done

log "updated to $VERSION (previous install kept at $BACKUP)"
