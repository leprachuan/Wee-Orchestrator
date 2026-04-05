#!/usr/bin/env python3
"""
Wee Orchestrator — Agentic Flow Evaluator

Runs realistic agentic workflow scenarios against the orchestrator API,
scores responses across 5 dimensions using an LLM judge, and produces
structured reports for comparing runtimes and models.

Usage:
    python3 test.py --runtime copilot --model claude-haiku-4.5
    python3 test.py --dry-run
    python3 test.py --tags core --model claude-sonnet-4.6
    python3 test.py --list
    python3 test.py --compare report1.json report2.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure lib is importable
sys.path.insert(0, str(Path(__file__).parent))

from evallib.runner import EvalRunner
from evallib.scoring import EvalReport, score_to_grade
from scenarios import (
    SCENARIOS,
    get_scenarios,
    list_categories,
    list_scenario_ids,
    list_tags,
)


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_list(args: argparse.Namespace) -> None:
    """List available scenarios with optional filtering."""
    scenarios = get_scenarios(
        tags=args.tags.split(",") if args.tags else None,
        categories=args.categories.split(",") if args.categories else None,
    )

    print(f"\n  {'ID':<35s} {'Cat':<20s} {'Diff':<8s} {'Name'}")
    print(f"  {'-' * 35} {'-' * 20} {'-' * 8} {'-' * 35}")
    for s in scenarios:
        diff = s.get("difficulty", "?")
        print(
            f"  {s['id']:<35s} {s.get('category', ''):<20s} "
            f"{diff:<8s} {s['name']}"
        )
    print(f"\n  Total: {len(scenarios)} scenarios")
    print(f"  Categories: {', '.join(list_categories())}")
    print(f"  Tags: {', '.join(list_tags())}")
    print()


def cmd_run(args: argparse.Namespace) -> None:
    """Run evaluation scenarios."""
    scenarios = get_scenarios(
        tags=args.tags.split(",") if args.tags else None,
        categories=args.categories.split(",") if args.categories else None,
        ids=args.scenarios.split(",") if args.scenarios else None,
    )

    if not scenarios:
        print("No scenarios matched the filter. Use --list to see available.")
        sys.exit(1)

    mode = "DRY RUN" if args.dry_run else "LIVE"
    judge_info = "heuristic" if args.heuristic_only else args.judge_model

    print(f"\n  🧪 Wee Orchestrator Agentic Eval ({mode})")
    print(f"     Runtime: {args.runtime}  |  Model: {args.model}")
    print(f"     Judge: {judge_info}  |  Scenarios: {len(scenarios)}")
    if args.concurrency > 1:
        print(f"     Concurrency: {args.concurrency}")
    print()

    runner = EvalRunner(
        runtime=args.runtime,
        model=args.model,
        judge_model=args.judge_model,
        api_base=args.api_base,
        use_llm_judge=not args.heuristic_only,
        dry_run=args.dry_run,
        concurrency=args.concurrency,
    )

    report = runner.run_all(scenarios)

    # Print summary
    print(report.summary_table())

    # Save JSON report
    if args.output:
        report_path = Path(args.output)
    else:
        report_dir = Path(__file__).parent / "reports"
        report_dir.mkdir(exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        model_slug = args.model.replace("/", "-").replace(" ", "-")
        filename = f"eval_{args.runtime}_{model_slug}_{ts}.json"
        report_path = report_dir / filename

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as f:
        f.write(report.to_json())

    print(f"\n  📄 Report saved: {report_path}")

    # Exit code reflects pass/fail
    if report.errors > 0 or report.avg_overall < 60:
        sys.exit(1)


def cmd_compare(args: argparse.Namespace) -> None:
    """Compare two or more evaluation reports side-by-side."""
    reports = []
    for path in args.reports:
        with open(path) as f:
            reports.append(json.load(f))

    if len(reports) < 2:
        print("Need at least 2 reports to compare.")
        sys.exit(1)

    r1, r2 = reports[0], reports[1]
    s1, s2 = r1["summary"], r2["summary"]

    label1 = f"{r1['runtime']}/{r1['model']}"
    label2 = f"{r2['runtime']}/{r2['model']}"

    print(f"\n{'=' * 76}")
    print(f"  📊 Comparison: {label1} vs {label2}")
    print(f"{'=' * 76}")
    print()

    # Overall
    diff = s2["avg_overall"] - s1["avg_overall"]
    arrow = "▲" if diff > 0 else "▼" if diff < 0 else "="
    g1, _ = score_to_grade(s1["avg_overall"])
    g2, _ = score_to_grade(s2["avg_overall"])
    print(
        f"  Overall: {s1['avg_overall']:.1f} ({g1}) → "
        f"{s2['avg_overall']:.1f} ({g2})  {arrow}{abs(diff):.1f}"
    )
    print()

    # Dimension comparison
    print(
        f"  {'Dimension':<25s} {label1:>12s} {label2:>12s} {'Delta':>10s}"
    )
    print(f"  {'-' * 64}")
    for dim in s1.get("avg_by_dimension", {}):
        v1 = s1["avg_by_dimension"].get(dim, 0)
        v2 = s2["avg_by_dimension"].get(dim, 0)
        d = v2 - v1
        arrow = "▲" if d > 0 else "▼" if d < 0 else "="
        print(
            f"  {dim:<25s} {v1:>11.1f} {v2:>11.1f} "
            f"{arrow}{abs(d):>8.1f}"
        )
    print()

    # Per-scenario comparison
    r1_by_id = {r["scenario_id"]: r for r in r1.get("results", [])}
    r2_by_id = {r["scenario_id"]: r for r in r2.get("results", [])}
    all_ids = sorted(set(r1_by_id) | set(r2_by_id))

    print(
        f"  {'Scenario':<35s} "
        f"{'R1':>6s} {'R2':>6s} {'Delta':>8s}"
    )
    print(f"  {'-' * 58}")
    for sid in all_ids:
        v1 = r1_by_id.get(sid, {}).get("overall_score", 0)
        v2 = r2_by_id.get(sid, {}).get("overall_score", 0)
        d = v2 - v1
        arrow = "▲" if d > 0 else "▼" if d < 0 else "="
        name = (
            r1_by_id.get(sid) or r2_by_id.get(sid, {})
        ).get("scenario_name", sid)
        print(
            f"  {name:<35s} {v1:>5.1f} {v2:>5.1f} "
            f"{arrow}{abs(d):>7.1f}"
        )

    print(f"\n{'=' * 76}")

    # Winner summary
    wins1 = sum(
        1 for sid in all_ids
        if r1_by_id.get(sid, {}).get("overall_score", 0)
        > r2_by_id.get(sid, {}).get("overall_score", 0)
    )
    wins2 = sum(
        1 for sid in all_ids
        if r2_by_id.get(sid, {}).get("overall_score", 0)
        > r1_by_id.get(sid, {}).get("overall_score", 0)
    )
    print(f"\n  Winner by scenario count: {label1} ({wins1}) vs {label2} ({wins2})")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Wee Orchestrator — Agentic Flow Evaluator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 test.py --list                                  # List scenarios
  python3 test.py --dry-run                               # Synthetic scores
  python3 test.py --runtime copilot --model claude-haiku-4.5   # Full eval
  python3 test.py --tags core                             # Core scenarios only
  python3 test.py --compare reports/a.json reports/b.json # Compare runs
  python3 test.py --concurrency 3 --dry-run               # Parallel dry run
        """,
    )

    # Mode flags
    parser.add_argument(
        "--list", action="store_true",
        help="List available scenarios",
    )
    parser.add_argument(
        "--compare", nargs="+", dest="reports", metavar="REPORT",
        help="Compare 2+ evaluation reports (JSON paths)",
    )

    # Run configuration
    parser.add_argument(
        "--runtime", default="copilot",
        help="Runtime to evaluate (copilot, claude, opencode, cursor)",
    )
    parser.add_argument(
        "--model", default="claude-haiku-4.5",
        help="Model to evaluate (default: claude-haiku-4.5)",
    )
    parser.add_argument(
        "--judge-model", default="claude-haiku-4.5",
        help="Model for LLM-as-judge scoring (default: claude-haiku-4.5)",
    )

    # Filters
    parser.add_argument(
        "--tags",
        help="Comma-separated tags to filter scenarios",
    )
    parser.add_argument(
        "--categories",
        help="Comma-separated categories to filter",
    )
    parser.add_argument(
        "--scenarios",
        help="Comma-separated scenario IDs to run",
    )

    # Options
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Generate synthetic results without calling the API",
    )
    parser.add_argument(
        "--heuristic-only", action="store_true",
        help="Use heuristic scoring instead of LLM judge",
    )
    parser.add_argument(
        "--concurrency", type=int, default=1,
        help="Number of scenarios to run in parallel (default: 1)",
    )
    parser.add_argument(
        "--api-base", default="https://127.0.0.1:8000",
        help="Wee Orchestrator API base URL",
    )
    parser.add_argument(
        "--output", "-o",
        help="Custom output path for the JSON report",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    if args.list:
        cmd_list(args)
    elif args.reports:
        cmd_compare(args)
    else:
        cmd_run(args)


if __name__ == "__main__":
    main()
