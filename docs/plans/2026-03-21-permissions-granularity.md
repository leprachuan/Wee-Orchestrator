# Permissions Granularity Implementation Plan

**Date:** 2026-03-21
**Status:** Draft
**Author:** Copilot / Foster
**Scope:** All runtimes (Claude Code, Copilot CLI, Gemini, CODEX, Devin, OpenCode)

---

## 1. Problem Statement

Every agent runtime currently executes with maximum permissions:

| Runtime | Current Flags | Risk |
|---------|--------------|------|
| Claude Code | `--permission-mode bypassPermissions` | All tool calls auto-approved, no directory restriction |
| Copilot CLI | `--allow-all-tools --allow-all-paths --yolo` | Every tool, path, and URL unrestricted |
| Gemini | `--yolo` (= `--approval-mode yolo`) | All tools auto-approved |
| CODEX | `--dangerously-bypass-approvals-and-sandbox` | Sandbox disabled, full env inherited |
| Devin | `--permission-mode dangerous` | All approvals bypassed |
| OpenCode | (config-based, no CLI bypass) | Least permissive today |

Any agent can read/write any file on the system, run arbitrary shell commands (including `sudo`), and access the network without restriction. A compromised prompt or misbehaving agent can:

- Overwrite production configs or credentials
- Exfiltrate data via network calls
- Interfere with other agents' working directories
- Modify the orchestrator itself

### Goal

Implement **per-agent, per-runtime permission profiles** that enforce the principle of least privilege while preserving the `/mode yolo` escape hatch for trusted interactive use.

---

## 2. Reference Model: OpenCode Permission Architecture

OpenCode's permission system (at `/opt/opencode/packages/opencode/src/permission/index.ts`) provides the design reference:

```typescript
export const Permission = z.enum(["ask", "allow", "deny"])
// "ask"  → prompt user for approval (not applicable in headless orchestrator)
// "allow" → auto-approve
// "deny"  → reject silently
```

### Per-Tool Permissions with Wildcards

```typescript
// Pattern-based tool matching:
// "git *"         → matches "git push", "git pull", etc.
// "git push *"    → matches "git push origin main"
// "*"             → matches everything (default)
```

### Permission Merge Logic

```typescript
function mergeAgentPermissions(basePermission, overridePermission) {
  // Agent-specific overrides merge on top of defaults
  // String "ask" becomes { "*": "ask" } (wildcard pattern)
  // Most specific pattern wins
}
```

### Key Permission Scopes

| Scope | Description |
|-------|-------------|
| `tool.<name>` | Per-tool allow/deny with glob patterns |
| `external_directory` | Access to paths outside working directory |
| `read` / `write` | File operation permissions |
| `shell` | Command execution permissions |

---

## 3. Proposed Permission Schema for `agents.json`

### 3.1 Schema Definition

```jsonc
{
  "agents": [
    {
      "name": "email_triage",
      "description": "Email triage and classification agent",
      "path": "/opt/email_triage",

      // NEW: Permission profile
      "permissions": {
        // Base permission mode for ALL runtimes
        // "restricted" (default) | "elevated" | "yolo"
        "mode": "restricted",

        // Directory access control
        "directories": {
          // Agent's own path is always read-write (implicit)
          "allow_read": [
            "/opt/fosterbot-home/TODOs"
          ],
          "allow_write": [],
          "deny": [
            "/opt/n8n-copilot-shim",
            "/opt/MyHomeDevops",
            "/etc",
            "/root"
          ]
        },

        // Tool/command access control (glob patterns)
        "tools": {
          "allow": [
            "Read",
            "Edit",
            "Bash(grep:*)",
            "Bash(cat:*)",
            "Bash(python3:*)"
          ],
          "deny": [
            "Bash(rm -rf:*)",
            "Bash(sudo:*)",
            "Bash(curl:*)",
            "Bash(wget:*)",
            "Bash(ssh:*)",
            "Bash(scp:*)"
          ]
        },

        // Network access control (Copilot CLI only today)
        "network": {
          "allow_urls": [],
          "deny_urls": ["*"]
        },

        // MCP server access control
        "mcp": {
          "allow": ["github-mcp-server"],
          "deny": ["*"]
        },

        // Per-runtime overrides (optional, merges on top)
        "runtime_overrides": {
          "claude": {
            "permission_mode": "default",
            "allowed_tools": ["Read", "Edit", "Bash(git:*)"]
          },
          "copilot": {
            "allow_tools": ["shell(git:*)", "write"],
            "deny_tools": ["shell(rm:*)", "shell(sudo:*)"]
          },
          "gemini": {
            "approval_mode": "auto_edit"
          }
        }
      }
    }
  ]
}
```

### 3.2 Schema Validation (Python)

