# N8N Copilot Shim: Model Listing Implementation Analysis

## Executive Summary
The model listing implementation consists of:
- **Static Models**: Hard-coded model definitions for Claude, Gemini, CODEX, and OpenCode
- **Dynamic Models**: Runtime CLI introspection for Copilot and OpenCode
- **CLI Integration**: Subprocess execution with model resolution
- **API Endpoint**: `/api/v1/models` for REST clients

---

## 1. STATIC MODEL DEFINITIONS

### Location: `agent_manager.py` lines 770-872 (SessionManager class)

All static models are defined as class attributes with structure:
```python
{
  "Category": [
    (model_id, display_name, [aliases]),
    ...
  ]
}
```

### CLAUDE_MODELS (lines 770-793)
**File**: `/opt/n8n-copilot-shim-dev/agent_manager.py:770-793`

```python
CLAUDE_MODELS = {
    "Anthropic Models": [
        (
            "sonnet",
            "Claude Sonnet (Latest)",
            ["claude-sonnet", "claude-sonnet-4-6", "claude-sonnet-4.6", "claude-sonnet-4.5", "sonnet-4.6"],
        ),
        (
            "haiku",
            "Claude Haiku (Latest)",
            ["claude-haiku", "claude-haiku-4-5", "claude-haiku-4.5", "haiku-4.5"],
        ),
        (
            "opus",
            "Claude Opus (Latest)",
            ["claude-opus", "claude-opus-4-6", "claude-opus-4.6", "opus-4.6"],
        ),
    ],
    "US Frontier Models (Comparison)": [
        ("claude-3-5-sonnet-latest", "Claude 3.5 Sonnet (V2)", ["claude-3-5-sonnet-20241022"]),
        ("claude-3-5-haiku-latest", "Claude 3.5 Haiku", ["claude-3-5-haiku-20241022"]),
        ("claude-3-opus-latest", "Claude 3 Opus", ["claude-3-opus-20240229"]),
    ]
}
```

**Key Features**:
- Uses CLI aliases (sonnet, haiku, opus) as primary IDs to let CLI resolve latest versions
- Multiple aliases for backward compatibility
- Organized by category (Anthropic vs US Frontier)

### OPENCODE_MODELS (lines 795-805)
**File**: `/opt/n8n-copilot-shim-dev/agent_manager.py:795-805`

```python
OPENCODE_MODELS = {
    "Meta (US Models)": [
        ("llama-3.3-70b-versatile", "Llama 3.3 70B", ["llama-3.3", "llama-3-70b"]),
        ("llama-3.1-405b", "Llama 3.1 405B", ["llama-405b"]),
        ("llama-3.2-90b-vision", "Llama 3.2 90B Vision", ["llama-90b-vision"]),
    ],
    "xAI (US Models)": [
        ("grok-2", "Grok-2", ["grok"]),
        ("grok-2-mini", "Grok-2 Mini", ["grok-mini"]),
    ]
}
```

### GEMINI_MODELS (lines 809-850)
**File**: `/opt/n8n-copilot-shim-dev/agent_manager.py:809-850`

```python
GEMINI_MODELS = {
    "Google Models": [
        ("gemini-3-pro-preview", "Gemini 3 Pro (Preview)", ["gemini-3-pro", "pro-3"]),
        ("gemini-3-flash-preview", "Gemini 3 Flash (Preview)", ["gemini-3-flash", "flash-3"]),
        ("gemini-2.5-pro", "Gemini 2.5 Pro", ["gemini-pro-2.5", "pro-2.5"]),
        ("gemini-2.5-flash", "Gemini 2.5 Flash", ["gemini-flash-2.5", "flash-2.5"]),
        ("gemini-2.5-flash-lite", "Gemini 2.5 Flash Lite", ["gemini-flash-lite-2.5", "flash-lite-2.5"]),
        ("gemini-2.0-flash-exp", "Gemini 2.0 Flash (Experimental)", ["gemini-2.0-flash", "flash-2.0"]),
        ("gemini-1.5-pro", "Gemini 1.5 Pro", ["gemini-pro-1.5", "pro-1.5"]),
        ("gemini-1.5-flash", "Gemini 1.5 Flash", ["gemini-flash-1.5", "flash-1.5"]),
        ("gemini-pro", "Gemini Pro", ["gemini-1.0-pro"]),
    ],
    "US Frontier Models (Comparison)": [
        ("gemini-1.5-pro-latest", "Gemini 1.5 Pro", ["gemini-1.5-pro"]),
        ("gemini-1.5-flash-latest", "Gemini 1.5 Flash", ["gemini-1.5-flash"]),
        ("gemini-2.0-flash-001", "Gemini 2.0 Flash", ["gemini-2.0-flash"]),
    ]
}
```

