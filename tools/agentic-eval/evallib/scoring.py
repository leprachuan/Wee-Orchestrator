"""
Scoring engine for agentic flow evaluation.

Dimensions:
  - task_completion (0-100): Did the flow achieve the stated goal?
  - runtime_efficiency (0-100): Speed, token use, parallelization
  - error_handling (0-100): Graceful degradation, retries, clear errors
  - agent_coordination (0-100): Proper delegation, sequencing, communication
  - output_quality (0-100): Clarity, formatting, notification appropriateness

Each dimension is scored by an LLM judge using the scenario's expected_behaviors
as the rubric.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass, field
from typing import Optional

# Default weights for overall score computation
DEFAULT_WEIGHTS = {
    "task_completion": 0.30,
    "runtime_efficiency": 0.15,
    "error_handling": 0.15,
    "agent_coordination": 0.25,
    "output_quality": 0.15,
}

DIMENSIONS = list(DEFAULT_WEIGHTS.keys())


@dataclass
class DimensionScore:
    """Score for a single dimension."""
    name: str
    score: int  # 0-100
    reasoning: str = ""
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScenarioResult:
    """Full evaluation result for one scenario."""
    scenario_id: str
    scenario_name: str
    category: str
    runtime: str
    model: str
    dimensions: list[DimensionScore] = field(default_factory=list)
    overall_score: float = 0.0
    execution_time_s: float = 0.0
    raw_response: str = ""
    error: Optional[str] = None
    status: str = "pending"  # pending | running | passed | failed | error

    def compute_overall(self, weights: dict | None = None) -> float:
        w = weights or DEFAULT_WEIGHTS
        total_weight = 0.0
        weighted_sum = 0.0
        for dim in self.dimensions:
            if dim.name in w:
                weighted_sum += dim.score * w[dim.name]
                total_weight += w[dim.name]
        self.overall_score = round(weighted_sum / total_weight, 1) if total_weight else 0.0
        self.status = "passed" if self.overall_score >= 60 else "failed"
        return self.overall_score

    def to_dict(self) -> dict:
        return {
            "scenario_id": self.scenario_id,
            "scenario_name": self.scenario_name,
            "category": self.category,
            "runtime": self.runtime,
            "model": self.model,
            "dimensions": [d.to_dict() for d in self.dimensions],
            "overall_score": self.overall_score,
            "execution_time_s": self.execution_time_s,
            "error": self.error,
            "status": self.status,
        }


@dataclass
class EvalReport:
    """Aggregate evaluation report."""
    runtime: str
    model: str
    results: list[ScenarioResult] = field(default_factory=list)
    timestamp: str = ""

    @property
    def total_scenarios(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.status == "passed")

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == "failed")

    @property
    def errors(self) -> int:
        return sum(1 for r in self.results if r.status == "error")

    @property
    def avg_overall(self) -> float:
        scores = [r.overall_score for r in self.results if r.status != "error"]
        return round(statistics.mean(scores), 1) if scores else 0.0

    @property
    def avg_by_dimension(self) -> dict[str, float]:
        dim_scores: dict[str, list[int]] = {d: [] for d in DIMENSIONS}
        for r in self.results:
            if r.status == "error":
                continue
            for dim in r.dimensions:
                if dim.name in dim_scores:
                    dim_scores[dim.name].append(dim.score)
        return {
            d: round(statistics.mean(scores), 1) if scores else 0.0
            for d, scores in dim_scores.items()
        }

    @property
    def avg_by_category(self) -> dict[str, float]:
        cat_scores: dict[str, list[float]] = {}
        for r in self.results:
            if r.status == "error":
                continue
            cat_scores.setdefault(r.category, []).append(r.overall_score)
        return {
            c: round(statistics.mean(s), 1) for c, s in cat_scores.items()
        }

    def summary_table(self) -> str:
        """Return a formatted summary string."""
        lines = []
        lines.append(f"{'=' * 72}")
        lines.append(
            f"  Wee Orchestrator Agentic Eval — {self.runtime} / {self.model}"
        )
        lines.append(f"  {self.timestamp}")
        lines.append(f"{'=' * 72}")
        lines.append("")
        lines.append(f"  Scenarios: {self.total_scenarios}  |  "
                      f"Passed: {self.passed}  |  Failed: {self.failed}  |  "
                      f"Errors: {self.errors}")
        lines.append(f"  Overall Average: {self.avg_overall}/100")
        lines.append("")

        # Dimension averages
        lines.append("  Dimension Averages:")
        for dim, avg in self.avg_by_dimension.items():
            bar = "█" * int(avg / 5) + "░" * (20 - int(avg / 5))
            lines.append(f"    {dim:25s} {bar} {avg:5.1f}")
        lines.append("")

        # Category averages
        lines.append("  Category Averages:")
        for cat, avg in self.avg_by_category.items():
            lines.append(f"    {cat:25s} {avg:5.1f}")
        lines.append("")

        # Per-scenario results
        lines.append(f"  {'Scenario':<40s} {'Score':>6s}  {'Status':>8s}  {'Time':>6s}")
        lines.append(f"  {'-' * 66}")
        for r in self.results:
            status_icon = {"passed": "✅", "failed": "❌", "error": "⚠️"}.get(
                r.status, "⏳"
            )
            lines.append(
                f"  {r.scenario_name:<40s} {r.overall_score:5.1f}  "
                f"{status_icon} {r.status:>6s}  {r.execution_time_s:5.1f}s"
            )
        lines.append(f"{'=' * 72}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "runtime": self.runtime,
            "model": self.model,
            "timestamp": self.timestamp,
            "summary": {
                "total_scenarios": self.total_scenarios,
                "passed": self.passed,
                "failed": self.failed,
                "errors": self.errors,
                "avg_overall": self.avg_overall,
                "avg_by_dimension": self.avg_by_dimension,
                "avg_by_category": self.avg_by_category,
            },
            "results": [r.to_dict() for r in self.results],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)
