"""Release resolution and verification for package-based Linux installs.

Issues #406 / #411. The Linux path has been git-based: `install-linux.sh` clones
and `update_orchestrator.sh` runs `git pull`. That works for a developer
checkout, but it means a *downloaded package* cannot update itself, and nothing
verifies what was downloaded.

The macOS client already does this properly — it reads GitHub releases, verifies
a published sha256, then swaps the bundle. This module is the equivalent for the
API, kept in Python rather than shell so the version comparison and checksum
verification are unit-testable; the shell scripts are thin wrappers around it.

Release contract, matching `scripts/package-api-release.sh`:

    tag      api-vMAJOR.MINOR.PATCH
    asset    Wee-Orchestrator-API-vMAJOR.MINOR.PATCH.tar.gz
    checksum Wee-Orchestrator-API-vMAJOR.MINOR.PATCH.tar.gz.sha256
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Optional

DEFAULT_REPOSITORY = "leprachuan/Wee-Orchestrator"
TAG_PREFIX = "api-v"
ASSET_TEMPLATE = "Wee-Orchestrator-API-v{version}.tar.gz"
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def parse_version(value: str) -> Optional[tuple[int, int, int]]:
    """Return a comparable version tuple, or None if it isn't MAJOR.MINOR.PATCH.

    Deliberately strict: a tag we cannot parse must not be treated as newer than
    the running version, or a stray tag could trigger a bogus update.
    """
    match = _VERSION_RE.match((value or "").strip().lstrip("v"))
    if not match:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def version_from_tag(tag: str) -> Optional[str]:
    """Extract `1.2.3` from `api-v1.2.3`, ignoring unrelated tags."""
    text = (tag or "").strip()
    if not text.startswith(TAG_PREFIX):
        return None
    candidate = text[len(TAG_PREFIX):]
    return candidate if parse_version(candidate) else None


def is_newer(candidate: str, current: str) -> bool:
    """Whether `candidate` is a strictly newer release than `current`.

    An unparseable candidate is never newer. An unparseable *current* is treated
    as "unknown", and any valid candidate counts as newer — otherwise an install
    with a missing VERSION file could never recover.
    """
    new = parse_version(candidate)
    if new is None:
        return False
    old = parse_version(current)
    if old is None:
        return True
    return new > old


def latest_release(releases: Iterable[dict[str, Any]]) -> Optional[dict[str, str]]:
    """Pick the newest `api-v*` release from a GitHub releases payload.

    Drafts and prereleases are skipped. Ordering is by parsed version rather than
    publish date, so a late-published patch for an older line cannot masquerade
    as the newest.
    """
    best: Optional[tuple[tuple[int, int, int], dict[str, str]]] = None
    for release in releases or []:
        if not isinstance(release, dict):
            continue
        if release.get("draft") or release.get("prerelease"):
            continue
        version = version_from_tag(release.get("tag_name") or "")
        if not version:
            continue
        parsed = parse_version(version)
        if parsed is None:
            continue
        if best is None or parsed > best[0]:
            best = (parsed, {"version": version, "tag": release["tag_name"]})
    return best[1] if best else None


def asset_urls(repository: str, tag: str, version: str) -> dict[str, str]:
    """Download URLs for a release's archive and its checksum."""
    base = f"https://github.com/{repository}/releases/download/{tag}"
    asset = ASSET_TEMPLATE.format(version=version)
    return {"archive": f"{base}/{asset}", "checksum": f"{base}/{asset}.sha256"}


def expected_sha256(checksum_text: str) -> Optional[str]:
    """Read the digest out of a `shasum -a 256` line.

    The file is `<64 hex>  <filename>`; take only the digest so a differing path
    in the checksum file does not cause a false mismatch.
    """
    for line in (checksum_text or "").splitlines():
        token = line.strip().split()
        if token and re.fullmatch(r"[0-9a-fA-F]{64}", token[0]):
            return token[0].lower()
    return None


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_archive(path: str | Path, checksum_text: str) -> None:
    """Raise unless `path` matches the published digest.

    Refusing on a *missing* digest is deliberate: an unverifiable download must
    not be installed just because no checksum was published.
    """
    expected = expected_sha256(checksum_text)
    if not expected:
        raise ValueError("no sha256 digest found in the published checksum file")
    actual = file_sha256(path)
    if actual != expected:
        raise ValueError(f"checksum mismatch: expected {expected}, got {actual}")


def installed_version(install_dir: str | Path) -> str:
    """Read the VERSION file written at install time; '' when absent."""
    try:
        return (Path(install_dir) / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def fetch_latest(repository: str = DEFAULT_REPOSITORY, timeout: float = 20.0):
    """Query GitHub for the newest api-v release. Returns None on any failure."""
    url = f"https://api.github.com/repos/{repository}/releases?per_page=50"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "wee-release",
            # GitHub serves this listing with `max-age=60, s-maxage=60`, so a
            # check within a minute of publishing can return the *previous*
            # release. Observed: publishing api-v1.1.0 then resolving
            # immediately returned 1.0.0. Ask for a fresh listing so an install
            # run right after a release does not fetch stale data.
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return latest_release(json.loads(response.read().decode("utf-8", "replace")))
    except Exception:
        return None


def main(argv: Optional[list[str]] = None) -> int:
    """CLI used by the shell scripts.

    Subcommands:
      latest [repo]                     print "<version> <tag> <archive> <checksum>"
      installed <dir>                   print the installed version
      newer <candidate> <current>       exit 0 when candidate is newer
      verify <archive> <checksum-file>  exit 0 when the digest matches
    """
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(main.__doc__)
        return 2
    command, rest = args[0], args[1:]

    if command == "latest":
        repository = rest[0] if rest else DEFAULT_REPOSITORY
        found = fetch_latest(repository)
        if not found:
            return 1
        urls = asset_urls(repository, found["tag"], found["version"])
        print(f"{found['version']} {found['tag']} {urls['archive']} {urls['checksum']}")
        return 0

    if command == "installed":
        print(installed_version(rest[0] if rest else "."))
        return 0

    if command == "newer":
        if len(rest) < 2:
            return 2
        return 0 if is_newer(rest[0], rest[1]) else 1

    if command == "verify":
        if len(rest) < 2:
            return 2
        try:
            verify_archive(rest[0], Path(rest[1]).read_text(encoding="utf-8"))
        except Exception as error:
            print(f"verification failed: {error}", file=sys.stderr)
            return 1
        print("checksum ok")
        return 0

    print(f"unknown subcommand: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