### CODEX_MODELS (lines 853-872)
**File**: `/opt/n8n-copilot-shim-dev/agent_manager.py:853-872`

```python
CODEX_MODELS = {
    "OpenAI Models": [
        ("gpt-5.3-codex", "GPT-5.3 Codex", ["gpt-5.3", "codex-latest"]),
        ("gpt-5.2-codex", "GPT-5.2 Codex", ["gpt-5.2-codex"]),
        ("gpt-5.2", "GPT-5.2", ["gpt-5.2"]),
        ("gpt-5.1-codex-max", "GPT-5.1 Codex Max", ["gpt-5.1", "codex-max"]),
        ("gpt-5.1-codex", "GPT-5.1 Codex", ["codex"]),
        ("gpt-5.1", "GPT-5.1", []),
        ("gpt-5.1-codex-mini", "GPT-5.1 Codex Mini", ["codex-mini"]),
        ("gpt-5-mini", "GPT-5 Mini", ["gpt-5", "mini"]),
        ("gpt-4.1", "GPT-4.1", ["gpt-4"]),
    ],
    "US Frontier Models (Comparison)": [
        ("gpt-4o", "GPT-4o (Omni)", ["gpt-4o-latest"]),
        ("gpt-4o-mini", "GPT-4o Mini", ["gpt-4o-mini-latest"]),
        ("gpt-4-turbo", "GPT-4 Turbo", ["gpt-4-turbo-latest"]),
        ("o1-preview", "OpenAI o1-preview", ["o1-preview-2024-09-12"]),
        ("o1-mini", "OpenAI o1-mini", ["o1-mini-2024-09-12"]),
    ]
}
```

---

## 2. DYNAMIC MODEL FETCHING

### fetch_copilot_models() - Dynamic CLI Introspection
**File**: `/opt/n8n-copilot-shim-dev/agent_manager.py:1135-1209`

Fetches models from Copilot CLI help text via regex parsing:

```python
def fetch_copilot_models(self) -> Dict:
    """Fetch available models from copilot CLI help text"""
    if not self.copilot_bin:
        return {}
    
    try:
        cmd = [self.copilot_bin, "--help", "--no-color"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        # Method 1: Robust Regex - extract (choices: ...) section
        match = re.search(
            r"--model\s+<model>[\s\S]*?\(choices:\s*([\s\S]*?)\)", result.stdout
        )
        
        models = []
        if match:
            raw_content = match.group(1)
            models = re.findall(r'"([^"]+)"', raw_content)
        
        # Method 2: Fallback - known models sanity check
        if not models:
            fallback_models = [
                "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "claude-3.5-sonnet",
                "claude-3-5-sonnet", "claude-3.5-haiku", "gemini-1.5-pro",
                "gemini-1.5-flash", "gpt-5", "gpt-5.2", "claude-sonnet-4.5"
            ]
            found_fallbacks = [m for m in fallback_models if m in result.stdout]
            if found_fallbacks:
                loose_match = re.findall(r'"([a-zA-Z0-9\-\.]+)"', result.stdout)
                models = [m for m in loose_match 
                         if "gpt" in m or "claude" in m or "gemini" in m]
        
        # Categorize by provider
        categorized = {}
        for m in models:
            cat = "Other Models"
            if "claude" in m.lower():
                cat = "Claude Models"
            elif "gpt" in m.lower() or re.match(r"^o\d", m.lower()):
                cat = "GPT Models"
            elif "gemini" in m.lower():
                cat = "Google Models"
            
            if cat not in categorized:
                categorized[cat] = []
            categorized[cat].append(m)
        
        return categorized
    except Exception as e:
        return {}
```

