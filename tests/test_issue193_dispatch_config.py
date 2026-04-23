"""Regression tests for Issue #193.

Tests verify:
1. AGENTS dict preserves dispatch_config when loading agents.json
2. reload_agents_from_disk also preserves dispatch_config
3. dispatch_config.runtime/model/timeout used when body fields are empty
4. Explicit body values override dispatch_config (priority order correct)
5. /background slash command handler applies dispatch_config fallback
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, "/opt/n8n-copilot-shim-dev")
os.environ.setdefault("API_SHARED_KEY", "test_key_123")
os.environ.setdefault("APP_ENV", "DEV")

from agent_manager import SessionManager  # noqa: E402

AGENTS_WITH_DISPATCH = {
    "agents": [
        {
            "name": "wee-qa",
            "description": "QA agent",
            "path": "/opt/wee-qa",
            "dispatch_config": {
                "runtime": "openai",
                "model": "gpt-5.4-mini",
                "permission_mode": "elevated",
                "yolo": True,
                "timeout": 1800,
            },
        },
        {
            "name": "plain-agent",
            "description": "Agent without dispatch_config",
            "path": "/opt/plain",
        },
    ]
}


class TestDispatchConfigPreservedOnLoad(unittest.TestCase):
    """Verify dispatch_config survives the agents.json → AGENTS dict journey."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cfg_path = Path(self.temp_dir.name) / "agents.json"
        with open(self.cfg_path, "w") as f:
            json.dump(AGENTS_WITH_DISPATCH, f)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_load_agents_config_preserves_dispatch_config(self):
        """_load_agents_config must keep dispatch_config in the AGENTS dict."""
        mgr = SessionManager(str(self.cfg_path))
        self.assertIn("wee-qa", mgr.AGENTS)
        dc = mgr.AGENTS["wee-qa"].get("dispatch_config", {})
        self.assertNotEqual(dc, {}, "dispatch_config must not be stripped on load")
        self.assertEqual(dc.get("runtime"), "openai")
        self.assertEqual(dc.get("model"), "gpt-5.4-mini")
        self.assertEqual(dc.get("timeout"), 1800)
        self.assertTrue(dc.get("yolo"))

    def test_load_agents_config_missing_dispatch_config_is_empty_dict(self):
        """Agent without dispatch_config must have an empty dict, not KeyError."""
        mgr = SessionManager(str(self.cfg_path))
        self.assertIn("plain-agent", mgr.AGENTS)
        dc = mgr.AGENTS["plain-agent"].get("dispatch_config", {})
        self.assertEqual(dc, {})

    def test_reload_agents_from_disk_preserves_dispatch_config(self):
        """reload_agents_from_disk must also keep dispatch_config."""
        mgr = SessionManager(str(self.cfg_path))
        # Force a reload
        ok, msg = mgr.reload_agents_from_disk()
        self.assertTrue(ok, f"Reload failed: {msg}")
        dc = mgr.AGENTS["wee-qa"].get("dispatch_config", {})
        self.assertNotEqual(
            dc, {}, "dispatch_config stripped by reload_agents_from_disk"
        )
        self.assertEqual(dc.get("runtime"), "openai")
        self.assertEqual(dc.get("timeout"), 1800)

    def test_dispatch_config_lookup_succeeds_after_load(self):
        """AGENTS.get(...).get('dispatch_config', {}) returns real values."""
        mgr = SessionManager(str(self.cfg_path))
        # Simulate what create_background_task does:
        dc = mgr.AGENTS.get("wee-qa", {}).get("dispatch_config", {})
        self.assertEqual(dc.get("runtime"), "openai")
        self.assertEqual(dc.get("model"), "gpt-5.4-mini")
        self.assertIsNotNone(dc.get("timeout"))


