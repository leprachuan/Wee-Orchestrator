# Context Window Management

Wee-Orchestrator (`wee_cli.py`) includes automatic context window tracking and LLM-powered compaction to keep long conversations within model limits.

---

## Overview

Every model has a finite context window — the maximum number of tokens it can see in a single API call. Without management, long conversations accumulate history that eventually exceeds this limit, causing API errors.

Wee-Orchestrator solves this with:

1. **Automatic tracking** — `TokenTracker` measures current context usage after each turn
2. **Proactive warnings** — alert when context hits 75% capacity
3. **On-demand compaction** — `/compact` replaces old history with an LLM-generated summary

---

## Model Context Window Registry

`wee_runtime.py` ships a built-in registry (`MODEL_CONTEXT_WINDOWS`) covering 20+ models:

| Model Family | Context Window |
|---|---|
| GPT-5.5, GPT-5.4-mini, GPT-5, GPT-4.1, GPT-4.1-mini, GPT-4.1-nano | 1,047,576 tokens |
| GPT-4o, GPT-4-turbo | 128,000 tokens |
| GPT-4 | 8,192 tokens |
| GPT-3.5-turbo | 16,385 tokens |
| Claude 3 (all variants) | 200,000 tokens |
| Claude 2 | 100,000 tokens |
| Llama 3 | 131,072 tokens |
| Llama 2 | 4,096 tokens |
| Gemma 4/3 | 131,072 tokens |
| Gemma 2 | 8,192 tokens |
| Qwen 2/3 | 32,768 tokens |
| Mistral / Mixtral | 32,768 tokens |
| Phi-3 | 128,000 tokens |
| DeepSeek | 65,536 tokens |
| CodeLlama | 16,384 tokens |
| **Default (unknown models)** | **4,096 tokens** |

### Lookup API

```python
from wee_runtime import get_context_window

window = get_context_window("gpt-4o")        # → 128000
window = get_context_window("claude-3-opus") # → 200000
window = get_context_window("unknown-model") # → 4096 (default)
```

Lookup uses **longest-substring matching** — `"claude-3-haiku"` matches `"claude-3"` correctly.

---

## Token Estimation

```python
from wee_runtime import estimate_tokens, count_message_tokens

# Single string
n = estimate_tokens("Hello world", model="gpt-4o")

# Full message history
messages = [
    {"role": "user", "content": "What is Python?"},
    {"role": "assistant", "content": "Python is a programming language..."},
]
total = count_message_tokens(messages, model="gpt-4o")
```

- Uses **tiktoken** for OpenAI models when available
- Falls back to `len(text) / 4` (character-based estimate) for other models

---

## TokenTracker

`TokenTracker` is instantiated once per REPL session and updated after every API call:

```python
from wee_cli import TokenTracker

tracker = TokenTracker(context_window=128000)
tracker.update(usage_object)         # called after each API response

tracker.percent_used()               # → 23.4 (percentage, 0-100)
tracker.last_prompt_tokens           # → tokens sent in the most recent call
tracker.context_window               # → model's context window size
print(tracker.summary())             # → formatted multi-line stats string
```

### Key Design: `last_prompt_tokens` not `session_total`

`percent_used()` uses `last_prompt_tokens` (the prompt token count from the most recent API call), **not** the cumulative `session_total`. This is critical: because every turn re-sends the full message history, `session_total` would grow quadratically and trigger false-positive warnings.

```
Turn 1: prompt=1000 tokens  → percent_used = 1000/128000 = 0.78%
Turn 2: prompt=1500 tokens  → percent_used = 1500/128000 = 1.17%  ✓ correct
Turn 10: prompt=9000 tokens → percent_used = 9000/128000 = 7.03%  ✓ correct
```

### Warning Threshold

A context usage warning fires automatically after each REPL turn when usage ≥ 75%:

```
⚠ Context at 76.3% — consider /compact to free space.
```

The threshold constant is `COMPACT_TRIGGER_FRACTION = 0.75` in `wee_runtime.py`.

---

## `/tokens` Command

View current token statistics at any time:

```
/tokens
```

**Output:**
```
Tokens — prompt: 45,231, completion: 12,442, total: 57,673, turns: 14
Context window: 9,450/128,000 tokens (7.4% used)
```

---

## `/compact` Command

Manually compact the message history when approaching the context limit.

### Basic usage

```
/compact          # compact to 50% of context window (default)
/compact 30       # compact to 30% of context window
/compact 70       # compact to 70% of context window
```

Target percentage must be between **10 and 90**.

### What happens during compaction

1. System messages (e.g., `AGENTS.md` context) are **always preserved**
2. The **most recent 6 messages** are always kept verbatim
3. If the oldest kept message is a `tool` result, the paired `tool_use` assistant message is also kept (prevents API errors)
4. Older messages are summarized into a single user/assistant exchange via LLM call
5. `TokenTracker` is reset to reflect the compacted context size

**Before:**
```
Compacting: 42 messages, ~9,450 tokens → target 50% (64,000 tokens)...
```

**After:**
```
Done: 42 → 10 messages, ~9,450 → ~1,820 tokens
Summary: The user is building a FastAPI service with PostgreSQL. Key decisions so far:...
```

### Automatic compaction flag

The warning at 75% is informational — `/compact` must be run manually. Fully-automatic background compaction is not implemented; the user retains control.

---

## `compact_messages()` API

For programmatic use:

```python
from wee_runtime import compact_messages

compacted_msgs, summary_text = compact_messages(
    messages=conversation_history,
    target_tokens=64000,          # target token budget
    model="gpt-4o",
    client=openai_client,
    keep_recent=6,                # number of recent messages to keep verbatim
)

if summary_text:
    print(f"Compacted. Summary: {summary_text[:200]}")
```

**Returns:** `(compacted_messages, summary_text)` tuple. `summary_text` is empty string if no compaction occurred (history already short). A `warnings.warn` is emitted if the compacted result still exceeds `target_tokens` (the `keep_recent` messages alone are too large).

---

## Wee CLI Integration

When running `wee_cli.py` interactively:

- Context window is set automatically from the model name at startup
- `TokenTracker` updates after every turn
- Warning fires at 75% usage
- `/model <name>` updates the context window automatically when switching models
- `/tokens` shows live stats
- `/compact [N]` triggers on-demand compaction

The context window is displayed when switching models:

```
Model switched: gpt-4o → claude-3-opus (context: 200,000 tokens)
```

---

## Environment

No additional environment variables are required. Token estimation uses `tiktoken` if installed (recommended for OpenAI models):

```bash
pip install tiktoken
```

Without tiktoken, token counts use the `len(text) / 4` fallback — accurate enough for warnings and compaction decisions.

---

## Testing

Regression tests: `tests/test_issue_273.py` (47 tests)

Key test cases:
- `test_percent_used_uses_last_prompt_not_session_total` — validates the `last_prompt_tokens` fix
- `test_compact_messages_no_orphaned_tool_message` — validates tool-pair preservation
- `test_compact_resets_token_tracker` — validates tracker reset after `/compact`
- `test_get_context_window_*` — validates registry lookup for all model families