**Key Features**:
- Parses `copilot --help` output for `--model` choices
- Extracts quoted model names from help text
- Falls back to known model names if regex fails
- Auto-categorizes by provider (claude, gpt, gemini)
- Returns: `Dict[str, List[str]]` - category -> [model names]

### fetch_opencode_models() - Dynamic CLI Output
**File**: `/opt/n8n-copilot-shim-dev/agent_manager.py:1211-1256`

Fetches models from OpenCode CLI `models` command:

```python
def fetch_opencode_models(self) -> Dict:
    """Fetch available models from opencode CLI"""
    try:
        cmd = [str(self.opencode_bin), "models"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.command_timeout)
        
        if result.returncode != 0:
            return {}
        
        models_by_provider = {}
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            
            # Parse "provider/model" format
            parts = line.split("/", 1)
            if len(parts) == 2:
                provider, model = parts
            else:
                provider = "other"
                model = line
            
            if provider not in models_by_provider:
                models_by_provider[provider] = []
            models_by_provider[provider].append(line)
        
        return models_by_provider
    except subprocess.TimeoutExpired:
        return {}
    except Exception as e:
        return {}
```

**Key Features**:
- Runs `opencode models` command with configurable timeout
- Parses "provider/model" format
- Groups by provider automatically
- Returns: `Dict[str, List[str]]` - provider -> [full model names]

---

## 3. MODEL RESOLUTION & ALIASING

### get_model_from_name() - Alias Resolution
**File**: `/opt/n8n-copilot-shim-dev/agent_manager.py:1685-1770`

Resolves user-friendly names/aliases to full model IDs:

```python
def get_model_from_name(self, name: str, runtime: str) -> Optional[str]:
    """Convert model name/alias to full model ID based on runtime."""
    name_lower = name.lower().strip('"\'')
    
    if runtime == "claude":
        for category, models in self.CLAUDE_MODELS.items():
            for model_id, desc, aliases in models:
                if name_lower == model_id.lower() or name_lower in aliases:
                    return model_id
        return None
    
    if runtime == "gemini":
        for category, models in self.GEMINI_MODELS.items():
            for model_id, desc, aliases in models:
                aliases_lower = [a.lower() for a in aliases]
                if name_lower == model_id.lower() or name_lower in aliases_lower:
                    return model_id
        return None
    
    if runtime == "codex":
        for category, models in self.CODEX_MODELS.items():
            for model_id, desc, aliases in models:
                aliases_lower = [a.lower() for a in aliases]
                if name_lower == model_id.lower() or name_lower in aliases_lower:
                    return model_id
        return None
    
    if runtime == "opencode":
        for category, models in self.OPENCODE_MODELS.items():
            for model_id, desc, aliases in models:
                aliases_lower = [a.lower() for a in aliases]
                if name_lower == model_id.lower() or name_lower in aliases_lower:
                    return model_id
        # If not in static list, fetch from CLI and try matching
        pass
    
    # Fetch dynamic models if above fails
    all_models = []
    if runtime == "opencode":
        models_by_provider = self.fetch_opencode_models()
        all_models = [m for sublist in models_by_provider.values() for m in sublist]
    else:  # copilot
        models_by_cat = self.fetch_copilot_models()
        all_models = [m for sublist in models_by_cat.values() for m in sublist]
    
    # 1. Exact match (case insensitive)
    for m in all_models:
        if m.lower() == name_lower:
            return m
    
    # 2. Suffix/Substring matching with preference logic
    matches = []
    for m in all_models:
        if name_lower in m.lower():
            matches.append(m)
    
    if len(matches) == 1:
        return matches[0]
    
    # Preference logic for ambiguous matches
    if matches:
        # Return longest match (most specific)
        return max(matches, key=len)
    
    return None
```