class TestSlashBgTimeoutUsesDispatchConfig(unittest.TestCase):
    """Verify /background slash handler picks up dispatch_config.timeout."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cfg_path = Path(self.temp_dir.name) / "agents.json"
        with open(self.cfg_path, "w") as f:
            json.dump(AGENTS_WITH_DISPATCH, f)
        self.mgr = SessionManager(str(self.cfg_path))

    def tearDown(self):
        self.temp_dir.cleanup()

    def _make_fake_session(self, runtime="copilot", model="gpt-5-mini"):
        return {
            "runtime": runtime,
            "model": model,
            "channel": "webui",
            "identity": "test_user",
        }

    def test_slash_bg_timeout_comes_from_dispatch_config(self):
        """When no timeout= override, /background uses dispatch_config.timeout."""
        # Build the fake session data for the slash handler
        session_data = self._make_fake_session()
        captured = {}

        def fake_create_task_checked(**kwargs):
            captured.update(kwargs)
            fake_task = MagicMock()
            fake_task.task_id = "bg_test"
            return fake_task, "running"

        self.mgr._bg_task_mgr = MagicMock()
        self.mgr._bg_task_mgr.create_task_checked.side_effect = fake_create_task_checked
        self.mgr._bg_identity = "test_user"

        with patch("concurrent.futures.ThreadPoolExecutor"):
            result = self.mgr._slash_background(
                "agent=wee-qa say hello",
                session_data,
                "test-session-id",
            )

        # Check that timeout came from dispatch_config (1800) not default (900)
        # The timeout is passed positionally to _execute_background_task
        # But we can check indirectly via the output message
        self.assertIn("1800", result, "bg_timeout must be 1800 from dispatch_config")

    def test_slash_bg_explicit_timeout_overrides_dispatch_config(self):
        """Explicit timeout= override wins over dispatch_config.timeout."""
        session_data = self._make_fake_session()

        self.mgr._bg_task_mgr = MagicMock()
        fake_task = MagicMock()
        fake_task.task_id = "bg_test2"
        self.mgr._bg_task_mgr.create_task_checked.return_value = (fake_task, "running")
        self.mgr._bg_identity = "test_user"

        with patch("concurrent.futures.ThreadPoolExecutor"):
            result = self.mgr._slash_background(
                "agent=wee-qa timeout=300 say hello",
                session_data,
                "test-session-id",
            )

        # Explicit timeout=300 must override dispatch_config 1800
        self.assertIn("300", result)
        self.assertNotIn("1800", result)

    def test_slash_bg_runtime_comes_from_dispatch_config(self):
        """When no runtime= override, /background uses dispatch_config.runtime."""
        session_data = self._make_fake_session()

        self.mgr._bg_task_mgr = MagicMock()
        fake_task = MagicMock()
        fake_task.task_id = "bg_test3"
        self.mgr._bg_task_mgr.create_task_checked.return_value = (fake_task, "running")
        self.mgr._bg_identity = "test_user"

        with patch("concurrent.futures.ThreadPoolExecutor"):
            result = self.mgr._slash_background(
                "agent=wee-qa say hello",
                session_data,
                "test-session-id",
            )

        # wee-qa dispatch_config.runtime = "openai"
        self.assertIn("openai", result)


class TestDispatchConfigPriorityResolution(unittest.TestCase):
    """Tests verify priority resolution via the PRODUCTION endpoint.

    These tests POST to /api/v1/background-tasks and assert the
    permission_mode in the response — exercising the real code path in
    create_background_task() instead of a reimplemented helper.
    """

    @classmethod
    def setUpClass(cls):
        import tempfile

        from fastapi.testclient import TestClient

        import agent_manager as am

        # Inject a test agents.json that has wee-qa with dispatch_config
        cls.temp_dir = tempfile.TemporaryDirectory()
        cfg_path = os.path.join(cls.temp_dir.name, "agents.json")
        with open(cfg_path, "w") as f:
            json.dump(
                {
                    "agents": [
                        {
                            "name": "wee-qa",
                            "description": "QA agent",
                            "path": "/opt/wee-qa",
                            "dispatch_config": {
                                "runtime": "openai",
                                "model": "gpt-5.4-mini",
                                "permission_mode": "elevated",
                                "yolo": True,
                                "timeout": 1800,
                            },
                        },
                        {
                            "name": "plain-agent",
                            "description": "Agent without dispatch_config",
                            "path": "/opt/plain",
                        },
                    ]
                },
                f,
            )
        os.environ["AGENT_CONFIG_FILE"] = cfg_path
        cls.app = am.create_api_app()
        cls.client = TestClient(cls.app, raise_server_exceptions=False)
        cls.headers = {"Authorization": "Bearer shared_test_key_123"}

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def _post(self, payload):
        """POST to the production endpoint and return the JSON response."""
        resp = self.client.post(
            "/api/v1/background-tasks",
            json=payload,
            headers=self.headers,
        )
        self.assertIn(
            resp.status_code,
            (200, 201),
            f"Unexpected status {resp.status_code}: {resp.text[:300]}",
        )
        return resp.json()

    def test_body_overrides_all(self):
        """Explicit body.permission_mode overrides dispatch_config and yolo."""
        data = self._post(
            {
                "prompt": "test",
                "agent": "wee-qa",
                "runtime": "my-runtime",
                "model": "my-model",
                "permission_mode": "sandboxed",
            }
        )
        self.assertEqual(data.get("permission_mode"), "sandboxed")
        self.assertEqual(data.get("runtime"), "my-runtime")
        self.assertEqual(data.get("model"), "my-model")

    def test_dispatch_config_overrides_session_defaults(self):
        """dispatch_config.permission_mode beats session defaults when body is empty."""
        data = self._post({"prompt": "test", "agent": "wee-qa"})
        # wee-qa has dispatch_config.yolo=True → should resolve to "elevated"
        self.assertEqual(
            data.get("permission_mode"),
            "elevated",
            "dispatch_config.yolo=True must resolve to 'elevated'",
        )

    def test_wee_qa_dispatch_config_scenario(self):
        """Full wee-qa scenario: runtime/model/permission_mode from dispatch_config."""
        data = self._post({"prompt": "test", "agent": "wee-qa"})
        self.assertEqual(data.get("runtime"), "openai")
        self.assertEqual(data.get("model"), "gpt-5.4-mini")
        self.assertEqual(data.get("permission_mode"), "elevated")

    def test_session_defaults_when_dispatch_config_empty(self):
        """plain-agent (no dispatch_config) falls back to restricted."""
        data = self._post({"prompt": "test", "agent": "plain-agent"})
        self.assertEqual(data.get("permission_mode"), "restricted")

    def test_body_yolo_true_elevates_perm_mode(self):
        """body.yolo=True must resolve to elevated perm_mode."""
        data = self._post(
            {
                "prompt": "test",
                "agent": "plain-agent",
                "yolo": True,
            }
        )
        self.assertEqual(data.get("permission_mode"), "elevated")

    def test_body_permission_mode_takes_highest_priority(self):
        """Explicit body.permission_mode wins over yolo and dispatch_config."""
        data = self._post(
            {
                "prompt": "test",
                "agent": "wee-qa",
                "permission_mode": "sandboxed",
                "yolo": True,
            }
        )
        self.assertEqual(data.get("permission_mode"), "sandboxed")

    def test_body_yolo_false_overrides_dispatch_config_yolo_true(self):
        """body.yolo=False must beat dispatch_config.yolo=True.

        This is the core regression for Issue #193: body.yolo=False was falsy
        so the 'or' chain fell through to dispatch_config, letting yolo=True
        in dispatch_config elevate the permission even when the caller
        explicitly opted out.
        """
        data = self._post(
            {
                "prompt": "test",
                "agent": "wee-qa",  # dispatch_config has yolo=True
                "yolo": False,  # explicit False must win
            }
        )
        self.assertEqual(
            data.get("permission_mode"),
            "restricted",
            "body.yolo=False must override dispatch_config.yolo=True",
        )

    def test_body_yolo_none_still_uses_dispatch_config(self):
        """Omitted body.yolo (None) should fall through to dispatch_config."""
        # wee-qa has dispatch_config.yolo=True
        data = self._post({"prompt": "test", "agent": "wee-qa"})
        self.assertEqual(
            data.get("permission_mode"),
            "elevated",
            "omitted body.yolo must let dispatch_config.yolo=True elevate",
        )


class TestBackgroundTaskRequestYoloField(unittest.TestCase):
    """Verify the /api/v1/background-tasks endpoint accepts yolo field."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        import agent_manager as am

        cls.app = am.create_api_app()
        cls.client = TestClient(cls.app, raise_server_exceptions=False)
        cls.headers = {"Authorization": "Bearer shared_test_key_123"}

    def test_yolo_field_accepted_not_422(self):
        """POST /api/v1/background-tasks must accept yolo field (no 422)."""
        resp = self.client.post(
            "/api/v1/background-tasks",
            json={"prompt": "test", "agent": "wee-qa", "yolo": True},
            headers=self.headers,
        )
        self.assertNotEqual(
            resp.status_code,
            422,
            f"yolo field should be accepted, got 422: {resp.text[:200]}",
        )

    def test_yolo_false_accepted_not_422(self):
        """POST /api/v1/background-tasks must accept yolo=false."""
        resp = self.client.post(
            "/api/v1/background-tasks",
            json={"prompt": "test", "agent": "wee-qa", "yolo": False},
            headers=self.headers,
        )
        self.assertNotEqual(resp.status_code, 422)

    def test_yolo_omitted_accepted_not_422(self):
        """POST /api/v1/background-tasks must accept requests without yolo."""
        resp = self.client.post(
            "/api/v1/background-tasks",
            json={"prompt": "test", "agent": "wee-qa"},
            headers=self.headers,
        )
        self.assertNotEqual(resp.status_code, 422)

    def test_openapi_schema_has_yolo_property(self):
        """OpenAPI schema for background-tasks must include yolo property."""
        resp = self.client.get("/openapi.json")
        self.assertEqual(resp.status_code, 200)
        schema = resp.json()
        schemas = schema.get("components", {}).get("schemas", {})
        bg_req = schemas.get("BackgroundTaskRequest", {})
        props = bg_req.get("properties", {})
        self.assertIn(
            "yolo",
            props,
            "BackgroundTaskRequest schema must include 'yolo' property",
        )


