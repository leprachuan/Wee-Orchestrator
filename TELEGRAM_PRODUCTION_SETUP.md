# Telegram Bot Listener - Production Setup

**Setup Date:** 2026-02-14
**Status:** ✅ Active and Running

## Bot Configuration

- **Bot Name:** lipkeyhomebot
- **Bot ID:** 8405010413
- **Bot Token:** `8405010413:AAH2dwV-PEt5Md3q7MHHPFKGc1DH6XVqbU0`
- **Webhook:** Active at n8n (`https://n8n-external.leprachuan.com/webhook/...`)

## Configuration Files

- **Config File:** `/opt/n8n-copilot-shim/telegram_config.json`
  - Status: ✅ Gitignored (safe from accidental commits)
  - Contains: Bot token and allowed users list
  - Never commit this file

## Allowed Users

| User ID | Name |
|---------|------|
| 8193231291 | Foster Lipkey (@vtflip) |

## Systemd Service

**Service File:** `/etc/systemd/system/telegram-bot-listener.service`

**Current Status:**
```
State: active (running)
PID: 3568394
Memory: 21.6M
Started: Sat 2026-02-14 18:03:06 UTC
```

**Service Configuration:**
- User: `n8n`
- Working Directory: `/opt/n8n-copilot-shim`
- Executable: `/usr/bin/python3 /opt/n8n-copilot-shim/telegram_connector.py`
- Restart Policy: Always (RestartSec=10)
- Boot Enabled: Yes (symlink in multi-user.target.wants)
- Logging: `/var/log/telegram-bot-listener.log`

## Service Management Commands

**Start:**
```bash
sudo systemctl start telegram-bot-listener.service
```

**Stop:**
```bash
sudo systemctl stop telegram-bot-listener.service
```

**Restart:**
```bash
sudo systemctl restart telegram-bot-listener.service
```

**Check Status:**
```bash
sudo systemctl status telegram-bot-listener.service
```

**View Real-time Logs:**
```bash
sudo journalctl -u telegram-bot-listener.service -f
```

**View Recent Logs (last 50 lines):**
```bash
sudo journalctl -u telegram-bot-listener.service -n 50
```

## Log Locations

- **Systemd Journal:** `sudo journalctl -u telegram-bot-listener.service`
- **Service Log File:** `/var/log/telegram-bot-listener.log`
- **Connector Log:** `/opt/n8n-copilot-shim/telegram_connector.log` (if enabled)

## Runtime Details

**Script:** `/opt/n8n-copilot-shim/telegram_connector.py`
- Accepts updates via polling (getUpdates endpoint)
- Routes messages to agent_manager.py
- Sends responses back via Telegram API
- Maintains persistent SessionManager per session_id

**Configuration Passed:**
- `--token 8405010413:AAH2dwV-PEt5Md3q7MHHPFKGc1DH6XVqbU0`
- `--config /opt/n8n-copilot-shim/telegram_config.json`

## Important Notes

⚠️ **Webhook Active:** Do NOT call `deleteWebhook` without restoring it afterwards.

⚠️ **Token Security:** The bot token is stored in:
- `/opt/n8n-copilot-shim/telegram_config.json` (gitignored)
- Systemd service environment variable (protected)
- Never commit or share this token

📋 **User Management:**
```bash
# Allow a new user
python3 telegram_connector.py --allow-user USER_ID

# Deny a user
python3 telegram_connector.py --deny-user USER_ID

# List allowed users
python3 telegram_connector.py --list-users
```

## Troubleshooting

**Service won't start:**
```bash
sudo systemctl status telegram-bot-listener.service
sudo journalctl -u telegram-bot-listener.service -n 100
```

**High memory usage:**
Check logs for session leaks. The service maintains SessionManager instances in memory.

**Messages not responding:**
1. Verify allowed_users list includes the sender
2. Check agent_manager.py is accessible
3. Review logs for errors from agent_manager

**Logs filling up:**
Configure logrotate if `/var/log/telegram-bot-listener.log` grows too large.

## Related Documentation

- Bot setup: See TELEGRAM_CONNECTOR.md (in git)
- Quick start: See TELEGRAM_QUICK_START.md (in git)
- This file: TELEGRAM_PRODUCTION_SETUP.md (⚠️ NOT in git - contains sensitive info)
