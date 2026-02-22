# Dev Environment Access Guide

The dev API (`webex-connector-dev.service`) and WebUI are intentionally bound to
**localhost (`127.0.0.1`) and the Tailscale interface only**.  They are not reachable
from the local LAN or the public internet.

## Access Methods

### Option 1 – Tailscale (Recommended)

1. Install Tailscale on your machine: https://tailscale.com/download
2. Join the same Tailscale network (get an invite key from the admin)
3. Access directly:

```
API:   http://100.124.186.75:8001
WebUI: http://100.124.186.75:8001/ui
```

### Option 2 – SSH SOCKS Proxy

Forward all traffic through an SSH tunnel without needing Tailscale.

**Start the SOCKS proxy:**
```bash
ssh -N -D 1080 flipkey@lepbuntu
# Keep this terminal open (add -f to background it)
```

> Replace `lepbuntu` with the hostname/IP if not in your SSH config.

**Configure your browser to use the SOCKS proxy:**
- **Firefox**: Settings → General → Network Settings → Manual proxy →
  SOCKS Host `127.0.0.1`, Port `1080`, SOCKS v5, check "Proxy DNS"
- **Chrome/Edge**: Launch with flag:
  ```
  google-chrome --proxy-server="socks5://127.0.0.1:1080"
  ```
- **macOS System-wide**: System Settings → Network → (adapter) → Proxies →
  SOCKS Proxy: `127.0.0.1:1080`

Once the proxy is active, open:
```
http://127.0.0.1:8001/ui   ← or the Tailscale IP — both work via SOCKS
```

### Option 3 – SSH Port Forwarding (single port)

If you only need the API, forward just that port:
```bash
ssh -N -L 8001:127.0.0.1:8001 flipkey@lepbuntu
```
Then open `http://localhost:8001` locally.

---

## Configuration

Binding is controlled by `API_HOST` in the dev `.env` file:

```dotenv
# Comma-separated — binds to each interface
API_HOST=127.0.0.1,100.124.186.75
API_PORT=8001
```

To add another Tailscale peer or interface, append it comma-separated.

---

## Why is This Restricted?

The dev environment runs experimental code and may expose unauthenticated test
endpoints.  Restricting it to Tailscale + localhost prevents accidental exposure
on the LAN while still allowing authorised team members to reach it via Tailscale.
