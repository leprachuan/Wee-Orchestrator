# Wee Orchestrator — Docker Quickstart

## One-liner (interactive)
```bash
git clone https://github.com/leprachuan/Wee-Orchestrator && cd Wee-Orchestrator && ./docker-setup.sh
```

## Non-interactive (CI/automation)
```bash
WEE_NON_INTERACTIVE=1 \
WEE_AUTH_SECRET="$(openssl rand -base64 32)" \
WEE_TELEGRAM_TOKEN="your-bot-token" \
./docker-setup.sh
```

## What docker-setup.sh does
- Auto-detects AI runtimes (Copilot CLI, Claude Code, Gemini, OpenCode)
- Generates docker-compose.yml with correct bind mounts for each found runtime
- Configures Telegram bot integration (optional)
- Starts containers and prints your URL

## Runtimes
The script auto-detects and mounts these if found on your host:

| Runtime | Binary | Auth Config |
|---------|--------|-------------|
| GitHub Copilot CLI | `~/.local/bin/copilot` | `~/.config/gh/hosts.yml` |
| Claude Code CLI | `~/.local/bin/claude` | `~/.claude/` |
| Gemini CLI | `~/.local/bin/gemini` | — |
| OpenCode | `~/.local/bin/opencode` | — |

## Non-interactive Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `WEE_NON_INTERACTIVE` | Set to `1` to skip prompts | — |
| `WEE_AUTH_SECRET` | API auth token secret | auto-generated |
| `WEE_API_HOST` | API listen address | auto-detected |
| `WEE_API_PORT` | API listen port | `8000` |
| `WEE_TELEGRAM_TOKEN` | Telegram bot token | — (skip = no telegram) |
| `WEE_COPILOT_BIN` | Override copilot binary path | auto-detected |
| `WEE_GH_AUTH` | Override gh hosts.yml path | auto-detected |

## Requirements
- Docker + Docker Compose plugin
- At least one AI runtime installed and authenticated

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
```
