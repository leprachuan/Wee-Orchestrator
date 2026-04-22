"""Regression tests for Issue #193: Background task API ignores agent dispatch_config.

Tests verify:
1. BackgroundTaskRequest includes a yolo field
2. dispatch_config.runtime/model are used when body fields are not set
3. dispatch_config.permission_mode/yolo map to correct perm_mode
4. dispatch_config.timeout is used when body.timeout is not set
5. Explicit body values override dispatch_config (priority order correct)
6. /background slash command handler applies dispatch_config fallback
"""

import re
import sys
import unittest

sys.path.insert(0, "/opt/n8n-copilot-shim-dev")

SOURCE_PATH = "/opt/n8n-copilot-shim-dev/agent_manager.py"


def _read_source():
    with open(SOURCE_PATH, "r") as f:
        return f.read()


class TestIssue193BackgroundTaskDispatchConfig(unittest.TestCase):
    """Regression tests for Issue #193."""

    @classmethod
    def setUpClass(cls):
        cls.src = _read_source()

    # ── Test 1: BackgroundTaskRequest has yolo field ──────────────────────────

    def test_background_task_request_has_yolo_field(self):
        """BackgroundTaskRequest must include a yolo: Optional[bool] field."""
        # Find the class definition and check for yolo field
        req_class_match = re.search(
            r"class BackgroundTaskRequest\(BaseModel\):(.*?)(?=\n    \w|\n    #|\nclass|\ndef )",
            self.src,
            re.DOTALL,
        )
        self.assertIsNotNone(req_class_match, "BackgroundTaskRequest class not found")
        class_body = req_class_match.group(1)
        self.assertIn(
            "yolo",
            class_body,
            "BackgroundTaskRequest must have a 'yolo' field",
        )

    def test_yolo_field_is_optional_bool(self):
        """BackgroundTaskRequest.yolo must be Optional[bool] with None default."""
        req_class_match = re.search(
            r"class BackgroundTaskRequest\(BaseModel\):(.*?)@field_validator",
            self.src,
            re.DOTALL,
        )
        self.assertIsNotNone(req_class_match)
        class_body = req_class_match.group(1)
        self.assertIn(
            "yolo: Optional[bool] = None",
            class_body,
            "yolo field must be 'Optional[bool] = None'",
        )

    # ── Test 2: dispatch_config lookup exists in create_background_task ───────

    def test_dispatch_config_lookup_in_create_bg_task(self):
        """create_background_task must load agent dispatch_config from AGENTS."""
        self.assertIn(
            '_dispatch_config = session_mgr.AGENTS.get(agent, {}).get("dispatch_config", {})',
            self.src,
            "dispatch_config must be loaded from session_mgr.AGENTS in create_background_task",
        )

    def test_runtime_falls_back_to_dispatch_config(self):
        """runtime resolution must consult dispatch_config before session defaults."""
        # The fix pattern: body.runtime or _dispatch_config.get("runtime") or defaults...
        self.assertRegex(
            self.src,
            r'runtime\s*=\s*body\.runtime\s+or\s+_dispatch_config\.get\("runtime"\)',
            "runtime must use dispatch_config as 2nd-tier fallback",
        )

    def test_model_falls_back_to_dispatch_config(self):
        """model resolution must consult dispatch_config before session defaults."""
        self.assertRegex(
            self.src,
            r'model\s*=\s*body\.model\s+or\s+_dispatch_config\.get\("model"\)',
            "model must use dispatch_config as 2nd-tier fallback",
        )

    def test_timeout_falls_back_to_dispatch_config(self):
        """bg_timeout must use dispatch_config.timeout before global default."""
        self.assertIn(
            '_dispatch_config.get("timeout")',
            self.src,
            "bg_timeout must fall back to dispatch_config.timeout",
        )

    # ── Test 3: perm_mode resolution logic ────────────────────────────────────

    def test_perm_mode_resolved_from_dispatch_config(self):
        """perm_mode resolution must include dispatch_config fallback."""
        self.assertIn(
            '_dc_perm',
            self.src,
            "perm_mode resolution must use _dc_perm from dispatch_config",
        )
        self.assertIn(
            '_dispatch_config.get("yolo")',
            self.src,
            "dispatch_config.yolo must be checked when resolving perm_mode",
        )
        self.assertIn(
            '_dispatch_config.get("permission_mode", "")',
            self.src,
            "dispatch_config.permission_mode must be checked when resolving perm_mode",
        )

    def test_body_yolo_true_elevates_perm_mode(self):
        """body.yolo=True must resolve to perm_mode='elevated'."""
        self.assertIn(
            '"elevated" if body.yolo else None',
            self.src,
            "body.yolo=True must produce 'elevated' perm_mode",
        )

    def test_perm_mode_computed_before_queued_response(self):
        """perm_mode must be resolved before the queued early-return."""
        # Search within create_background_task function scope only
        fn_start = self.src.find("async def create_background_task(")
        self.assertGreater(fn_start, 0, "create_background_task function not found")
        fn_section = self.src[fn_start:fn_start + 10000]
        perm_mode_idx = fn_section.find("# Resolve permission mode early")
        create_task_idx = fn_section.find("bg_task_mgr.create_task_checked")
        self.assertGreater(perm_mode_idx, 0, "perm_mode early resolution comment not found in create_background_task")
        self.assertGreater(create_task_idx, 0, "create_task_checked call not found in create_background_task")
        self.assertLess(
            perm_mode_idx,
            create_task_idx,
            "perm_mode must be resolved BEFORE create_task_checked to be available in queued response",
        )

    def test_queued_response_uses_perm_mode_variable(self):
        """Queued response must use the resolved perm_mode variable (not body.permission_mode)."""
        # The wrong pattern that this fixes:
        self.assertNotIn(
            '"permission_mode": body.permission_mode or "restricted"',
            self.src,
            "Queued response must NOT hardcode 'body.permission_mode or restricted'",
        )

    # ── Test 4: /background slash command uses dispatch_config ────────────────

    def test_slash_bg_command_uses_dispatch_config(self):
        """/background slash command must apply dispatch_config fallback."""
        self.assertIn(
            '_bg_dispatch_cfg = self.AGENTS.get(bg_agent, {}).get("dispatch_config", {})',
            self.src,
            "/background handler must look up dispatch_config for the target agent",
        )

    def test_slash_bg_runtime_fallback_chain(self):
        """In /background handler, runtime must use dispatch_config before session."""
        self.assertIn(
            'bg_runtime = bg_runtime or _bg_dispatch_cfg.get("runtime")',
            self.src,
            "/background handler must apply dispatch_config.runtime as fallback",
        )

    def test_slash_bg_model_fallback_chain(self):
        """In /background handler, model must use dispatch_config before session."""
        self.assertIn(
            'bg_model = bg_model or _bg_dispatch_cfg.get("model")',
            self.src,
            "/background handler must apply dispatch_config.model as fallback",
        )

    # ── Test 5: Priority order verification (logic unit tests) ────────────────

    def _resolve(self, body_runtime, body_model, body_perm, body_yolo,
                 dispatch_cfg, session_runtime="copilot", session_model="gpt-5-mini"):
        """Simulate the resolution logic from create_background_task."""
        runtime = body_runtime or dispatch_cfg.get("runtime") or session_runtime
        model = body_model or dispatch_cfg.get("model") or session_model
        _dc_perm = (
            "elevated" if dispatch_cfg.get("yolo")
            else dispatch_cfg.get("permission_mode", "")
        )
        perm_mode = (
            body_perm
            or ("elevated" if body_yolo else None)
            or _dc_perm
            or "restricted"
        )
        if perm_mode not in ("elevated", "restricted", "sandboxed"):
            perm_mode = "restricted"
        return runtime, model, perm_mode

    def test_body_overrides_all(self):
        """Explicit body values must override dispatch_config and session defaults."""
        rt, mdl, pm = self._resolve(
            "my-runtime", "my-model", "sandboxed", None,
            {"runtime": "openai", "model": "gpt-5.4", "permission_mode": "elevated"},
            "session-runtime", "session-model",
        )
        self.assertEqual(rt, "my-runtime")
        self.assertEqual(mdl, "my-model")
        self.assertEqual(pm, "sandboxed")

    def test_dispatch_config_overrides_session_defaults(self):
        """dispatch_config must override session defaults when body is empty."""
        rt, mdl, pm = self._resolve(
            None, None, None, None,
            {"runtime": "openai", "model": "gpt-5.4", "permission_mode": "elevated"},
            "session-runtime", "session-model",
        )
        self.assertEqual(rt, "openai")
        self.assertEqual(mdl, "gpt-5.4")
        self.assertEqual(pm, "elevated")

    def test_wee_qa_dispatch_config_scenario(self):
        """Simulate the exact wee-qa dispatch scenario from the issue report."""
        # wee-qa has: runtime="openai", model="gpt-5.4-mini", permission_mode (via yolo=True)
        rt, mdl, pm = self._resolve(
            None, None, None, None,
            {"runtime": "openai", "model": "gpt-5.4-mini", "yolo": True},
            "copilot", "gpt-5-mini",
        )
        self.assertEqual(rt, "openai", "wee-qa should use openai runtime from dispatch_config")
        self.assertEqual(mdl, "gpt-5.4-mini", "wee-qa should use gpt-5.4-mini from dispatch_config")
        self.assertEqual(pm, "elevated", "wee-qa yolo=True should result in elevated perm_mode")

    def test_session_defaults_used_when_dispatch_config_empty(self):
        """Session defaults apply when dispatch_config is empty."""
        rt, mdl, pm = self._resolve(
            None, None, None, None,
            {},
            "copilot", "claude-haiku",
        )
        self.assertEqual(rt, "copilot")
        self.assertEqual(mdl, "claude-haiku")
        self.assertEqual(pm, "restricted")


if __name__ == "__main__":
    unittest.main()
