#!/usr/bin/env bash
# Build a reproducible API release archive from a committed Git ref.
# Usage: scripts/package-api-release.sh 1.0.0 [ref] [output-directory]

set -Eeuo pipefail

VERSION="${1:-}"
REF="${2:-HEAD}"
OUTPUT_DIR="${3:-dist}"

die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || \
  die "Usage: $0 MAJOR.MINOR.PATCH [ref] [output-directory]"

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "Run this inside the Wee-Orchestrator Git checkout."
git rev-parse --verify "${REF}^{commit}" >/dev/null 2>&1 || die "Unknown Git ref: $REF"

mkdir -p "$OUTPUT_DIR"
ARCHIVE_NAME="Wee-Orchestrator-API-v${VERSION}.tar.gz"
ARCHIVE_PATH="${OUTPUT_DIR}/${ARCHIVE_NAME}"
CHECKSUM_PATH="${ARCHIVE_PATH}.sha256"
PREFIX="Wee-Orchestrator-API-v${VERSION}/"

git archive --format=tar --prefix="$PREFIX" "$REF" | gzip -n > "$ARCHIVE_PATH"

if command -v shasum >/dev/null 2>&1; then
  shasum -a 256 "$ARCHIVE_PATH" > "$CHECKSUM_PATH"
elif command -v sha256sum >/dev/null 2>&1; then
  sha256sum "$ARCHIVE_PATH" > "$CHECKSUM_PATH"
else
  die "shasum or sha256sum is required to create a release checksum."
fi

printf 'Created %s from %s (%s)\n' "$ARCHIVE_PATH" "$REF" "$(git rev-parse --short "$REF")"
printf 'Created %s\n' "$CHECKSUM_PATH"