**Resolution Logic**:
1. Static models: exact match + case-insensitive alias lookup
2. Dynamic models: fetch from CLI if static fails
3. For Copilot: fetch from help text
4. For OpenCode: fetch from `opencode models` command
5. Fallback: substring matching with longest-match preference

---

## 4. /MODEL SLASH COMMAND

### Command Structure & Help Text
**File**: `/opt/n8n-copilot-shim-dev/agent_manager.py:3438-3441`

```python
**Model Management:**
   • /model list - Show available models for current runtime
   • /model set "model_name" - Switch model
   • /model current - Show current model
```

### /model list Implementation
**File**: `/opt/n8n-copilot-shim-dev/agent_manager.py:3688-3745`

```python
elif command == "/model":
    if not argument:
        argument = "list"  # Default to list if no argument provided
    if argument == "list" or argument.startswith("list "):
        if current_runtime == "opencode":
            models_by_provider = self.fetch_opencode_models()
            # Add static models for comparison
            for provider, entries in self.OPENCODE_MODELS.items():
                if provider not in models_by_provider:
                    models_by_provider[provider] = []
                for model_id, display_name, aliases in entries:
                    models_by_provider[provider].append(model_id)
            
            out = f"📋 **Available Models ({current_runtime})**\n\n"
            if not models_by_provider:
                return out + "❌ No models available. Check that OpenCode is properly configured."
            for provider in sorted(models_by_provider.keys()):
                out += f"**{provider}:**\n"
                for model_id in sorted(models_by_provider[provider]):
                    out += f"  • `{model_id}`\n"
            return out
            
        elif current_runtime == "claude":
            out = f"📋 **Available Models ({current_runtime})**\n\n"
            for cat, models in self.CLAUDE_MODELS.items():
                out += f"**{cat}:**\n"
                for mid, desc, _ in models:
                    out += f"  • `{mid}` - {desc}\n"
            return out
            
        elif current_runtime == "gemini":
            out = f"📋 **Available Models ({current_runtime})**\n\n"
            for cat, models in self.GEMINI_MODELS.items():
                out += f"**{cat}:**\n"
                for mid, desc, _ in models:
                    out += f"  • `{mid}` - {desc}\n"
            return out
            
        elif current_runtime == "codex":
            out = f"📋 **Available Models ({current_runtime})**\n\n"
            for cat, models in self.CODEX_MODELS.items():
                out += f"**{cat}:**\n"
                for mid, desc, _ in models:
                    out += f"  • `{mid}` - {desc}\n"
            return out
            
        else:
            # Default to copilot
            models_dict = self.fetch_copilot_models()
            out = f"📋 **Available Models ({current_runtime})**\n\n"
            if not models_dict:
                return out + "❌ No models available. Check that Copilot CLI is properly configured."
            for cat in sorted(models_dict.keys()):
                out += f"**{cat}:**\n"
                for mid in sorted(models_dict[cat]):
                    out += f"  • `{mid}`\n"
            return out
```

### /model set Implementation
**File**: `/opt/n8n-copilot-shim-dev/agent_manager.py:3751-3757`

```python
elif argument.startswith("set "):
    model_name = argument[4:].strip().strip('"')
    model_id = self.get_model_from_name(model_name, current_runtime)
    if not model_id:
        return f"Unknown model '{model_name}' for runtime {current_runtime}"
    self.update_session_field(n8n_session_id, "model", model_id)
    return f"✓ Switched to model `{model_id}`"
```

### /model current Implementation
**File**: `/opt/n8n-copilot-shim-dev/agent_manager.py:3747-3750`

```python
elif argument == "current":
    return (
        f"Current Model: `{session_data.get('model')}` ({current_runtime})"
    )
```

