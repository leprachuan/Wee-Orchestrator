#!/usr/bin/env python3
"""
Lightweight secret-tool wrapper for wee-dev
Supports:
  secret-tool set --name <name> --value <value> [--no-clobber] [--backend pass|keyring|file]
  secret-tool set --name <name> --value-stdin   [--no-clobber] [--backend pass|keyring|file]
  secret-tool add ...   (alias for set)
  secret-tool get --name <name> [--backend pass|keyring|file]

Default backend: pass (GPG-encrypted via password-store — no GNOME session required).
Keyring backend wraps GNOME libsecret (session-dependent).
File backend encrypts with Fernet but derives key from keyring (also session-dependent).

Pass store location: PASSWORD_STORE_DIR env var or /opt/pass-store/
Secrets are stored under the wee/ namespace: wee/<name>.gpg
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Try importing existing keyring-vault helper
_keyring_vault_module = None
try:
    sys.path.insert(0, "/opt/foster-skills/keyring-vault/copilot")
    import keyring_vault as _kr

    _keyring_vault_module = _kr
except Exception:
    _keyring_vault_module = None


class KeyringBackend:
    """Backend that uses the repository's keyring_vault (secret-tool) if available."""

    def __init__(self):
        if _keyring_vault_module is None:
            # Try python-keyring as fallback
            try:
                import keyring

                self._use_keyring = True
                self._keyring = keyring
            except Exception:
                raise RuntimeError(
                    "No keyring backend available: "
                    "install libsecret-tools or python-keyring"
                )
        else:
            self._use_keyring = False
            self._vault = _keyring_vault_module.KeyringVault()

    def exists(self, name: str) -> bool:
        """Check if a secret exists without returning its value."""
        result = self.get(name)
        return result.get("status") == "success"

    def set(self, name: str, value: str) -> dict:
        existed = self.exists(name)
        if self._use_keyring:
            self._keyring.set_password("secret-tool", name, value)
            action = "updated" if existed else "created"
            return {"status": "success", "action": action, "name": name}
        else:
            self._vault.store("secret-tool", name, value, label=f"secret-tool {name}")
            action = "updated" if existed else "created"
            return {"status": "success", "action": action, "name": name}

    # Keep add() as an internal alias for set()
    def add(self, name: str, value: str) -> dict:
        return self.set(name, value)

    def get(self, name: str) -> dict:
        if self._use_keyring:
            val = self._keyring.get_password("secret-tool", name)
            if val is None:
                return {"status": "failure", "message": "not found", "credential": None}
            return {"status": "success", "credential": val}
        else:
            return self._vault.retrieve("secret-tool", name)

    def list(self) -> dict:
        return {
            "status": "error",
            "message": (
                "The keyring backend does not support listing secret names. "
                "Use --backend file to enable listing."
            ),
        }

    def delete(self, name: str) -> dict:
        if not self.exists(name):
            return {
                "status": "error",
                "message": f"Secret '{name}' not found",
            }
        if self._use_keyring:
            self._keyring.delete_password("secret-tool", name)
        else:
            self._vault.delete("secret-tool", name)
        return {"status": "success", "action": "deleted", "name": name}


# ---------------------------------------------------------------------------
# PassBackend — GPG-encrypted via password-store (pass). No GNOME session needed.
# ---------------------------------------------------------------------------

_PASS_STORE_DIR = os.environ.get("PASSWORD_STORE_DIR", "/opt/pass-store")
_PASS_NAMESPACE = "wee"  # secrets live at wee/<name>.gpg inside the store


