#!/usr/bin/env python3
"""Tests for secret_tool.py — validates the F011 UX decisions.

Covers:
- `set` command with upsert semantics (create + update)
- `add` command as alias for `set`
- `--no-clobber` flag rejects overwrites
- `--value-stdin` reads from stdin
- `get` returns raw value on success, JSON on failure
- Output never contains secret values (except `get`)
- Argument parsing: mutual exclusion, required fields
- `exists()` method on both backends
"""

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
# secret_tool became a package (secret_tool/__init__.py re-exports for
# pre-package callers), but this test calls main()/parse_args() directly and
# patches names like FileBackend that main() references via its OWN module
# globals -- those live in the submodule, not the package's __init__, so the
# patches must target secret_tool.secret_tool, aliased here to keep the rest
# of this file unchanged.
import secret_tool.secret_tool as secret_tool  # noqa: E402


class FakeKeyring:
    """In-memory keyring for testing."""

    def __init__(self):
        self._store: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, name: str, value: str):
        self._store[(service, name)] = value

    def get_password(self, service: str, name: str):
        return self._store.get((service, name))

    def delete_password(self, service: str, name: str):
        self._store.pop((service, name), None)


class TestParseArgs(unittest.TestCase):
    """Argument parsing for the new UX."""

    def test_set_with_value(self):
        args = secret_tool.parse_args(["set", "--name", "k", "--value", "v"])
        self.assertEqual(args.cmd, "set")
        self.assertEqual(args.name, "k")
        self.assertEqual(args.value, "v")
        self.assertFalse(args.value_stdin)
        self.assertFalse(args.no_clobber)
        self.assertEqual(args.backend, "pass")

    def test_set_with_value_stdin(self):
        args = secret_tool.parse_args(["set", "--name", "k", "--value-stdin"])
        self.assertEqual(args.cmd, "set")
        self.assertTrue(args.value_stdin)
        self.assertIsNone(args.value)

    def test_add_is_alias(self):
        args = secret_tool.parse_args(["add", "--name", "k", "--value", "v"])
        self.assertEqual(args.cmd, "add")
        self.assertEqual(args.name, "k")

    def test_set_no_clobber(self):
        args = secret_tool.parse_args(
            ["set", "--name", "k", "--value", "v", "--no-clobber"]
        )
        self.assertTrue(args.no_clobber)

    def test_set_file_backend(self):
        args = secret_tool.parse_args(
            ["set", "--name", "k", "--value", "v", "--backend", "file"]
        )
        self.assertEqual(args.backend, "file")

    def test_value_and_value_stdin_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            secret_tool.parse_args(
                ["set", "--name", "k", "--value", "v", "--value-stdin"]
            )

    def test_set_requires_value_or_stdin(self):
        with self.assertRaises(SystemExit):
            secret_tool.parse_args(["set", "--name", "k"])

    def test_get_command(self):
        args = secret_tool.parse_args(["get", "--name", "k"])
        self.assertEqual(args.cmd, "get")
        self.assertEqual(args.name, "k")

    def test_no_command_returns_none(self):
        args = secret_tool.parse_args([])
        self.assertIsNone(args.cmd)

    def test_list_command(self):
        args = secret_tool.parse_args(["list"])
        self.assertEqual(args.cmd, "list")
        self.assertEqual(args.backend, "pass")
        self.assertFalse(args.json_output)

    def test_list_json_flag(self):
        args = secret_tool.parse_args(["list", "--json"])
        self.assertTrue(args.json_output)

    def test_list_file_backend(self):
        args = secret_tool.parse_args(["list", "--backend", "file"])
        self.assertEqual(args.backend, "file")

    def test_delete_command(self):
        args = secret_tool.parse_args(["delete", "--name", "mykey"])
        self.assertEqual(args.cmd, "delete")
        self.assertEqual(args.name, "mykey")

    def test_delete_requires_name(self):
        with self.assertRaises(SystemExit):
            secret_tool.parse_args(["delete"])


class TestKeyringBackendUX(unittest.TestCase):
    """KeyringBackend with mocked python-keyring."""

    def setUp(self):
        self.fake_kr = FakeKeyring()
        patcher = patch.object(secret_tool, "_keyring_vault_module", None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.backend = secret_tool.KeyringBackend.__new__(secret_tool.KeyringBackend)
        self.backend._use_keyring = True
        self.backend._keyring = self.fake_kr

    def test_set_creates_new(self):
        res = self.backend.set("mykey", "myval")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["action"], "created")
        self.assertEqual(res["name"], "mykey")

    def test_set_updates_existing(self):
        self.backend.set("mykey", "v1")
        res = self.backend.set("mykey", "v2")
        self.assertEqual(res["action"], "updated")

    def test_add_is_alias_for_set(self):
        res = self.backend.add("k", "v")
        self.assertEqual(res["status"], "success")
        self.assertIn(res["action"], ("created", "updated"))

    def test_exists_false(self):
        self.assertFalse(self.backend.exists("nonexistent"))

    def test_exists_true(self):
        self.backend.set("k", "v")
        self.assertTrue(self.backend.exists("k"))

    def test_get_success(self):
        self.backend.set("k", "secret123")
        res = self.backend.get("k")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["credential"], "secret123")

    def test_get_not_found(self):
        res = self.backend.get("missing")
        self.assertEqual(res["status"], "failure")

    def test_list_unsupported(self):
        res = self.backend.list()
        self.assertEqual(res["status"], "error")
        self.assertIn("does not support listing", res["message"])

    def test_delete_success(self):
        self.backend.set("k", "v")
        res = self.backend.delete("k")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["action"], "deleted")
        self.assertFalse(self.backend.exists("k"))

    def test_delete_not_found(self):
        res = self.backend.delete("nonexistent")
        self.assertEqual(res["status"], "error")
        self.assertIn("not found", res["message"])