```python
from typing import Optional, Literal
from pydantic import BaseModel, Field

class DirectoryPermissions(BaseModel):
    allow_read: list[str] = Field(default_factory=list)
    allow_write: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)

class ToolPermissions(BaseModel):
    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)

class NetworkPermissions(BaseModel):
    allow_urls: list[str] = Field(default_factory=list)
    deny_urls: list[str] = Field(default_factory=lambda: ["*"])

class McpPermissions(BaseModel):
    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)

class RuntimeOverride(BaseModel):
    """Runtime-specific permission overrides."""
    permission_mode: Optional[str] = None
    approval_mode: Optional[str] = None
    allowed_tools: list[str] = Field(default_factory=list)
    denied_tools: list[str] = Field(default_factory=list)
    allow_tools: list[str] = Field(default_factory=list)
    deny_tools: list[str] = Field(default_factory=list)

class AgentPermissions(BaseModel):
    mode: Literal["restricted", "elevated", "yolo"] = "restricted"
    directories: DirectoryPermissions = Field(default_factory=DirectoryPermissions)
    tools: ToolPermissions = Field(default_factory=ToolPermissions)
    network: NetworkPermissions = Field(default_factory=NetworkPermissions)
    mcp: McpPermissions = Field(default_factory=McpPermissions)
    runtime_overrides: dict[str, RuntimeOverride] = Field(default_factory=dict)

class AgentConfig(BaseModel):
    name: str
    description: str = ""
    path: str = ""
    todo_dir: Optional[str] = None
    permissions: AgentPermissions = Field(default_factory=AgentPermissions)
```

### 3.3 Permission Modes

| Mode | Behavior | Use Case |
|------|----------|----------|
| `restricted` | Enforce all allow/deny lists. Unknown tools denied. | Default for all agents |
| `elevated` | Enforce deny lists only. Unknown tools allowed. | Trusted agents (orchestrator, devops) |
| `yolo` | No restrictions. Equivalent to current behavior. | Interactive debugging only |

### 3.4 Merge Precedence