---

## 5. /API/V1/MODELS REST ENDPOINT

### Route Handler
**File**: `/opt/n8n-copilot-shim-dev/agent_manager.py:4695-4742`

```python
@app.get("/api/v1/models")
async def get_models(runtime: str = "copilot"):
    """Return available models for the specified runtime."""
    runtime = runtime.lower().strip()
    
    if runtime == "claude":
        models = []
        for group, entries in session_mgr.CLAUDE_MODELS.items():
            for model_id, display_name, aliases in entries:
                models.append({"id": model_id, "label": display_name})
        return {"runtime": runtime, "models": models}
    
    elif runtime == "gemini":
        models = []
        for group, entries in session_mgr.GEMINI_MODELS.items():
            for model_id, display_name, aliases in entries:
                models.append({"id": model_id, "label": display_name})
        return {"runtime": runtime, "models": models}
    
    elif runtime == "codex":
        models = []
        for group, entries in session_mgr.CODEX_MODELS.items():
            for model_id, display_name, aliases in entries:
                models.append({"id": model_id, "label": display_name})
        return {"runtime": runtime, "models": models}
    
    elif runtime == "copilot":
        try:
            raw = session_mgr.fetch_copilot_models()
            models = [{"id": m, "label": m} for group in raw.values() for m in group]
            return {"runtime": runtime, "models": models}
        except Exception as e:
            return {"runtime": runtime, "models": [], "error": str(e)}
    
    elif runtime == "opencode":
        try:
            raw = session_mgr.fetch_opencode_models()
            models = [{"id": m, "label": m} for group in raw.values() for m in group]
            # Add static comparison models
            for group, entries in session_mgr.OPENCODE_MODELS.items():
                for model_id, display_name, aliases in entries:
                    models.append({"id": model_id, "label": f"{display_name} (Comparison)"})
            return {"runtime": runtime, "models": models}
        except Exception as e:
            return {"runtime": runtime, "models": [], "error": str(e)}
    
    else:
        return {"runtime": runtime, "models": [], "error": f"Unknown runtime: {runtime}"}
```

**Response Format**:
```json
{
  "runtime": "claude",
  "models": [
    {"id": "sonnet", "label": "Claude Sonnet (Latest)"},
    {"id": "haiku", "label": "Claude Haiku (Latest)"},
    ...
  ]
}
```

---

## 6. RUNTIME CLI INTEGRATION

### SessionManager Initialization
**File**: `/opt/n8n-copilot-shim-dev/agent_manager.py:874-940`

```python
def __init__(self, config_file: Optional[str] = None):
    # Executable paths (resolved dynamically)
    self.copilot_bin = find_executable("copilot")
    self.claude_bin = find_executable("claude")
    
    # OpenCode binary resolution
    self.opencode_bin = Path(
        find_executable("opencode")
        or str(self.opencode_home / "bin" / "opencode")
    )
    
    # Home directories for each runtime
    self.copilot_home = Path.home() / ".copilot"
    self.claude_home = Path.home() / ".claude"
    self.gemini_home = Path.home() / ".gemini"
    self.codex_home = Path.home() / ".codex"
    self.opencode_home = Path.home() / ".opencode"
    
    # Session state directories
    self.session_state_dir = self.copilot_home / "session-state"  # Copilot
    self.claude_debug_dir = self.claude_home / "debug"            # Claude
    self.gemini_session_dir = self.gemini_home / "sessions"       # Gemini
    self.codex_session_dir = self.codex_home / "sessions"         # CODEX
    self.opencode_session_storage = (
        Path.home() / ".local" / "share" / "opencode" / "storage" / "session" / "global"
    )
```

### run_copilot() - Execution with Model
**File**: `/opt/n8n-copilot-shim-dev/agent_manager.py:2828-2914`

