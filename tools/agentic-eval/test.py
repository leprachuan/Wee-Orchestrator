#!/usr/bin/env python3
"""
Wee Orchestrator — Agentic Flow Evaluator

Runs realistic agentic workflow scenarios against the orchestrator API,
scores responses across 5 dimensions using an LLM judge, and produces
structured reports for comparing runtimes and models.

Usage:
    python3 test.py --runtime copilot --model claude-haiku-4.5
    python3 test.py --dry-run                          # synthetic scores
    python3 test.py --tags core --model claude-sonnet-4.6
    python3 test.py --list                             # list all scenarios
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
from evallib.scoring import EvalReport
from scenarios import SCENARIOS, get_scenarios, list_scenario_ids


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_list(args: argparse.Namespace) -> None:
    """List available scenarios."""
    scenarios = get_scenarios(
        tags=args.tags.split(",") if args.tags else None,
        categories=args.categories.split(",") if args.categories else None,
    )
    print(f"\n{'ID':<35s} {'Category':<20s} {'Name'}")
    print(f"{'-' * 35} {'-' * 20} {'-' * 40}")
    for s in scenarios:
        print(f"{s['id']:<35s} {s.get('category', ''):<20s} {s['name']}")
    print(f"\nTotal: {len(scenarios)} scenarios")


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

    print(f"\n🧪 Wee Orchestrator Agentic Eval")
    print(f"   Runtime: {args.runtime}  |  Model: {args.model}")
    print(f"   Judge: {args.judge_model}  |  Scenarios: {len(scenarios)}")
    print(f"   Dry run: {args.dry_run}")
    print()

    runner = EvalRunner(
        runtime=args.runtime,
        model=args.model,
        judge_model=args.judge_model,
        api_base=args.api_base,
        use_llm_judge=not args.heuristic_only,
        dry_run=args.dry_run,
    )

    report = runner.run_all(scenarios)

    # Print summary
    print(report.summary_table())

    # Save JSON report
    report_dir = Path(__file__).parent / "reports"
    report_dir.mkdir(exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"eval_{args.runtime}_{args.model}_{ts}.json"
    report_path = report_dir / filename

    with open(report_path, "w") as f:
        f.write(report.to_json())

    print(f"\n📄 Report saved: {report_path}")


def cmd_compare(args: argparse.Namespace) -> None:
    """Compare two evaluation reports."""
    reports = []
    for path in args.reports:
        with open(path) as f:
            reports.append(json.load(f))

    if len(reports) < 2:
        print("Need at least 2 reports to compare.")
        sys.exit(1)

    r1, r2 = reports[0], reports[1]
    s1, s2 = r1["summary"], r2["summary"]

    print(f"\n{'=' * 72}")
    print(f"  Comparison: {r1['runtime']}/{r1['model']} vs {r2['runtime']}/{r2['model']}")
    print(f"{'=' * 72}")
    print()

    # Overall
    diff = s2["avg_overall"] - s1["avg_overall"]
    arrow = "▲" if diff > 0 else "▼" if diff < 0 else "="
    print(f"  Overall: {s1['avg_overall']:.1f} → {s2['avg_overall']:.1f} ({arrow}{abs(diff):.1f})")
    print()

    # Dimension comparison
    print(f"  {'Dimension':<25s} {'Report 1':>10s} {'Report 2':>10s} {'Delta':>10s}")
    print(f"  {'-' * 60}")
    for dim in s1.get("avg_by_dimension", {}):
        v1 = s1["avg_by_dimension"].get(dim, 0)
        v2 = s2["avg_by_dimension"].get(dim, 0)
        d = v2 - v1
        arrow = "▲" if d > 0 else "▼" if d < 0 else "="
        print(f"  {dim:<25s} {v1:>9.1f} {v2:>9.1f} {arrow}{abs(d):>8.1f}")
    print()

    # Per-scenario comparison
    r1_by_id = {r["scenario_id"]: r for r in r1.get("results", [])}
    r2_by_id = {r["scenario_id"]: r for r in r2.get("results", [])}
    all_ids = sorted(set(r1_by_id) | set(r2_by_id))

    print(f"  {'Scenario':<35s} {'R1':>6s} {'R2':>6s} {'Delta':>8s}")
    print(f"  {'-' * 58}")
    for sid in all_ids:
        v1 = r1_by_id.get(sid, {}).get("overall_score", 0)
        v2 = r2_by_id.get(sid, {}).get("overall_score", 0)
        d = v2 - v1
        arrow = "▲" if d > 0 else "▼" if d < 0 else "="
        name = (r1_by_id.get(sid) or r2_by_id.get(sid, {})).get(
            "scenario_name", sid
        )
        print(f"  {name:<35s} {v1:>5.1f} {v2:>5.1f} {arrow}{abs(d):>7.1f}")

    print(f"\n{'=' * 72}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Wee Orchestrator — Agentic Flow Evaluator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 test.py --list                                 # List all scenarios
  python3 test.py --dry-run                              # Quick test with synthetic scores
  python3 test.py --runtime copilot --model claude-haiku-4.5  # Full eval
  python3 test.py --tags core                            # Only core scenarios
  python3 test.py --compare reports/a.json reports/b.json     # Compare runs
        """,
    )

    # Mode flags
    parser.add_argument(
        "--list", action="store_true", help="List available scenarios"
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
        help="Model to evaluate (claude-haiku-4.5, claude-sonnet-4.6, claude-opus-4.6, etc.)",
    )
    parser.add_argument(
        "--judge-model", default="claude-haiku-4.5",
        help="Model used for LLM-as-judge scoring (default: claude-haiku-4.5)",
    )

    # Filters
    parser.add_argument(
        "--tags", help="Comma-separated tags to filter scenarios",
    )
    parser.add_argument(
        "--categories", help="Comma-separated categories to filter",
    )
    parser.add_argument(
        "--scenarios", help="Comma-separated scenario IDs to run",
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
        "--api-base", default="https://127.0.0.1:8000",
        help="Wee Orchestrator API base URL",
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