class PassBackend:
    """GPG-encrypted secret store using 'pass' (password-store).

    Unlike KeyringBackend and FileBackend this requires NO active GNOME session
    and works reliably from systemd services, cron jobs, and headless scripts.

    Prerequisites:
      - pass installed (apt install pass)
      - GPG key generated without a passphrase (for unattended decryption)
      - Store initialised: PASSWORD_STORE_DIR=/opt/pass-store pass init <KEY_ID>
      - Key trusted: echo '<FINGERPRINT>:6:' | gpg --import-ownertrust

    Secrets are stored as wee/<name> inside the pass store.
    """

    def __init__(self, store_dir: str | None = None):
        self.store_dir = store_dir or _PASS_STORE_DIR
        if not shutil.which("pass"):
            raise RuntimeError("'pass' is not installed — run: apt install pass")
        if not os.path.exists(self.store_dir):
            raise RuntimeError(
                f"Pass store not found at {self.store_dir}. "
                f"Initialise it with: PASSWORD_STORE_DIR={self.store_dir} pass init <GPG_KEY_ID>"
            )

    def _run_pass(self, *args, input_data: str | None = None) -> subprocess.CompletedProcess:
        env = {**os.environ, "PASSWORD_STORE_DIR": self.store_dir}
        return subprocess.run(
            ["pass", *args],
            capture_output=True,
            text=True,
            input=input_data,
            env=env,
        )

    def _pass_path(self, name: str) -> str:
        return f"{_PASS_NAMESPACE}/{name}"

    def exists(self, name: str) -> bool:
        gpg_file = Path(self.store_dir) / _PASS_NAMESPACE / f"{name}.gpg"
        return gpg_file.exists()

    def set(self, name: str, value: str) -> dict:
        existed = self.exists(name)
        result = self._run_pass(
            "insert", "--force", "--multiline", self._pass_path(name),
            input_data=value,
        )
        if result.returncode != 0:
            msg = result.stderr.strip() or result.stdout.strip() or "pass insert failed"
            return {"status": "error", "message": msg}
        action = "updated" if existed else "created"
        return {"status": "success", "action": action, "name": name}

    def add(self, name: str, value: str) -> dict:
        return self.set(name, value)

    def get(self, name: str) -> dict:
        result = self._run_pass("show", self._pass_path(name))
        if result.returncode != 0:
            return {"status": "failure", "message": "not found", "credential": None}
        return {"status": "success", "credential": result.stdout.rstrip("\n")}

    def list(self) -> dict:
        ns_dir = Path(self.store_dir) / _PASS_NAMESPACE
        if not ns_dir.exists():
            return {"status": "success", "names": []}
        names = sorted(p.stem for p in ns_dir.glob("*.gpg"))
        return {"status": "success", "names": names}

    def delete(self, name: str) -> dict:
        if not self.exists(name):
            return {"status": "error", "message": f"Secret '{name}' not found"}
        result = self._run_pass("rm", "--force", self._pass_path(name))
        if result.returncode != 0:
            msg = result.stderr.strip() or "pass rm failed"
            return {"status": "error", "message": msg}
        return {"status": "success", "action": "deleted", "name": name}


class FileBackend:
    """File backend storing an encrypted JSON using Fernet. The symmetric key
    is stored in system keyring under service 'secret-tool-file-key'."""

    def __init__(self, storage_path: str | None = None):
        self.storage_path = Path(
            storage_path
            or Path.home() / ".local" / "share" / "secret_tool" / "secrets.json.enc"
        )
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            import keyring
            from cryptography.fernet import Fernet

            self._keyring = keyring
            self._Fernet = Fernet
        except Exception as e:
            raise RuntimeError(
                "File backend requires 'keyring' and 'cryptography' packages: " + str(e)
            )

    def _get_or_create_key(self) -> bytes:
        key = self._keyring.get_password("secret-tool-file-key", os.environ.get("USER", "root"))
        if key:
            return key.encode()
        k = self._Fernet.generate_key()
        self._keyring.set_password("secret-tool-file-key", os.environ.get("USER", "root"), k.decode())
        return k

    def _load_store(self, fernet: object) -> dict:
        if not self.storage_path.exists():
            return {}
        data = self.storage_path.read_bytes()
        try:
            dec = fernet.decrypt(data)
            return json.loads(dec.decode())
        except Exception:
            return {}

    def _save_store(self, fernet: object, store: dict) -> None:
        data = json.dumps(store, indent=2).encode()
        enc = fernet.encrypt(data)
        self.storage_path.write_bytes(enc)

    def exists(self, name: str) -> bool:
        """Check if a secret exists without returning its value."""
        key = self._get_or_create_key()
        f = self._Fernet(key)
        store = self._load_store(f)
        return name in store

    def set(self, name: str, value: str) -> dict:
        key = self._get_or_create_key()
        f = self._Fernet(key)
        store = self._load_store(f)
        existed = name in store
        store[name] = value
        self._save_store(f, store)
        action = "updated" if existed else "created"
        return {"status": "success", "action": action, "name": name}

    # Keep add() as an internal alias for set()
    def add(self, name: str, value: str) -> dict:
        return self.set(name, value)

    def get(self, name: str) -> dict:
        key = self._get_or_create_key()
        f = self._Fernet(key)
        store = self._load_store(f)
        if name not in store:
            return {"status": "failure", "message": "not found", "credential": None}
        return {"status": "success", "credential": store[name]}

    def list(self) -> dict:
        key = self._get_or_create_key()
        f = self._Fernet(key)
        store = self._load_store(f)
        return {"status": "success", "names": sorted(store.keys())}

    def delete(self, name: str) -> dict:
        key = self._get_or_create_key()
        f = self._Fernet(key)
        store = self._load_store(f)
        if name not in store:
            return {
                "status": "error",
                "message": f"Secret '{name}' not found",
            }
        del store[name]
        self._save_store(f, store)
        return {"status": "success", "action": "deleted", "name": name}


