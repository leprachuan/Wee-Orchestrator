"""
Tests for package-based Linux install and self-update (issues #406 / #411).

The Linux path was git-based: `install-linux.sh` cloned and
`update_orchestrator.sh` ran `git pull`. A *downloaded package* therefore could
not update itself, and nothing verified what had been downloaded. The macOS
client already reads GitHub releases and verifies a published sha256 before
swapping the bundle; `wee_release.py` is the equivalent for the API.

Checksum verification is the security-relevant part, so the cases that must
*refuse* are covered as carefully as the happy path.
"""

import hashlib
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wee_release  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestVersionComparison(unittest.TestCase):
    def test_parses_semver_and_tolerates_a_v_prefix(self):
        self.assertEqual(wee_release.parse_version("1.2.3"), (1, 2, 3))
        self.assertEqual(wee_release.parse_version("v1.2.3"), (1, 2, 3))

    def test_rejects_anything_not_major_minor_patch(self):
        for value in ("1.2", "1.2.3.4", "1.2.x", "", "  ", None, "latest"):
            with self.subTest(value=value):
                self.assertIsNone(wee_release.parse_version(value))

    def test_version_is_extracted_only_from_api_tags(self):
        self.assertEqual(wee_release.version_from_tag("api-v1.4.0"), "1.4.0")
        # A macOS tag must never be treated as an API release.
        self.assertIsNone(wee_release.version_from_tag("macos-v0.8.0"))
        self.assertIsNone(wee_release.version_from_tag("v1.4.0"))
        self.assertIsNone(wee_release.version_from_tag("api-vnightly"))

    def test_ordering_is_numeric_not_lexical(self):
        self.assertTrue(wee_release.is_newer("1.10.0", "1.9.0"))
        self.assertFalse(wee_release.is_newer("1.9.0", "1.10.0"))
        self.assertFalse(wee_release.is_newer("1.2.3", "1.2.3"))

    def test_unparseable_candidate_is_never_newer(self):
        """A stray tag must not be able to trigger an update."""
        self.assertFalse(wee_release.is_newer("garbage", "1.0.0"))
        self.assertFalse(wee_release.is_newer("", "1.0.0"))

    def test_missing_installed_version_allows_recovery(self):
        """An install with no VERSION file must still be updatable."""
        self.assertTrue(wee_release.is_newer("1.0.0", ""))
        self.assertTrue(wee_release.is_newer("1.0.0", "unknown"))


class TestLatestReleaseSelection(unittest.TestCase):
    def _release(self, tag, draft=False, prerelease=False):
        return {"tag_name": tag, "draft": draft, "prerelease": prerelease}

    def test_picks_the_highest_api_version(self):
        found = wee_release.latest_release([
            self._release("api-v1.0.0"),
            self._release("api-v1.10.0"),
            self._release("api-v1.9.0"),
        ])
        self.assertEqual(found["version"], "1.10.0")
        self.assertEqual(found["tag"], "api-v1.10.0")

    def test_ignores_macos_releases_entirely(self):
        """Both live in the same repository, so this matters."""
        found = wee_release.latest_release([
            self._release("macos-v0.8.0"),
            self._release("api-v1.0.0"),
        ])
        self.assertEqual(found["version"], "1.0.0")

    def test_skips_drafts_and_prereleases(self):
        found = wee_release.latest_release([
            self._release("api-v2.0.0", draft=True),
            self._release("api-v1.9.0", prerelease=True),
            self._release("api-v1.0.0"),
        ])
        self.assertEqual(found["version"], "1.0.0")

    def test_ordering_beats_publish_order(self):
        """A late patch on an old line must not look like the newest."""
        found = wee_release.latest_release([
            self._release("api-v2.0.0"),
            self._release("api-v1.0.1"),  # published after, but older
        ])
        self.assertEqual(found["version"], "2.0.0")

    def test_no_api_release_yields_none(self):
        self.assertIsNone(wee_release.latest_release([]))
        self.assertIsNone(wee_release.latest_release([self._release("macos-v0.8.0")]))
        self.assertIsNone(wee_release.latest_release(None))

    def test_malformed_entries_do_not_raise(self):
        found = wee_release.latest_release(
            ["not a dict", None, {}, self._release("api-v1.0.0")]
        )
        self.assertEqual(found["version"], "1.0.0")

    def test_asset_urls_match_the_packaging_contract(self):
        urls = wee_release.asset_urls("owner/repo", "api-v1.2.3", "1.2.3")
        self.assertEqual(
            urls["archive"],
            "https://github.com/owner/repo/releases/download/api-v1.2.3/"
            "Wee-Orchestrator-API-v1.2.3.tar.gz",
        )
        self.assertEqual(urls["checksum"], urls["archive"] + ".sha256")


