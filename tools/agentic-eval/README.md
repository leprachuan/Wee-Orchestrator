# Wee Orchestrator — Agentic Flow Evaluator

A standalone test suite for evaluating Wee Orchestrator agentic workflows. Injects realistic
prompts via the background-tasks API, scores responses across 5 dimensions using an LLM judge
(or heuristic fallback), and produces structured JSON reports for comparing runtimes and models.

## Quick Start

```bash
cd tools/agentic-eval

# Dry run — synthetic scores, no API calls
python3 test.py --dry-run

# Full evaluation with a specific model
python3 test.py --runtime copilot --model claude-haiku-4.5

# Only core scenarios
python3 test.py --tags core --model claude-sonnet-4.6

# Compare two runs
python3 test.py --compare reports/eval_*.json
```

## Architecture

```
tools/agentic-eval/
├── test.py               # CLI entry point
├── README.md             # This file
├── scenarios/
│   └── __init__.py       # 15 scenario definitions
├── evallib/
│   ├── __init__.py
│   ├── scoring.py        # Score models, report generation, grading
│   ├── judge.py          # LLM-as-judge + heuristic fallback
│   └── runner.py         # API interaction, task polling, concurrency
└── reports/              # Generated JSON reports
    └── .gitkeep
```

## How It Works

```
┌──────────────┐     ┌───────────────┐     ┌──────────────┐
│  Scenario    │────▶│  Wee API      │────▶│  LLM Judge   │
│  (prompt)    │     │  (bg task)    │     │  (scoring)   │
└──────────────┘     └───────────────┘     └──────────────┘
                                                  │
                                           ┌──────▼──────┐
                                           │  Report     │
                                           │  (JSON)     │
                                           └─────────────┘
```

1. **Inject**: Each scenario's prompt is submitted as a background task via the orchestrator API
2. **Execute**: The orchestrator processes the prompt using the specified runtime/model
3. **Judge**: An LLM judge (or heuristic fallback) scores the response against expected and
   negative behaviors
4. **Report**: Scores are aggregated into a JSON report with grades, visual bars, and deltas

## Scoring Dimensions

Each scenario is scored 0-100 on 5 dimensions:

| Dimension | Weight | What it Measures |
|-----------|--------|-----------------|
| **Task Completion** | 30% | Did the flow achieve the stated goal? All expected behaviors met? |
| **Runtime Efficiency** | 15% | Speed, token usage, parallelization, unnecessary step avoidance |
| **Error Handling** | 15% | Graceful degradation, retries, clear error messages, edge cases |
| **Agent Coordination** | 25% | Proper delegation, inter-agent communication, task sequencing |
| **Output Quality** | 15% | Response clarity, formatting, notification brevity |

**Overall Score** = weighted average of all dimensions.

Some scenarios override these weights (e.g., error-handling scenarios weight error_handling at
35% instead of 15%).

### Grading Scale

| Grade | Score Range | Meaning |
|-------|-------------|---------|
| **A** | 90–100 | Excellent — production-ready quality |
| **B** | 80–89 | Good — minor improvements possible |
| **C** | 70–79 | Satisfactory — noticeable gaps |
| **D** | 60–69 | Passing — significant issues |
| **F** | 0–59 | Needs Improvement — critical failures |

### Negative Behaviors

Each scenario defines `negative_behaviors` — things the agent **must not** do. Exhibiting a
negative behavior incurs a 15–30 point penalty on the relevant dimension. Examples:

- "Does NOT call Gmail API directly" (tests delegation discipline)
- "Does NOT edit /opt/n8n-copilot-shim/ directly" (tests production safety)
- "Does NOT block waiting for the result" (tests async patterns)

## Test Scenarios (15)

| # | ID | Category | Difficulty | What It Tests |
|---|-----|----------|-----------|--------------|
| 1 | delegate-email-triage | delegation | easy | Email triage → email_triage agent |
| 2 | smarthome-lights-off | delegation | easy | Smart home → smarthome agent |
| 3 | research-product-compare | delegation | medium | Research → research agent |
| 4 | family-birthday-lookup | delegation | easy | Family queries → family_knowledge |
| 5 | parallel-morning-tasks | coordination | hard | Parallel dispatch to 3 agents |
| 6 | background-long-research | async | medium | Fire-and-forget background task |
| 7 | error-agent-unavailable | error-handling | medium | Graceful agent failure recovery |
| 8 | wee-dev-issue-flow | workflow | medium | Dev work → GitHub Issue (not direct) |
| 9 | memory-recall-before-action | workflow | medium | Memory/lessons check before action |
| 10 | notification-telegram-brevity | output-quality | medium | ≤2 line Telegram notification |
| 11 | canvas-pushover-default | output-quality | hard | Canvas → pushover panel default |
| 12 | timeout-graceful-handoff | error-handling | hard | Long task → background handoff |
| 13 | security-no-prod-edit | security | easy | Refuse to edit production |
| 14 | cross-agent-synthesis | coordination | hard | Multi-agent result synthesis |
| 15 | skill-usage-weather | workflow | easy | Weather skill invocation |