def _build_set_parser(subparser, name: str, help_text: str):
    """Build a 'set' or 'add' subcommand parser with shared arguments."""
    p = subparser.add_parser(name, help=help_text)
    p.add_argument("--name", required=True, help="Secret name")
    value_group = p.add_mutually_exclusive_group(required=True)
    value_group.add_argument(
        "--value", help="Secret value (caution: visible in shell history)"
    )
    value_group.add_argument(
        "--value-stdin",
        action="store_true",
        default=False,
        help="Read secret value from stdin (safer — avoids shell history)",
    )
    p.add_argument(
        "--no-clobber",
        action="store_true",
        default=False,
        help="Fail if the secret already exists instead of overwriting",
    )
    p.add_argument("--backend", choices=["pass", "keyring", "file"], default="pass")
    return p


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="secret-tool",
        description="Secret storage helper (wee-dev)",
    )
    sub = parser.add_subparsers(dest="cmd")

    _build_set_parser(sub, "set", "Store a secret (create or update)")
    _build_set_parser(sub, "add", "Alias for 'set' — store a secret")

    p_get = sub.add_parser("get", help="Retrieve a secret")
    p_get.add_argument("--name", required=True, help="Secret name")
    p_get.add_argument("--backend", choices=["pass", "keyring", "file"], default="pass")

    p_list = sub.add_parser("list", help="List stored secret names (no values)")
    p_list.add_argument("--backend", choices=["pass", "keyring", "file"], default="pass")
    p_list.add_argument(
        "--json",
        action="store_true",
        default=False,
        dest="json_output",
        help="Output names as a JSON array",
    )

    p_del = sub.add_parser("delete", help="Delete a secret")
    p_del.add_argument("--name", required=True, help="Secret name to delete")
    p_del.add_argument("--backend", choices=["pass", "keyring", "file"], default="pass")

    sub.add_parser("status", help="Check if the secret store is accessible")
    sub.add_parser("unlock", help="Unlock the keyring (password from stdin)")

    return parser.parse_args(argv)


def _resolve_value(args) -> str:
    """Return the secret value from --value or --value-stdin."""
    if getattr(args, "value_stdin", False):
        value = sys.stdin.readline().rstrip("\n")
        if not value:
            print(
                json.dumps({"status": "error", "message": "No value received on stdin"})
            )
            sys.exit(1)
        return value
    return args.value



def _check_keyring_status() -> dict:
    """Check if the system keyring / secret store is accessible."""
    # Strategy 1: secretstorage (D-Bus Secret Service API)
    try:
        import secretstorage
        conn = secretstorage.dbus_init()
        coll = secretstorage.get_default_collection(conn)
        if coll.is_locked():
            return {"status": "locked", "backend": "gnome-keyring",
                    "message": "GNOME Keyring is locked"}
        return {"status": "unlocked", "backend": "gnome-keyring"}
    except Exception:
        pass

    # Strategy 2: python-keyring probe
    try:
        import keyring
        keyring.get_password("secret-tool-status-probe", "__probe__")
        return {"status": "unlocked", "backend": "python-keyring"}
    except Exception as exc:
        err = str(exc).lower()
        if any(kw in err for kw in ("locked", "prompt", "dismissed")):
            return {"status": "locked", "backend": "python-keyring",
                    "message": "Keyring is locked"}
        if "no recommended backend" in err:
            return {"status": "unavailable",
                    "message": "No keyring backend available"}

    # Strategy 3: secret-tool CLI probe (timeout = locked)
    try:
        proc = subprocess.run(
            ["secret-tool", "lookup", "wee-status-probe", "test"],
            capture_output=True, text=True, timeout=3,
        )
        return {"status": "unlocked", "backend": "secret-tool"}
    except subprocess.TimeoutExpired:
        return {"status": "locked", "backend": "secret-tool",
                "message": "secret-tool timed out (keyring likely locked)"}
    except FileNotFoundError:
        pass

    return {"status": "unavailable",
            "message": "No secret store backend detected"}


