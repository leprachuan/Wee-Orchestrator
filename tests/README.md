# Test Suite for n8n-copilot-shim

This directory contains comprehensive unit tests for the `agent_manager.py` script, ensuring all capabilities work correctly and preventing regressions when making code changes.

## Overview

The test suite covers the following areas:

- **Session Management**: Creating, resuming, and persisting sessions
- **Agent Configuration**: Loading and managing agent configurations
- **Slash Commands**: All interactive commands (`/help`, `/runtime`, `/model`, `/agent`, `/session`)
- **Model Resolution**: Converting model names/aliases to full IDs
- **Metadata Stripping**: Cleaning CLI output from different runtimes
- **Runtime Management**: Switching between Copilot, OpenCode, and Claude
- **Agent Switching**: Changing agents and session context

## Running the Tests

### Prerequisites

```bash
# Ensure Python 3.8+ is installed
python3 --version

# Install any required dependencies (currently uses only stdlib)
```

### Run All Tests

```bash
# From the project root directory
python3 -m pytest tests/
```

Or using unittest directly:

```bash
python3 -m unittest discover -s tests -p "test_*.py" -v
```

### Run Specific Test Class

```bash
# Run only session persistence tests
python3 -m unittest tests.test_agent_manager.TestSessionPersistence -v
```

### Run Specific Test

```bash
# Run a single test method
python3 -m unittest tests.test_agent_manager.TestSlashCommands.test_help_command -v
```

## Test Coverage

### TestSessionManager
Tests initialization and configuration loading:
- `test_session_manager_initialization` - Verify directories are created
- `test_load_agents_config` - Load agents from JSON config
- `test_load_agents_config_missing_file` - Handle missing config gracefully
- `test_load_agents_config_invalid_json` - Handle malformed JSON

### TestSessionPersistence
Tests session state management:
- `test_create_new_session` - Create session with defaults
- `test_resume_existing_session` - Resume previously created session
- `test_update_session_field` - Update individual session properties
- `test_session_persistence_across_instances` - Sessions persist across manager instances

### TestSlashCommands
Tests interactive command parsing and execution:
- `test_parse_slash_command` - Parse command syntax
- `test_help_command` - `/help` displays available commands
- `test_runtime_list_command` - `/runtime list` shows available runtimes
- `test_runtime_current_command` - `/runtime current` shows active runtime
- `test_runtime_set_command` - `/runtime set <runtime>` switches runtime
- `test_agent_list_command` - `/agent list` shows available agents
- `test_agent_set_command` - `/agent set <agent>` switches agent
- `test_agent_set_invalid` - `/agent set <invalid>` shows error
- `test_session_reset_command` - `/session reset` clears session state

### TestMetadataStripping
Tests output cleaning for different runtimes:
- `test_strip_thinking_tags` - Remove `<think>...</think>` tags
- `test_strip_copilot_metadata` - Remove Copilot usage stats
- `test_strip_opencode_metadata` - Remove OpenCode banners and token counts
- `test_strip_claude_metadata` - Handle Claude output (passthrough)

### TestModelResolution
Tests model name matching and resolution:
- `test_get_claude_model_by_alias` - Resolve Claude by alias (e.g., "sonnet")
- `test_get_claude_model_by_full_name` - Resolve by full name
- `test_get_invalid_model` - Return None for unknown models
- `test_get_copilot_model_exact_match` - Exact matching for Copilot
- `test_get_copilot_model_substring_match` - Substring/partial matching

### TestAgentSwitching
Tests agent context management:
- `test_set_agent_success` - Successfully switch agents
- `test_set_agent_nonexistent` - Error on invalid agent
- `test_set_agent_generates_new_session_id` - New backend session on agent switch

### TestSessionExistence
Tests session state file checking:
- `test_copilot_session_exists` - Check for Copilot session files
- `test_invalid_runtime_session_check` - Invalid runtime returns False

## Mocking Strategy

The tests use Python's `unittest.mock` to avoid:
- Creating real CLI commands (Copilot, OpenCode, Claude)
- Modifying the actual user's home directory
- Making real API calls

### Key Mock Points

```python
# Mock the user's home directory to use temporary test directory
@patch('agent_manager.Path.home')
def test_something(self, mock_home):
    mock_home.return_value = self.temp_path
```

This isolates tests from the user's environment while still testing session persistence logic.

## Adding New Tests

When adding new functionality to `agent_manager.py`, follow this pattern:

```python
class TestNewFeature(unittest.TestCase):
    """Test description"""

    def setUp(self):
        """Set up test fixtures"""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        # ... initialization ...

    def tearDown(self):
        """Clean up"""
        self.temp_dir.cleanup()

    def test_specific_behavior(self):
        """Test specific behavior"""
        # Arrange
        manager = SessionManager(...)

        # Act
        result = manager.some_method()

        # Assert
        self.assertEqual(result, expected_value)
```

## Test Execution in CI/CD

To run tests in a continuous integration pipeline:

```bash
#!/bin/bash
cd /path/to/n8n-copilot-shim
python3 -m pytest tests/ -v --tb=short || exit 1
echo "All tests passed!"
```

Or with coverage reporting:

```bash
python3 -m pytest tests/ --cov=. --cov-report=html
```

## Expected Test Output

When all tests pass, you should see output like:

```
test_agent_list_command (tests.test_agent_manager.TestSlashCommands) ... ok
test_agent_set_command (tests.test_agent_manager.TestSlashCommands) ... ok
test_agent_set_invalid (tests.test_agent_manager.TestSlashCommands) ... ok
test_create_new_session (tests.test_agent_manager.TestSessionPersistence) ... ok
test_help_command (tests.test_agent_manager.TestSlashCommands) ... ok
...

----------------------------------------------------------------------
Ran 28 tests in 0.123s

OK
```

## Troubleshooting

### Tests failing with "module not found"

Ensure you're running tests from the project root:

```bash
cd /Users/fosterlipkey/Documents/n8n-copilot-shim-1
python3 -m unittest discover -s tests -p "test_*.py"
```

### Permission errors in temp directories

Tests create temporary directories that should be auto-cleaned. If you see permission issues, ensure `/tmp` has proper read/write permissions.

### Mock not working as expected

When using `@patch`, the patch applies in the order arguments appear. If mocking `Path.home`:

```python
@patch('agent_manager.Path.home')
def test_something(self, mock_home):  # mock_home is in correct position
    mock_home.return_value = self.temp_path
```

## Test Maintenance

When modifying `agent_manager.py`:

1. **Add/update tests** for new methods before committing
2. **Run the full test suite** before pushing changes
3. **Check test coverage** to ensure new code is tested
4. **Update this README** if adding new test classes

## Integration Testing

These unit tests cover individual components. For end-to-end integration testing:

1. **Manual testing** with actual CLI tools (Copilot, OpenCode, Claude)
2. **N8N workflow testing** - Use the `EXAMPLE_WORKFLOW.json` to test integration
3. **Session persistence** - Verify sessions resume correctly across multiple calls

## Performance Considerations

Tests use temporary directories and mocking to keep execution fast (~0.1-0.2s typically). If tests slow down:

1. Avoid actual subprocess calls
2. Use `@patch` for external CLI invocations
3. Keep temporary file creation minimal
4. Use `setUp/tearDown` to clean between tests

## Related Documentation

- [README.md](../README.md) - Main project documentation
- [EXAMPLE_WORKFLOW.json](../EXAMPLE_WORKFLOW.json) - N8N workflow example
- [agent_manager.py](../agent_manager.py) - Source code being tested
