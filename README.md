# n8n-copilot-shim

A unified AI agent manager that bridges N8N workflows with multiple AI CLI tools (GitHub Copilot, OpenCode, Claude Code, Google Gemini). Features session management, multi-agent support with dynamic configuration, and model switching.

## Overview

This shim provides a flexible framework to:
- Call AI CLIs (Copilot, OpenCode, Claude Code, Gemini) from N8N workflows
- Maintain session affinity across multiple conversation turns
- Switch between different agent repositories dynamically
- Configure agents via JSON config files instead of hardcoding
- Support multiple AI models and runtimes
- Execute bash commands directly with `!` prefix

## Requirements

This project requires one or more of the following AI CLI tools to be installed:

### Claude Code CLI

**Prerequisites:**
- Node.js 18+ (for npm installation) OR native binary support
- Anthropic API key for authentication

**Installation:**

Native binary (recommended):
```bash
curl -fsSL https://claude.ai/install.sh | bash
```

Or via npm:
```bash
npm install -g @anthropic-ai/claude-code
```

**Supported Systems:** macOS 10.15+, Linux (Ubuntu 20.04+/Debian 10+, Alpine), Windows 10+ (via WSL)

**Reference:** [Claude Code Quickstart Documentation](https://code.claude.com/docs/en/quickstart)

### GitHub Copilot CLI

**Prerequisites:**
- Node.js 22 or higher
- Active GitHub Copilot subscription (Pro, Pro+, Business, or Enterprise plan)
- GitHub account for authentication

**Installation:**

```bash
npm install -g @github/copilot
copilot  # Launch and authenticate
```

For authentication, use the `/login` command or set `GH_TOKEN` environment variable with a fine-grained PAT.

**Supported Systems:** macOS, Linux, Windows (via WSL)

**Reference:** [GitHub Copilot CLI Installation Guide](https://docs.github.com/en/copilot/how-tos/set-up/install-copilot-cli)

### OpenCode CLI

**Prerequisites:**
- Node.js or compatible runtime

**Installation (Recommended):**

```bash
curl -fsSL https://opencode.ai/install | bash
```

Or via npm:
```bash
npm i -g opencode-ai@latest
```

Alternative package managers:
- Homebrew: `brew install opencode`
- Scoop (Windows): `scoop bucket add extras && scoop install extras/opencode`
- Arch Linux: `paru -S opencode-bin`

**Supported Systems:** Windows, macOS, Linux

**Reference:** [OpenCode Documentation](https://opencode.ai/docs/)

### Google Gemini CLI

**Prerequisites:**
- Python 3.7 or higher
- Google Cloud account with Gemini API access
- Google API key for authentication

**Installation:**

```bash
pip install google-generativeai
# Or using the CLI wrapper
pip install gemini-cli
```

**Authentication:**

Set your API key as an environment variable:
```bash
export GOOGLE_API_KEY='your-api-key-here'
```

Or configure it in your shell profile:
```bash
echo 'export GOOGLE_API_KEY="your-api-key-here"' >> ~/.bashrc
source ~/.bashrc
```

**Supported Systems:** Windows, macOS, Linux

**Reference:** [Google Gemini API Documentation](https://ai.google.dev/tutorials/python_quickstart)

## Tool Permissions & Access Control

All AI runtimes in this system are configured with **full tool access** to enable read, write, and execute operations without approval prompts. This provides maximum automation capabilities.

### Permission Configuration by Runtime

#### GitHub Copilot CLI
- **Flags Used:** `--allow-all-tools --allow-all-paths`
- **Enables:** 
  - All MCP tools and shell commands without approval
  - Read/write/execute permissions for all files and directories
- **Security Note:** Gives Copilot the same permissions as your user account

#### Claude Code CLI
- **Flags Used:** `--permission-mode bypassPermissions`
- **Enables:**
  - Auto-approve all file edits, writes, and reads
  - Execute shell commands without approval
  - Access web/network tools without prompts
- **Also Known As:** YOLO mode or dontAsk mode

#### OpenCode CLI
- **Configuration:** Uses `opencode.json` file for permission settings
- **Required Setup:**
  1. Copy the example config: `cp opencode.example.json opencode.json`
  2. Place `opencode.json` in your agent directories or project root
- **Permissions Enabled:**
  - `edit`: allow
  - `write`: allow
  - `bash`: allow
  - `read`: allow
  - `webfetch`: allow
- **Reference:** [OpenCode Permissions Documentation](https://opencode.ai/docs/permissions/)

#### Google Gemini CLI
- **Flags Used:** `--yolo`
- **Enables:**
  - Read/write file operations without confirmation
  - Shell command execution without approval
  - All built-in tools with unrestricted access
- **Built-in Tools:** read_file, write_file, run_shell_command

#### OpenAI Codex CLI
- **Flags Used:** `--dangerously-bypass-approvals-and-sandbox`
- **Enables:**
  - Disables all approval prompts
  - Removes sandbox restrictions (full file system access)
  - Allows all shell commands and tools without confirmation
- **Security Note:** Only use in trusted, controlled environments

### Security Considerations

⚠️ **Warning:** These configurations grant AI agents extensive system access:

- **Full file system access:** Can read, modify, or delete any file your user can access
- **Command execution:** Can run any shell command with your user privileges
- **No safety prompts:** All operations execute automatically without confirmation

**Best Practices:**
1. **Use in controlled environments:** Development containers, VMs, or sandboxed systems
2. **Regular backups:** Maintain backups of critical files and directories
3. **Code review:** Review AI-generated changes before committing to production
4. **Limit agent scope:** Configure agents to work in specific project directories
5. **Monitor activity:** Review session logs and agent outputs regularly

**Recommended Use Cases:**
- ✅ Development and testing environments
- ✅ Automated CI/CD pipelines in isolated containers
- ✅ Personal projects with version control
- ❌ Production systems without review
- ❌ Shared systems with sensitive data
- ❌ Public or untrusted environments

## Configuration

### Agent Configuration

The system loads agents from `agents.json` or a custom config file. Each agent represents a repository context where the AI CLI will operate.

**Config Format:**
```json
{
  "agents": [
    {
      "name": "devops",
      "description": "DevOps and infrastructure management",
      "path": "/path/to/MyHomeDevops"
    },
    {
      "name": "projects",
      "description": "Software development projects",
      "path": "/path/to/projects"
    }
  ]
}
```

**Configuration Fields:**
- `name` (required): Short identifier for the agent (used in `/agent set` commands)
- `description` (required): Brief human-readable description of the agent
- `path` (required): Full path to the repository or project directory

### Environment Configuration

The default agent, model, and runtime can be customized via environment variables. This is useful for:
- Different users having different defaults
- Docker container configuration
- CI/CD pipeline customization
- Development vs. production setups

**Available Environment Variables:**

```bash
# Default agent for new sessions
COPILOT_DEFAULT_AGENT=orchestrator        # Default: orchestrator

# Default model for new sessions  
COPILOT_DEFAULT_MODEL=gpt-5-mini          # Default: gpt-5-mini

# Default runtime for new sessions
COPILOT_DEFAULT_RUNTIME=copilot           # Default: copilot
```

**Usage Examples:**

```bash
# Set orchestrator as default
export COPILOT_DEFAULT_AGENT=orchestrator
export COPILOT_DEFAULT_RUNTIME=copilot

# Or set family agent with Claude runtime
export COPILOT_DEFAULT_AGENT=family
export COPILOT_DEFAULT_MODEL=claude-sonnet
export COPILOT_DEFAULT_RUNTIME=claude

# Run the agent
python3 agent_manager.py "Your prompt" "session_id"
```

**Docker Example:**

```dockerfile
ENV COPILOT_DEFAULT_AGENT=orchestrator
ENV COPILOT_DEFAULT_MODEL=gpt-5-mini
ENV COPILOT_DEFAULT_RUNTIME=copilot
```

**Reference Configuration:**

Copy `.env.example` to `.env` and customize:

```bash
cp .env.example .env
# Edit .env with your defaults
```

When environment variables are not set, the system uses these hardcoded defaults:
- Agent: `orchestrator`
- Model: `gpt-5-mini`
- Runtime: `copilot`

### Setup

1. **Copy the agent manager script:**
   ```bash
   cp agent_manager.py /usr/local/bin/agent-manager
   chmod +x /usr/local/bin/agent-manager
   ```

2. **Configure your agents:**
   - Copy `agents.example.json` to `agents.json`
   - Edit `agents.json` with your actual repository paths
   - Place `agents.json` in the same directory as the script or current working directory

3. **Optional: Specify config location via environment variable**
   ```bash
   export AGENTS_CONFIG=/path/to/custom/agents.json
   ```

## Usage

### Command Line

The agent manager supports both positional arguments (for backwards compatibility) and named options for more flexibility.

#### Basic Usage (Positional Arguments)

```bash
python agent_manager.py "<prompt>" [session_id] [config_file]
```

**Arguments:**
- `prompt`: The prompt/command to send to the AI CLI
- `session_id` (optional): N8N session identifier for tracking conversations (default: "default")
- `config_file` (optional): Path to agents.json config file

**Examples:**
```bash
# Basic usage
python agent_manager.py "List all files in the current directory"

# With session ID
python agent_manager.py "Continue debugging the issue" "session-123"

# With custom config file
python agent_manager.py "Deploy the app" "session-456" "/etc/agents.json"
```

#### Advanced Usage (Named Arguments)

```bash
python agent_manager.py [options] "<prompt>" [session_id]
```

**Options:**

Agent Options:
- `--agent NAME` - Set the agent to use (e.g., devops, family, projects)
- `--list-agents` - List all available agents and exit

Model Options:
- `--model NAME` - Set the model to use (e.g., gpt-5, sonnet, gemini-1.5-pro)
- `--list-models` - List all available models for current runtime and exit

Runtime Options:
- `--runtime NAME` - Set the runtime to use (choices: copilot, opencode, claude, gemini, codex)
- `--list-runtimes` - List all available runtimes and exit

Configuration:
- `--config FILE` or `-c FILE` - Path to agents.json configuration file

**Examples:**

```bash
# List available agents
python agent_manager.py --list-agents

# List available agents with custom config
python agent_manager.py --list-agents --config my-agents.json

# List available runtimes
python agent_manager.py --list-runtimes

# List available models
python agent_manager.py --list-models

# Set agent via CLI
python agent_manager.py --agent devops "Check server status"

# Set runtime and model via CLI
python agent_manager.py --runtime gemini --model gemini-1.5-pro "Analyze this code"

# Combine multiple options
python agent_manager.py --agent family --runtime claude --model sonnet "Find recipes for dinner"

# Use custom configuration file
python agent_manager.py --config /etc/my-agents.json --agent projects "Review pull requests"

# All options together
python agent_manager.py --config my-agents.json --agent devops --runtime claude --model haiku "Deploy to production" "session-123"
```

**Getting Help:**
```bash
python agent_manager.py --help
```

### Slash Commands

Interact with the agent manager using slash commands:

#### Bash Commands
```
!<command>                 # Execute bash command directly (e.g., !pwd, !ls -la)
```

**Examples:**
```bash
!pwd                       # Show current working directory
!echo "Hello World"        # Echo a message
!ls -lh                    # List files with details
!date                      # Show current date/time
!git status                # Run git commands
!python3 --version         # Check installed versions
```

**Features:**
- Commands execute directly without hitting any AI runtime
- 10-second timeout for safety
- Runs in current working directory
- Supports pipes, redirects, and command chaining (&&, ||, |)
- Returns stdout/stderr output

#### Runtime Management
```
/runtime list              # Show available runtimes (copilot, opencode, claude, gemini)
/runtime set <runtime>     # Switch runtime (e.g., /runtime set gemini)
/runtime current           # Show current runtime
```

#### Model Management
```
/model list                # Show available models for current runtime
/model set "<model>"       # Switch model (e.g., /model set "claude-opus-4.5")
/model current             # Show current model
```

#### Agent Management
```
/agent list                # Show all available agents with descriptions
/agent set "<agent>"       # Switch to an agent (e.g., /agent set "projects")
/agent current             # Show current agent and its context
```

#### Session Management
```
/session reset             # Reset the current session (starts fresh next message)
/help                      # Show all available commands
```

#### Query Management
```
/status                    # Check status of running query for this session
/cancel                    # Cancel running query for this session
```

**Query Tracking**: When a query is executing, the agent manager tracks its process ID (PID), runtime, agent, and output. Use `/status` to check if a query is running and see recent output, or `/cancel` to terminate a long-running query.

### N8N Integration

Use in an N8N workflow:

#### Basic N8N Integration (Positional Arguments)
```javascript
// Execute the agent manager from N8N
const { exec } = require('child_process');
const prompt = "Your prompt here";
const sessionId = "n8n_session_123";
const configFile = "/path/to/agents.json";

exec(`python agent_manager.py "${prompt}" "${sessionId}" "${configFile}"`,
  (error, stdout, stderr) => {
    if (error) console.error(error);
    console.log(stdout);
  }
);
```

#### Advanced N8N Integration (Named Arguments)
```javascript
// Execute with specific agent, runtime, and model
const { exec } = require('child_process');
const agent = "devops";
const runtime = "claude";
const model = "sonnet";
const prompt = "Check production status";
const sessionId = "n8n_session_123";

const cmd = `python agent_manager.py --agent ${agent} --runtime ${runtime} --model ${model} "${prompt}" "${sessionId}"`;

exec(cmd, (error, stdout, stderr) => {
  if (error) console.error(error);
  console.log(stdout);
});
```

#### List Agents from N8N
```javascript
// Get available agents dynamically
const { exec } = require('child_process');
const configFile = "/path/to/agents.json";

exec(`python agent_manager.py --list-agents --config ${configFile}`,
  (error, stdout, stderr) => {
    if (error) console.error(error);
    // Parse stdout to get agent list
    console.log(stdout);
  }
);
```

## Session Management

Sessions are automatically tracked and stored in:
- **Copilot:** `~/.copilot/n8n-session-map.json`
- **OpenCode:** `~/.opencode/n8n-session-map.json`
- **Claude:** `~/.claude/` (debug directory)
- **Gemini:** `~/.gemini/sessions/`

Each N8N session ID is mapped to:
- A unique backend session ID (for resuming AI CLI sessions)
- Current runtime (copilot/opencode/claude/gemini)
- Current model
- Current agent

Session data persists across requests, allowing multi-turn conversations.

### Query Tracking

Running queries are tracked in `~/.copilot/running-queries.json` with:
- **PID**: Process ID for the running query
- **Runtime**: Which AI runtime is executing the query
- **Agent**: Which agent context is being used
- **Start Time**: When the query started
- **Last Output**: Recent output snippet (last 500 characters)

This enables the `/status` and `/cancel` commands to monitor and control long-running queries.

## Default Behavior

When creating a new session:
- **Runtime:** copilot (use `/runtime set` to change)
- **Model:** gpt-5-mini (Copilot) / opencode/gpt-5-nano (OpenCode) / haiku (Claude) / gemini-1.5-flash (Gemini)
- **Agent:** devops (or first available agent from config)

## Advanced Features

### Dynamic Agent Loading
Instead of hardcoding agent paths, the system:
1. Looks for `agents.json` in the current directory
2. Falls back to the script directory if not found
3. Supports custom config paths via argument

### Session Resumption
- The system automatically detects and resumes existing sessions
- If a session is lost or corrupted, it starts a fresh session automatically
- Use `/session reset` to explicitly clear session state

### Model Resolution
The system intelligently matches model names:
- Exact matches (case-insensitive)
- Substring/suffix matching
- Latest version preference for ambiguous matches

### Metadata Stripping
Automatically removes CLI metadata from output:
- Thinking tags (`<think>...</think>`)
- Token usage statistics
- Session headers and banners

## Testing

A comprehensive test suite is included to ensure code quality and prevent regressions when making changes.

### Running Tests

#### Quick Start

```bash
# Run all tests
./run_tests.sh

# Or using Python directly
python3 -m unittest discover -s tests -p "test_*.py" -v
```

#### Test Options

```bash
# Run with verbose output
./run_tests.sh -v

# Run specific test class
./run_tests.sh -t tests.test_agent_manager.TestSlashCommands

# Generate coverage report
./run_tests.sh -c
```

### Test Coverage

The test suite includes 62 tests covering:

- **Session Management** (5 tests) - Creating, resuming, and persisting sessions
- **Agent Configuration** (4 tests) - Loading and managing agent configurations
- **Slash Commands** (9 tests) - All interactive commands (`/help`, `/runtime`, `/model`, `/agent`, `/session`)
- **Query Tracking** (8 tests) - Process tracking for `/status` and `/cancel` commands
- **Model Resolution** (5 tests) - Converting model names/aliases to full IDs
- **Metadata Stripping** (4 tests) - Cleaning CLI output from different runtimes
- **Agent Switching** (3 tests) - Changing agents and session context
- **Session Existence** (2 tests) - Checking session state file existence

### Test Results

All tests pass with no external CLI dependencies required:

```
Ran 31 tests in 0.021s
OK
```

Tests use mocking to isolate functionality and avoid:
- Executing real CLI commands (Copilot, OpenCode, Claude)
- Modifying user's home directory
- Making real API calls

### Adding Tests

When adding new features to `agent_manager.py`:

1. Add corresponding test cases to `tests/test_agent_manager.py`
2. Run the full test suite to ensure no regressions
3. Aim for high coverage of new functionality

For detailed testing documentation, see [tests/README.md](tests/README.md).

## File Structure

```
n8n-copilot-shim-1/
├── agent_manager.py           # Main agent manager script
├── agents.json                # Agent configuration (git-ignored)
├── agents.example.json        # Example configuration template
├── run_tests.sh               # Test runner script
├── .testrc                    # Test configuration
├── EXAMPLE_WORKFLOW.json      # N8N workflow example
├── README.md                  # This file
├── tests/
│   ├── __init__.py            # Test package marker
│   ├── test_agent_manager.py  # Comprehensive unit tests
│   └── README.md              # Testing documentation
└── .gitignore                 # Git configuration
```

## Architecture

### SessionManager Class

The core class that manages:
- Agent configuration loading
- AI CLI execution (Copilot, OpenCode, Claude)
- Session state persistence
- Slash command parsing and execution
- Model and runtime switching

### Key Methods

- `_load_agents_config()` - Load agents from JSON config file
- `execute()` - Main entry point for processing prompts and commands
- `run_copilot()`, `run_opencode()`, `run_claude()` - Execute respective CLIs
- `strip_metadata()` - Clean CLI output
- `set_agent()`, `update_session_field()` - Session state management

## Troubleshooting

### Agents not loading
- Check that `agents.json` exists in the script directory or current directory
- Verify JSON syntax with `python -m json.tool agents.json`
- Check file permissions

### Session issues
- Run `/session reset` to start fresh
- Check session storage directories exist:
  - `~/.copilot/session-state/`
  - `~/.local/share/opencode/storage/session/global/`
  - `~/.claude/debug/`
  - `~/.gemini/sessions/`

### CLI not found
- Ensure copilot, opencode, claude, and gemini binaries are in PATH or at expected locations
- Check `/usr/bin/copilot`, `/usr/bin/claude`, `~/.opencode/bin/opencode`, and `gemini` in PATH

## Migration from Original Script

This version replaces hardcoded agents with JSON configuration:

**Before:**
```python
AGENTS = {
    'devops': {'path': '/opt/MyHomeDevops', ...},
    'family': {'path': '/opt/family_knowledge', ...}
}
```

**After:**
```json
{
  "agents": [
    {"name": "devops", "path": "/opt/MyHomeDevops", ...},
    {"name": "family", "path": "/opt/family_knowledge", ...}
  ]
}
```

To migrate:
1. Copy `agent_manager.py` to your environment
2. Create `agents.json` with your agent definitions
3. No code changes needed - same interface as before

## Agent Orchestration

This project supports multi-agent orchestration with dynamic agent discovery. See the comprehensive agent documentation:

- **[AGENTS.md](./AGENTS.md)** - Agent orchestration overview and usage guide
- **[SKILL_SUBAGENTS.md](./SKILL_SUBAGENTS.md)** - Detailed subagent management and advanced patterns
- **[agents.json](./agents.json)** - Agent configuration file (controls available agents)

### Quick Agent Start

```bash
# List available agents
/agent list

# Switch to an agent
/agent set devops

# Execute in agent context
"Deploy the latest version"

# Resume agent session
"What's the status?"

# Switch to different agent
/agent set family
```

All agents are loaded dynamically from `agents.json`, enabling easy expansion and customization.

## Telegram Connector

The Telegram connector bridges Telegram chat with your N8N Copilot Shim agents.

### Features

- 💬 Receive messages from Telegram users
- 👤 User pairing by Telegram user ID
- 🔐 User access control (whitelist/blacklist)
- 🎯 Route to any configured agent
- ⚙️ Per-user session management

### Quick Start

```bash
# With environment variable
export TELEGRAM_BOT_TOKEN="your-token-here"
python telegram_connector.py

# Or with token argument
python telegram_connector.py --token "your-token-here"
```

### Managing Users

```bash
# Allow specific user
python telegram_connector.py --token TOKEN --allow-user 123456789

# Deny user
python telegram_connector.py --token TOKEN --deny-user 123456789

# List allowed users
python telegram_connector.py --token TOKEN --list-users
```

See **[TELEGRAM_CONNECTOR.md](./TELEGRAM_CONNECTOR.md)** for full documentation.