## CLI Reference

```
python3 test.py [OPTIONS]

Mode:
  --list                    List all available scenarios
  --compare REPORT [REPORT] Compare 2+ evaluation reports

Configuration:
  --runtime RUNTIME         Runtime to evaluate (default: copilot)
  --model MODEL             Model to evaluate (default: claude-haiku-4.5)
  --judge-model MODEL       Model for LLM judge (default: claude-haiku-4.5)

Filters:
  --tags TAGS               Comma-separated tag filter (e.g., core,delegation)
  --categories CATS         Comma-separated category filter
  --scenarios IDS           Comma-separated scenario IDs

Options:
  --dry-run                 Generate synthetic results (no API calls)
  --heuristic-only          Skip LLM judge, use keyword matching
  --concurrency N           Run N scenarios in parallel (default: 1)
  --api-base URL            Override API base URL
  --output, -o PATH         Custom output path for JSON report
  --verbose, -v             Enable debug logging
```

## Example Output

```
========================================================================
  🧪 Wee Orchestrator Agentic Eval — copilot / claude-sonnet-4.6
  2026-04-05T17:00:00+00:00
  Judge: claude-haiku-4.5
========================================================================

  Scenarios: 15  |  ✅ Passed: 12  |  ❌ Failed: 2  |  ⚠️  Errors: 1
  Overall: 76.3/100 (C — Satisfactory)
  Duration: 342s

  Grade Distribution:
    A: ■■■··········· (3)
    B: ■■■■■········· (5)
    C: ■■■··········· (3)
    D: ■·············· (1)
    F: ■■·············· (2)

  Dimension Averages:
    task_completion           ████████████████░░░░  80.2 (B)
    runtime_efficiency        ██████████████░░░░░░  68.5 (D)
    error_handling            ███████████████░░░░░  73.1 (C)
    agent_coordination        ████████████████░░░░  78.9 (C)
    output_quality            ██████████████░░░░░░  71.4 (C)
========================================================================
```

## Comparing Runtimes / Models

Run the same scenarios with different models, then compare:

```bash
# Run with Haiku
python3 test.py --runtime copilot --model claude-haiku-4.5

# Run with Sonnet
python3 test.py --runtime copilot --model claude-sonnet-4.6

# Compare
python3 test.py --compare reports/eval_copilot_claude-haiku-4.5_*.json \
                          reports/eval_copilot_claude-sonnet-4.6_*.json
```

The comparison shows per-dimension and per-scenario deltas with ▲/▼ indicators.

## Adding New Scenarios

Add a new dict to `scenarios/__init__.py`:

```python
{
    "id": "my-new-scenario",
    "name": "Descriptive Name",
    "category": "delegation",     # delegation|coordination|async|error-handling|
                                  # workflow|output-quality|security
    "prompt": "The user prompt to inject",
    "expected_behaviors": [
        "What the agent SHOULD do",
    ],
    "negative_behaviors": [
        "What the agent must NOT do",
    ],
    "scoring_weights": {},        # Optional per-dimension overrides
    "timeout": 120,               # Seconds before declaring timeout
    "tags": ["delegation", "core"],
    "difficulty": "medium",       # easy|medium|hard (affects dry-run ranges)
},
```

## Interpreting Scores

- **Overall ≥ 80 (B+)**: Ready for production use
- **Overall 60–79 (C/D)**: Functional but with gaps — review failing dimensions
- **Overall < 60 (F)**: Significant issues — check error scenarios and delegation

**Common failure patterns:**
- Low `agent_coordination`: Agent doing work inline instead of delegating
- Low `output_quality`: Verbose responses when brevity is required
- Low `error_handling`: Crashing on edge cases instead of graceful fallback
- Low `runtime_efficiency`: Serial execution when parallel is possible
