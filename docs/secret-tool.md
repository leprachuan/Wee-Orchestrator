# Secret-Tool — Wee-Dev Secret Storage CLI

Lightweight CLI for storing and retrieving secrets. Wraps the existing
keyring-vault skill (or python-keyring) with a consistent interface.

---

## Quick Reference

```bash
# Store a secret (creates or updates)
python3 secret_tool/secret_tool.py set --name API_KEY --value "sk-abc123"

# Store — fail if already exists
python3 secret_tool/secret_tool.py set --name API_KEY --value "sk-abc123" --no-clobber

# Store — read value from stdin (avoids shell history)
echo "sk-abc123" | python3 secret_tool/secret_tool.py set --name API_KEY --value-stdin

# Retrieve a secret (raw value to stdout)
python3 secret_tool/secret_tool.py get --name API_KEY

# List stored secret names
python3 secret_tool/secret_tool.py list

# Delete a secret
python3 secret_tool/secret_tool.py delete --name API_KEY
```

---

## CLI UX Specification (F011)

> Resolved in F011 to unblock F010 implementation.

### Write Command: `set` (primary), `add` (alias)

**Command:** `set` with **upsert** semantics (create-or-update).

```
secret-tool set --name <NAME> --value <VALUE> [--no-clobber] [--backend keyring|file]
secret-tool set --name <NAME> --value-stdin   [--no-clobber] [--backend keyring|file]
secret-tool add ...   (alias — identical to set)
```

**Why `set` over `add`:**
- `set` naturally reads as "make this the value" — no ambiguity about whether
  it creates or updates. Matches `gh secret set`, HashiCorp Vault `kv put`,
  AWS `put-secret-value`, and GNOME `secret-tool store`.
- `add` implies "append" or "create-only", which misleads users who expect
  upsert behavior.
- `add` is retained as an exact alias for backward compatibility with
  existing scripts and muscle memory.

**Overwrite behavior (default — upsert):**
- If the secret name does not exist → create it.
- If the secret name already exists → silently overwrite.
- Feedback distinguishes the two cases:
  - `{"status": "success", "action": "created", "name": "API_KEY"}`
  - `{"status": "success", "action": "updated", "name": "API_KEY"}`

**`--no-clobber` flag (opt-in reject-duplicates):**
- If the secret name already exists → exit code 1 with:
  `{"status": "error", "message": "Secret 'API_KEY' already exists (omit --no-clobber to overwrite)"}`
- If the secret does not exist → create as normal.

**`--value-stdin` flag (security best practice):**
- Reads the secret value from stdin instead of the `--value` argument.
- Prevents the secret from appearing in shell history or `/proc/cmdline`.
- Mutually exclusive with `--value`; error if both supplied.
- Reads exactly one line (strips trailing newline).

### Read Command: `get` (unchanged)

```
secret-tool get --name <NAME> [--backend keyring|file]
```

- **stdout:** raw secret value (no JSON wrapper) for easy shell capture:
  `VAL=$(python3 secret_tool.py get --name API_KEY)`
- **Exit 0** on success; **exit 1** with JSON error on not-found:
  `{"status": "failure", "message": "not found", "credential": null}`

### List Command: `list` (new in F010)

```
secret-tool list [--backend keyring|file] [--json]
```

- Default output: one secret name per line (no values).
- `--json`: output as JSON array of names.
- Exit 0 even if the store is empty (prints nothing / empty array).
- **Note:** only the `file` backend can enumerate names (it owns the
  encrypted JSON store). The `keyring` backend may not support listing
  unless the underlying keyring exposes a search/enumerate API.
  If listing is unsupported for the active backend, exit 1 with a
  clear message.

### Delete Command: `delete` (new in F010)

```
secret-tool delete --name <NAME> [--backend keyring|file]
```

- Exit 0 with `{"status": "success", "action": "deleted", "name": "NAME"}`.
- Exit 1 if the name does not exist:
  `{"status": "error", "message": "Secret 'NAME' not found"}`.
- No `--force` needed — single-item delete is already explicit.

### Output Conventions

| Command  | stdout on success               | stdout on failure             | Exit code |
|----------|---------------------------------|-------------------------------|-----------|
| `set`    | JSON status (never includes value) | JSON error                 | 0 / 1    |
| `get`    | Raw secret value                | JSON error                    | 0 / 1    |
| `list`   | One name per line (or JSON)     | JSON error                    | 0 / 1    |
| `delete` | JSON status                     | JSON error                    | 0 / 1    |

**The secret value is NEVER printed by any command except `get`.**

### Backend Selection

| Backend  | Default | Requires                          | Listing support |
|----------|---------|-----------------------------------|-----------------|
| `keyring`| ✅ yes  | keyring-vault skill OR python-keyring | limited       |
| `file`   | no      | `cryptography` + `python-keyring` | ✅ yes          |

### Error Messages

All error output is JSON on stdout (not stderr), matching the existing
pattern. Callers can parse `status` field programmatically.

---

## Installation (dev)

```bash
# GNOME keyring integration
sudo apt-get install libsecret-tools gnome-keyring

# Python backends
pip install keyring cryptography
```

## Security Notes

- **Never log or print secret values** except via `get` (and only to stdout).
- Prefer `--value-stdin` in automation to avoid leaking secrets to shell
  history and process listings.
- The file backend encrypts its store with a Fernet key that is itself
  stored in the system keyring — no plaintext secrets on disk.