```python
def run_copilot(self, prompt: str, model: str, agent: str, ...):
    """Execute Copilot CLI with configurable path access"""
    if not self.copilot_bin:
        return "Error: Copilot executable not found..."
    
    # Build command
    cmd = [
        self.copilot_bin,
        "-p", context_prompt,
        "--allow-all-tools",
        "--no-color",
        "--silent",
        "--model", model,  # Model passed to CLI
    ]
    
    # Add yolo flags if needed
    if mode == "yolo":
        cmd.insert(4, "--allow-all-paths")
        cmd.append("--yolo")
    
    # Resume session if provided
    if resume and session_id:
        cmd.extend(["--resume", session_id])
    
    output = self._execute_subprocess_with_tracking(
        cmd, agent_dir, effective_timeout, "copilot", agent, prompt, n8n_session_id
    )
    return self.strip_metadata(output, "copilot")
```

**CLI Pattern**: `copilot -p "<prompt>" --allow-all-tools --model gpt-5 [--resume <session>]`

### run_claude() - Execution with Model
**File**: `/opt/n8n-copilot-shim-dev/agent_manager.py:2979-3053`

```python
def run_claude(self, prompt: str, model: str, agent: str, ...):
    """Execute Claude CLI with configurable path access"""
    if not self.claude_bin:
        return "Error: Claude executable not found..."
    
    # Set permission mode
    permission_mode = "bypassPermissions" if mode == "yolo" else "default"
    
    cmd = [
        self.claude_bin,
        "-p", context_prompt,
        "--permission-mode", permission_mode,
        "--model", model,  # Model passed to CLI
        "--output-format", "stream-json",
        "--include-partial-messages",
        "--verbose",
    ]
    
    # Resume session if provided
    if resume and session_id:
        cmd.extend(["--resume", session_id])
    elif session_id:
        cmd.extend(["--session-id", session_id])
    
    output = self._execute_subprocess_with_tracking(
        cmd, agent_dir, effective_timeout, "claude", agent, prompt, n8n_session_id
    )
    return self.strip_metadata(output, "claude")
```

**CLI Pattern**: `claude -p "<prompt>" --model sonnet --permission-mode default [--resume <session>]`

### run_opencode() - Execution with Model
**File**: `/opt/n8n-copilot-shim-dev/agent_manager.py:2916-2977`

```python
def run_opencode(self, prompt: str, model: str, agent: str, ...):
    """Execute OpenCode CLI with configurable path access"""
    cmd = [str(self.opencode_bin), "run", "--model", model]
    
    # Resume session if provided
    if resume and session_id:
        cmd.extend(["--session", session_id])
    
    cmd.append(context_prompt)
    
    output = self._execute_subprocess_with_tracking(
        cmd, agent_dir, effective_timeout, "opencode", agent, prompt, n8n_session_id
    )
    return self.strip_metadata(output, "opencode")
```

**CLI Pattern**: `opencode run --model llama-3.3-70b-versatile "<prompt>" [--session <session_id>]`

### run_gemini() - Execution with Model
**File**: `/opt/n8n-copilot-shim-dev/agent_manager.py:3055-3122`

```python
def run_gemini(self, prompt: str, model: str, agent: str, ...):
    """Execute Gemini CLI with full tool access"""
    # Note: Gemini CLI appears to have model handling issues
    # For now, use default model and do not pass --model flag
    
    cmd = ["gemini"]
    if mode == "yolo":
        cmd.append("--yolo")
    cmd.append(context_prompt)
    
    # Resume session if provided
    if resume and session_id:
        cmd.extend(["--resume", session_id])
```

**CLI Pattern**: `gemini "<prompt>" [--yolo] [--resume <session_id>]`

**Note**: Gemini CLI does not support `--model` flag yet

### run_codex() - Execution with Model
**File**: `/opt/n8n-copilot-shim-dev/agent_manager.py:3124-3215`

