"""
Scoring engine for agentic flow evaluation.

Dimensions:
  - task_completion (0-100): Did the flow achieve the stated goal?
  - runtime_efficiency (0-100): Speed, token use, parallelization
  - error_handling (0-100): Graceful degradation, retries, clear errors
  - agent_coordination (0-100): Proper delegation, sequencing, communication
  - output_quality (0-100): Clarity, formatting, notification appropriateness

Each dimension is scored by an LLM judge using the scenario's expected_behaviors
and negative_behaviors as the rubric.
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

# Grade thresholds
GRADE_THRESHOLDS = [
    (90, "A", "Excellent"),
    (80, "B", "Good"),
    (70, "C", "Satisfactory"),
    (60, "D", "Passing"),
    (0, "F", "Needs Improvement"),
]


def score_to_grade(score: float) -> tuple[str, str]:
    """Convert a 0-100 score to a letter grade and label."""
    for threshold, letter, label in GRADE_THRESHOLDS:
        if score >= threshold:
            return letter, label
    return "F", "Needs Improvement"


@dataclass
class DimensionScore:
    """Score for a single dimension."""
    name: str
    score: int  # 0-100
    reasoning: str = ""
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def grade(self) -> str:
        return score_to_grade(self.score)[0]


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
        """Compute weighted overall score from dimension scores."""
        w = weights or DEFAULT_WEIGHTS
        total_weight = 0.0
        weighted_sum = 0.0
        for dim in self.dimensions:
            if dim.name in w:
                weighted_sum += dim.score * w[dim.name]
                total_weight += w[dim.name]
        self.overall_score = round(
            weighted_sum / total_weight, 1
        ) if total_weight else 0.0
        self.status = "passed" if self.overall_score >= 60 else "failed"
        return self.overall_score

    @property
    def grade(self) -> str:
        return score_to_grade(self.overall_score)[0]

    def to_dict(self) -> dict:
        return {
            "scenario_id": self.scenario_id,
            "scenario_name": self.scenario_name,
            "category": self.category,
            "runtime": self.runtime,
            "model": self.model,
            "dimensions": [d.to_dict() for d in self.dimensions],
            "overall_score": self.overall_score,
            "grade": self.grade,
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
    judge_model: str = ""
    total_duration_s: float = 0.0

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
    def overall_grade(self) -> str:
        return score_to_grade(self.avg_overall)[0]

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

    @property
    def grade_distribution(self) -> dict[str, int]:
        """Count how many scenarios got each grade."""
        dist: dict[str, int] = {"A": 0, "B": 0, "C": 0, "D": 0, "F": 0}
        for r in self.results:
            if r.status != "error":
                dist[r.grade] = dist.get(r.grade, 0) + 1
        return dist

    def summary_table(self) -> str:
        """Return a formatted summary string for console output."""
        lines: list[str] = []
        grade_letter, grade_label = score_to_grade(self.avg_overall)

        lines.append("")
        lines.append(f"{'=' * 76}")
        lines.append(
            f"  🧪 Wee Orchestrator Agentic Eval — "
            f"{self.runtime} / {self.model}"
        )
        lines.append(f"  {self.timestamp}")
        if self.judge_model:
            lines.append(f"  Judge: {self.judge_model}")
        lines.append(f"{'=' * 76}")
        lines.append("")

        # Summary stats
        lines.append(
            f"  Scenarios: {self.total_scenarios}  |  "
            f"✅ Passed: {self.passed}  |  ❌ Failed: {self.failed}  |  "
            f"⚠️  Errors: {self.errors}"
        )
        lines.append(
            f"  Overall: {self.avg_overall}/100 "
            f"({grade_letter} — {grade_label})"
        )
        if self.total_duration_s > 0:
            lines.append(
                f"  Duration: {self.total_duration_s:.0f}s"
            )
        lines.append("")

        # Grade distribution
        gdist = self.grade_distribution
        lines.append("  Grade Distribution:")
        for g in ["A", "B", "C", "D", "F"]:
            count = gdist.get(g, 0)
            bar = "■" * count + "·" * (self.total_scenarios - count)
            lines.append(f"    {g}: {bar} ({count})")
        lines.append("")

        # Dimension averages with visual bars
        lines.append("  Dimension Averages:")
        for dim, avg in self.avg_by_dimension.items():
            bar_len = int(avg / 5)
            bar = "█" * bar_len + "░" * (20 - bar_len)
            g, _ = score_to_grade(avg)
            lines.append(f"    {dim:25s} {bar} {avg:5.1f} ({g})")
        lines.append("")

        # Category averages
        if self.avg_by_category:
            lines.append("  Category Averages:")
            for cat, avg in sorted(self.avg_by_category.items()):
                g, _ = score_to_grade(avg)
                lines.append(f"    {cat:25s} {avg:5.1f} ({g})")
            lines.append("")

        # Per-scenario results
        lines.append(
            f"  {'Scenario':<38s} {'Score':>6s} {'Grade':>6s} "
            f"{'Status':>8s} {'Time':>6s}"
        )
        lines.append(f"  {'-' * 70}")
        for r in sorted(self.results, key=lambda x: -x.overall_score):
            icon = {
                "passed": "✅", "failed": "❌", "error": "⚠️"
            }.get(r.status, "⏳")
            lines.append(
                f"  {r.scenario_name:<38s} {r.overall_score:5.1f} "
                f"  {r.grade:>3s}  "
                f"{icon} {r.status:>6s}  {r.execution_time_s:5.1f}s"
            )
        lines.append(f"{'=' * 76}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "runtime": self.runtime,
            "model": self.model,
            "judge_model": self.judge_model,
            "timestamp": self.timestamp,
            "total_duration_s": self.total_duration_s,
            "summary": {
                "total_scenarios": self.total_scenarios,
                "passed": self.passed,
                "failed": self.failed,
                "errors": self.errors,
                "avg_overall": self.avg_overall,
                "overall_grade": self.overall_grade,
                "grade_distribution": self.grade_distribution,
                "avg_by_dimension": self.avg_by_dimension,
                "avg_by_category": self.avg_by_category,
            },
            "results": [r.to_dict() for r in self.results],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)
