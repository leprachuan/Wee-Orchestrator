# Quick Reference: File & Image Handling

## TL;DR - Just Use This

### Send a File
```
[FILE:/opt/n8n-copilot-shim-dev/{telegram|webex|webui}_downloads/myfile.pdf:My Report Title]
```

### Send an Image
```
![Alt text](https://example.com/image.png)
```

### That's It!
The system automatically:
- ✓ Detects the markers
- ✓ Extracts the files/images  
- ✓ Sends to the right platform
- ✓ Includes captions

---

## Where to Save Files

| Platform | Directory |
|----------|-----------|
| **Telegram** | `/opt/n8n-copilot-shim-dev/telegram_downloads/` |
| **WebEx** | `/opt/n8n-copilot-shim-dev/webex_downloads/` |
| **WebUI** | `/opt/n8n-copilot-shim-dev/webui_downloads/` |

---

## Size Limits

| Platform | Max Size |
|----------|----------|
| **Telegram** | 50 MB |
| **WebEx** | 100 MB |
| **WebUI** | 500 MB |

---

## Common Tasks

### Screenshot
```python
# Capture screenshot
screenshot_path = "/opt/n8n-copilot-shim-dev/webex_downloads/snort_screenshot.png"
page.screenshot(path=screenshot_path)

# Return response
return f"""Here's the screenshot:
[FILE:{screenshot_path}:Snort Homepage]
"""
```

### PDF Report  
```python
from reportlab.pdfgen import canvas

# Generate PDF
pdf_path = "/opt/n8n-copilot-shim-dev/telegram_downloads/report.pdf"
c = canvas.Canvas(pdf_path)
c.drawString(100, 750, "My Report")
c.save()

# Return response
return f"""Your report is ready:
[FILE:{pdf_path}:Monthly Report Jan 2026]
"""
```

### CSV Export
```python
import csv

# Generate CSV
csv_path = "/opt/n8n-copilot-shim-dev/webui_downloads/export.csv"
with open(csv_path, 'w') as f:
    writer = csv.writer(f)
    writer.writerow(['ID', 'Name', 'Email'])
    writer.writerow(['1', 'John', 'john@example.com'])

# Return response  
return f"""Data exported:
[FILE:{csv_path}:User Data Export]
"""
```

### Image Search
```python
# Find real image
response = """Check out this network diagram:
![Network Diagram](https://commons.wikimedia.org/wiki/File:example.png)
"""
```

---

## Syntax Rules

| Correct | Wrong |
|---------|-------|
| `[FILE:/opt/.../file.pdf:Caption]` | `FILE:/opt/.../file.pdf` |
| `![text](https://url.png)` | `<img src="https://url.png">` |
| `/opt/downloads/file.pdf` | `./file.pdf` or `~/file.pdf` |
| `Snort Homepage Screenshot` | `screenshot` |

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "File does not exist" | Check path is absolute and correct |
| "File outside directory" | Save to platform-specific `*_downloads/` folder |
| "File too large" | Compress or split into pieces |
| "File not appearing" | Verify caption is included |
| "Image blank" | Use public URL, not localhost |

---

## Full Docs

- 📚 **Comprehensive Guide:** `FILE_MEDIA_HANDLING_SKILL.md`
- 📖 **Agent Instructions:** Search `[File Handling - ALL PLATFORMS]` in `agent_manager.py`
- 🔧 **Platform Details:** `WEBEX_FILE_HANDLING.md`, `telegram_connector.py`

---

That's all you need to know! The system handles the rest. ✨
