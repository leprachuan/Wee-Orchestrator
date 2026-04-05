"""
LLM-as-Judge module for scoring agentic flow responses.

Uses the orchestrator's own API to evaluate responses against expected behaviors.
Supports multiple judge models (defaults to claude-haiku-4.5 for speed/cost).
Falls back to heuristic keyword-matching if API unavailable.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from typing import Optional

from .scoring import DIMENSIONS, DimensionScore

logger = logging.getLogger("agentic-eval.judge")

JUDGE_SYSTEM_PROMPT = """\
You are an expert evaluator for an AI orchestrator system called "Wee Orchestrator".
Your job is to score an agent's response to a user prompt across multiple dimensions.

You will be given:
1. The user prompt (what was asked)
2. The agent's full response (what it did/said)
3. Expected behaviors (what a correct response should include)
4. Negative behaviors (what the agent must NOT do)
5. The scenario category and context

Score EACH dimension from 0-100 with brief reasoning (1-2 sentences).

Dimensions:
- task_completion: Did the response achieve the stated goal? Were expected behaviors met?
- runtime_efficiency: Was the approach efficient? Did it parallelize? Avoid unnecessary steps?
- error_handling: Did it handle edge cases? Provide clear error messages? Degrade gracefully?
- agent_coordination: Did it delegate to the right agents? Avoid doing work itself when an \
agent exists? Sequence correctly?
- output_quality: Is the response clear, well-formatted, appropriately brief \
(≤2 lines for notifications)?

Scoring rules:
- If the response exhibits a NEGATIVE behavior, subtract 15-30 points from the relevant \
dimension.
- If an expected behavior is clearly met, that's +15-25 points on the relevant dimension.
- If the agent does work itself when it should have delegated, agent_coordination ≤ 40.
- If the response is >5 lines on a notification channel, output_quality ≤ 50.

Return ONLY valid JSON in this exact format:
{
  "dimensions": {
    "task_completion": {"score": <0-100>, "reasoning": "<brief explanation>"},
    "runtime_efficiency": {"score": <0-100>, "reasoning": "<brief explanation>"},
    "error_handling": {"score": <0-100>, "reasoning": "<brief explanation>"},
    "agent_coordination": {"score": <0-100>, "reasoning": "<brief explanation>"},
    "output_quality": {"score": <0-100>, "reasoning": "<brief explanation>"}
  }
}
"""

JUDGE_USER_TEMPLATE = """\
## Scenario: {scenario_name}
Category: {category}

## User Prompt
{prompt}

## Expected Behaviors
{expected_behaviors}

## Negative Behaviors (MUST NOT do)
{negative_behaviors}

## Agent Response
{response}

---
Score each dimension 0-100. Return ONLY the JSON object.
"""


def build_judge_prompt(scenario: dict, response: str) -> str:
    """Build the judge evaluation prompt."""
    expected = "\n".join(
        f"- {b}" for b in scenario.get("expected_behaviors", [])
    )
    negative = "\n".join(
        f"- {b}" for b in scenario.get("negative_behaviors", [])
    ) or "- (none specified)"

    return JUDGE_USER_TEMPLATE.format(
        scenario_name=scenario["name"],
        category=scenario.get("category", "unknown"),
        prompt=scenario["prompt"],
        expected_behaviors=expected,
        negative_behaviors=negative,
        response=response[:8000],
    )


def judge_via_api(
    scenario: dict,
    response: str,
    model: str = "claude-haiku-4.5",
    api_base: str = "https://127.0.0.1:8000",
    api_key: Optional[str] = None,
) -> list[DimensionScore]:
    """
    Use the Wee Orchestrator API to judge a response.

    Creates a background task with the judge prompt and polls for result.
    Falls back to heuristic scoring if API is unavailable.
    """
    key = api_key or os.environ.get(
        "WEE_API_KEY",
        "shared_R6R6wReORUV6bouLntScMTowbsh30Rzqa3hzjs3bWgU",
    )

    judge_prompt = (
        f"You are an evaluation judge. {JUDGE_SYSTEM_PROMPT}\n\n"
        f"{build_judge_prompt(scenario, response)}"
    )

    try:
        create_cmd = [
            "curl", "-s", "-k", "-X", "POST",
            f"{api_base}/api/v1/background-tasks",
            "-H", "Content-Type: application/json",
            "-H", f"Authorization: Bearer {key}",
            "-H", "X-User-Identity: eval-judge",
            "-H", "X-Auth-Channel: api",
            "-d", json.dumps({
                "prompt": judge_prompt,
                "agent": "orchestrator",
                "runtime": "copilot",
                "model": model,
                "timeout": 120,
            }),
        ]

        result = subprocess.run(
            create_cmd, capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            logger.warning("Judge API call failed: %s", result.stderr)
            return _heuristic_score(scenario, response)

        task_data = json.loads(result.stdout)
        task_id = task_data.get("task_id") or task_data.get("id")
        if not task_id:
            logger.warning(
                "No task_id in response: %s", result.stdout[:200]
            )
            return _heuristic_score(scenario, response)

        # Poll for completion (up to 90s)
        for _ in range(18):
            time.sleep(5)
            poll_cmd = [
                "curl", "-s", "-k",
                f"{api_base}/api/v1/background-tasks/{task_id}",
                "-H", f"Authorization: Bearer {key}",
            ]
            poll_result = subprocess.run(
                poll_cmd, capture_output=True, text=True, timeout=15
            )
            if poll_result.returncode != 0:
                continue

            poll_data = json.loads(poll_result.stdout)
            status = poll_data.get("status", "")
            if status in ("completed", "done", "success"):
                judge_text = (
                    poll_data.get("result", "")
                    or poll_data.get("response", "")
                    or poll_data.get("output", "")
                )
                return _parse_judge_response(judge_text)
            elif status in ("failed", "error", "cancelled"):
                logger.warning("Judge task %s: %s", status, task_id)
                return _heuristic_score(scenario, response)

        logger.warning("Judge task timed out: %s", task_id)
        return _heuristic_score(scenario, response)

    except Exception as e:
        logger.warning("Judge error, falling back to heuristic: %s", e)
        return _heuristic_score(scenario, response)


def _parse_judge_response(text: str) -> list[DimensionScore]:
    """Parse the LLM judge's JSON response into DimensionScores."""
    try:
        json_match = re.search(r'\{[\s\S]*"dimensions"[\s\S]*\}', text)
        if not json_match:
            raise ValueError("No JSON found in judge response")

        data = json.loads(json_match.group())
        dims = data.get("dimensions", {})

        scores = []
        for dim_name in DIMENSIONS:
            dim_data = dims.get(dim_name, {})
            scores.append(DimensionScore(
                name=dim_name,
                score=max(0, min(100, int(dim_data.get("score", 50)))),
                reasoning=dim_data.get("reasoning", ""),
            ))
        return scores

    except (json.JSONDecodeError, ValueError, KeyError) as e:
        logger.warning("Failed to parse judge response: %s", e)
        return [
            DimensionScore(
                name=d, score=50,
                reasoning="Parse error — default score"
            )
            for d in DIMENSIONS
        ]


