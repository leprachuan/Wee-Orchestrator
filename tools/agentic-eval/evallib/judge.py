"""
LLM-as-Judge module for scoring agentic flow responses.

Uses the orchestrator's own API to evaluate responses against expected behaviors.
Supports multiple judge models (defaults to claude-haiku-4.5 for speed/cost).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import re
from typing import Optional

from .scoring import DIMENSIONS, DimensionScore

logger = logging.getLogger("agentic-eval.judge")

# Judge prompt template
JUDGE_SYSTEM_PROMPT = """\
You are an expert evaluator for an AI orchestrator system called "Wee Orchestrator".
Your job is to score an agent's response to a user prompt across multiple dimensions.

You will be given:
1. The user prompt (what was asked)
2. The agent's full response (what it did/said)
3. Expected behaviors (what a correct response should include)
4. The scenario category and context

Score EACH dimension from 0-100 with brief reasoning.

Dimensions:
- task_completion: Did the response achieve the stated goal? Were all expected behaviors met?
- runtime_efficiency: Was the approach efficient? Did it parallelize where possible? Avoid unnecessary steps?
- error_handling: Did it handle edge cases? Provide clear error messages? Degrade gracefully?
- agent_coordination: Did it delegate to the right agents? Avoid doing work itself when an agent exists? Sequence correctly?
- output_quality: Is the response clear, well-formatted, appropriately brief (≤2 lines for notifications)?

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

## Agent Response
{response}

---
Score each dimension 0-100. Return ONLY the JSON object.
"""


def build_judge_prompt(scenario: dict, response: str) -> str:
    """Build the judge evaluation prompt."""
    expected = "\n".join(f"- {b}" for b in scenario.get("expected_behaviors", []))
    return JUDGE_USER_TEMPLATE.format(
        scenario_name=scenario["name"],
        category=scenario.get("category", "unknown"),
        prompt=scenario["prompt"],
        expected_behaviors=expected,
        response=response[:8000],  # truncate very long responses
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
    Falls back to local heuristic scoring if API is unavailable.
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
        # Create background task for judging
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
            logger.warning("No task_id in response: %s", result.stdout[:200])
            return _heuristic_score(scenario, response)

        # Poll for completion (up to 90s)
        import time
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
                # Extract the response text
                judge_response = (
                    poll_data.get("result", "")
                    or poll_data.get("response", "")
                    or poll_data.get("output", "")
                )
                return _parse_judge_response(judge_response)
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
        # Extract JSON from response (may have surrounding text)
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
            DimensionScore(name=d, score=50, reasoning="Parse error — default score")
            for d in DIMENSIONS
        ]


def _heuristic_score(scenario: dict, response: str) -> list[DimensionScore]:
    """
    Fallback heuristic scoring when LLM judge is unavailable.

    Uses keyword matching against expected_behaviors.
    """
    expected = scenario.get("expected_behaviors", [])
    response_lower = response.lower()

    # Count how many expected behaviors are partially matched
    matches = 0
    for behavior in expected:
        # Extract key terms from each behavior
        keywords = [
            w.lower() for w in behavior.split()
            if len(w) > 3 and w.lower() not in (
                "the", "that", "this", "with", "from", "does", "should",
                "into", "about", "when", "than", "were", "been", "have",
            )
        ]
        if any(kw in response_lower for kw in keywords):
            matches += 1

    match_ratio = matches / len(expected) if expected else 0.5
    base_score = int(40 + match_ratio * 50)  # range: 40-90

    # Delegation check: penalize if response seems to do work inline
    delegation_penalty = 0
    if scenario.get("category") == "delegation":
        inline_markers = ["curl", "requests.get", "import requests", "api_call"]
        if any(m in response_lower for m in inline_markers):
            delegation_penalty = 20

    # Brevity check for notification scenarios
    brevity_penalty = 0
    if "notification" in scenario.get("tags", []):
        lines = [l for l in response.strip().split("\n") if l.strip()]
        if len(lines) > 5:
            brevity_penalty = 15

    scores = []
    for dim in DIMENSIONS:
        score = base_score
        if dim == "agent_coordination":
            score = max(0, score - delegation_penalty)
        if dim == "output_quality":
            score = max(0, score - brevity_penalty)
        scores.append(DimensionScore(
            name=dim,
            score=max(0, min(100, score)),
            reasoning=f"Heuristic: {matches}/{len(expected)} behaviors matched",
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
        use_api: Whether to use the Wee Orchestrator API (True) or heuristic (False)
    """
    if use_api:
        return judge_via_api(scenario, response, model=judge_model)
    return _heuristic_score(scenario, response)
