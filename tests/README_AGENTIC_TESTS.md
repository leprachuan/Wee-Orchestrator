# Wee Native Runtime — Agentic Test Suite

Comprehensive test suite for `wee_runtime.py` validating model resolution,
tool calling, streaming, permissions, and live provider integration.

## Quick Start

```bash
# All tests (unit + live if providers available)
pytest tests/test_wee_runtime_agentic.py -v

# Unit tests only (fast, no API calls)
./scripts/run_agentic_tests.sh --unit

# Ollama live tests only
./scripts/run_agentic_tests.sh --ollama

# OpenRouter live tests only
./scripts/run_agentic_tests.sh --openrouter
```

## Test Categories

| Category | Class(es) | Live API? | Count |
|----------|-----------|-----------|-------|
| Model Resolution | `TestModelResolution`, `TestCrossProviderResolution` | No | 12 |
| Tool Definitions | `TestToolDefinitions` | No | 6 |
| Tool Execution | `TestExecuteTool` | No | 11 |
| SSH Sanitization | `TestSanitizeBashCommand` | No | 5 |
| CLI Args | `TestCLIArgParsing` | No | 3 |
| Tool Loop (mocked) | `TestToolCallingLoopMocked` | No | 4 |
| Permission Levels | `TestPermissionLevels` | No | 5 |
| Streaming Output | `TestStreamingOutput` | No | 2 |
| Error Handling | `TestErrorHandling` | No | 4 |
| Performance | `TestPerformanceBaseline` | No | 2 |
| Ollama Basic | `TestOllamaLiveBasic` | **Yes** | 3 |
| Ollama Tool Calling | `TestOllamaLiveToolCalling` | **Yes** | 4 |
| OpenRouter Basic | `TestOpenRouterLiveBasic` | **Yes** | 4 |
| OpenRouter Tool Calling | `TestOpenRouterLiveToolCalling` | **Yes** | 3 |

**Total: ~68 tests** (54 unit/mock + 14 live integration)

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_HOST` | `http://192.168.1.101:11434` | Ollama API base |
| `OLLAMA_TEST_MODEL` | `ollama/qwen3:8b` | Ollama model for live tests |
| `OPENROUTER_TEST_MODEL` | `openrouter/google/gemma-3-12b-it:free` | OpenRouter model (free tier) |
| `OPENROUTER_API_KEY` | (keyring) | OpenRouter API key |

## Architecture

- **Unit tests**: Mock `openai.OpenAI` to test tool loop logic without network
- **Live tests**: Skip automatically if provider is unreachable
- **Performance tests**: Validate import time and resolution speed

## Adding New Tests

1. Add test methods to existing classes or create new `unittest.TestCase`
2. For live tests, use `@skip_ollama` or `@skip_openrouter` decorators
3. Use `run_wee_cli()` helper for subprocess-based tests
4. Use `_make_chunk()` / `_make_tool_call_delta()` for mock streaming

---

## test_wee_runtime_comprehensive_v2.py (70 tests)

Supplementary suite covering gaps not addressed in `test_wee_runtime_agentic.py`.

| Category | Class | Live? | Tests |
|----------|-------|-------|-------|
| Temperature propagation | `TestTemperaturePropagation` | No | 4 |
| Message history structure | `TestMessageHistoryStructure` | No | 5 |
| Parallel tool calls | `TestParallelToolCalls` | No | 2 |
| Synthetic tool call ID | `TestSyntheticToolCallID` | No | 1 |
| Env var resolution | `TestEnvVarResolution` | No | 5 |
| Stderr diagnostics | `TestStderrDiagnostics` | No | 3 |
| System prompt combination | `TestSystemPromptCombination` | No | 3 |
| Content/tool interleaving | `TestContentAndToolCallInterleaving` | No | 2 |
| Tool output edge cases | `TestToolOutputEdgeCases` | No | 8 |
| Fast timeout | `TestFastToolTimeout` | No | 1 |
| Model name in API call | `TestModelNameInAPICall` | No | 4 |
| Multi-step tool chain | `TestMultiStepToolChain` | No | 1 |
| OpenRouter config | `TestOpenRouterConfiguration` | No | 5 |
| Ollama config | `TestOllamaConfiguration` | No | 4 |
| CLI argument validation | `TestCLIArgumentValidation` | No | 4 |
| Module structure | `TestModuleStructure` | No | 9 |
| Ollama live expanded | `TestOllamaLiveExpanded` | **Yes** | 4 |
| OpenRouter live expanded | `TestOpenRouterLiveExpanded` | **Yes** | 4 |

**Total: 70 tests** (62 unit/mock + 8 live integration)

### Key Design Note

`call_args_list` stores references to mutable argument objects — not copies.
When inspecting message-list lengths across rounds, use a capturing `side_effect`
function to snapshot values at the time of each call.

### Running

```bash
# Unit/mock only (fast, ~3s)
python3 -m pytest tests/test_wee_runtime_comprehensive_v2.py -k "not Live" -q

# All including live (requires Ollama on 192.168.1.101 and OPENROUTER_API_KEY)
python3 -m pytest tests/test_wee_runtime_comprehensive_v2.py -v

# Combined (both files, skip slow timeout test from agentic suite)
python3 -m pytest tests/test_wee_runtime_agentic.py tests/test_wee_runtime_comprehensive_v2.py \
  -k "not (Live or timeout_enforcement)" -q
```