# ── Heuristic keyword sets for fallback scoring ─────────────────────────

_DELEGATION_AGENTS = {
    "email_triage", "smarthome", "research", "family_knowledge",
    "family-knowledge", "devops", "wee-dev", "wee-qa",
}

_DELEGATION_KEYWORDS = [
    "delegat", "background task", "agent_manager", "sub-agent",
    "dispatching", "routing to", "fire-and-forget",
]

_ERROR_KEYWORDS = [
    "error", "failed", "unavailable", "retry", "fallback",
    "could not", "graceful", "issue",
]

_INLINE_WORK_MARKERS = [
    "curl ", "requests.get", "import requests", "subprocess.run",
    "api_call", "http.client",
]


def _heuristic_score(
    scenario: dict, response: str
) -> list[DimensionScore]:
    """
    Fallback heuristic scoring when LLM judge is unavailable.

    Checks expected_behaviors (positive) and negative_behaviors (penalty).
    """
    expected = scenario.get("expected_behaviors", [])
    negative = scenario.get("negative_behaviors", [])
    response_lower = response.lower()
    category = scenario.get("category", "")

    # ── Positive: count matched expected behaviors ───────────────────
    expected_matches = 0
    for behavior in expected:
        keywords = [
            w.lower() for w in behavior.split()
            if len(w) > 3 and w.lower() not in {
                "the", "that", "this", "with", "from", "does", "should",
                "into", "about", "when", "than", "were", "been", "have",
                "must", "will", "what", "which", "their", "there",
            }
        ]
        if any(kw in response_lower for kw in keywords):
            expected_matches += 1

    match_ratio = expected_matches / len(expected) if expected else 0.5
    base_score = int(40 + match_ratio * 50)  # 40–90

    # ── Negative: check for forbidden behaviors ──────────────────────
    negative_violations = 0
    for neg in negative:
        neg_keywords = [
            w.lower() for w in neg.split()
            if len(w) > 3 and w.lower() not in {
                "does", "not", "the", "this", "that", "without",
            }
        ]
        if any(kw in response_lower for kw in neg_keywords):
            negative_violations += 1
    neg_penalty = negative_violations * 12

    # ── Category-specific adjustments ────────────────────────────────

    # Delegation: penalize inline work
    delegation_penalty = 0
    if category == "delegation":
        if any(m in response_lower for m in _INLINE_WORK_MARKERS):
            delegation_penalty = 20
        if any(kw in response_lower for kw in _DELEGATION_KEYWORDS):
            delegation_penalty = max(0, delegation_penalty - 10)

    # Notification brevity
    brevity_penalty = 0
    if "notification" in scenario.get("tags", []):
        visible_lines = [
            ln for ln in response.strip().split("\n") if ln.strip()
        ]
        if len(visible_lines) > 5:
            brevity_penalty = 15

    # Error handling: bonus for graceful language
    error_bonus = 0
    if category == "error-handling":
        if any(kw in response_lower for kw in _ERROR_KEYWORDS):
            error_bonus = 10

    # ── Build per-dimension scores ───────────────────────────────────
    scores = []
    for dim in DIMENSIONS:
        score = base_score - neg_penalty

        if dim == "agent_coordination":
            score -= delegation_penalty
        if dim == "output_quality":
            score -= brevity_penalty
        if dim == "error_handling":
            score += error_bonus

        reasoning = (
            f"Heuristic: {expected_matches}/{len(expected)} expected, "
            f"{negative_violations} violations"
        )

        scores.append(DimensionScore(
            name=dim,
            score=max(0, min(100, score)),
            reasoning=reasoning,
        ))

    return scores


def judge_response(
    scenario: dict,
    response: str,
    judge_model: str = "claude-haiku-4.5",
    use_api: bool = True,
) -> list[DimensionScore]:
    """
    Main entry point for judging a response.

    Args:
        scenario: The scenario definition dict
        response: The agent's raw response text
        judge_model: Model to use for LLM judging
        use_api: Whether to use the Wee Orchestrator API or heuristic only
    """
    if use_api:
        return judge_via_api(scenario, response, model=judge_model)
    return _heuristic_score(scenario, response)
