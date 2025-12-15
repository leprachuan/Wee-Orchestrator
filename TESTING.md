# N8N Copilot Shim - Test Suite Documentation

## Overview

The n8n-copilot-shim includes a comprehensive test suite with two modes:
1. **Safe Mode** (default): Unit tests without CLI runtime dependencies
2. **Full Mode** (optional): Integration tests with real CLI runtimes

This design allows the test suite to run safely in any environment (CI/CD, containerized, etc.) while supporting thorough integration testing when runtimes are available.

## Running Tests

### Default Mode (Safe for CI/CD)

```bash
# Using the standard test runner
./run_tests.sh

# Or directly with Python
python3 tests/test_agent_manager.py
```

**Features:**
- ✓ 45 unit tests always execute
- ✓ 9 runtime tests are skipped
- ✓ No CLI runtime dependencies required
- ✓ Safe for CI/CD pipelines
- ✓ Fast execution (~0.14 seconds)

**Output:**
```
=== TEST CONFIGURATION ===
✓ Runtime tests DISABLED (safe for CI/CD)
  To enable: set TEST_WITH_RUNTIMES=1
========================

Ran 54 tests in 0.140s
OK (skipped=9)
```

### Full Mode (With Real Runtimes)

```bash
# Using the convenience script
./test_with_runtimes.sh

# Or with environment variable
TEST_WITH_RUNTIMES=1 python3 tests/test_agent_manager.py

# Or with the standard runner
TEST_WITH_RUNTIMES=1 ./run_tests.sh
```

**Features:**
- ✓ All 54 tests execute (45 unit + 9 integration)
- ✓ Requires CLI runtimes (copilot, opencode, claude, gemini, codex)
- ✓ Tests actual command execution
- ✓ Validates metadata stripping with real output
- ✓ Comprehensive integration verification

**Output:**
```
=== TEST CONFIGURATION ===
✓ Runtime tests ENABLED (TEST_WITH_RUNTIMES=1)

Available runtimes:
  copilot      ✓ AVAILABLE
  opencode     ✓ AVAILABLE
  claude       ✓ AVAILABLE
  gemini       ✗ NOT FOUND
  codex        ✗ NOT FOUND
========================

Ran 54 tests in 2.450s
OK
```

## Test Organization

### Unit Tests (Always Run)

#### TestSessionManager
- Session initialization and persistence
- Agent configuration loading
- Configuration error handling

#### TestSessionPersistence
- Session creation with defaults
- Session resumption
- Session field updates
- Cross-instance persistence

#### TestSlashCommands
- Command parsing
- Help command
- Runtime management commands
- Agent switching commands
- Session reset

#### TestMetadataStripping
- Thinking tag removal
- Runtime-specific metadata filtering
  - Copilot metadata
  - OpenCode metadata
  - Claude metadata
  - Gemini metadata
  - CODEX metadata

#### TestModelResolution
- Claude model resolution (aliases and full names)
- Copilot model matching (exact and substring)
- Invalid model handling

#### TestAgentSwitching
- Agent switching success
- Non-existent agent handling
- Session ID generation on switch

#### TestSessionExistence
- Session file existence checking
- Runtime-specific session validation

#### TestGeminiSupport
- Gemini model definitions
- Gemini runtime switching
- Gemini model resolution and aliasing
- Gemini session directory creation
- Gemini session existence checking
- Gemini model listing
- Gemini model setting

#### TestCapabilityDiscovery
- `/capabilities` command
- Capability descriptions
- Dynamic discovery from agents.json
- Empty agents handling
- Help command integration

### Integration Tests (Conditional)

These tests only run when `TEST_WITH_RUNTIMES=1` and the corresponding CLI is available.

#### TestRealRuntimeExecution

Tests actual command execution with each runtime:

- **test_copilot_simple_prompt**: Execute prompt with Copilot CLI
- **test_opencode_simple_prompt**: Execute prompt with OpenCode CLI
- **test_claude_simple_prompt**: Execute prompt with Claude CLI
- **test_gemini_simple_prompt**: Execute prompt with Gemini CLI
- **test_codex_simple_prompt**: Execute prompt with CODEX CLI

