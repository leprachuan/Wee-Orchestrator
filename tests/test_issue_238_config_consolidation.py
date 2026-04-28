"""Regression tests for issue #238: Consolidate agent config into single source of truth.

Verifies:
1. get_agent_dispatch_config reads only from agents.json (no config/agents.json dependency)
2. Flat primary_runtime/primary_model fields are used correctly
3. permission_mode defaults correctly per agent (elevated for wee-dev/wee-qa, restricted for others)
4. yolo defaults correctly per agent
5. Explicit permission_mode/yolo fields in agents.json override defaults
6. Legacy dispatch_config block still works for backward compat
7. Missing agent raises RuntimeError without consulting config/agents.json
8. AGENTS_DISPATCH_CONFIG_PATH constant is removed from dispatch script
"""

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, "/opt/n8n-copilot-shim-dev")


def _make_dispatch_func(agents_json_content: dict):
    """Helper: create a get_agent_dispatch_config function bound to a temp agents.json."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "dispatch_mod",
        "/opt/n8n-copilot-shim-dev/scripts/dispatch_wee_dev_work_queue.py",
    )
    mod = importlib.util.module_from_spec(spec)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
        json.dump(agents_json_content, tmp)
        tmp_path = tmp.name

    mod.AGENTS_CONFIG_PATH = Path(tmp_path)

    try:
        spec.loader.exec_module(mod)
    except (SystemExit, Exception):
        pass

    mod.AGENTS_CONFIG_PATH = Path(tmp_path)
    return mod.get_agent_dispatch_config, tmp_path


class TestIssue238SingleSourceOfTruth(unittest.TestCase):
    """Issue #238: agents.json is the single source of truth for dispatch config."""

    def _make_agents(self, extra_agents=None):
        """Build a minimal agents.json-style dict."""
        agents = [
            {
                "name": "wee-dev",
                "path": "/opt/wee-dev",
                "primary_runtime": "claude",
                "primary_model": "sonnet",
                "fallback_runtime": "copilot",
                "fallback_model": "auto",
                "permission_mode": "elevated",
                "yolo": True,
            },
            {
                "name": "wee-qa",
                "path": "/opt/wee-qa",
                "primary_runtime": "codex",
                "primary_model": "gpt-5.4-mini",
                "fallback_runtime": "copilot",
                "fallback_model": "auto",
                "permission_mode": "elevated",
                "yolo": True,
            },
            {
                "name": "orchestrator",
                "path": "/opt/",
                "primary_runtime": "claude",
                "primary_model": "haiku",
                "fallback_runtime": "copilot",
                "fallback_model": "auto",
            },
        ]
        if extra_agents:
            agents.extend(extra_agents)
        return {"agents": agents}

    def test_wee_dev_uses_flat_fields_from_agents_json(self):
        """wee-dev config is read from agents.json flat fields without needing config/agents.json."""
        get_cfg, tmp = _make_dispatch_func(self._make_agents())
        try:
            cfg = get_cfg("wee-dev")
            self.assertEqual(cfg["runtime"], "claude")
            self.assertEqual(cfg["model"], "sonnet")
            self.assertEqual(cfg["fallback_runtime"], "copilot")
            self.assertEqual(cfg["permission_mode"], "elevated")
            self.assertTrue(cfg["yolo"])
        finally:
            Path(tmp).unlink(missing_ok=True)

    def test_wee_qa_uses_flat_fields_from_agents_json(self):
        """wee-qa config is read from agents.json flat fields."""
        get_cfg, tmp = _make_dispatch_func(self._make_agents())
        try:
            cfg = get_cfg("wee-qa")
            self.assertEqual(cfg["runtime"], "codex")
            self.assertEqual(cfg["model"], "gpt-5.4-mini")
            self.assertEqual(cfg["permission_mode"], "elevated")
            self.assertTrue(cfg["yolo"])
        finally:
            Path(tmp).unlink(missing_ok=True)

    def test_regular_agent_gets_restricted_permission_mode(self):
        """Non wee-dev/wee-qa agents default to restricted permission_mode."""
        get_cfg, tmp = _make_dispatch_func(self._make_agents())
        try:
            cfg = get_cfg("orchestrator")
            self.assertEqual(cfg["permission_mode"], "restricted")
            self.assertFalse(cfg["yolo"])
        finally:
            Path(tmp).unlink(missing_ok=True)

    def test_explicit_permission_mode_overrides_default(self):
        """Explicit permission_mode field overrides the agent-name-based default."""
        agents = self._make_agents(
            extra_agents=[
                {
                    "name": "custom-agent",
                    "path": "/opt/custom",
                    "primary_runtime": "claude",
                    "primary_model": "haiku",
                    "permission_mode": "elevated",
                }
            ]
        )
        get_cfg, tmp = _make_dispatch_func(agents)
        try:
            cfg = get_cfg("custom-agent")
            self.assertEqual(cfg["permission_mode"], "elevated")
        finally:
            Path(tmp).unlink(missing_ok=True)

    def test_explicit_yolo_false_overrides_default_for_wee_dev(self):
        """Explicit yolo=False in agents.json overrides the wee-dev default of True."""
        agents = {
            "agents": [
                {
                    "name": "wee-dev",
                    "path": "/opt/wee-dev",
                    "primary_runtime": "claude",
                    "primary_model": "sonnet",
                    "yolo": False,
                }
            ]
        }
        get_cfg, tmp = _make_dispatch_func(agents)
        try:
            cfg = get_cfg("wee-dev")
            self.assertFalse(cfg["yolo"])
        finally:
            Path(tmp).unlink(missing_ok=True)

    def test_legacy_dispatch_config_block_still_works(self):
        """Legacy dispatch_config block in agents.json is still respected."""
        agents = {
            "agents": [
                {
                    "name": "wee-dev",
                    "path": "/opt/wee-dev",
                    "dispatch_config": {
                        "runtime": "copilot",
                        "model": "claude-opus-4.6",
                        "permission_mode": "elevated",
                        "yolo": True,
                        "timeout": 1800,
                    },
                    "fallback_runtime": "codex",
                    "fallback_model": "gpt-5.4-mini",
                }
            ]
        }
        get_cfg, tmp = _make_dispatch_func(agents)
        try:
            cfg = get_cfg("wee-dev")
            self.assertEqual(cfg["runtime"], "copilot")
            self.assertEqual(cfg["model"], "claude-opus-4.6")
            self.assertEqual(cfg["timeout"], 1800)
            # fallback fields back-filled from top-level
            self.assertEqual(cfg["fallback_runtime"], "codex")
        finally:
            Path(tmp).unlink(missing_ok=True)

    def test_missing_agent_raises_runtime_error(self):
        """Unknown agent name raises RuntimeError immediately (no fallback to other files)."""
        get_cfg, tmp = _make_dispatch_func(self._make_agents())
        try:
            with self.assertRaises(RuntimeError) as ctx:
                get_cfg("nonexistent-agent")
            self.assertIn("nonexistent-agent", str(ctx.exception))
        finally:
            Path(tmp).unlink(missing_ok=True)

    def test_no_agents_dispatch_config_path_constant(self):
        """AGENTS_DISPATCH_CONFIG_PATH constant must not exist in the dispatch script.

        This is the core of issue #238: the old overlay path was removed so
        config/agents.json can never be silently consulted.
        """
        script_path = Path(
            "/opt/n8n-copilot-shim-dev/scripts/dispatch_wee_dev_work_queue.py"
        )
        self.assertTrue(script_path.exists(), "dispatch script missing")
        content = script_path.read_text()
        self.assertNotIn(
            "AGENTS_DISPATCH_CONFIG_PATH",
            content,
            "AGENTS_DISPATCH_CONFIG_PATH still present in dispatch script — "
            "config/agents.json overlay not removed (issue #238)",
        )

    def test_no_path_instantiation_for_config_agents_json(self):
        """Path('config/agents.json') must not be instantiated anywhere in the dispatch script.

        Checks actual AST to distinguish docstring mentions from live code.
        """
        script_path = Path(
            "/opt/n8n-copilot-shim-dev/scripts/dispatch_wee_dev_work_queue.py"
        )
        content = script_path.read_text()
        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            self.fail(f"dispatch script has syntax errors: {e}")

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id == "Path":
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            self.assertNotIn(
                                "config/agents.json",
                                arg.value,
                                f"Path('config/agents.json') still in dispatch script "
                                f"(issue #238 consolidation): {arg.value!r}",
                            )

    def test_wee_dev_has_permission_mode_and_yolo_in_agents_json(self):
        """wee-dev entry in agents.json has explicit permission_mode and yolo fields."""
        agents_path = Path("/opt/n8n-copilot-shim-dev/agents.json")
        self.assertTrue(agents_path.exists(), "agents.json missing")
        config = json.loads(agents_path.read_text())
        wee_dev = next(
            (a for a in config.get("agents", []) if a.get("name") == "wee-dev"),
            None,
        )
        self.assertIsNotNone(wee_dev, "wee-dev agent not found in agents.json")
        self.assertIn(
            "permission_mode",
            wee_dev,
            "wee-dev missing permission_mode in agents.json (issue #238)",
        )
        self.assertEqual(wee_dev["permission_mode"], "elevated")
        self.assertIn(
            "yolo", wee_dev, "wee-dev missing yolo in agents.json (issue #238)"
        )
        self.assertTrue(wee_dev["yolo"])

    def test_no_duplicate_agents_in_agents_json(self):
        """agents.json must not have duplicate agent name entries."""
        agents_path = Path("/opt/n8n-copilot-shim-dev/agents.json")
        config = json.loads(agents_path.read_text())
        names = [a.get("name") for a in config.get("agents", [])]
        duplicates = [n for n in set(names) if names.count(n) > 1]
        self.assertEqual(
            duplicates,
            [],
            f"Duplicate agent entries in agents.json: {duplicates}",
        )

    def test_scheduler_job_points_to_scripts_version(self):
        """Scheduler job for wee-dev work queue must use the scripts/ version, not /opt/bin/."""
        jobs_path = Path("/opt/.task-scheduler-dev/jobs.json")
        self.assertTrue(jobs_path.exists(), "scheduler jobs file missing")
        jobs = json.loads(jobs_path.read_text())
        wee_dev_jobs = [
            j
            for j in jobs.get("jobs", [])
            if "dispatch_wee_dev" in j.get("task", "")
            or "work-queue" in j.get("id", "")
        ]
        self.assertTrue(
            len(wee_dev_jobs) > 0, "No wee-dev work queue scheduler job found"
        )
        for job in wee_dev_jobs:
            self.assertIn(
                "scripts/dispatch_wee_dev",
                job.get("task", ""),
                f"Scheduler job {job['id']} still points to old /opt/bin/ path. "
                "Update it to use /opt/n8n-copilot-shim/scripts/ (issue #238).",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