```python
def run_codex(self, prompt: str, model: str, agent: str, ...):
    """Execute CODEX CLI with configurable access"""
    
    if resume and session_id:
        cmd = ["codex", "exec", "resume"]
        if mode == "yolo":
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
            cmd += ["-c", "shell_environment_policy.inherit=all"]
        cmd += [session_id, context_prompt]
    else:
        cmd = ["codex", "exec"]
        if mode == "yolo":
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
            cmd += ["-c", "shell_environment_policy.inherit=all"]
        cmd.append(context_prompt)
    
    output = self._execute_subprocess_with_tracking(
        cmd, agent_dir, effective_timeout, "codex", agent, prompt, n8n_session_id
    )
    return self.strip_metadata(output, "codex")
```

**CLI Pattern**: `codex exec "<prompt>" [--dangerously-bypass-approvals-and-sandbox]`

**Note**: Model specification not visible in current implementation

---

## 7. TELEGRAM & WEBEX CONNECTORS

### Telegram Model Pinning
**File**: `/opt/n8n-copilot-shim-dev/telegram_connector.py`

#### Get Pinned Model (lines 132-137)
```python
def get_pinned_model(self, user_id: int) -> Optional[str]:
    """Get the pinned model for a user, or None if not set"""
    pinned = self.config.get("pinned_users", {}).get(str(user_id))
    if pinned:
        return pinned.get("model")
    return None
```

#### Enforce Pinned Session (lines 221-236)
```python
def _enforce_pinned_session(self, user_id: int, session_id: str):
    """For pinned users, push pinned agent/runtime/model into the SessionManager.
    Must be called before every query or command."""
    if not self.config.is_user_pinned(user_id):
        return
    session_mgr = self.get_session_manager(session_id)
    pinned_model = self.config.get_pinned_model(user_id)
    if pinned_model:
        session_mgr.update_session_field(session_id, "model", pinned_model)
```

#### Block /model set for Pinned Users (lines 1042-1047)
```python
elif cmd_lower.startswith("/model set") and self.config.get_pinned_model(user_id):
    pinned_model = self.config.get_pinned_model(user_id)
    self.send_message(
        chat_id,
        f"❌ Your model is pinned to **{pinned_model}** by an administrator. You cannot change models.",
    )
```

### WebEX Model Handling
**File**: `/opt/n8n-copilot-shim-dev/webex_connector.py` - Similar structure to Telegram

---

## 8. TEST COVERAGE

### Model Resolution Tests
**File**: `/opt/n8n-copilot-shim-dev/tests/test_agent_manager.py:475-533`

```python
class TestModelResolution(unittest.TestCase):
    """Test model name resolution and switching"""
    
    def test_get_claude_model_by_alias(self):
        """Test resolving Claude models by alias"""
        result = self.manager.get_model_from_name("sonnet", "claude")
        self.assertEqual(result, "sonnet")
        
        result = self.manager.get_model_from_name("haiku", "claude")
        self.assertEqual(result, "haiku")
    
    def test_get_claude_model_by_full_name(self):
        """Test resolving Claude models by full name"""
        result = self.manager.get_model_from_name("claude-sonnet-4.5", "claude")
        self.assertEqual(result, "sonnet")
    
    def test_get_invalid_model(self):
        """Test resolving non-existent model"""
        result = self.manager.get_model_from_name("nonexistent-model", "claude")
        self.assertIsNone(result)
    
    @patch.object(SessionManager, "fetch_copilot_models")
    def test_get_copilot_model_exact_match(self, mock_fetch):
        """Test exact model name matching for Copilot"""
        mock_fetch.return_value = {
            "GPT Models": ["gpt-5", "gpt-4"],
            "Other": ["gemini-pro"],
        }
        
        result = self.manager.get_model_from_name("gpt-5", "copilot")
        self.assertEqual(result, "gpt-5")
    
    @patch.object(SessionManager, "fetch_copilot_models")
    def test_get_copilot_model_substring_match(self, mock_fetch):
        """Test substring matching for Copilot models"""
        mock_fetch.return_value = {
            "GPT Models": ["gpt-5.2", "gpt-4"],
            "Other": ["gemini-pro"],
        }
        
        result = self.manager.get_model_from_name("5.2", "copilot")
        self.assertEqual(result, "gpt-5.2")
```

