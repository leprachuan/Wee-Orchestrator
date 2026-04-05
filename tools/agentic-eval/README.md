# Wee Orchestrator — Agentic Flow Evaluator

A standalone test suite for evaluating agentic workflows in Wee Orchestrator. Runs realistic multi-agent scenarios, scores responses across 5 quality dimensions using LLM-as-judge, and generates structured reports for comparing runtimes and models.

## Quick Start

```bash
cd tools/agentic-eval

# List all 15 test scenarios
python3 test.py --list

# Dry run (synthetic scores, no API calls — verify setup)
python3 test.py --dry-run

# Full evaluation with specific runtime + model
python3 test.py --runtime copilot --model claude-haiku-4.5

# Run only core scenarios
python3 test.py --tags core

# Compare two evaluation reports
python3 test.py --compare reports/eval_copilot_*.json reports/eval_copilot_*.json
```

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

### Score Interpretation

| Range | Grade | Meaning |
|-------|-------|---------|
| 90-100 | A | Excellent — production-quality orchestration |
| 75-89 | B | Good — meets most expectations, minor gaps |
| 60-74 | C | Passing — functional but with notable issues |
| 40-59 | D | Poor — significant failures in coordination or output |
| 0-39 | F | Failing — fundamental misunderstanding of task |

## Test Scenarios (15)

### Delegation (4 scenarios)
| ID | Name | Tests |
|----|------|-------|
| `delegate-email-triage` | Email Triage Delegation | Routes to email_triage, doesn't do inline |
| `smarthome-lights-off` | Smart Home Lights Control | Routes to smarthome agent |
| `research-product-compare` | Product Research Delegation | Routes to research agent |
| `family-birthday-lookup` | Family Knowledge Query | Routes to family_knowledge agent |

### Coordination (2 scenarios)
| ID | Name | Tests |
|----|------|-------|
| `parallel-morning-tasks` | Parallel Task Dispatch | Dispatches 2-3 tasks simultaneously |
| `cross-agent-synthesis` | Cross-Agent Synthesis | Multi-agent results aggregation |

### Async (1 scenario)
| ID | Name | Tests |
|----|------|-------|
| `background-long-research` | Background Task Fire-and-Forget | Creates bg task, returns immediately |

### Error Handling (2 scenarios)
| ID | Name | Tests |
|----|------|-------|
| `error-agent-unavailable` | Graceful Agent Failure | Handles agent/service unavailability |
| `timeout-graceful-handoff` | Timeout Graceful Handoff | Queues work when facing timeouts |

### Workflow (3 scenarios)
| ID | Name | Tests |
|----|------|-------|
| `wee-dev-issue-flow` | Dev Work → GitHub Issue | Files issue instead of editing prod |
| `memory-recall-before-action` | Memory Check Before Task | Checks memories before acting |
| `skill-usage-weather` | Skill Invocation | Correct skill selection and formatting |

### Output Quality (2 scenarios)
| ID | Name | Tests |
|----|------|-------|
| `notification-telegram-brevity` | Telegram Notification Brevity | ≤2 line notifications |
| `canvas-pushover-default` | Canvas Default to Pushover | Uses pushover panel, not standalone |

### Security (1 scenario)
| ID | Name | Tests |
|----|------|-------|
| `security-no-prod-edit` | Production Safety Gate | Refuses to edit prod directly |

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
3. **Judge**: An LLM judge (or heuristic fallback) scores the response against expected behaviors
4. **Report**: Scores are aggregated into a JSON report with per-scenario and per-dimension breakdowns

## CLI Reference

```
python3 test.py [OPTIONS]

Options:
  --list                    List all available scenarios
  --compare REPORT [REPORT] Compare 2+ evaluation reports
  --runtime RUNTIME         Runtime to evaluate (default: copilot)
  --model MODEL             Model to evaluate (default: claude-haiku-4.5)
  --judge-model MODEL       Model for LLM judge (default: claude-haiku-4.5)
  --tags TAGS               Comma-separated tag filter
  --categories CATS         Comma-separated category filter
  --scenarios IDS           Comma-separated scenario IDs
  --dry-run                 Generate synthetic results (no API)
  --heuristic-only          Skip LLM judge, use keyword matching
  --api-base URL            Override API base URL
  --verbose, -v             Enable debug logging
```

## Comparing Runtimes/Models

Run the same scenarios with different models, then compare:

```bash
# Evaluate Haiku
python3 test.py --model claude-haiku-4.5 --tags core

# Evaluate Sonnet
python3 test.py --model claude-sonnet-4.6 --tags core

# Compare
python3 test.py --compare reports/eval_copilot_claude-haiku-4.5_*.json \
                          reports/eval_copilot_claude-sonnet-4.6_*.json
```

The comparison shows per-dimension deltas and identifies which model is stronger in each area.

## Report Format (JSON)

```json
{
  "runtime": "copilot",
  "model": "claude-haiku-4.5",
  "timestamp": "2026-04-05T12:00:00+00:00",
  "summary": {
    "total_scenarios": 15,
    "passed": 12,
    "failed": 2,
    "errors": 1,
    "avg_overall": 74.3,
    "avg_by_dimension": {
      "task_completion": 78.5,
      "runtime_efficiency": 71.2,
      "error_handling": 69.8,
      "agent_coordination": 80.1,
      "output_quality": 72.4
    },
    "avg_by_category": {
      "delegation": 82.0,
      "coordination": 75.5,
      "error-handling": 65.0
    }
  },
  "results": [...]
}
```

## Adding New Scenarios

Edit `scenarios/__init__.py` and add a new dict to the `SCENARIOS` list:

```python
{
    "id": "my-new-scenario",
    "name": "Descriptive Name",
    "category": "delegation",  # delegation|coordination|async|error-handling|workflow|output-quality|security
    "prompt": "The user prompt to inject",
    "expected_behaviors": [
        "First expected behavior",
        "Second expected behavior",
    ],
    "timeout": 120,
    "tags": ["core", "delegation"],
}
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
│   ├── scoring.py        # Score models, report generation
│   ├── judge.py          # LLM-as-judge + heuristic fallback
│   └── runner.py         # API interaction, task polling
└── reports/              # Generated JSON reports
    └── .gitkeep
```

## Requirements

- Python 3.10+
- Network access to Wee Orchestrator API (default: https://127.0.0.1:8000)
- `curl` available in PATH
- No additional pip dependencies (stdlib only)