Each test verifies:
- Non-empty output
- No error messages
- Proper output formatting

#### TestRuntimeIntegration

Tests runtime switching and session management:

- **test_runtime_switching_with_copilot**: Verify runtime switching
- **test_multi_runtime_session_isolation**: Ensure session independence
- **test_session_resumption_with_real_cli**: Session tracking
- **test_metadata_stripping_with_real_output**: Metadata removal with real output

#### TestCapabilityDiscovery

Tests dynamic capability discovery:

- **test_capabilities_command**: `/capabilities` displays agents
- **test_capabilities_include_descriptions**: Descriptions included
- **test_capabilities_dynamic_discovery**: Dynamically loaded from config
- **test_capabilities_empty_agents**: Handles empty configuration
- **test_help_includes_capabilities_command**: Help integrates feature

## Test Decorators and Utilities

### requires_runtime(*runtimes)

Decorator that skips test if:
1. `TEST_WITH_RUNTIMES` is not enabled
2. Required runtime CLI is not found in PATH

```python
@requires_runtime('copilot')
def test_copilot_simple_prompt(self):
    """Test requires Copilot CLI to be installed"""
    ...

@requires_runtime('copilot', 'opencode')
def test_multi_runtime(self):
    """Test requires both runtimes"""
    ...
```

### has_runtime(runtime)

Helper function to check CLI availability:

```python
if has_runtime('copilot'):
    # Do something with Copilot
    ...
```

### Test Configuration Display

Tests display configuration at startup:

```python
# In __main__:
if ENABLE_RUNTIME_TESTS:
    print("✓ Runtime tests ENABLED (TEST_WITH_RUNTIMES=1)")
    print("\nAvailable runtimes:")
    for runtime in ['copilot', 'opencode', 'claude', 'gemini', 'codex']:
        available = "✓ AVAILABLE" if has_runtime(runtime) else "✗ NOT FOUND"
        print(f"  {runtime:12} {available}")
else:
    print("✓ Runtime tests DISABLED (safe for CI/CD)")
```

## Environment Variables

### TEST_WITH_RUNTIMES

Controls whether integration tests run:

```bash
# Disable (default)
TEST_WITH_RUNTIMES=0 ./run_tests.sh
TEST_WITH_RUNTIMES=false ./run_tests.sh

# Enable
TEST_WITH_RUNTIMES=1 ./run_tests.sh
TEST_WITH_RUNTIMES=true ./run_tests.sh
TEST_WITH_RUNTIMES=yes ./run_tests.sh
```

## Test Execution Flow

### Default Mode (TEST_WITH_RUNTIMES not set)

```
Start Tests
    ↓
Run 45 Unit Tests ✓
    ↓
Skip 9 Runtime Tests (decorator skips them)
    ↓
Display Configuration (runtime tests disabled)
    ↓
Results: 54 tests, 45 run, 9 skipped
```

### Full Mode (TEST_WITH_RUNTIMES=1)

```
Start Tests
    ↓
Run 45 Unit Tests ✓
    ↓
For each Runtime Test:
    ├→ Check if runtime CLI exists
    ├→ If yes: Execute test ✓
    └→ If no: Skip test (decorator)
    ↓
Display Configuration (shows available runtimes)
    ↓
Results: 54 tests, some/all run, some skipped
```

## CI/CD Integration

### GitHub Actions Example

```yaml
# Safe by default, no runtime dependencies
- name: Run Tests
  run: ./run_tests.sh

# Optional: Full testing if runtimes are available
- name: Run Tests with Runtimes
  run: TEST_WITH_RUNTIMES=1 ./run_tests.sh
  continue-on-error: true  # Don't fail if runtimes unavailable
```

### Docker Container

```dockerfile
# Install application without CLI runtimes
FROM python:3.11
RUN pip install ...
COPY . /app
WORKDIR /app

# Run safe tests (no CLI dependencies)
RUN ./run_tests.sh
```

### Local Development with Runtimes

