#!/usr/bin/env bash
# Publish an API release so package installs can find and verify it.
# Mirrors scripts/publish-macos-release.sh (issue #411).
#
# Usage: scripts/publish-api-release.sh MAJOR.MINOR.PATCH [ref] [notes-file]

set -Eeuo pipefail

VERSION="${1:-}"
REF="${2:-HEAD}"
NOTES_PATH="${3:-}"

REPOSITORY="${WEE_RELEASE_REPOSITORY:-leprachuan/Wee-Orchestrator}"
TARGET="${WEE_RELEASE_TARGET:-main}"
OUTPUT_DIR="${WEE_RELEASE_OUTPUT_DIR:-dist}"
TAG="api-v${VERSION}"
TITLE="Wee Orchestrator API v${VERSION}"

die() { printf 'Error: %s\n' "$*" >&2; exit 1; }

[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "Usage: $0 MAJOR.MINOR.PATCH [ref] [notes-file]"
command -v gh >/dev/null 2>&1 || die "GitHub CLI (gh) is required."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Build the archive + checksum from a committed ref.
"$SCRIPT_DIR/package-api-release.sh" "$VERSION" "$REF" "$OUTPUT_DIR"

ARCHIVE_PATH="${OUTPUT_DIR}/Wee-Orchestrator-API-v${VERSION}.tar.gz"
CHECKSUM_PATH="${ARCHIVE_PATH}.sha256"
[[ -f "$ARCHIVE_PATH"  ]] || die "archive not produced: $ARCHIVE_PATH"
[[ -f "$CHECKSUM_PATH" ]] || die "checksum not produced: $CHECKSUM_PATH"

# The updater refuses an archive it cannot verify, so fail here rather than
# publishing something that can never be installed.
python3 "$SCRIPT_DIR/../wee_release.py" verify "$ARCHIVE_PATH" "$CHECKSUM_PATH" \
  || die "the archive does not match its own checksum"

if [[ -z "$NOTES_PATH" ]]; then
  NOTES_PATH="$(mktemp)"
  printf 'Wee Orchestrator API v%s\n\nBuilt from %s (%s).\n' \
    "$VERSION" "$REF" "$(git rev-parse --short "$REF")" > "$NOTES_PATH"
fi

if gh release view "$TAG" --repo "$REPOSITORY" >/dev/null 2>&1; then
  gh release upload "$TAG" "$ARCHIVE_PATH" "$CHECKSUM_PATH" --clobber --repo "$REPOSITORY"
  gh release edit   "$TAG" --repo "$REPOSITORY" --title "$TITLE" --notes-file "$NOTES_PATH"
else
  gh release create "$TAG" "$ARCHIVE_PATH" "$CHECKSUM_PATH" \
    --repo "$REPOSITORY" --target "$TARGET" --title "$TITLE" --notes-file "$NOTES_PATH"
fi

printf 'Published %s to %s\n' "$TAG" "$REPOSITORY"
printf 'Installs will resolve it via: python3 wee_release.py latest %s\n' "$REPOSITORY"
