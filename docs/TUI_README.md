# Wee TUI - Terminal User Interface for Wee Orchestrator

A feature-rich Terminal User Interface (TUI) for managing and interacting with the Wee Orchestrator from the command line.

## Features

### Core Functionality
- **Real-time Session Management**: View and manage active sessions with live status updates
- **Interactive Chat Panel**: Display chat history with streaming response support
- **Background Task Queue**: Monitor and manage background tasks with progress tracking
- **Service Status Monitoring**: View status of Telegram/WebEx connectors and other services
- **Agent Switcher**: Quickly switch between available agents (orchestrator, wee-dev, wee-qa, etc.)
- **Model/Runtime/Timeout Configuration**: Adjust execution parameters on the fly

### User Experience
- **Keyboard Navigation**: Tab through panels, Enter to submit prompts, Ctrl+Q to quit
- **Color-Coded Status**: Visual indicators for session status, agent type, service health
- **Responsive Layout**: Automatically adapts to terminal size
- **Live Updates**: Real-time updates from the Wee Orchestrator API via polling
- **Scrollable History**: Navigate through chat history and previous interactions

### Advanced Features
- **Custom Themes**: Support for different color schemes
- **Configurable Polling**: Adjust refresh rate for different network conditions
- **Error Handling**: Graceful handling of API connection issues
- **Command Shortcuts**: Quick commands like `/agent`, `/model`, `/runtime`
- **Logging**: Comprehensive logging for debugging

## Installation

### Prerequisites
- Python 3.9+
- Textual 0.68.0+
- httpx 0.27.0+
- websockets 12.0+

### Setup
```bash
# Install dependencies
pip install -r requirements.txt

# Or install specific TUI packages
pip install textual websockets rich
```

## Usage

### Start the TUI
```bash
# Start with default settings
python3 wee-tui

# Or use the CLI directly
./wee-tui
```

### Configuration via Environment Variables
```bash
# API URL
export WEE_API_URL="https://192.168.1.100:8001"

# Authentication token
export WEE_AUTH_TOKEN="your_token_here"

# User identity
export WEE_USER_ID="8193231291"

# Theme
export WEE_TUI_THEME="default"

# Verify SSL (false by default)
export WEE_VERIFY_SSL="false"

# Start TUI
./wee-tui
```

### Command Line Options
```bash
./wee-tui --help

Options:
  --api-url URL         Wee Orchestrator API URL
  --token TOKEN         Authentication token
  --user-id ID          User identity
  --theme {default,dark,light}  Color theme
  --log-level {DEBUG,INFO,WARNING,ERROR}  Logging level
  --log-file PATH       Log file path
  --verify-ssl          Verify SSL certificates
```

### Keyboard Shortcuts
| Key | Action |
|-----|--------|
| `Tab` | Focus next panel |
| `Shift+Tab` | Focus previous panel |
| `Ctrl+N` | Create new session |
| `Ctrl+S` | Send prompt |
| `Ctrl+Q` | Quit TUI |
| `Enter` | Submit input in focus panel |

### Commands in Input Field
```
/agent <name>     Switch to agent (e.g., /agent wee-dev)
/model <name>     Change model
/runtime <name>   Change runtime (copilot, claude, opencode, etc.)
/timeout <sec>    Set timeout in seconds
/help             Show available commands
/status           Show system status
```

## Architecture

### Main Components
- **SessionListPanel**: Left panel showing available sessions
- **ChatPanel**: Center panel for chat history and streaming responses
- **ControlPanel**: Right panel for settings and controls
- **TaskQueuePanel**: Background tasks queue viewer
- **ServiceStatusPanel**: Service health indicators
- **InputField**: Command/prompt entry field

### API Integration
- **WeeAPIClient**: Async HTTP client for API communication
- Supports all major endpoints: sessions, background tasks, agents, runtimes, models
- Automatic error handling and reconnection

### Data Models
- **Session**: Session information with runtime, model, agent, status
- **Message**: Chat message with role, content, timestamp
- **BackgroundTask**: Task tracking with progress and status
- **ServiceStatus**: Service health information
- **Agent**: Agent configuration
- **RuntimeInfo**: Runtime availability and models

## Development

### Project Structure
```
tui/
├── __init__.py              # Package init
├── app.py                   # Main Textual app
├── api/
│   ├── __init__.py
│   └── client.py            # Async API client
├── components/
│   ├── __init__.py
│   ├── task_queue.py        # Task queue panel
│   └── service_status.py    # Service status panel
├── models/
│   ├── __init__.py
│   └── types.py             # Data types
├── theme.py                 # Colors and styling
└── config.py                # Configuration

wee-tui                      # CLI entry point
requirements.txt             # Python dependencies
```

### Running Tests
```bash
# Run all TUI tests
pytest tests/test_issue_272_wee_tui.py -v

# Run with coverage
pytest tests/test_issue_272_wee_tui.py --cov=tui -v
```

### Building
The TUI is built as part of the Wee Orchestrator. No separate build step required.

## Integration with Wee Orchestrator

The TUI communicates with the Wee Orchestrator API at `/api/v1/`:

### Key Endpoints Used
- `GET /api/v1/health` - Health check
- `GET /api/v1/agents` - List agents
- `GET /api/v1/history/sessions` - List sessions
- `GET /api/v1/sessions/{id}/status` - Session status
- `POST /api/v1/sessions/create` - Create session
- `POST /api/v1/sessions/{id}/stream` - Stream execution
- `GET /api/v1/background-tasks` - List background tasks
- `POST /api/v1/background-tasks` - Create background task
- `GET /api/v1/service-status` - Service status

## Troubleshooting

### Connection Issues
```bash
# Test API connectivity
curl -sk https://127.0.0.1:8001/api/v1/health

# Enable debug logging
./wee-tui --log-level DEBUG
```

### Rendering Issues
- Ensure terminal supports 256 colors (most modern terminals do)
- Try different themes: `--theme dark` or `--theme light`
- Check terminal size (min 80x24)

### Performance
- Adjust polling interval via `update_interval` in config.py
- Reduce max history lines for lower memory usage
- Use `--log-level WARNING` to reduce disk I/O

## Related Issues
- Issue #236: Service Status API
- Issue #248: Agent name badges (color-coding)
- Issue #267: WebEx passive queue (AMQP compatibility)
- Issue #249: Agent defaults on switch

## Future Enhancements
- [ ] WebSocket support for real-time updates
- [ ] Kanban board view for work queue items
- [ ] Settings panel for persistent configuration
- [ ] Search/filter for sessions and tasks
- [ ] Custom themes and color schemes
- [ ] Mouse support for terminal UI
- [ ] Export/save chat history
- [ ] Multi-pane layouts
- [ ] Plugin system for custom components
- [ ] Headless mode for CI/CD integration

## Contributing
When working on the wee-tui, follow the wee-dev workflow:
1. Create a feature branch: `git checkout -b issue/<number>`
2. Make changes and test
3. Commit with proper message: `feat: description`
4. Push and open PR to `dev` branch
5. Dispatch wee-qa for code review

## License
See main LICENSE file in Wee Orchestrator repository.

## Support
For issues, bugs, or feature requests, file a GitHub Issue in the [Wee-Orchestrator repository](https://github.com/leprachuan/Wee-Orchestrator).
