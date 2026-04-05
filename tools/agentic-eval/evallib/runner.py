"""
Runner — executes scenarios against the Wee Orchestrator API.

Injects the prompt via background-tasks API, waits for completion,
captures the full response (transcript), and hands it to the judge.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from typing import Optional

from .scoring import DimensionScore, EvalReport, ScenarioResult
from .judge import judge_response

logger = logging.getLogger("agentic-eval.runner")


class EvalRunner:
    """Runs evaluation scenarios against the Wee Orchestrator."""

    def __init__(
        self,
        runtime: str = "copilot",
        model: str = "claude-haiku-4.5",
        judge_model: str = "claude-haiku-4.5",
        api_base: str = "https://127.0.0.1:8000",
        api_key: Optional[str] = None,
        use_llm_judge: bool = True,
        dry_run: bool = False,
    ):
        self.runtime = runtime
        self.model = model
        self.judge_model = judge_model
        self.api_base = api_base
        self.api_key = api_key or os.environ.get(
            "WEE_API_KEY",
            "shared_R6R6wReORUV6bouLntScMTowbsh30Rzqa3hzjs3bWgU",
        )
        self.use_llm_judge = use_llm_judge
        self.dry_run = dry_run

    def run_scenario(self, scenario: dict) -> ScenarioResult:
        """Execute a single scenario and return scored result."""
        result = ScenarioResult(
            scenario_id=scenario["id"],
            scenario_name=scenario["name"],
            category=scenario.get("category", "unknown"),
            runtime=self.runtime,
            model=self.model,
        )
        result.status = "running"

        if self.dry_run:
            return self._dry_run_result(scenario, result)

        timeout = scenario.get("timeout", 120)
        start = time.time()

        try:
            # Inject prompt via background-tasks API
            eval_prompt = self._build_eval_prompt(scenario)
            task_id = self._create_task(eval_prompt, timeout)

            if not task_id:
                result.status = "error"
                result.error = "Failed to create background task"
                result.execution_time_s = round(time.time() - start, 1)
                return result

            logger.info(
                "Scenario %s → task %s (timeout %ds)",
                scenario["id"], task_id, timeout,
            )

            # Poll for completion
            response = self._poll_task(task_id, timeout + 30)
            result.execution_time_s = round(time.time() - start, 1)

            if response is None:
                result.status = "error"
                result.error = f"Task {task_id} timed out after {timeout}s"
                return result

            result.raw_response = response

            # Judge the response
            dim_scores = judge_response(
                scenario, response,
                judge_model=self.judge_model,
                use_api=self.use_llm_judge,
            )
            result.dimensions = dim_scores
            result.compute_overall()

        except Exception as e:
            result.status = "error"
            result.error = str(e)
            result.execution_time_s = round(time.time() - start, 1)
            logger.exception("Scenario %s failed: %s", scenario["id"], e)

        return result

    def run_all(self, scenarios: list[dict]) -> EvalReport:
        """Run all scenarios and produce an aggregate report."""
        from datetime import datetime, timezone

        report = EvalReport(
            runtime=self.runtime,
            model=self.model,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        for i, scenario in enumerate(scenarios, 1):
            logger.info(
                "[%d/%d] Running: %s", i, len(scenarios), scenario["name"]
            )
            result = self.run_scenario(scenario)
            report.results.append(result)
            logger.info(
                "  → %s (%.1f) in %.1fs",
                result.status, result.overall_score, result.execution_time_s,
            )

        return report

    def _build_eval_prompt(self, scenario: dict) -> str:
        """Wrap the scenario prompt with eval context."""
        return (
            f"[EVAL MODE] This is an automated evaluation. "
            f"Respond naturally as if this were a real user request.\n\n"
            f"User: {scenario['prompt']}"
        )

    def _create_task(self, prompt: str, timeout: int) -> Optional[str]:
        """Create a background task and return task_id."""
        try:
            cmd = [
                "curl", "-s", "-k", "-X", "POST",
                f"{self.api_base}/api/v1/background-tasks",
                "-H", "Content-Type: application/json",
                "-H", f"Authorization: Bearer {self.api_key}",
                "-H", "X-User-Identity: eval-harness",
                "-H", "X-Auth-Channel: api",
                "-d", json.dumps({
                    "prompt": prompt,
                    "agent": "orchestrator",
                    "runtime": self.runtime,
                    "model": self.model,
                    "timeout": timeout,
                }),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if proc.returncode != 0:
                logger.error("Task creation failed: %s", proc.stderr)
                return None

            data = json.loads(proc.stdout)
            return data.get("task_id") or data.get("id")

        except Exception as e:
            logger.error("Task creation exception: %s", e)
            return None

    def _poll_task(self, task_id: str, max_wait: int) -> Optional[str]:
        """Poll a background task until completion. Returns response text."""
        deadline = time.time() + max_wait

        while time.time() < deadline:
            try:
                cmd = [
                    "curl", "-s", "-k",
                    f"{self.api_base}/api/v1/background-tasks/{task_id}",
                    "-H", f"Authorization: Bearer {self.api_key}",
                ]
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=15
                )
                if proc.returncode != 0:
                    time.sleep(5)
                    continue

                data = json.loads(proc.stdout)
                status = data.get("status", "")

                if status in ("completed", "done", "success"):
                    # Try multiple response fields
                    return (
                        data.get("result", "")
                        or data.get("response", "")
                        or data.get("output", "")
                        or data.get("transcript", "")
                        or json.dumps(data)
                    )
                elif status in ("failed", "error", "cancelled"):
                    logger.warning(
                        "Task %s ended with status: %s", task_id, status
                    )
                    return data.get("error", f"Task {status}")

            except Exception as e:
                logger.debug("Poll error: %s", e)

            time.sleep(5)

        return None

    def _dry_run_result(
        self, scenario: dict, result: ScenarioResult
    ) -> ScenarioResult:
        """Generate a synthetic result for dry-run mode."""
        import random

        result.raw_response = f"[DRY RUN] Would execute: {scenario['prompt']}"
        result.execution_time_s = 0.0

        dims = []
        for dim_name in ["task_completion", "runtime_efficiency",
                         "error_handling", "agent_coordination",
                         "output_quality"]:
            dims.append(DimensionScore(
                name=dim_name,
                score=random.randint(55, 95),
                reasoning="Dry run — synthetic score",
            ))
        result.dimensions = dims
        result.compute_overall()
        return result
