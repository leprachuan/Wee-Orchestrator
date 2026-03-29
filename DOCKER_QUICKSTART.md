# Wee Orchestrator — Docker Quickstart

## Requirements
- Docker + Docker Compose plugin
- API keys / credentials for the AI runtimes you want to use (see below)

## One-liner (interactive)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/leprachuan/Wee-Orchestrator/main/docker-setup.sh)
```

## Non-interactive (CI/automation)

```bash
WEE_NON_INTERACTIVE=1 \
WEE_AUTH_SECRET=$(openssl rand -base64 32) \
WEE_TELEGRAM_TOKEN=your_bot_token \
  bash <(curl -fsSL https://raw.githubusercontent.com/leprachuan/Wee-Orchestrator/main/docker-setup.sh)
```

## What docker-setup.sh does
- Generates docker-compose.yml with correct bind mounts
- Configures Telegram bot integration (optional)
- Starts containers and prints your URL

## Pre-installed AI Runtimes

The Docker image ships with **all five** supported AI runtime CLIs pre-installed.
No additional binary installation is required after deployment.

| Runtime    | Binary    | Version Source                  | Pre-installed? |
|------------|-----------|---------------------------------|:--------------:|
| **Copilot**  | `copilot` | `@github/copilot` (npm)       | ✅ Yes          |
| **Claude**   | `claude`  | `@anthropic-ai/claude-code` (npm) | ✅ Yes      |
| **Gemini**   | `gemini`  | `@google/gemini-cli` (npm)    | ✅ Yes          |
| **Codex**    | `codex`   | `@openai/codex` (npm)         | ✅ Yes          |
| **OpenCode** | `opencode`| GitHub release binary          | ✅ Yes          |

### Runtime Authentication

Binaries are pre-installed but **credentials are NOT included** in the image.
After starting the container, authenticate each runtime you plan to use:

| Runtime    | How to Authenticate                                               |
|------------|-------------------------------------------------------------------|
| **Copilot**  | Run `copilot auth login` inside the container (interactive GitHub OAuth) or mount a pre-authenticated `~/.config/gh/hosts.yml` as a volume. |
| **Claude**   | Set `ANTHROPIC_API_KEY` environment variable in `.env` or `docker-compose.yml`. |
| **Gemini**   | Set `GEMINI_API_KEY` environment variable, or run `gemini auth` inside the container. |
| **Codex**    | Set `OPENAI_API_KEY` environment variable in `.env` or `docker-compose.yml`. |
| **OpenCode** | Provide an API key via its configuration file or environment variable (see OpenCode docs). |

**Environment variable example** (add to your `.env` file):
```bash
# AI Runtime API Keys (uncomment and fill in the ones you need)
# ANTHROPIC_API_KEY=sk-ant-...
# GEMINI_API_KEY=AI...
# OPENAI_API_KEY=sk-...
```

### Caveats

- **Copilot** requires an interactive OAuth login (`copilot auth login`).
  It cannot be pre-authenticated via environment variable alone.  To avoid
  interactive login on every container start, bind-mount a pre-authenticated
  `~/.config/gh/hosts.yml` from the host.
- **Claude**, **Codex**, and **OpenCode** work with API key environment
  variables — no interactive login needed.
- **Gemini** supports both `GEMINI_API_KEY` env var and interactive
  `gemini auth` login.
- Runtime versions are pinned at image build time.  Rebuild the image to
  pick up newer CLI versions.

## Non-interactive Environment Variables

| Variable | Purpose | Required? |
|----------|---------|-----------|
| `WEE_NON_INTERACTIVE` | Skip interactive prompts | For CI only |
| `WEE_AUTH_SECRET` | API auth token | Yes |
| `WEE_TELEGRAM_TOKEN` | Telegram bot token | No |
| `COPILOT_DEFAULT_RUNTIME` | Default runtime (copilot/claude/gemini/opencode/codex) | No (default: copilot) |

## Managing the Deployment

```bash
# View logs
docker compose logs -f

# Restart
docker compose restart

# Stop
docker compose down

# Update image
docker compose pull && docker compose up -d

# Rebuild image locally (to pick up newer runtime versions)
docker compose build --no-cache && docker compose up -d
```
