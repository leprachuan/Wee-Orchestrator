"""Regression tests for GitHub Issue #200.

- BackgroundTaskManager._trim_tool_call missing caused task crash on tool-call logging
- gpt-5.4-mini missing from CODEX_MODELS static configuration
"""

import os
import sys
import types
import unittest

sys.path.insert(0, "/opt/n8n-copilot-shim-dev")

# Minimal stubs so agent_manager imports cleanly
for mod_name in ["anthropic", "google.generativeai", "google", "openai"]:
    parts = mod_name.split(".")
    parent = None
    for i, part in enumerate(parts):
        full = ".".join(parts[: i + 1])
        if full not in sys.modules:
            m = types.ModuleType(full)
            if parent is not None:
                setattr(parent, part, m)
            sys.modules[full] = m
        parent = sys.modules[full]


class TestTrimToolCall(unittest.TestCase):
    """_trim_tool_call must exist and truncate oversized string fields."""

    def _make_manager(self):
        # Import lazily inside test so the stub environment is in place
        from agent_manager import BackgroundTaskManager  # noqa: PLC0415

        return BackgroundTaskManager()

    def test_trim_tool_call_method_exists(self):
        """BackgroundTaskManager must have a _trim_tool_call method."""
        from agent_manager import BackgroundTaskManager  # noqa: PLC0415

        self.assertTrue(
            hasattr(BackgroundTaskManager, "_trim_tool_call"),
            "_trim_tool_call method is missing from BackgroundTaskManager",
        )

    def test_trim_tool_call_truncates_long_strings(self):
        """Long string values must be truncated to MAX_TOOL_FIELD_CHARS."""
        mgr = self._make_manager()
        long_value = "x" * (mgr.MAX_TOOL_FIELD_CHARS + 500)
        tool_call = {"id": "tc1", "name": "shell", "input": long_value}
        result = mgr._trim_tool_call(tool_call)
        self.assertLessEqual(
            len(result["input"]),
            mgr.MAX_TOOL_FIELD_CHARS + 50,  # allow for truncation suffix
        )
        self.assertIn("truncated", result["input"])

    def test_trim_tool_call_preserves_short_strings(self):
        """Short string values must pass through unchanged."""
        mgr = self._make_manager()
        tool_call = {"id": "tc2", "name": "shell", "input": "echo hello"}
        result = mgr._trim_tool_call(tool_call)
        self.assertEqual(result["input"], "echo hello")

    def test_trim_tool_call_preserves_non_string_values(self):
        """Non-string values (int, bool, None) must pass through unchanged."""
        mgr = self._make_manager()
        tool_call = {"id": "tc3", "count": 42, "active": True, "meta": None}
        result = mgr._trim_tool_call(tool_call)
        self.assertEqual(result["count"], 42)
        self.assertEqual(result["active"], True)
        self.assertIsNone(result["meta"])

    def test_append_tool_call_does_not_crash(self):
        """append_tool_call must not crash.

        Regression: AttributeError on _trim_tool_call.
        """
        import json
        import tempfile

        mgr = self._make_manager()
        # Provide a temporary task store
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump(
                [{"task_id": "bg_test001", "tool_calls": [], "output_lines": []}],
                f,
            )
            tmp_path = f.name

        mgr._path = tmp_path
        mgr._tasks_cache = None  # force load from disk

        try:
            # This must not raise AttributeError
            mgr.append_tool_call(
                "bg_test001", {"id": "tc_a", "name": "shell", "input": "ls"}
            )
            task = mgr.get_task("bg_test001")
            self.assertEqual(len(task["tool_calls"]), 1)
            self.assertEqual(task["tool_calls"][0]["name"], "shell")
        finally:
            os.unlink(tmp_path)

    def test_update_tool_call_does_not_crash(self):
        """update_tool_call must not crash.

        Regression: AttributeError on _trim_tool_call.
        """
        import json
        import tempfile

        mgr = self._make_manager()
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump(
                [
                    {
                        "task_id": "bg_test002",
                        "tool_calls": [
                            {"id": "tc_b", "name": "shell", "status": "running"}
                        ],
                        "output_lines": [],
                    }
                ],
                f,
            )
            tmp_path = f.name

        mgr._path = tmp_path
        mgr._tasks_cache = None

        try:
            mgr.update_tool_call("bg_test002", "tc_b", status="done", output="ok")
            task = mgr.get_task("bg_test002")
            self.assertEqual(task["tool_calls"][0]["status"], "done")
        finally:
            os.unlink(tmp_path)


class TestCodexModelsGpt54Mini(unittest.TestCase):
    """gpt-5.4-mini must be present in the static CODEX_MODELS list."""

    def test_gpt_54_mini_in_codex_models(self):
        """CODEX_MODELS must include gpt-5.4-mini."""
        from agent_manager import SessionManager  # noqa: PLC0415

        all_ids = []
        for _category, entries in SessionManager.CODEX_MODELS.items():
            for entry in entries:
                model_id = entry[0]
                all_ids.append(model_id)

        self.assertIn(
            "gpt-5.4-mini",
            all_ids,
            "gpt-5.4-mini is missing from SessionManager.CODEX_MODELS",
        )

    def test_gpt_54_mini_in_fetch_codex_models(self):
        """fetch_codex_models() must include gpt-5.4-mini (no env override)."""
        from agent_manager import SessionManager  # noqa: PLC0415

        orig = os.environ.pop("CODEX_MODELS_JSON", None)
        try:
            mgr = SessionManager.__new__(SessionManager)
            result = mgr.fetch_codex_models()
            all_ids = []
            for _cat, models in result.items():
                for m in models:
                    all_ids.append(m["id"] if isinstance(m, dict) else m)
            self.assertIn(
                "gpt-5.4-mini",
                all_ids,
                "gpt-5.4-mini missing from fetch_codex_models() output",
            )
        finally:
            if orig is not None:
                os.environ["CODEX_MODELS_JSON"] = orig


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestCodexModelsEndpoint(unittest.TestCase):
    """gpt-5.4-mini must appear in /api/v1/models endpoint for codex runtime."""

    def test_models_endpoint_codex_includes_gpt_54_mini(self):
        """Endpoint logic must return gpt-5.4-mini; _group NameError must not occur."""
        from agent_manager import SessionManager  # noqa: PLC0415

        mgr = SessionManager.__new__(SessionManager)
        mgr._env_codex_models = None
        raw = mgr.fetch_codex_models()

        # Reproduce the /api/v1/models endpoint loop (post e3d0009 cherry-pick)
        models = []
        for group_name, model_ids in raw.items():
            for model_id in model_ids:
                if isinstance(model_id, tuple):
                    model_id = model_id[0]
                models.append({"id": model_id, "label": model_id, "group": group_name})

        all_ids = [m["id"] for m in models]
        self.assertIn(
            "gpt-5.4-mini",
            all_ids,
            "gpt-5.4-mini missing from codex models endpoint simulation",
        )
        for m in models:
            self.assertIsInstance(
                m["group"], str, f"group must be a string, got: {m['group']!r}"
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