1. **Explicit deny always wins** (deny > allow > default)
2. **Runtime overrides merge onto base** (runtime-specific extends base, doesn't replace)
3. **Agent path is always implicitly allowed** for read/write
4. **`/mode yolo` temporarily overrides** to `yolo` mode for the session (existing behavior, preserved)

---

## 4. Per-Agent Permission Profiles

### 4.1 Default Profiles

| Agent | Mode | Allowed Dirs (extra) | Denied Dirs | Allowed Tools | Denied Tools | Network |
|-------|------|---------------------|-------------|---------------|--------------|---------|
| **fosterbot** | `elevated` | `/opt/fosterbot-home`, `/opt/foster-skills` | `/etc/shadow`, `/root/.ssh` | `*` | `Bash(rm -rf /)` | `*` (all) |
| **orchestrator** | `elevated` | `/opt/n8n-copilot-shim`, `/opt/fosterbot-home` | `/root/.ssh` | `*` | — | `*` (all) |
| **devops** | `elevated` | `/opt/MyHomeDevops`, `/etc/systemd` | — | `*` | — | `*` (all) |
| **family** | `restricted` | `/opt/family_knowledge` | `/opt/n8n-copilot-shim`, `/opt/MyHomeDevops`, `/etc` | `Read`, `Edit`, `Bash(grep:*)`, `Bash(find:*)`, `Bash(cat:*)`, `Bash(python3:*)` | `Bash(sudo:*)`, `Bash(ssh:*)`, `Bash(curl:*)` | deny `*` |
| **email_triage** | `restricted` | `/opt/email_triage` | `/opt/n8n-copilot-shim`, `/opt/MyHomeDevops`, `/root` | `Read`, `Edit`, `Bash(python3:*)`, `Bash(grep:*)` | `Bash(sudo:*)`, `Bash(rm -rf:*)`, `Bash(ssh:*)` | `allow: ["imap.gmail.com", "smtp.gmail.com"]` |

### 4.2 Profile Explanation

**fosterbot / orchestrator / devops** — These are trusted administrative agents that need broad access. They use `elevated` mode: deny lists block catastrophic operations but unknown tools are allowed.

**family** — A knowledge/documentation agent that should only read/write its own directory. Uses `restricted` mode: only explicitly allowed tools work. No network, no sudo, no system access.

**email_triage** — Handles email processing. Needs Python and file access in its directory, plus IMAP/SMTP network access. Everything else denied.

---

## 5. Runtime Flag Mapping

### 5.1 How Permissions Translate to CLI Flags

The orchestrator's `agent_manager.py` must translate the abstract permission schema into runtime-specific CLI flags.

#### Claude Code

```python
def _build_claude_cmd(self, prompt, model, agent_name, session_id, resume, permissions):
    cmd = [self.claude_bin, "-p", prompt]

    if permissions.mode == "yolo":
        cmd += ["--permission-mode", "bypassPermissions"]
    else:
        # Use runtime override or default
        override = permissions.runtime_overrides.get("claude", {})
        perm_mode = override.get("permission_mode", "default")
        cmd += ["--permission-mode", perm_mode]

        # Per-tool allow/deny
        allowed = override.get("allowed_tools", permissions.tools.allow)
        denied = override.get("denied_tools", permissions.tools.deny)

        if allowed:
            cmd += ["--allowed-tools", ",".join(allowed)]
        if denied:
            cmd += ["--disallowed-tools", ",".join(denied)]

        # Directory access
        for d in permissions.directories.allow_read + permissions.directories.allow_write:
            cmd += ["--add-dir", d]

    cmd += ["--model", model, "--output-format", "stream-json",
            "--include-partial-messages", "--verbose"]

    if resume and session_id:
        cmd += ["--resume", session_id]
    else:
        cmd += ["--session-id", str(uuid.uuid4())]

    return cmd
```

#### Copilot CLI

```python
def _build_copilot_cmd(self, prompt, model, agent_name, session_id, resume, permissions):
    cmd = [self.copilot_bin, "-p", prompt]

    if permissions.mode == "yolo":
        cmd += ["--allow-all-tools", "--allow-all-paths", "--yolo"]
    else:
        override = permissions.runtime_overrides.get("copilot", {})

        # Tool permissions (Copilot uses kind(argument) syntax)
        allow_tools = override.get("allow_tools", [])
        deny_tools = override.get("deny_tools", [])

        for tool in allow_tools or permissions.tools.allow:
            copilot_tool = _translate_to_copilot_syntax(tool)
            cmd += [f"--allow-tool={copilot_tool}"]

        for tool in deny_tools or permissions.tools.deny:
            copilot_tool = _translate_to_copilot_syntax(tool)
            cmd += [f"--deny-tool={copilot_tool}"]

        # Directory access
        for d in permissions.directories.allow_read + permissions.directories.allow_write:
            cmd += ["--add-dir", d]

        # URL/network access
        for url in permissions.network.allow_urls:
            cmd += [f"--allow-url={url}"]
        for url in permissions.network.deny_urls:
            cmd += [f"--deny-url={url}"]

        # MCP control
        for server in permissions.mcp.deny:
            if server != "*":
                cmd += [f"--disable-mcp-server={server}"]

    cmd += ["--no-color", "--silent", "--model", model]

    if resume and session_id:
        cmd += ["--resume", session_id]

    return cmd


def _translate_to_copilot_syntax(tool_pattern: str) -> str:
    """Translate generic tool pattern to Copilot's kind(arg) syntax.

    Examples:
        "Bash(git:*)"  → "shell(git:*)"
        "Read"         → "read"
        "Edit"         → "write"
        "Bash(sudo:*)" → "shell(sudo:*)"
    """
    mapping = {
        "Read": "read",
        "Edit": "write",
        "Write": "write",
    }
    if tool_pattern in mapping:
        return mapping[tool_pattern]
    if tool_pattern.startswith("Bash("):
        inner = tool_pattern[5:-1]  # strip Bash( and )
        return f"shell({inner})"
    return tool_pattern
```

#### Gemini

```python
def _build_gemini_cmd(self, prompt, model, agent_name, session_id, resume, permissions):
    cmd = ["gemini"]

    if permissions.mode == "yolo":
        cmd += ["--yolo"]
    else:
        override = permissions.runtime_overrides.get("gemini", {})
        approval_mode = override.get("approval_mode", "default")
        cmd += [f"--approval-mode={approval_mode}"]

        # Directory access
        for d in permissions.directories.allow_read + permissions.directories.allow_write:
            cmd += ["--include-directories", d]

        # MCP control
        allowed_mcp = permissions.mcp.allow
        if allowed_mcp:
            for server in allowed_mcp:
                cmd += ["--allowed-mcp-server-names", server]

    cmd.append(prompt)

    if resume:
        cmd += ["--resume", "latest"]

    return cmd
```

#### CODEX

```python
def _build_codex_cmd(self, prompt, model, session_id, resume, permissions):
    if resume and session_id:
        cmd = ["codex", "exec", "resume"]
    else:
        cmd = ["codex", "exec"]

    if permissions.mode == "yolo":
        cmd += ["--dangerously-bypass-approvals-and-sandbox",
                "-c", "shell_environment_policy.inherit=all"]
    # else: CODEX runs in sandbox by default — no extra flags needed

    if model:
        cmd += ["-m", model]

    if resume and session_id:
        cmd += [session_id, prompt]
    else:
        cmd.append(prompt)

    return cmd
```

#### Devin

```python
def _build_devin_cmd(self, prompt, model, session_id, resume, permissions):
    cmd = [self.devin_bin, "-p"]

    if model:
        cmd += ["--model", model]

    if permissions.mode == "yolo":
        cmd += ["--permission-mode", "dangerous"]
    else:
        cmd += ["--permission-mode", "auto"]

    if resume and session_id:
        cmd += ["-r", session_id]

    cmd += ["--", prompt]
    return cmd
```

### 5.2 Tool Pattern Syntax Translation Table

| Generic Pattern | Claude | Copilot | Gemini | Notes |
|----------------|--------|---------|--------|-------|
| `Read` | `Read` | `read` | (built-in) | File reading |
| `Edit` | `Edit` | `write` | (built-in) | File editing |
| `Bash(git:*)` | `Bash(git:*)` | `shell(git:*)` | N/A | Git commands |
| `Bash(sudo:*)` | `Bash(sudo:*)` | `shell(sudo:*)` | N/A | Sudo commands |
| `Bash(python3:*)` | `Bash(python3:*)` | `shell(python3:*)` | N/A | Python scripts |
| `*` | `*` (all) | `--allow-all-tools` | `--yolo` | Everything |

---

## 6. Enforcement in `agent_manager.py`

### 6.1 Architecture Changes

```
agent_manager.py
├── _load_agents_config()          # MODIFY: parse permissions block
├── _build_permission_flags()      # NEW: translate permissions → CLI flags
├── _validate_permissions()        # NEW: validate schema on load
├── run_claude()                   # MODIFY: use _build_claude_cmd()
├── run_copilot()                  # MODIFY: use _build_copilot_cmd()
├── run_gemini()                   # MODIFY: use _build_gemini_cmd()
├── run_codex()                    # MODIFY: use _build_codex_cmd()
├── run_devin()                    # MODIFY: use _build_devin_cmd()
├── _execute_subprocess_with_tracking()  # MODIFY: add bwrap wrapper option
└── _parse_mode_command()          # KEEP: /mode yolo still works as override
```

### 6.2 Config Loading Changes

```python
# In _load_agents_config(), extend the parsing:

def _load_agents_config(self, config_file=None):
    # ... existing file resolution logic ...

    with open(config_path, "r") as f:
        config = json.load(f)
        agents = {}
        for agent in config.get("agents", []):
            name = agent.get("name")
            if not name:
                continue
            agents[name] = {
                "path": agent.get("path", ""),
                "description": agent.get("description", ""),
                "todo_dir": agent.get("todo_dir", ""),
                # NEW: Load permissions (with defaults)
                "permissions": self._parse_permissions(
                    agent.get("permissions", {}),
                    agent.get("path", "")
                ),
            }
        return agents

def _parse_permissions(self, perms_dict, agent_path):
    """Parse and validate permission config, applying defaults."""
    defaults = {
        "mode": "restricted",
        "directories": {
            "allow_read": [],
            "allow_write": [],
            "deny": [],
        },
        "tools": {
            "allow": [],
            "deny": [],
        },
        "network": {
            "allow_urls": [],
            "deny_urls": ["*"],
        },
        "mcp": {
            "allow": [],
            "deny": [],
        },
        "runtime_overrides": {},
    }

    # Merge provided config onto defaults
    result = {**defaults}
    for key in defaults:
        if key in perms_dict:
            if isinstance(defaults[key], dict):
                result[key] = {**defaults[key], **perms_dict[key]}
            else:
                result[key] = perms_dict[key]

    # Agent's own path is always implicitly allowed
    if agent_path and agent_path not in result["directories"]["allow_read"]:
        result["directories"]["allow_read"].insert(0, agent_path)
    if agent_path and agent_path not in result["directories"]["allow_write"]:
        result["directories"]["allow_write"].insert(0, agent_path)

    return result
```

### 6.3 YOLO Mode Override Flow

The existing `/mode yolo` command (parsed at line 2142) is preserved. When active, it overrides the agent's permission profile for that session:

```python
# In each run_*() method, after parsing mode:
effective_permissions = copy.deepcopy(agent_config["permissions"])
if mode == "yolo":
    effective_permissions["mode"] = "yolo"
    log.warning(f"YOLO mode active for agent={agent}, session={n8n_session_id}")
```

### 6.4 Optional OS-Level Enforcement with bubblewrap

For `restricted` agents, add an optional `bwrap` wrapper around subprocess execution. This is the **strongest enforcement** — even if the runtime ignores CLI flags, the OS prevents access.

```python
def _wrap_with_bwrap(self, cmd, permissions, agent_path):
    """Wrap command in bubblewrap sandbox for restricted agents."""
    if permissions["mode"] != "restricted":
        return cmd  # Only sandbox restricted agents

    if not shutil.which("bwrap"):
        log.warning("bubblewrap not installed, skipping OS sandbox")
        return cmd

    bwrap_cmd = [
        "bwrap",
        "--unshare-all",       # Isolate all namespaces
        "--share-net",         # Keep network (needed for API calls)
        "--die-with-parent",   # Kill sandbox if parent dies

        # Essential system mounts
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",

        # Read-only system access
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", "/lib", "/lib",
        "--ro-bind", "/lib64", "/lib64",
        "--ro-bind", "/bin", "/bin",
        "--ro-bind", "/sbin", "/sbin",
        "--ro-bind", "/etc/resolv.conf", "/etc/resolv.conf",
        "--ro-bind", "/etc/ssl", "/etc/ssl",
        "--ro-bind", "/etc/passwd", "/etc/passwd",

        # Agent's own directory: read-write
        "--bind", agent_path, agent_path,
    ]

    # Allowed read directories
    for d in permissions["directories"].get("allow_read", []):
        if os.path.exists(d):
            bwrap_cmd += ["--ro-bind", d, d]

    # Allowed write directories
    for d in permissions["directories"].get("allow_write", []):
        if os.path.exists(d):
            bwrap_cmd += ["--bind", d, d]

    # Home directory for runtime config (read-only)
    home = os.path.expanduser("~")
    bwrap_cmd += ["--ro-bind", home, home]

    # Set working directory
    bwrap_cmd += ["--chdir", agent_path]

    # Append the actual command
    bwrap_cmd += ["--"] + cmd

    return bwrap_cmd
```

**Important:** `bwrap` sandboxing is Phase 3 (optional hardening). Phase 1–2 use CLI flags only.

---

## 7. Implementation Phases

### Phase 1: Schema & Config (Week 1-2)

**Goal:** Define the permission schema, add it to `agents.json`, and validate on load. No behavioral changes yet.

| Task | File | Description |
|------|------|-------------|
| 1.1 | `agents.json` | Add `permissions` block to all agent configs (with current equivalent: `"mode": "yolo"`) |
| 1.2 | `agents.example.json` | Update example with permission profiles |
| 1.3 | `agent_manager.py` | Extend `_load_agents_config()` to parse `permissions` |
| 1.4 | `agent_manager.py` | Add `_parse_permissions()` with defaults and validation |
| 1.5 | `agent_manager.py` | Add logging: log effective permissions at agent start |
| 1.6 | `tests/test_permissions.py` | Unit tests for schema parsing, merge logic, defaults |

**Backwards Compatibility:**
- Agents without a `permissions` block get `{"mode": "yolo"}` (preserves current behavior exactly)
- No runtime behavior changes in Phase 1
- All existing `/mode yolo` functionality preserved

**Rollout:** Deploy to dev only. Verify all agents load correctly.

### Phase 2: CLI Flag Enforcement (Week 3-5)

**Goal:** Translate permission profiles into runtime-specific CLI flags. This is where behavior changes.

| Task | File | Description |
|------|------|-------------|
| 2.1 | `agent_manager.py` | Add `_build_claude_cmd()` with flag translation |
| 2.2 | `agent_manager.py` | Add `_build_copilot_cmd()` with flag translation |
| 2.3 | `agent_manager.py` | Add `_build_gemini_cmd()` with flag translation |
| 2.4 | `agent_manager.py` | Add `_build_codex_cmd()` with flag translation |
| 2.5 | `agent_manager.py` | Add `_build_devin_cmd()` with flag translation |
| 2.6 | `agent_manager.py` | Add `_translate_to_copilot_syntax()` helper |
| 2.7 | `agent_manager.py` | Refactor `run_claude()` to use builder (lines 3564-3678) |
| 2.8 | `agent_manager.py` | Refactor `run_copilot()` to use builder (lines 3413-3499) |
| 2.9 | `agent_manager.py` | Refactor `run_gemini()` to use builder (lines 3680-3750) |
| 2.10 | `agent_manager.py` | Refactor `run_codex()` to use builder (lines 3752-3847) |
| 2.11 | `agent_manager.py` | Refactor `run_devin()` to use builder (lines 3849-3946) |
| 2.12 | `agent_manager.py` | Preserve `/mode yolo` override in all runners |
| 2.13 | `agents.json` | Transition agent profiles from `"mode": "yolo"` to actual profiles |
| 2.14 | `tests/test_permissions.py` | Test flag generation for each runtime |
| 2.15 | `tests/test_permissions.py` | Test yolo override still works |
| 2.16 | Manual testing | Test each agent with each runtime in dev |

**Backwards Compatibility:**
- `/mode yolo` still works (overrides to unrestricted)
- Agents with `"mode": "yolo"` in config behave exactly as before
- Gradual transition: change one agent at a time from `yolo` → `restricted`

**Rollout Strategy:**
1. Deploy to dev with all agents still on `"mode": "yolo"`
2. Switch `family` agent to `restricted` first (lowest risk, read-only workload)
3. Test thoroughly for 1 week
4. Switch `email_triage` to `restricted`
5. Leave `fosterbot`, `orchestrator`, `devops` on `elevated`

### Phase 3: OS-Level Sandboxing (Week 6-8)

**Goal:** Add optional `bubblewrap` sandboxing as defense-in-depth for `restricted` agents.

| Task | File | Description |
|------|------|-------------|
| 3.1 | `agent_manager.py` | Add `_wrap_with_bwrap()` method |
| 3.2 | `agent_manager.py` | Integrate bwrap into `_execute_subprocess_with_tracking()` |
| 3.3 | `agents.json` | Add `"sandbox": true/false` to permission config |
| 3.4 | `tests/test_sandbox.py` | Test bwrap command generation |
| 3.5 | Manual testing | Verify agents work inside bwrap sandbox |
| 3.6 | Documentation | Update ARCHITECTURE.md, SECURITY.md |

**Backwards Compatibility:**
- `sandbox` defaults to `false` — opt-in only
- If `bwrap` is not installed, logs warning and runs without sandbox
- YOLO mode always bypasses sandbox

### Phase 4: Audit & Monitoring (Week 8-10)

**Goal:** Add permission violation logging, audit trail, and alerting.

| Task | File | Description |
|------|------|-------------|
| 4.1 | `agent_manager.py` | Log all permission decisions (allowed/denied/overridden) |
| 4.2 | `agent_manager.py` | Track yolo mode activations per session |
| 4.3 | `notification_manager.py` | Alert on yolo mode activation (optional) |
| 4.4 | `webui/` | Show agent permission profile in UI |
| 4.5 | API endpoint | `GET /api/v1/agents/{name}/permissions` |
| 4.6 | Documentation | Admin guide for permission configuration |

---

## 8. Detailed File Change Map

### `agent_manager.py` — Primary Changes

| Line Range | Current Code | Change |
|------------|-------------|--------|
| 1062-1111 | `_load_agents_config()` | Extend to parse `permissions` block |
| 1090-1104 | Agent dict construction | Add `"permissions"` key |
| 3413-3499 | `run_copilot()` | Replace hardcoded flags with `_build_copilot_cmd()` |
| 3461-3472 | Copilot YOLO prompt injection | Move to builder, gated by `permissions.mode` |
| 3474-3488 | Copilot cmd construction | Replace with builder output |
| 3564-3678 | `run_claude()` | Replace hardcoded flags with `_build_claude_cmd()` |
| 3608-3620 | Claude cmd construction | Replace with builder output |
| 3680-3750 | `run_gemini()` | Replace hardcoded flags with `_build_gemini_cmd()` |
| 3725-3728 | Gemini cmd construction | Replace with builder output |
| 3752-3847 | `run_codex()` | Replace hardcoded flags with `_build_codex_cmd()` |
| 3800-3813 | CODEX YOLO prompt injection | Move to builder, gated by `permissions.mode` |
| 3815-3838 | CODEX cmd construction | Replace with builder output |
| 3849-3946 | `run_devin()` | Replace hardcoded flags with `_build_devin_cmd()` |
| 3903-3915 | Devin YOLO prompt injection | Move to builder, gated by `permissions.mode` |
| 3918-3932 | Devin cmd construction | Replace with builder output |
| 3225-3243 | `_execute_subprocess_with_tracking()` Popen | Add optional bwrap wrapping |
| NEW | `_build_*_cmd()` methods (~200 lines) | New methods for each runtime |
| NEW | `_parse_permissions()` (~50 lines) | Permission parsing with defaults |
| NEW | `_wrap_with_bwrap()` (~60 lines) | Optional OS sandbox |
| NEW | `_translate_to_copilot_syntax()` (~20 lines) | Tool pattern translator |

### `agents.json` — Config Changes

Add `permissions` block to each agent entry. Example diff:

```diff
 {
   "name": "family",
   "description": "Family knowledge and recipes...",
-  "path": "/opt/family_knowledge"
+  "path": "/opt/family_knowledge",
+  "permissions": {
+    "mode": "restricted",
+    "directories": {
+      "allow_read": ["/opt/family_knowledge"],
+      "deny": ["/opt/n8n-copilot-shim", "/opt/MyHomeDevops", "/etc", "/root"]
+    },
+    "tools": {
+      "allow": ["Read", "Edit", "Bash(grep:*)", "Bash(cat:*)", "Bash(python3:*)"],
+      "deny": ["Bash(sudo:*)", "Bash(ssh:*)", "Bash(curl:*)"]
+    },
+    "network": {
+      "deny_urls": ["*"]
+    }
+  }
 }
```

### New Files

| File | Purpose |
|------|---------|
| `tests/test_permissions.py` | Unit tests for permission schema, parsing, flag generation |
| `tests/test_sandbox.py` | Unit tests for bwrap command generation |
| `docs/permissions.md` | User/admin documentation for permission configuration |

---

## 9. Testing Strategy

### 9.1 Unit Tests (`tests/test_permissions.py`)

```python
class TestPermissionParsing:
    """Test _parse_permissions() defaults and merging."""

    def test_empty_permissions_get_defaults(self):
        """Agent with no permissions block gets yolo defaults."""
        result = manager._parse_permissions({}, "/opt/test")
        assert result["mode"] == "restricted"
        assert "/opt/test" in result["directories"]["allow_read"]

    def test_agent_path_always_allowed(self):
        """Agent's own path is implicitly in allow_read and allow_write."""
        result = manager._parse_permissions({"mode": "restricted"}, "/opt/myagent")
        assert "/opt/myagent" in result["directories"]["allow_read"]
        assert "/opt/myagent" in result["directories"]["allow_write"]

    def test_deny_overrides_allow(self):
        """If a path is in both allow and deny, deny wins."""
        perms = {
            "directories": {
                "allow_read": ["/opt/secret"],
                "deny": ["/opt/secret"],
            }
        }
        result = manager._parse_permissions(perms, "/opt/test")
        # Validation should warn or remove from allow
        assert "/opt/secret" in result["directories"]["deny"]


class TestFlagGeneration:
    """Test _build_*_cmd() output."""

    def test_claude_restricted_uses_default_mode(self):
        perms = {"mode": "restricted", "tools": {"allow": ["Read", "Edit"]}}
        cmd = manager._build_claude_cmd("test", "sonnet", "test", None, False, perms)
        assert "--permission-mode" in cmd
        assert "default" in cmd
        assert "--allowed-tools" in cmd

    def test_claude_yolo_uses_bypass(self):
        perms = {"mode": "yolo"}
        cmd = manager._build_claude_cmd("test", "sonnet", "test", None, False, perms)
        assert "bypassPermissions" in cmd

    def test_copilot_tool_translation(self):
        assert _translate_to_copilot_syntax("Read") == "read"
        assert _translate_to_copilot_syntax("Edit") == "write"
        assert _translate_to_copilot_syntax("Bash(git:*)") == "shell(git:*)"

    def test_copilot_restricted_has_deny_tools(self):
        perms = {
            "mode": "restricted",
            "tools": {"deny": ["Bash(sudo:*)", "Bash(rm -rf:*)"]},
        }
        cmd = manager._build_copilot_cmd("test", "gpt-5", "test", None, False, perms)
        assert "--deny-tool=shell(sudo:*)" in cmd
        assert "--deny-tool=shell(rm -rf:*)" in cmd
        assert "--yolo" not in cmd

    def test_gemini_restricted_uses_default_approval(self):
        perms = {"mode": "restricted"}
        cmd = manager._build_gemini_cmd("test", "gemini", "test", None, False, perms)
        assert "--approval-mode=default" in cmd
        assert "--yolo" not in cmd


class TestYoloOverride:
    """Test that /mode yolo still overrides to full permissions."""

    def test_mode_yolo_overrides_restricted(self):
        perms = {"mode": "restricted", "tools": {"deny": ["Bash(sudo:*)"]}}
        effective = copy.deepcopy(perms)
        effective["mode"] = "yolo"  # /mode yolo override
        cmd = manager._build_claude_cmd("test", "sonnet", "test", None, False, effective)
        assert "bypassPermissions" in cmd
```

### 9.2 Integration Tests (Manual)

| Test | Agent | Runtime | Expected |
|------|-------|---------|----------|
| Restricted agent can read own dir | family | Claude | ✅ Reads /opt/family_knowledge |
| Restricted agent denied system files | family | Claude | ❌ Cannot read /etc/passwd |
| Restricted agent denied sudo | family | Copilot | ❌ `sudo` commands rejected |
| Elevated agent can use sudo | devops | Copilot | ✅ sudo works |
| YOLO override works | family | Claude | ✅ After `/mode yolo`, all tools available |
| Resume session preserves perms | family | Claude | ✅ Permissions survive session resume |

### 9.3 Rollback Strategy

Each phase has a clear rollback:

- **Phase 1 rollback:** Remove `permissions` from agents.json. Parser ignores missing key.
- **Phase 2 rollback:** Set all agents to `"mode": "yolo"`. Behavior identical to pre-change.
- **Phase 3 rollback:** Set `"sandbox": false` or remove bwrap. Falls through to CLI-only enforcement.

---

## 10. Security Considerations

### 10.1 What This Does NOT Protect Against

- **Prompt injection attacks** that trick agents into using allowed tools maliciously (e.g., `git push --force` when `Bash(git:*)` is allowed)
- **Data exfiltration via allowed channels** (e.g., encoding data in git commit messages)
- **Runtime vulnerabilities** (bugs in Claude/Copilot/Gemini that ignore CLI flags)

### 10.2 Defense in Depth

| Layer | Mechanism | Phase |
|-------|-----------|-------|
| 1. Config | Permission profiles in agents.json | Phase 1 |
| 2. CLI Flags | Runtime-specific restriction flags | Phase 2 |
| 3. OS Sandbox | bubblewrap namespace isolation | Phase 3 |
| 4. Audit | Permission decision logging | Phase 4 |

### 10.3 YOLO Mode Hardening

The `/mode yolo` escape hatch is preserved for interactive debugging but should be hardened:

```python
# Log every yolo activation with user identity
log.warning(
    f"YOLO_MODE_ACTIVATED agent={agent} user={user_identity} "
    f"session={n8n_session_id} channel={auth_channel}"
)

# Optional: send notification on yolo activation
if self.notification_manager:
    self.notification_manager.send(
        f"⚠️ YOLO mode activated by {user_identity} for agent {agent}"
    )
```

---

## 11. Migration Path

### Step-by-Step Migration

```
Week 1:  Add schema + parser (no behavior change)
         ├── All agents: permissions.mode = "yolo" (matches current)
         └── Deploy to dev, verify green

Week 2:  Add CLI flag builders (no behavior change yet)
         ├── Builders exist but aren't called yet
         └── Unit tests pass

Week 3:  Wire builders into run_*() methods
         ├── Still all agents on "yolo" — no behavior change
         └── Integration test: all agents work identically

Week 4:  Switch "family" agent to "restricted"
         ├── Test: can read/edit /opt/family_knowledge
         ├── Test: cannot sudo, ssh, curl
         └── Monitor for 1 week

Week 5:  Switch "email_triage" to "restricted" (if family is stable)
         ├── Verify email workflows still work
         └── Monitor for 1 week

Week 6:  Switch admin agents to "elevated"
         ├── fosterbot, orchestrator, devops
         ├── deny lists only — no functional change expected
         └── Monitor

Week 7:  Optional: enable bwrap for "restricted" agents
Week 8:  Add audit logging (Phase 4)
Week 9:  Deploy to production
Week 10: Documentation and training
```

---

## 12. Open Questions

| # | Question | Impact | Decision Needed By |
|---|----------|--------|-------------------|
| 1 | Should `elevated` agents get bwrap sandboxing? | Phase 3 scope | Week 5 |
| 2 | Should YOLO mode require re-authentication? | Security vs convenience | Week 4 |
| 3 | Per-user permission overrides (admin vs regular user)? | Multi-tenant | Future |
| 4 | Should permission profiles be stored separately from agents.json? | Config management | Phase 1 |
| 5 | How to handle Gemini's deprecated `--allowed-tools` vs new policy engine? | Gemini compatibility | Phase 2 |
| 6 | Should denied operations return error to user or silently fail? | UX | Phase 2 |

---

## 13. Appendix: Complete `agents.json` Example with Permissions

```json
{
  "agents": [
    {
      "name": "fosterbot",
      "description": "Main orchestrator agent...",
      "path": "/opt/fosterbot-home",
      "todo_dir": "/opt/fosterbot-home/TODOs",
      "permissions": {
        "mode": "elevated",
        "directories": {
          "allow_read": ["/opt/fosterbot-home", "/opt/foster-skills", "/opt/n8n-copilot-shim"],
          "allow_write": ["/opt/fosterbot-home", "/opt/foster-skills"],
          "deny": ["/root/.ssh", "/etc/shadow"]
        },
        "tools": {
          "allow": ["*"],
          "deny": ["Bash(rm -rf /)"]
        },
        "network": {
          "allow_urls": ["*"],
          "deny_urls": []
        }
      }
    },
    {
      "name": "orchestrator",
      "description": "AI runtime orchestration and management...",
      "path": "/opt/n8n-copilot-shim",
      "todo_dir": "/opt/fosterbot-home/TODOs",
      "permissions": {
        "mode": "elevated",
        "directories": {
          "allow_read": ["/opt/n8n-copilot-shim", "/opt/fosterbot-home"],
          "allow_write": ["/opt/n8n-copilot-shim"],
          "deny": ["/root/.ssh"]
        },
        "tools": {
          "allow": ["*"],
          "deny": []
        },
        "network": {
          "allow_urls": ["*"],
          "deny_urls": []
        }
      }
    },
    {
      "name": "devops",
      "description": "DevOps and infrastructure management...",
      "path": "/opt/MyHomeDevops",
      "permissions": {
        "mode": "elevated",
        "directories": {
          "allow_read": ["/opt/MyHomeDevops", "/etc/systemd", "/var/log"],
          "allow_write": ["/opt/MyHomeDevops", "/etc/systemd"],
          "deny": []
        },
        "tools": {
          "allow": ["*"],
          "deny": []
        },
        "network": {
          "allow_urls": ["*"],
          "deny_urls": []
        }
      }
    },
    {
      "name": "family",
      "description": "Family knowledge and recipes...",
      "path": "/opt/family_knowledge",
      "permissions": {
        "mode": "restricted",
        "directories": {
          "allow_read": ["/opt/family_knowledge"],
          "allow_write": ["/opt/family_knowledge"],
          "deny": ["/opt/n8n-copilot-shim", "/opt/MyHomeDevops", "/etc", "/root"]
        },
        "tools": {
          "allow": ["Read", "Edit", "Bash(grep:*)", "Bash(find:*)", "Bash(cat:*)", "Bash(python3:*)", "Bash(git:*)"],
          "deny": ["Bash(sudo:*)", "Bash(ssh:*)", "Bash(scp:*)", "Bash(curl:*)", "Bash(wget:*)", "Bash(nc:*)", "Bash(nmap:*)"]
        },
        "network": {
          "allow_urls": [],
          "deny_urls": ["*"]
        },
        "mcp": {
          "allow": [],
          "deny": ["*"]
        },
        "runtime_overrides": {
          "claude": {
            "permission_mode": "default"
          },
          "copilot": {},
          "gemini": {
            "approval_mode": "auto_edit"
          }
        }
      }
    },
    {
      "name": "email_triage",
      "description": "Email triage and classification...",
      "path": "/opt/email_triage",
      "permissions": {
        "mode": "restricted",
        "directories": {
          "allow_read": ["/opt/email_triage", "/opt/fosterbot-home/TODOs"],
          "allow_write": ["/opt/email_triage"],
          "deny": ["/opt/n8n-copilot-shim", "/opt/MyHomeDevops", "/root", "/etc"]
        },
        "tools": {
          "allow": ["Read", "Edit", "Bash(python3:*)", "Bash(grep:*)", "Bash(cat:*)", "Bash(git:*)"],
          "deny": ["Bash(sudo:*)", "Bash(rm -rf:*)", "Bash(ssh:*)", "Bash(scp:*)", "Bash(nc:*)"]
        },
        "network": {
          "allow_urls": ["imap.gmail.com", "smtp.gmail.com", "oauth2.googleapis.com"],
          "deny_urls": ["*"]
        },
        "mcp": {
          "allow": [],
          "deny": ["*"]
        }
      }
    }
  ]
}
```

---

## 14. Related Documents

- [Agent Manager API Design](/opt/n8n-copilot-shim/docs/plans/2026-02-16-agent-manager-api-design.md)
- [OpenCode Permission Model](/opt/opencode/packages/opencode/src/permission/index.ts)
- [OpenCode Permission Docs](/opt/opencode/packages/web/src/content/docs/permissions.mdx)
- [ARCHITECTURE.md](/opt/n8n-copilot-shim/ARCHITECTURE.md)
- [SECURITY.md](/opt/n8n-copilot-shim/SECURITY.md)