---

## 9. SESSION DATA STRUCTURE

### Model Storage in Sessions
**File**: `/opt/n8n-copilot-shim-dev/agent_manager.py`

Session data stored per user contains:
```python
{
    "session_id": "uuid",
    "model": "gpt-5-mini",        # Current model ID
    "runtime": "copilot",          # Current runtime
    "agent": "orchestrator",       # Current agent
    "yolo_mode": "restricted",     # Access mode
    "timeout": 300,                # Command timeout
    "render_type": "text",         # Output format
    "channel": "webui"             # Communication channel
}
```

### Model Initialization
Default model set in config files:
- `telegram_config.json`: `"default_model": "gpt-5-mini"`
- `webex_config.json`: `"default_model": "gpt-5-mini"`

---

## 10. EXECUTION FLOW

### /model set Flow
1. User executes: `/model set sonnet`
2. `parse_slash_command()` extracts `/model` and `set sonnet`
3. `execute()` calls `/model set` handler
4. Handler calls `get_model_from_name("sonnet", "claude")`
5. Alias resolver finds "sonnet" in CLAUDE_MODELS
6. Returns model_id: `"sonnet"`
7. Calls `update_session_field(session_id, "model", "sonnet")`
8. On next prompt, runtime CLI invoked with `--model sonnet`

### /api/v1/models Flow
1. Client: `GET /api/v1/models?runtime=claude`
2. Endpoint extracts runtime parameter
3. Iterates over `CLAUDE_MODELS` dictionary
4. Builds response: `{"id": model_id, "label": display_name}`
5. Returns JSON with all models for runtime

---

## 11. KEY FEATURES

| Feature | Implementation | Location |
|---------|----------------|----------|
| **Static Models** | Hard-coded dictionaries | agent_manager.py:770-872 |
| **Dynamic Models** | CLI subprocess + regex | agent_manager.py:1135-1256 |
| **Model Resolution** | Alias + substring matching | agent_manager.py:1685-1770 |
| **CLI Integration** | Subprocess with --model flag | agent_manager.py:2828-3215 |
| **REST API** | /api/v1/models endpoint | agent_manager.py:4695-4742 |
| **Slash Commands** | /model list/set/current | agent_manager.py:3688-3757 |
| **Model Pinning** | Telegram/WebEX config | telegram_connector.py:132-236 |
| **Session Storage** | Per-user model in JSON | agent_manager.py session_map |

---

## 12. FALLBACK & ERROR HANDLING

- **Dynamic fetch fails**: Returns empty dict, slash commands show empty list
- **Invalid model name**: `get_model_from_name()` returns `None`, user gets error message
- **CLI not found**: Returns error message with installation instructions
- **CLI timeout**: Caught and logged, returns empty dict
- **Regex parsing fails**: Falls back to known model names, then loose regex

---

## 13. CONFIGURATION DEFAULTS

### Environment Variables
- `COPILOT_DEFAULT_MODEL`: Default model for copilot runtime (default: "gpt-5-mini")
- `COMMAND_TIMEOUT`: Max seconds for CLI execution (default: 10)

### Configuration Files
- `agents.json`: Agent definitions
- `telegram_config.json`: Telegram-specific settings
- `webex_config.json`: WebEX-specific settings
- `opencode.example.json`: OpenCode setup (if needed)

---

## Summary

The model listing implementation provides:
1. **Static definitions** for Claude, Gemini, CODEX, OpenCode with multiple aliases
2. **Dynamic fetching** from Copilot and OpenCode CLIs via subprocess
3. **Smart resolution** combining alias lookup + substring matching with fallbacks
4. **CLI integration** passing model names to runtimes via `--model` flag
5. **REST API** for programmatic access
6. **Slash commands** for user-friendly interaction
7. **Session persistence** storing selected models per user
8. **Admin control** via model pinning for specific users
9. **Comprehensive error handling** with fallbacks and user-friendly messages