class TestFileBackendUX(unittest.TestCase):
    """FileBackend with mocked keyring + cryptography."""

    def setUp(self):
        import tempfile

        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        storage = Path(self._tmpdir.name) / "secrets.json.enc"

        self.fake_kr = FakeKeyring()
        # Pre-seed a Fernet key in the fake keyring

        try:
            from cryptography.fernet import Fernet

            self._fernet_cls = Fernet
        except ImportError:
            self.skipTest("cryptography not installed")

        key = Fernet.generate_key()
        self.fake_kr.set_password("secret-tool-file-key", "testuser", key.decode())

        self.backend = secret_tool.FileBackend.__new__(secret_tool.FileBackend)
        self.backend.storage_path = storage
        self.backend.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.backend._keyring = self.fake_kr
        self.backend._Fernet = Fernet

        # Patch os.getlogin to return "testuser"
        self._login_patcher = patch("os.getlogin", return_value="testuser")
        self._login_patcher.start()
        self.addCleanup(self._login_patcher.stop)

    def test_set_creates_new(self):
        res = self.backend.set("filekey", "fileval")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["action"], "created")

    def test_set_updates_existing(self):
        self.backend.set("filekey", "v1")
        res = self.backend.set("filekey", "v2")
        self.assertEqual(res["action"], "updated")

    def test_exists(self):
        self.assertFalse(self.backend.exists("nope"))
        self.backend.set("yep", "v")
        self.assertTrue(self.backend.exists("yep"))

    def test_get_round_trip(self):
        self.backend.set("rt", "roundtrip_val")
        res = self.backend.get("rt")
        self.assertEqual(res["credential"], "roundtrip_val")

    def test_add_alias(self):
        res = self.backend.add("a", "b")
        self.assertEqual(res["status"], "success")

    def test_list_empty(self):
        res = self.backend.list()
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["names"], [])

    def test_list_with_secrets(self):
        self.backend.set("beta", "v1")
        self.backend.set("alpha", "v2")
        res = self.backend.list()
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["names"], ["alpha", "beta"])  # sorted

    def test_delete_success(self):
        self.backend.set("dkey", "dval")
        res = self.backend.delete("dkey")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["action"], "deleted")
        self.assertFalse(self.backend.exists("dkey"))

    def test_delete_not_found(self):
        res = self.backend.delete("ghost")
        self.assertEqual(res["status"], "error")
        self.assertIn("not found", res["message"])

    def test_delete_then_get_fails(self):
        self.backend.set("tmp", "val")
        self.backend.delete("tmp")
        res = self.backend.get("tmp")
        self.assertEqual(res["status"], "failure")