```bash
# When all runtimes are installed locally
./test_with_runtimes.sh

# See which runtimes are available
TEST_WITH_RUNTIMES=1 python3 tests/test_agent_manager.py 2>&1 | grep -A10 "Available runtimes"
```

## Adding New Runtime Tests

To add a test for a new runtime:

1. Add method to `TestRealRuntimeExecution`:

```python
@requires_runtime('mynewruntime')
def test_mynewruntime_simple_prompt(self):
    """Test executing with MyNewRuntime CLI"""
    self.manager.execute("/runtime set mynewruntime", "test_session")
    
    result = self.manager.execute("Say hello", "test_session")
    
    self.assertIsNotNone(result)
    self.assertGreater(len(result), 0)
    self.assertNotIn("Error:", result)
```

2. The decorator automatically:
   - Skips test if `TEST_WITH_RUNTIMES` is disabled
   - Skips test if runtime CLI is not found
   - Runs test if both conditions are met

## Test Coverage

### Tested Components

- ✓ Session management and persistence
- ✓ Agent configuration and switching
- ✓ Multi-runtime support (5 runtimes)
- ✓ Command parsing and execution
- ✓ Model resolution and switching
- ✓ Metadata stripping (all runtimes)
- ✓ Dynamic capability discovery
- ✓ Real CLI integration (conditional)

### Not Tested (Out of Scope)

- Actual AI responses (mocked)
- Network connectivity
- Authentication with AI services
- Performance benchmarks
- Memory usage

## Troubleshooting

### Tests Skip When Expected to Run

**Problem:** `TEST_WITH_RUNTIMES=1` set but tests still skip

**Solution:** Check if runtimes are in PATH:

```bash
# Check if runtime exists
which copilot
which opencode
which claude
which gemini
which codex

# If not found, install the runtime
# Or add to PATH:
export PATH="/path/to/runtime:$PATH"
```

### Tests Hang or Timeout

**Problem:** Real runtime tests hang

**Solution:**

1. Check runtime is responsive:
```bash
copilot --help
```

2. Check agent directory is accessible
3. Set timeout if running in resource-constrained environment
4. Run without runtime tests:
```bash
./run_tests.sh  # No TEST_WITH_RUNTIMES
```

### Some Runtimes Show "NOT FOUND"

**Problem:** Configuration shows unavailable runtimes

**Solution:** This is expected if runtimes aren't installed. Only affects tests using `@requires_runtime('missing-runtime')`. Tests for installed runtimes will run normally.

```bash
# Lists which runtimes are available
TEST_WITH_RUNTIMES=1 python3 tests/test_agent_manager.py 2>&1 | grep -A5 "Available runtimes"
```

## Performance

### Test Execution Times

**Safe Mode (default):**
- Cold start: ~0.14 seconds
- Typical run: ~0.15 seconds
- No network calls
- No external dependencies

**Full Mode (TEST_WITH_RUNTIMES=1):**
- With 1 runtime available: ~2-3 seconds
- With all 5 runtimes: ~5-10 seconds
- Depends on runtime responsiveness
- May vary based on network

### Optimization Tips

1. Run unit tests during active development:
```bash
./run_tests.sh  # Fast feedback
```

2. Run full tests before committing:
```bash
./test_with_runtimes.sh  # Comprehensive validation
```

3. Run specific test class:
```bash
python3 -m unittest tests.test_agent_manager.TestSlashCommands
```

4. Run specific test:
```bash
python3 -m unittest tests.test_agent_manager.TestSlashCommands.test_help_command
```

## Future Enhancements

Potential test improvements:

1. **Parameterized tests**: Test all runtimes with same test code
2. **Performance benchmarks**: Track execution times across versions
3. **Load testing**: Concurrent session management
4. **End-to-end workflows**: Multi-agent orchestration scenarios
5. **Property-based testing**: Fuzzing for edge cases

## References

- [Python unittest documentation](https://docs.python.org/3/library/unittest.html)
- [Environment variable convention](https://peps.python.org/pep-0003/)
- [Agent orchestration documentation](./AGENTS.md)
- [Skill subagents guide](./SKILL_SUBAGENTS.md)