class TestChecksumVerification(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.archive = Path(self.dir) / "api.tar.gz"
        self.archive.write_bytes(b"pretend tarball contents")
        self.digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()

    def test_accepts_a_matching_digest(self):
        wee_release.verify_archive(self.archive, f"{self.digest}  api.tar.gz")

    def test_digest_is_read_regardless_of_the_filename_column(self):
        """A differing path must not cause a false mismatch."""
        wee_release.verify_archive(self.archive, f"{self.digest}  /some/other/path.tar.gz")

    def test_rejects_a_mismatched_digest(self):
        with self.assertRaises(ValueError) as raised:
            wee_release.verify_archive(self.archive, f"{'0' * 64}  api.tar.gz")
        self.assertIn("mismatch", str(raised.exception))

    def test_refuses_when_no_digest_is_published(self):
        """An unverifiable download must not be installed."""
        for text in ("", "   ", "no digest here", "deadbeef  short.tar.gz"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    wee_release.verify_archive(self.archive, text)

    def test_tampered_archive_fails_against_the_original_digest(self):
        original = f"{self.digest}  api.tar.gz"
        self.archive.write_bytes(b"pretend tarball contents (tampered)")
        with self.assertRaises(ValueError):
            wee_release.verify_archive(self.archive, original)


class TestInstalledVersion(unittest.TestCase):
    def test_reads_and_strips_the_version_file(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "VERSION").write_text("1.4.0\n")
            self.assertEqual(wee_release.installed_version(directory), "1.4.0")

    def test_missing_file_is_empty_not_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(wee_release.installed_version(directory), "")


class TestShellIntegration(unittest.TestCase):
    """The scripts are the delivery mechanism; check they are wired coherently."""

    def _script(self, name):
        path = REPO_ROOT / "scripts" / name
        self.assertTrue(path.exists(), f"missing script: {name}")
        return path.read_text(encoding="utf-8")

    def test_scripts_are_executable(self):
        for name in ("update-api-package.sh", "publish-api-release.sh", "install-linux.sh"):
            path = REPO_ROOT / "scripts" / name
            self.assertTrue(
                path.stat().st_mode & stat.S_IXUSR, f"{name} is not executable"
            )

    def test_updater_verifies_before_replacing_anything(self):
        body = self._script("update-api-package.sh")
        verify_at = body.index('"$HELPER" verify')
        # The backup/copy step must come after verification.
        install_at = body.index("backing up current install")
        self.assertLess(
            verify_at, install_at, "must verify the download before touching the install"
        )
        self.assertIn("refusing to install", body)

    def test_updater_preserves_deployment_state(self):
        body = self._script("update-api-package.sh")
        for keep in (".env", ".task-scheduler", ".canvas-sessions"):
            self.assertIn(keep, body, f"{keep} must survive an update")

    def test_git_updater_hands_off_when_there_is_no_checkout(self):
        body = (REPO_ROOT / "update_orchestrator.sh").read_text(encoding="utf-8")
        self.assertIn('if [ ! -d "$REPO_DIR/.git" ]', body)
        self.assertIn("update-api-package.sh", body)

    def test_installer_defaults_to_package_and_writes_a_version(self):
        body = self._script("install-linux.sh")
        self.assertIn('INSTALL_METHOD="${WEE_INSTALL_METHOD:-package}"', body)
        self.assertIn("install_from_package", body)
        self.assertIn('> "$INSTALL_DIR/VERSION"', body)
        # Verification is what makes an unattended curl|bash install safe.
        self.assertIn("verify", body)

    def test_publisher_refuses_an_archive_that_fails_its_own_checksum(self):
        body = self._script("publish-api-release.sh")
        self.assertIn("wee_release.py\" verify", body)
        self.assertIn("does not match its own checksum", body)
        self.assertIn("api-v", body)


class TestCliSurface(unittest.TestCase):
    def test_newer_exit_codes(self):
        self.assertEqual(wee_release.main(["newer", "1.1.0", "1.0.0"]), 0)
        self.assertEqual(wee_release.main(["newer", "1.0.0", "1.0.0"]), 1)

    def test_verify_reports_failure_via_exit_code(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "a.tar.gz"
            archive.write_bytes(b"x")
            checksum = Path(directory) / "a.sha256"
            checksum.write_text(f"{'0' * 64}  a.tar.gz")
            self.assertEqual(wee_release.main(["verify", str(archive), str(checksum)]), 1)

            checksum.write_text(f"{hashlib.sha256(b'x').hexdigest()}  a.tar.gz")
            self.assertEqual(wee_release.main(["verify", str(archive), str(checksum)]), 0)

    def test_unknown_subcommand_is_not_a_silent_success(self):
        self.assertEqual(wee_release.main(["nonsense"]), 2)


if __name__ == "__main__":
    unittest.main()
