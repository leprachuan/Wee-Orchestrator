# File & Media Handling Skill

## Overview

Unified system for sending files, images, and screenshots across **Telegram**, **WebEx**, and **WebUI**. Agents use the same syntax across all platforms.

---

## Quick Reference

### For Agents: How to Send Files

**Files:**
```
[FILE:/path/to/file.pdf:Your caption here]
```

**Images:**
```
![Alt text](https://example.com/image.png)
https://example.com/image.jpg
```

**Screenshots:**
1. Generate screenshot → save to downloads directory
2. Return: `[FILE:/path/to/screenshot.png:Screenshot of snort.org]`
3. System automatically sends to user's platform ✓

---

## Platform Support Matrix

| Feature | Telegram | WebEx | WebUI |
|---------|----------|-------|-------|
| **Files** | ✅ `[FILE:...]` | ✅ `[FILE:...]` | ✅ `[FILE:...]` |
| **Images** | ✅ Native photos | ✅ URL in text | ✅ URL in text |
| **Screenshots** | ✅ Via `[FILE:...]` | ✅ Via `[FILE:...]` | ✅ Via `[FILE:...]` |
| **Size limit** | 50 MB | 100 MB | 500 MB |
| **File types** | All | All | All |
| **Captions** | ✅ Yes | ✅ Yes | ✅ Yes |

---

## Implementation Details

### Telegram (`telegram_connector.py`)

**Methods:**
- `send_photo(chat_id, url, caption)` - Sends image URL as photo
- `send_document(chat_id, file_path, caption)` - Sends file
- `extract_image_urls(text)` - Parses markdown/bare URLs
- `extract_file_paths(text)` - Parses `[FILE:...]` markers

**File location:** `/opt/n8n-copilot-shim-dev/telegram_downloads/`

**Size limit:** 50 MB (Telegram API)

**Example response:**
```
Here's your report:

[FILE:/opt/n8n-copilot-shim-dev/telegram_downloads/report.pdf:Monthly Report]

Includes sales data and analysis.
```

### WebEx (`webex_connector.py`)

**Methods:**
- `send_file(room_id, file_path, caption)` - Sends file via multipart upload
- `extract_image_urls(text)` - Parses markdown/bare URLs
- `extract_file_paths(text)` - Parses `[FILE:...]` markers

**File location:** `/opt/n8n-copilot-shim-dev/webex_downloads/`

**Size limit:** 100 MB (WebEx API)

**Example response:**
```
Here's the screenshot you requested:

[FILE:/opt/n8n-copilot-shim-dev/webex_downloads/snort_screenshot.png:Snort.org Homepage]

Captured full page view.
```

### WebUI

**Status:** Ready for integration

**Approach:**
- Files served from `/opt/n8n-copilot-shim-dev/webui_downloads/`
- URLs sent as clickable links: `[Download: file.pdf](https://server/download/file.pdf)`
- Images embedded as: `![caption](https://server/images/file.png)`

**Size limit:** 500 MB (server configurable)

---

## Agent Guidelines

### Rule 1: Use Consistent Syntax

✅ **CORRECT:**
```python
response = """
Here's your data export:

[FILE:/opt/n8n-copilot-shim-dev/telegram_downloads/data.csv:Exported Data - All Records]

This includes 1,245 rows of customer information.
"""
```

❌ **WRONG:**
```python
response = """
File: /path/to/file.pdf
Please download from: /some/random/path
Check out this image: snort.png
"""
```

### Rule 2: Save to Platform-Specific Directories

```python
# Telegram
file_path = "/opt/n8n-copilot-shim-dev/telegram_downloads/report.pdf"

# WebEx
file_path = "/opt/n8n-copilot-shim-dev/webex_downloads/screenshot.png"

# WebUI
file_path = "/opt/n8n-copilot-shim-dev/webui_downloads/export.csv"
```

### Rule 3: Include Descriptive Captions

✅ Good captions:
```
Monthly Sales Report Q1 2026
User Data Export - All Active Records
Screenshot of snort.org Homepage
Screenshot of OpenWebUI Dashboard
```

❌ Bad captions:
```
file
data
screenshot
image
```

### Rule 4: For Screenshots - Always Use File Marker

```python
# When Playwright captures a screenshot
screenshot_path = "/opt/n8n-copilot-shim-dev/webex_downloads/snort_screenshot.png"

# Return with [FILE:...] marker
response = f"""
Here's the screenshot of snort.org:

[FILE:{screenshot_path}:Snort Homepage - Full Page View]

The page shows network intrusion detection information.
"""
```

### Rule 5: For Images - Use Markdown or Bare URLs

```python
# Search for real image URL
response = """
Here's an image of a network diagram:

![Network Architecture](https://example.com/network-diagram.png)

This shows the system topology.
"""
```

---

## File Path Reference

### Download/Cache Directories

```
Telegram: /opt/n8n-copilot-shim-dev/telegram_downloads/
WebEx:    /opt/n8n-copilot-shim-dev/webex_downloads/
WebUI:    /opt/n8n-copilot-shim-dev/webui_downloads/
```

All directories are:
- ✓ Created automatically if missing
- ✓ World-readable (chmod 644)
- ✓ Cleaned up by background tasks
- ✓ User-scoped to prevent conflicts

### Naming Convention

```
{user_id}_{filename}          # With user scope
user_123456_report.pdf
user_123456_screenshot.png

{descriptor}_{timestamp}      # Generic files
report_2026_02_21.csv
screenshot_13_28_48.png
```

---

## Common Patterns

### Pattern 1: Generate PDF Report

