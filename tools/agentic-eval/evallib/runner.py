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
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from .judge import judge_response
from .scoring import DimensionScore, EvalReport, ScenarioResult

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
        concurrency: int = 1,
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
        self.concurrency = max(1, concurrency)

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

            response = self._poll_task(task_id, timeout + 30)
            result.execution_time_s = round(time.time() - start, 1)

            if response is None:
                result.status = "error"
                result.error = f"Task {task_id} timed out after {timeout}s"
                return result

            result.raw_response = response

            # Judge the response (use scenario-specific weights)
            dim_scores = judge_response(
                scenario, response,
                judge_model=self.judge_model,
                use_api=self.use_llm_judge,
            )
            result.dimensions = dim_scores
            result.compute_overall(
                weights=scenario.get("scoring_weights")
            )

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
            judge_model=self.judge_model,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        suite_start = time.time()

        if self.concurrency > 1 and len(scenarios) > 1:
            report.results = self._run_concurrent(scenarios)
        else:
            report.results = self._run_sequential(scenarios)

        report.total_duration_s = round(time.time() - suite_start, 1)
        return report

    def _run_sequential(self, scenarios: list[dict]) -> list[ScenarioResult]:
        """Run scenarios one at a time with progress logging."""
        results = []
        for i, scenario in enumerate(scenarios, 1):
            logger.info(
                "[%d/%d] Running: %s", i, len(scenarios), scenario["name"]
            )
            result = self.run_scenario(scenario)
            results.append(result)
            logger.info(
                "  → %s (%.1f, %s) in %.1fs",
                result.status, result.overall_score,
                result.grade, result.execution_time_s,
            )
        return results

    def _run_concurrent(
        self, scenarios: list[dict]
    ) -> list[ScenarioResult]:
        """Run scenarios concurrently with a thread pool."""
        results: list[ScenarioResult] = []
        logger.info(
            "Running %d scenarios with concurrency=%d",
            len(scenarios), self.concurrency,
        )

        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            future_map = {
                pool.submit(self.run_scenario, s): s
                for s in scenarios
            }
            done_count = 0
            for future in as_completed(future_map):
                done_count += 1
                scenario = future_map[future]
                try:
                    result = future.result()
                    results.append(result)
                    logger.info(
                        "[%d/%d] %s → %s (%.1f) in %.1fs",
                        done_count, len(scenarios),
                        scenario["name"], result.status,
                        result.overall_score, result.execution_time_s,
                    )
                except Exception as e:
                    logger.error(
                        "Scenario %s raised: %s", scenario["id"], e
                    )
                    results.append(ScenarioResult(
                        scenario_id=scenario["id"],
                        scenario_name=scenario["name"],
                        category=scenario.get("category", "unknown"),
                        runtime=self.runtime,
                        model=self.model,
                        status="error",
                        error=str(e),
                    ))

        # Restore original ordering
        order = {s["id"]: i for i, s in enumerate(scenarios)}
        results.sort(key=lambda r: order.get(r.scenario_id, 999))
        return results

    def _build_eval_prompt(self, scenario: dict) -> str:
        """Wrap the scenario prompt with eval context."""
        return (
            f"[EVAL MODE] This is an automated evaluation. "
            f"Respond naturally as if this were a real user request.\n\n"
            f"User: {scenario['prompt']}"
        )

    def _create_task(
        self, prompt: str, timeout: int, retries: int = 2
    ) -> Optional[str]:
        """Create a background task and return task_id. Retries on failure."""
        for attempt in range(retries + 1):
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
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=30
                )
                if proc.returncode != 0:
                    logger.warning(
                        "Task creation attempt %d failed: %s",
                        attempt + 1, proc.stderr,
                    )
                    if attempt < retries:
                        time.sleep(2)
                    continue

                data = json.loads(proc.stdout)
                task_id = data.get("task_id") or data.get("id")
                if task_id:
                    return task_id
                logger.warning(
                    "No task_id in response: %s", proc.stdout[:200]
                )

            except Exception as e:
                logger.warning(
                    "Task creation attempt %d exception: %s",
                    attempt + 1, e,
                )
                if attempt < retries:
                    time.sleep(2)

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
                    return (
                        data.get("result", "")
                        or data.get("response", "")
                        or data.get("output", "")
                        or data.get("transcript", "")
                        or json.dumps(data)
                    )
                elif status in ("failed", "error", "cancelled"):
                    logger.warning(
                        "Task %s ended: %s", task_id, status
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
        import hashlib
        import random

        # Deterministic seed from scenario ID for reproducible dry runs
        seed = int(hashlib.sha256(
            scenario["id"].encode()
        ).hexdigest()[:8], 16)
        rng = random.Random(seed)

        result.raw_response = (
            f"[DRY RUN] Would execute: {scenario['prompt']}"
        )
        result.execution_time_s = 0.0

        # Difficulty affects score range
        difficulty = scenario.get("difficulty", "medium")
        ranges = {
            "easy": (70, 95),
            "medium": (55, 90),
            "hard": (45, 85),
        }
        lo, hi = ranges.get(difficulty, (55, 90))

        dims = []
        for dim_name in [
            "task_completion", "runtime_efficiency",
            "error_handling", "agent_coordination",
            "output_quality",
        ]:
            dims.append(DimensionScore(
                name=dim_name,
                score=rng.randint(lo, hi),
                reasoning=f"Dry run — synthetic ({difficulty})",
            ))
        result.dimensions = dims
        result.compute_overall(weights=scenario.get("scoring_weights"))
        return result
