# Telegram Connector for N8N Copilot Shim

Bridge your Telegram bot with the N8N Copilot Shim agent system.

## Features

- ✅ Receive messages from Telegram users
- ✅ Pair users by Telegram user ID
- ✅ User access control (whitelist/blacklist)
- ✅ Per-user session management
- ✅ Route messages to any configured agent
- ✅ Support for inline commands

## Installation

The connector requires `requests` library:

```bash
pip install requests
```

Or if using the package:

```bash
pip install -e .
```

## Configuration

### 1. Set Up Your Telegram Bot

1. Create a bot via [@BotFather](https://t.me/botfather) on Telegram
2. Get your bot token (format: `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`)

### 2. Create Configuration File

Copy the example config:

```bash
cp telegram_config.example.json telegram_config.json
```

Or use environment variable:

```bash
export TELEGRAM_BOT_TOKEN="your-token-here"
```

### 3. Manage Allowed Users

Allow specific users to chat with the bot:

```bash
# Allow user with ID 123456789
python telegram_connector.py --token YOUR_TOKEN --allow-user 123456789

# Deny a user
python telegram_connector.py --token YOUR_TOKEN --deny-user 123456789

# List allowed users
python telegram_connector.py --token YOUR_TOKEN --list-users
```

Leave the `allowed_users` list empty in config for unrestricted access.

## Usage

### Start the Connector

```bash
# Using token from environment
python telegram_connector.py

# Using command-line argument
python telegram_connector.py --token "your-token-here"

# Using custom config file
python telegram_connector.py --config /path/to/config.json
```

### User Commands

Once the bot is running, users can:

- `/start` - Welcome message
- `/help` - Show available commands
- `/agent <name>` - Set their agent (e.g., `/agent devops`)
- `/status` - Show current session info
- `/list_agents` - Show available agents

## Configuration Structure

```json
{
  "token": "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
  "allowed_users": [123456789, 987654321],
  "user_pairings": {
    "123456789": {
      "user_id": 123456789,
      "username": "john_doe",
      "paired_at": "2024-01-15T10:30:00",
      "agent": "orchestrator",
      "model": "gpt-5-mini"
    }
  },
  "enable_auto_pair": false,
  "default_agent": "orchestrator",
  "default_model": "gpt-5-mini"
}
```

### Configuration Fields

- **token** - Telegram bot API token
- **allowed_users** - List of user IDs allowed to chat (empty = allow all)
- **user_pairings** - Automatically managed user session data
- **enable_auto_pair** - Auto-approve new users (future feature)
- **default_agent** - Default agent for new users
- **default_model** - Default model for new users

## User Pairing Flow

1. User sends first message
2. Connector checks if user is allowed
3. User session is created with:
   - Telegram user ID
   - Username
   - Pairing timestamp
   - Default agent and model
4. Session is stored in config file
5. Future messages route to user's configured agent

## Integration with agent_manager.py

The connector calls `agent_manager.py` to:

- Route user queries to the specified agent
- Manage session context
- Execute agent-specific operations

Current implementation shows placeholder integration. Full integration:

```python
# In _query_agent method, replace with:
from agent_manager import SessionManager

session_mgr = SessionManager(user_id=user_id)
result = session_mgr.run_copilot(
    query=query,
    agent=agent,
    model=model
)
```

## Environment Variables

- `TELEGRAM_BOT_TOKEN` - Bot token (alternative to --token flag)
- `COPILOT_DEFAULT_AGENT` - Default agent (default: "orchestrator")
- `COPILOT_DEFAULT_MODEL` - Default model (default: "gpt-5-mini")

## Security Considerations

⚠️ **Important**: Never commit `telegram_config.json` with your bot token!

- Token is added to `.gitignore`
- Use environment variables for tokens in production
- Restrict bot access via `allowed_users` list
- Consider rate limiting for production use

## Running as a Service

### systemd (Linux)

Create `/etc/systemd/system/telegram-connector.service`:

```ini
[Unit]
Description=N8N Copilot Telegram Connector
After=network.target

[Service]
Type=simple
User=nobody
WorkingDirectory=/opt/n8n-copilot-shim-dev
Environment="TELEGRAM_BOT_TOKEN=your-token-here"
ExecStart=/usr/bin/python3 /opt/n8n-copilot-shim-dev/telegram_connector.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl enable telegram-connector
sudo systemctl start telegram-connector
sudo systemctl status telegram-connector
```

## Troubleshooting

### "Connection refused" error

- Verify bot token is correct
- Check internet connectivity
- Verify Telegram API is accessible

### User not receiving messages

- Check user is in `allowed_users` list (if configured)
- Verify bot token has message sending permissions
- Check user is not blocking the bot

### Session not being saved

- Verify `telegram_config.json` is writable
- Check disk space
- Verify JSON is valid after modifications

## Future Enhancements

- [ ] Webhook support (instead of polling)
- [ ] Auto-pairing with PIN verification
- [ ] Rate limiting per user
- [ ] Message history/context management
- [ ] Inline keyboard navigation
- [ ] Media file support (images, documents)
- [ ] Integration with N8N workflows