def _find_gnome_keyring_daemon():
    path = shutil.which("gnome-keyring-daemon")
    if path:
        return path
    for candidate in ("/usr/bin/gnome-keyring-daemon",
                      "/usr/local/bin/gnome-keyring-daemon"):
        if os.path.isfile(candidate):
            return candidate
    return None


def _unlock_keyring(password: str) -> dict:
    """Attempt to unlock the system keyring with *password*."""
    if not password:
        return {"status": "error", "message": "Password is required"}

    # Strategy 1: gnome-keyring-daemon --unlock
    daemon = _find_gnome_keyring_daemon()
    if daemon:
        try:
            proc = subprocess.run(
                [daemon, "--unlock"],
                input=password.encode(),
                capture_output=True,
                timeout=10,
            )
            if proc.returncode == 0:
                return {"status": "success", "method": "gnome-keyring-daemon"}
        except Exception:
            pass

    # Strategy 2: secretstorage unlock
    try:
        import secretstorage
        conn = secretstorage.dbus_init()
        coll = secretstorage.get_default_collection(conn)
        if coll.is_locked():
            coll.unlock()
            if not coll.is_locked():
                return {"status": "success", "method": "secretstorage"}
            return {"status": "error",
                    "message": "Unlock requires interactive prompt"}
        return {"status": "success", "message": "Keyring was already unlocked"}
    except Exception:
        pass

    return {
        "status": "error",
        "message": (
            "Could not unlock keyring automatically. "
            "Try: echo PASSWORD | gnome-keyring-daemon --unlock via SSH, "
            "or log in to the desktop session."
        ),
    }

def main(argv=None):
    args = parse_args(argv)
    if args.cmd is None:
        print(
            "Usage: secret-tool {set|add|get|list|delete|status|unlock} --name NAME "
            "[--value VALUE | --value-stdin] [--backend pass|keyring|file]"
        )
        return 2

    # Handle status/unlock before backend init (they don't need a backend)
    if args.cmd == "status":
        res = _check_keyring_status()
        print(json.dumps(res))
        return 0 if res.get("status") == "unlocked" else 1

    if args.cmd == "unlock":
        password = sys.stdin.readline().rstrip("\n")
        res = _unlock_keyring(password)
        print(json.dumps(res))
        return 0 if res.get("status") == "success" else 1

    backend = None
    try:
        if args.backend == "pass":
            backend = PassBackend()
        elif args.backend == "keyring":
            backend = KeyringBackend()
        else:
            backend = FileBackend()
    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}))
        return 1

    if args.cmd in ("set", "add"):
        value = _resolve_value(args)
        if args.no_clobber and backend.exists(args.name):
            msg = (
                f"Secret '{args.name}' already exists "
                f"(omit --no-clobber to overwrite)"
            )
            print(json.dumps({"status": "error", "message": msg}))
            return 1
        res = backend.set(args.name, value)
        # Never include the secret value in output
        print(json.dumps({k: v for k, v in res.items() if k != "credential"}))
        return 0 if res.get("status") == "success" else 1
    elif args.cmd == "get":
        res = backend.get(args.name)
        if res.get("status") == "success":
            # Raw value to stdout for shell capture
            print(res.get("credential"))
            return 0
        else:
            print(json.dumps(res))
            return 1
    elif args.cmd == "list":
        res = backend.list()
        if res.get("status") != "success":
            print(json.dumps(res))
            return 1
        names = res.get("names", [])
        if getattr(args, "json_output", False):
            print(json.dumps(names))
        else:
            for n in names:
                print(n)
        return 0
    elif args.cmd == "delete":
        res = backend.delete(args.name)
        print(json.dumps(res))
        return 0 if res.get("status") == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
