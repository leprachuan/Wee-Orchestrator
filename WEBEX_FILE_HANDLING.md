# WebEx File & Image Handling

## Overview

WebEx connector now supports sending files and images, mirroring the Telegram implementation. Agents can use the same `[FILE:...]` syntax to send files to WebEx users.

## Supported Features

### 1. File Sending
Files can be sent using the `[FILE:...]` marker syntax:

```
[FILE:/path/to/file.ext:Caption text]
```

- **Path**: Full path to the file (must exist and be in webex_downloads/)
- **Caption**: Optional caption that appears above the file (max 1000 chars)

**Example:**
```
Here's your report:
[FILE:/opt/n8n-copilot-shim-dev/webex_downloads/monthly_report.pdf:Monthly Sales Report Q1 2026]
```

### 2. Image Sending
Images are sent as clickable links within messages (WebEx doesn't have native image message API).

**Supported syntax:**
1. Markdown: `![alt text](https://example.com/image.png)`
2. Bare URL: `https://example.com/image.png`

## File Restrictions

### Security
- Files must exist in: `/opt/n8n-copilot-shim-dev/webex_downloads/`
- No path traversal attacks allowed
- File integrity validated before sending

### Size Limits
- **Maximum file size**: 100 MB (WebEx API limit)
- **File types**: All binary and text formats supported

## How It Works

### 1. Agent Response Processing
When agent_manager returns a response:

```python
response = """
Text content here.

[FILE:/path/to/file.pdf:File caption]

More text here.
"""
```

### 2. WebEx Connector Extraction
The `send_response()` method:
1. Extracts remaining text
2. Parses `[FILE:...]` markers
3. Sends text as message(s)
4. Sends each file via `send_file()`

### 3. File Upload Flow
```
send_response()
├─ extract_image_urls()      # Find image URLs
├─ extract_file_paths()      # Parse [FILE:...] markers
├─ send_message()            # Text portion
└─ send_file()               # Each file
    └─ multipart upload to WebEx API
```

## Usage Examples

### Example 1: Report Generation
```python
# Agent generates report
response = """
I've created your monthly report. It includes:
- Sales data
- Profit margins
- Trend analysis

[FILE:/opt/n8n-copilot-shim-dev/webex_downloads/monthly_report_jan2026.pdf:Monthly Report - January 2026]
"""

# WebEx sends: message text + PDF file with caption
```

### Example 2: Screenshot
```python
# Agent captures screenshot
response = """
Here's the screenshot of snort.org:

[FILE:/opt/n8n-copilot-shim-dev/webex_downloads/snort_screenshot.png:Snort.org Homepage Screenshot]

The website displays information about network intrusion detection.
"""

# WebEx sends: message text + PNG file with caption
```

### Example 3: Data Export
```python
response = """
Exported your data to CSV:

[FILE:/opt/n8n-copilot-shim-dev/webex_downloads/user_data_export.csv:User Data Export - 2026]

Contains: 1,245 records with full contact information.
"""

# WebEx sends: message text + CSV file with caption
```

## Implementation Details

### New Methods in WebEXConnector

#### `send_file(room_id, file_path, caption="")`
Sends a file to a WebEx room via multipart upload.

```python
def send_file(self, room_id: str, file_path: str, caption: str = "") -> Optional[str]:
    """
    Args:
        room_id: WebEx room/space ID
        file_path: Full path to file
        caption: Optional caption (max 1000 chars)
    
    Returns:
        Message ID if successful, None otherwise
    """
```

#### `extract_image_urls(text)`
Extracts image URLs from markdown and bare URLs.

```python
def extract_image_urls(self, text: str) -> tuple:
    """
    Supports:
    - ![caption](url)           # Markdown
    - https://example.com/img.jpg  # Bare URL
    
    Returns:
        (image_data, remaining_text)
        where image_data = [(url, caption), ...]
    """
```

#### `extract_file_paths(text)`
Extracts [FILE:...] markers from text.

```python
def extract_file_paths(self, text: str) -> tuple:
    """
    Supports:
    - [FILE:/path/file.ext]           # No caption
    - [FILE:/path/file.ext:Caption]   # With caption
    
    Returns:
        (file_data, remaining_text)
        where file_data = [(path, caption), ...]
    """
```

#### Updated `send_response(room_id, text, status_msg_id)`
Enhanced to handle files and images.

```python
def send_response(self, room_id: str, text: str, status_msg_id: Optional[str] = None):
    """
    1. Extracts images and files from text
    2. Sends remaining text (edits status message if exists)
    3. Sends each image as URL link
    4. Sends each file via send_file()
    """
```

## File Path Requirements

### Saved Files
When agent creates files (PDFs, CSVs, exports), save to:
```
/opt/n8n-copilot-shim-dev/webex_downloads/
```

### File Naming
Use descriptive names:
```
✅ report_january_2026.pdf
✅ user_data_export.csv
✅ snort_screenshot.png
❌ file1.pdf
❌ tmp.txt
```

## Testing

### Quick Test
```bash
cd /opt/n8n-copilot-shim-dev
python3 << 'EOF'
from webex_connector import WebEXConnector
connector = WebEXConnector("token")

# Test extraction
text = "Check [FILE:/opt/n8n-copilot-shim-dev/webex_downloads/test.pdf:Test PDF]"
files, remaining = connector.extract_file_paths(text)
print(f"Extracted: {files}")
EOF
```

## Comparison: Telegram vs WebEx

| Feature | Telegram | WebEx |
|---------|----------|-------|
| File sending | `send_document()` | `send_file()` |
| Size limit | 50 MB | 100 MB |
| Format | Binary multipart | Binary multipart |
| Syntax | `[FILE:path:caption]` | `[FILE:path:caption]` |
| Image links | Native sendPhoto API | URL in text |
| Status messages | Edit message | Edit message |

## Troubleshooting

### "File does not exist"
- Check file path is absolute and correct
- File must be in `/opt/n8n-copilot-shim-dev/webex_downloads/`
- Verify file permissions (must be readable)

### "File outside allowed directory"
- Only files in `webex_downloads/` can be sent
- Copy or generate files to this directory

### "File exceeds 100MB limit"
- WebEx API max is 100 MB
- Compress or split larger files

### File not appearing in WebEx
- Check WebEx bot has send_files permission
- Verify room_id is correct
- Check logs for error details

## See Also
- `telegram_connector.py` - Telegram file implementation (same pattern)
- `agent_manager.py` - Agent instructions for file references
- `webex_connector.py` - Full WebEx connector implementation