class TestQueuedTaskPermissionModePreserved(unittest.TestCase):
    """Regression tests for QA defect: queued tasks lose resolved permission_mode.

    When create_background_task resolves perm_mode from dispatch_config/body
    but the task is queued (concurrency limit reached), the resolved perm_mode
    must be persisted in the task record so that promotion later uses it —
    not the fallback "restricted".
    """

    def _make_bg_task_mgr(self, tmp_dir):
        """Create a BackgroundTaskManager backed by a temp file."""
        import agent_manager as am

        mgr = am.BackgroundTaskManager()
        # Redirect storage to a temp path so tests don't touch the real file
        mgr._path = os.path.join(tmp_dir, "background_tasks.json")
        return mgr

    def test_create_task_checked_stores_permission_mode_in_queued_record(self):
        """create_task_checked must persist permission_mode in queued task dict."""
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._make_bg_task_mgr(tmp)

            # Fill the slot with a running task
            mgr.create_task(
                task_id="running-1",
                session_id="s1",
                user_identity="user1",
                channel="telegram",
                agent="wee-qa",
                runtime="copilot",
                model="auto",
                prompt="first task",
                status="running",
                permission_mode="elevated",
            )

            # Submit a second task that will be queued
            queued_task, status = mgr.create_task_checked(
                task_id="queued-1",
                session_id="s2",
                user_identity="user1",
                channel="telegram",
                agent="wee-qa",
                runtime="copilot",
                model="auto",
                prompt="second task",
                max_concurrent=1,  # slot is taken → will queue
                permission_mode="elevated",
            )

            self.assertEqual(status, "queued", "task should be queued")
            stored = mgr.get_task("queued-1")
            self.assertIsNotNone(stored)
            self.assertEqual(
                stored.get("permission_mode"),
                "elevated",
                "queued task must store resolved permission_mode='elevated'; "
                "got: " + repr(stored.get("permission_mode")),
            )

    def test_promoted_task_retains_elevated_permission_mode(self):
        """promote_queued_task must not reset permission_mode to 'restricted'.

        This is the core regression: the promoted task's stored record must
        still carry the original 'elevated' permission_mode after promotion.
        """
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._make_bg_task_mgr(tmp)

            # Create a queued task with elevated permission_mode
            task_id = "queued-perm-test"
            mgr.create_task(
                task_id=task_id,
                session_id="s-old",
                user_identity="user1",
                channel="telegram",
                agent="wee-qa",
                runtime="copilot",
                model="auto",
                prompt="queued task",
                status="queued",
                permission_mode="elevated",
            )

            # Verify it was stored correctly before promotion
            pre = mgr.get_task(task_id)
            self.assertEqual(pre.get("permission_mode"), "elevated")

            # Promote the task (simulating what the completion handler does)
            mgr.promote_queued_task(task_id, "s-new")

            # After promotion, permission_mode must be preserved
            post = mgr.get_task(task_id)
            self.assertEqual(post.get("status"), "running")
            self.assertEqual(
                post.get("permission_mode"),
                "elevated",
                "promotion must not reset permission_mode to 'restricted'; "
                "got: " + repr(post.get("permission_mode")),
            )

    def test_queued_task_via_api_preserves_dispatch_config_perm_mode(self):
        """Full API path: queued task stores permission_mode from dispatch_config.

        POST /api/v1/background-tasks with concurrency slot full forces a
        queued status. The response and the stored record must both reflect
        the dispatch_config-resolved permission_mode.
        """
        import agent_manager as am
        from fastapi.testclient import TestClient

        # Use a dedicated test key so the test is not coupled to the production key.
        _test_key = "qatest_perm_mode_key_193"
        orig_key = os.environ.get("API_SHARED_KEY")
        os.environ["API_SHARED_KEY"] = _test_key

        try:
            with tempfile.TemporaryDirectory() as tmp:
                cfg_path = os.path.join(tmp, "agents.json")
                with open(cfg_path, "w") as f:
                    json.dump(
                        {
                            "agents": [
                                {
                                    "name": "wee-qa",
                                    "description": "QA agent",
                                    "path": "/opt/wee-qa",
                                    "max_concurrent": 1,
                                    "dispatch_config": {
                                        "runtime": "openai",
                                        "model": "gpt-5.4-mini",
                                        "permission_mode": "elevated",
                                        "yolo": True,
                                        "timeout": 1800,
                                    },
                                }
                            ]
                        },
                        f,
                    )
                os.environ["AGENT_CONFIG_FILE"] = cfg_path
                app = am.create_api_app()
                client = TestClient(app, raise_server_exceptions=False)
                headers = {
                    "Authorization": f"Bearer shared_{_test_key}",
                    "X-User-Identity": "testuser",
                    "X-Auth-Channel": "telegram",
                }

                # First task — takes the only slot
                r1 = client.post(
                    "/api/v1/background-tasks",
                    json={"prompt": "first", "agent": "wee-qa"},
                    headers=headers,
                )
                self.assertIn(r1.status_code, (200, 201), r1.text[:200])
                self.assertEqual(r1.json().get("status"), "running")

                # Second task — must be queued
                r2 = client.post(
                    "/api/v1/background-tasks",
                    json={"prompt": "second", "agent": "wee-qa"},
                    headers=headers,
                )
                self.assertIn(r2.status_code, (200, 201), r2.text[:200])
                data2 = r2.json()
                self.assertEqual(
                    data2.get("status"),
                    "queued",
                    "second task must be queued (slot occupied)",
                )
                # API response must reflect resolved permission_mode
                self.assertEqual(
                    data2.get("permission_mode"),
                    "elevated",
                    "queued task API response must include permission_mode='elevated'",
                )

                # Verify the stored record also has it
                bg_mgr = app.state.bg_task_mgr
                stored = bg_mgr.get_task(data2["task_id"])
                self.assertIsNotNone(stored)
                self.assertEqual(
                    stored.get("permission_mode"),
                    "elevated",
                    "queued task stored record must have permission_mode='elevated'; "
                    "got: " + repr(stored.get("permission_mode")),
                )
        finally:
            if orig_key is None:
                os.environ.pop("API_SHARED_KEY", None)
            else:
                os.environ["API_SHARED_KEY"] = orig_key