class TestMainFunction(unittest.TestCase):
    """Integration tests for main() — validates CLI UX end-to-end."""

    def _mock_backend(self):
        """Return a mock backend with predictable behavior."""
        be = MagicMock()
        be.exists.return_value = False
        be.set.return_value = {
            "status": "success",
            "action": "created",
            "name": "k",
        }
        be.get.return_value = {"status": "success", "credential": "secret_val"}
        be.list.return_value = {"status": "success", "names": ["a", "b"]}
        be.delete.return_value = {
            "status": "success",
            "action": "deleted",
            "name": "k",
        }
        return be

    @patch("secret_tool.secret_tool.PassBackend")
    def test_set_prints_json_without_value(self, mock_cls):
        be = self._mock_backend()
        mock_cls.return_value = be
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = secret_tool.main(["set", "--name", "k", "--value", "mysecret"])
        self.assertEqual(rc, 0)
        output = json.loads(out.getvalue())
        self.assertEqual(output["status"], "success")
        self.assertEqual(output["action"], "created")
        # Secret value must NOT appear in output
        self.assertNotIn("credential", output)
        self.assertNotIn("mysecret", out.getvalue())

    @patch("secret_tool.secret_tool.PassBackend")
    def test_set_no_clobber_rejects(self, mock_cls):
        be = self._mock_backend()
        be.exists.return_value = True
        mock_cls.return_value = be
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = secret_tool.main(
                ["set", "--name", "k", "--value", "v", "--no-clobber"]
            )
        self.assertEqual(rc, 1)
        output = json.loads(out.getvalue())
        self.assertEqual(output["status"], "error")
        self.assertIn("already exists", output["message"])

    @patch("secret_tool.secret_tool.PassBackend")
    def test_set_no_clobber_allows_new(self, mock_cls):
        be = self._mock_backend()
        be.exists.return_value = False
        mock_cls.return_value = be
        with patch("sys.stdout", new_callable=io.StringIO) as _:
            rc = secret_tool.main(
                ["set", "--name", "k", "--value", "v", "--no-clobber"]
            )
        self.assertEqual(rc, 0)

    @patch("secret_tool.secret_tool.PassBackend")
    def test_add_works_same_as_set(self, mock_cls):
        be = self._mock_backend()
        mock_cls.return_value = be
        with patch("sys.stdout", new_callable=io.StringIO) as _:
            rc = secret_tool.main(["add", "--name", "k", "--value", "v"])
        self.assertEqual(rc, 0)
        be.set.assert_called_once_with("k", "v")

    @patch("secret_tool.secret_tool.PassBackend")
    def test_get_prints_raw_value(self, mock_cls):
        be = self._mock_backend()
        mock_cls.return_value = be
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = secret_tool.main(["get", "--name", "k"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue().strip(), "secret_val")

    @patch("secret_tool.secret_tool.PassBackend")
    def test_get_not_found(self, mock_cls):
        be = self._mock_backend()
        be.get.return_value = {
            "status": "failure",
            "message": "not found",
            "credential": None,
        }
        mock_cls.return_value = be
        with patch("sys.stdout", new_callable=io.StringIO) as _:
            rc = secret_tool.main(["get", "--name", "missing"])
        self.assertEqual(rc, 1)

    @patch("secret_tool.secret_tool.PassBackend")
    def test_value_stdin(self, mock_cls):
        be = self._mock_backend()
        mock_cls.return_value = be
        with (
            patch("sys.stdin", io.StringIO("stdin_secret\n")),
            patch("sys.stdout", new_callable=io.StringIO) as _,
        ):
            rc = secret_tool.main(["set", "--name", "k", "--value-stdin"])
        self.assertEqual(rc, 0)
        be.set.assert_called_once_with("k", "stdin_secret")

    def test_no_command_returns_2(self):
        with patch("sys.stdout", new_callable=io.StringIO):
            rc = secret_tool.main([])
        self.assertEqual(rc, 2)

    # --- list command ---

    @patch("secret_tool.secret_tool.PassBackend")
    def test_list_prints_names(self, mock_cls):
        be = self._mock_backend()
        mock_cls.return_value = be
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = secret_tool.main(["list"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue().strip().split("\n"), ["a", "b"])

    @patch("secret_tool.secret_tool.PassBackend")
    def test_list_json(self, mock_cls):
        be = self._mock_backend()
        mock_cls.return_value = be
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = secret_tool.main(["list", "--json"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out.getvalue()), ["a", "b"])

    @patch("secret_tool.secret_tool.PassBackend")
    def test_list_empty(self, mock_cls):
        be = self._mock_backend()
        be.list.return_value = {"status": "success", "names": []}
        mock_cls.return_value = be
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = secret_tool.main(["list"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue().strip(), "")

    @patch("secret_tool.secret_tool.PassBackend")
    def test_list_empty_json(self, mock_cls):
        be = self._mock_backend()
        be.list.return_value = {"status": "success", "names": []}
        mock_cls.return_value = be
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = secret_tool.main(["list", "--json"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out.getvalue()), [])

    @patch("secret_tool.secret_tool.PassBackend")
    def test_list_unsupported_backend(self, mock_cls):
        be = self._mock_backend()
        be.list.return_value = {
            "status": "error",
            "message": "listing not supported",
        }
        mock_cls.return_value = be
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = secret_tool.main(["list"])
        self.assertEqual(rc, 1)
        output = json.loads(out.getvalue())
        self.assertEqual(output["status"], "error")

    # --- delete command ---

    @patch("secret_tool.secret_tool.PassBackend")
    def test_delete_success(self, mock_cls):
        be = self._mock_backend()
        mock_cls.return_value = be
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = secret_tool.main(["delete", "--name", "k"])
        self.assertEqual(rc, 0)
        output = json.loads(out.getvalue())
        self.assertEqual(output["status"], "success")
        self.assertEqual(output["action"], "deleted")
        be.delete.assert_called_once_with("k")

    @patch("secret_tool.secret_tool.PassBackend")
    def test_delete_not_found(self, mock_cls):
        be = self._mock_backend()
        be.delete.return_value = {
            "status": "error",
            "message": "Secret 'missing' not found",
        }
        mock_cls.return_value = be
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = secret_tool.main(["delete", "--name", "missing"])
        self.assertEqual(rc, 1)
        output = json.loads(out.getvalue())
        self.assertEqual(output["status"], "error")
        self.assertIn("not found", output["message"])


if __name__ == "__main__":
    unittest.main()
