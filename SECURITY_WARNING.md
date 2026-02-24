# ⚠️ SECURITY WARNING: Exposed Credentials in Git History

This repository previously contained hardcoded credentials in the following files (now removed):
- `SERVICE_DEPLOYMENT.md`
- `TELEGRAM_SETUP_SUMMARY.txt`
- `TELEGRAM_QUICK_START.md`
- `TELEGRAM_CONNECTOR.md`
- `TELEGRAM_PRODUCTION_SETUP.md`
- `webex_config.example.json`
- Various `.service` files

**THESE CREDENTIALS HAVE BEEN ROTATED AND ARE NO LONGER VALID.**

## If You Run Fosterbot

**DO NOT use credentials from git history!**

### Proper Credential Setup
1. Obtain fresh credentials from:
   - Telegram BotFather: https://t.me/botfather
   - Cisco WebEX API: https://developer.webex.com
   - RabbitMQ: Set up your own RabbitMQ instance

2. Store credentials in:
   - Environment variables (recommended)
   - Systemd drop-in configuration files
   - `.env` file (add to `.gitignore` - NEVER commit!)

3. Example systemd override:
```bash
sudo mkdir -p /etc/systemd/system/webex-connector.service.d/
sudo cat > /etc/systemd/system/webex-connector.service.d/override.conf << EOF
[Service]
Environment="WEBEX_BOT_TOKEN=YOUR_TOKEN_HERE"
Environment="RABBITMQ_PASSWORD=YOUR_PASSWORD_HERE"
EOF
sudo systemctl daemon-reload
```

## For Repository Maintainers

The Git history contains these secrets. GitHub Secret Scanning has been notified. If you have admin access:
1. Consider using `git filter-branch` or GitHub's "secret scanning" remediation
2. Educate team on security best practices
3. Use pre-commit hooks to prevent future credential commits
4. Implement `git-secrets` or similar tooling

See `CREDENTIAL_MANAGEMENT.md` for detailed guidance.