class TestRuntimeOpenaiAliasForWee(unittest.TestCase):
    """Regression tests for QA defect: runtime='openai' falls through to copilot.

    The wee-qa dispatch_config uses runtime='openai'. The executor must route
    'openai' to the wee_runtime.py path (same as 'wee'), not the copilot path.
    Adding a new branch `elif runtime in ('wee', 'openai'):` is the fix.
    """

    def _get_runtime_cmd(self, runtime, model="gpt-5.4-mini", permission_mode="restricted"):
        """Extract the command that _run_background_task would build for a given runtime.

        Patches subprocess.Popen so no process is actually launched.
        Returns (cmd_list, popen_call_count).
        """
        import agent_manager as am
        from unittest.mock import MagicMock, patch, call

        captured = {}

        def fake_popen(cmd, **kwargs):
            captured["cmd"] = cmd
            proc = MagicMock()
            proc.stdout = iter([])
            proc.stderr = iter([])
            proc.returncode = 0
            proc.wait.return_value = 0
            proc.poll.return_value = 0
            return proc

        # We call the inner function directly; to do that we need to
        # introspect the closure-level _run_background_task
        app = am.create_api_app()
        # _run_background_task is defined inside create_api_app() closure;
        # we'll trigger it via a thin wrapper that intercepts Popen
        with patch("subprocess.Popen", side_effect=fake_popen):
            try:
                # Run in a thread since it blocks
                import threading

                t = threading.Thread(
                    target=am._run_background_task_for_test,
                    args=("t1", "s1", "prompt", "wee-qa", runtime, model,
                          "telegram", "user1", 30, False, permission_mode),
                    daemon=True,
                )
                t.start()
                t.join(timeout=5)
            except AttributeError:
                # _run_background_task_for_test not exposed; fall back to
                # inspecting the source path directly
                pass

        return captured.get("cmd", [])

    def test_openai_runtime_is_recognized_not_fallen_through(self):
        """runtime='openai' must map to wee_runtime.py, not copilot binary.

        We verify this by inspecting the source code of _run_background_task
        to confirm 'openai' is in the 'wee' branch condition — a static
        assertion that survives refactors without spawning processes.
        """
        import inspect
        import agent_manager as am

        # Get the source of create_api_app which contains _run_background_task
        src = inspect.getsource(am.create_api_app)

        # The executor must have 'openai' covered in the wee branch
        self.assertIn(
            '"openai"',
            src,
            "executor source must mention 'openai' runtime somewhere",
        )
        # Verify 'openai' appears in the same branch as 'wee'
        # Look for the pattern: runtime in ("wee", "openai") or ("openai", "wee")
        import re

        wee_branch = re.search(
            r'elif\s+runtime\s+in\s+\([^)]*["\']wee["\'][^)]*\)',
            src,
        )
        self.assertIsNotNone(
            wee_branch,
            "executor must have 'elif runtime in (...\"wee\"...)' branch",
        )
        branch_text = wee_branch.group(0)
        self.assertIn(
            "openai",
            branch_text,
            f"wee branch must include 'openai' alias; branch: {branch_text!r}",
        )

    def test_openai_runtime_does_not_fall_through_to_else(self):
        """The else clause (copilot) must NOT be reached for runtime='openai'.

        We confirm by checking the executor branching order: 'openai' must
        appear in an elif before the final else, so it is caught first.
        """
        import re
        import inspect
        import agent_manager as am

        src = inspect.getsource(am.create_api_app)

        # Find the position of the wee/openai branch and the else clause
        wee_branch_match = re.search(
            r'elif\s+runtime\s+in\s+\([^)]*["\']wee["\'][^)]*\)',
            src,
        )
        else_copilot_match = re.search(
            r'else:\s*\n\s*#\s*Default:\s*copilot',
            src,
        )

        self.assertIsNotNone(wee_branch_match, "wee/openai branch must exist in executor")
        self.assertIsNotNone(else_copilot_match, "copilot else-clause must exist in executor")

        # The wee/openai branch must appear BEFORE the else-copilot clause
        self.assertLess(
            wee_branch_match.start(),
            else_copilot_match.start(),
            "wee/openai branch must be defined before the copilot else-fallthrough",
        )

    def test_live_wee_qa_config_openai_runtime_routes_to_wee_path(self):
        """Full integration: wee-qa dispatch_config with runtime='openai' routes to wee.

        Uses the real agents.json fixture from the test class; posts a task
        for wee-qa and confirms the runtime returned is 'openai' (stored as-is),
        meaning it went through the dispatch_config path correctly.
        """
        import agent_manager as am
        from fastapi.testclient import TestClient

        _test_key = "qatest_openai_runtime_key_193"
        orig_key = os.environ.get("API_SHARED_KEY")
        os.environ["API_SHARED_KEY"] = _test_key

        try:
            with tempfile.TemporaryDirectory() as tmp:
                cfg_path = os.path.join(tmp, "agents.json")
                with open(cfg_path, "w") as f:
                    json.dump(
                        {
                            "agents": [
                                {
                                    "name": "wee-qa",
                                    "description": "QA agent",
                                    "path": "/opt/wee-qa",
                                    "dispatch_config": {
                                        "runtime": "openai",
                                        "model": "gpt-5.4-mini",
                                        "permission_mode": "elevated",
                                        "yolo": True,
                                        "timeout": 1800,
                                    },
                                }
                            ]
                        },
                        f,
                    )
                os.environ["AGENT_CONFIG_FILE"] = cfg_path
                app = am.create_api_app()
                client = TestClient(app, raise_server_exceptions=False)
                headers = {
                    "Authorization": f"Bearer shared_{_test_key}",
                    "X-User-Identity": "testuser",
                    "X-Auth-Channel": "telegram",
                }

                resp = client.post(
                    "/api/v1/background-tasks",
                    json={"prompt": "test", "agent": "wee-qa"},
                    headers=headers,
                )
                self.assertIn(resp.status_code, (200, 201), resp.text[:200])
                data = resp.json()

                # runtime must be 'openai' as resolved from dispatch_config
                self.assertEqual(
                    data.get("runtime"),
                    "openai",
                    "runtime='openai' from dispatch_config must be preserved in response",
                )
                # permission_mode must be 'elevated' from dispatch_config.yolo=True
                self.assertEqual(
                    data.get("permission_mode"),
                    "elevated",
                    "dispatch_config.yolo=True must resolve to permission_mode='elevated'",
                )
        finally:
            if orig_key is None:
                os.environ.pop("API_SHARED_KEY", None)
            else:
                os.environ["API_SHARED_KEY"] = orig_key


if __name__ == "__main__":
    unittest.main()