```python
from reportlab.pdfgen import canvas
from pathlib import Path

# Generate PDF
pdf_path = Path("/opt/n8n-copilot-shim-dev/telegram_downloads/monthly_report.pdf")
c = canvas.Canvas(str(pdf_path))
c.drawString(100, 750, "Monthly Report Q1 2026")
c.showPage()
c.save()

# Return response
return f"""
Your monthly report is ready:

[FILE:{pdf_path}:Monthly Report - Q1 2026]

Contains sales data, trends, and analysis.
"""
```

### Pattern 2: Screenshot Request

```python
# Playwright captures screenshot
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto("https://snort.org")
    page.screenshot(path="/opt/n8n-copilot-shim-dev/webex_downloads/snort_screenshot.png")
    browser.close()

# Return response
return """
Here's the screenshot of snort.org:

[FILE:/opt/n8n-copilot-shim-dev/webex_downloads/snort_screenshot.png:Snort Homepage]

Full page view of the network intrusion detection system website.
"""
```

### Pattern 3: Data Export

```python
import csv

# Generate CSV export
csv_path = Path("/opt/n8n-copilot-shim-dev/webui_downloads/user_export.csv")
with open(csv_path, 'w') as f:
    writer = csv.writer(f)
    writer.writerow(['ID', 'Name', 'Email'])
    writer.writerow(['1', 'John Doe', 'john@example.com'])
    writer.writerow(['2', 'Jane Smith', 'jane@example.com'])

# Return response
return f"""
Your data export is ready:

[FILE:{csv_path}:User Data Export - All Records]

Contains {row_count} rows of active user information.
"""
```

### Pattern 4: Image Search and Display

```python
from web_search import search_images

# Find real image
results = search_images("network architecture diagram", max_results=1)
image_url = results[0] if results else "https://example.com/default.png"

# Return with markdown syntax
return f"""
Here's a network architecture diagram:

![Network Architecture]({image_url})

This shows a typical enterprise network topology.
"""
```

---

## Troubleshooting

### Issue: "File does not exist"

**Cause:** Path is wrong or file wasn't created

**Solution:**
```python
from pathlib import Path

file_path = Path("/opt/n8n-copilot-shim-dev/webex_downloads/file.pdf")
assert file_path.exists(), f"File not found: {file_path}"
```

### Issue: "File outside allowed directory"

**Cause:** Using wrong directory path

**Solution:**
```python
# ✅ CORRECT
file_path = "/opt/n8n-copilot-shim-dev/telegram_downloads/file.pdf"

# ❌ WRONG
file_path = "/tmp/file.pdf"
file_path = "/home/user/Downloads/file.pdf"
```

### Issue: "File exceeds size limit"

**Cause:** File too large for platform

**Solution:**
```python
# Check file size before sending
file_size_mb = file_path.stat().st_size / (1024 * 1024)

if file_size_mb > 50:  # Telegram limit
    return "File too large (>50MB). Please compress and try again."
```

### Issue: Image not appearing in WebEx/WebUI

**Cause:** URL is not publicly accessible

**Solution:**
```python
# ✅ CORRECT - Real, public URLs
response = "![](https://commons.wikimedia.org/wiki/File:Example.png)"
response = "![](https://unsplash.com/photos/example.jpg)"

# ❌ WRONG - Local/private URLs
response = "![](file:///home/user/image.png)"
response = "![](http://localhost:8000/image.png)"
```

---

## Best Practices

### ✅ DO:
- Use `[FILE:...]` syntax consistently
- Include meaningful captions
- Validate file exists before referencing
- Use absolute paths
- Keep file sizes reasonable
- Clean up old files periodically

### ❌ DON'T:
- Mix file syntaxes across platforms
- Use relative paths
- Generate files outside designated directories
- Send huge files without warning
- Create cryptic file names
- Reference files from untrusted sources

---

## Testing

### Quick Test: File Extraction

```python
from webex_connector import WebEXConnector

connector = WebEXConnector("webex_config.json")

# Test text with file marker
text = "Check [FILE:/opt/n8n-copilot-shim-dev/webex_downloads/test.pdf:Test File]"
files, remaining = connector.extract_file_paths(text)

print(f"Files found: {files}")  # [('/opt/n8n-copilot-shim-dev/webex_downloads/test.pdf', 'Test File')]
```

### Full Integration Test

```python
# User requests screenshot in WebEx
# Agent captures and returns:
response = """
Here's the screenshot:

[FILE:/opt/n8n-copilot-shim-dev/webex_downloads/screenshot.png:Page Screenshot]
"""

# webex_connector.send_response() automatically:
# 1. Extracts [FILE:...] marker
# 2. Sends text message
# 3. Calls send_file() with the file and caption
# 4. Screenshot appears in WebEx room ✓
```

---

## Integration Status

| Component | Status | Notes |
|-----------|--------|-------|
| Telegram | ✅ Full | Tested, production-ready |
| WebEx | ✅ Full | Tested, production-ready |
| WebUI | 🔄 Ready | Implementation pending |
| Agent docs | ✅ Complete | This file + agent_manager.py |
| Error handling | ✅ Complete | User-friendly messages |
| File validation | ✅ Complete | Security checks enabled |

---

## See Also

- `telegram_connector.py` - Telegram implementation
- `webex_connector.py` - WebEx implementation  
- `agent_manager.py` - Agent instructions
- `WEBEX_FILE_HANDLING.md` - WebEx-specific guide

---

## Questions?

Refer to the [File Handling - Platform Name] section in `agent_manager.py` for agent-facing instructions.
