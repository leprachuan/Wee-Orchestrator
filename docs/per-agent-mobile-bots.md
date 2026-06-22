# Per-Agent Mobile Bot Configuration

> **Feature:** Configure dedicated Telegram and WebEx bot tokens per agent via the Settings panel.
> Tokens are stored securely in the keyring and never exposed in logs or API responses.

---

## Overview

Every agent in `agents.json` can have its own Telegram and/or WebEx bot.
The **orchestrator** uses the primary/general bot (configured via the Settings panel or the legacy env-var/config-file path).
All other agents use dedicated bots managed by `agent_bot_manager.py`.

### Bot routing rules

| Bot type | Agent | `/agent set` allowed? |
|----------|-------|-----------------------|
| Primary orchestrator bot | `orchestrator` | ✅ Yes — can switch active agent |
| Per-agent bot (non-orchestrator) | Any other agent | ❌ No — pinned to its configured agent; rejected with a clear message |

---

## Configuring bot tokens in the Settings panel

1. Open the **⚙️ Settings** panel in the WebUI.
2. Select the agent you want to configure in the agent dropdown.
3. Scroll to the **📱 Mobile Bots** section.
4. For **Telegram**: click **Set Token**, paste the bot token, click **Save**.
5. For **WebEx**: click **Set Token**, paste the bot token, click **Save**.

The token is stored in the file-backend keyring under a name like
`wee.agent.<agent-name>.<channel>.bot_token`
and the `agents.json` entry is updated to reference it via `token_secret`.

Tokens are displayed only as **configured / not configured** — the actual value is never returned by the API.

---

## How the orchestrator connector loads its token

`telegram_connector.py` and `webex_connector.py` resolve the orchestrator bot token in this priority order:

1. **Settings-backed token** (`agents.json` → `orchestrator.bots.<channel>.token_secret` resolved via `secret_tool --backend file`)
2. **CLI argument** (`--token`) or **environment variable** (`TELEGRAM_BOT_TOKEN` / `WEBEX_BOT_TOKEN`)
3. **Config file** (`telegram_config.json` / `webex_config.json` `token` field)

This means existing installations continue to work without changes.
Once a token is configured via Settings, it becomes the primary source and the env-var/config-file is used only as a fallback if the Settings token is absent.

---

## How per-agent bots are managed

`agent_bot_manager.py` reads `agents.json` on startup and polls for changes every 30 seconds (configurable via `--reload-interval`).
For each agent with a `bots.telegram` or `bots.webex` block:

1. Resolves the token from `secret_tool --backend file` using the `token_secret` name.
2. Starts a dedicated polling thread (`TelegramAgentBot`) or RabbitMQ consumer (`WebExAgentBot`).
3. All messages are routed directly to the configured agent — no orchestrator hop.
4. `/agent set` (and all `/agent` subcommands) are rejected with:
   > `⚠️ Agent switching is disabled on per-agent bots. This bot is dedicated to <agent-name>.`

Hot-reload: when `agents.json` changes, affected bot threads are restarted automatically.

---

## agents.json structure

```json
{
  "agents": [
    {
      "name": "orchestrator",
      "path": "/opt/n8n-copilot-shim-dev",
      "bots": {
        "telegram": {
          "token_secret": "wee.agent.orchestrator.telegram.bot_token"
        },
        "webex": {
          "token_secret": "wee.agent.orchestrator.webex.bot_token"
        }
      }
    },
    {
      "name": "wee-dev",
      "path": "/opt/wee-dev",
      "bots": {
        "telegram": {
          "token_secret": "wee.agent.wee-dev.telegram.bot_token",
          "allowed_users": ["8193231291"]
        }
      }
    }
  ]
}
```

`allowed_users` is optional. If omitted, the agent inherits the global allowed-user list from the `AgentBotManager` configuration. If empty on the global level too, all users are allowed (not recommended for production).

---

## Starting the agent bot manager

The `agent-bot-manager-dev.service` systemd unit runs the bot manager automatically.
To start it manually:

```bash
python3 agent_bot_manager.py \
  --config /opt/n8n-copilot-shim-dev/agents.json \
  --api-url https://127.0.0.1:8001 \
  --api-key <shared_key> \
  --reload-interval 30
```

The `--api-key` value must match the `API_SHARED_KEY` environment variable used by the API server.

---

## Security notes

- Bot tokens are never committed to repository files.
- Tokens are stored encrypted in the file backend at `~/.local/share/secret_tool/secrets.json.enc`.
- The Settings API (`PUT /agents/{name}/bots/{channel}/token`) writes only to the keyring — the plaintext value is not stored in `agents.json`.
- `GET /agents/{name}/bots/{channel}/token-status` returns only `configured: true/false` and the secret reference name, never the token value.
- Logs never contain token values; they reference only the secret name.

---

## Removing a bot token

In the Settings panel, click **Remove** next to the configured channel.
This deletes the secret from the keyring and clears the `token_secret` field in `agents.json`.
The bot manager will detect the change on the next reload cycle and stop the corresponding bot thread.

---

## Migration from env-var / config-file tokens

Existing setups using `TELEGRAM_BOT_TOKEN` / `WEBEX_BOT_TOKEN` env vars or `telegram_config.json` / `webex_config.json` `token` fields continue to work without any changes.

To migrate to Settings-backed tokens:
1. Set the token via the Settings panel.
2. Optionally remove the env-var from the systemd unit file (the Settings token takes priority).
3. The old config-file token is no longer read once a Settings-backed token is configured.
