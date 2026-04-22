"""Regression tests for Issue #168: Global Background Runtime Configuration.

Tests:
- GET /api/v1/runtime-preferences returns defaults
- PUT /api/v1/runtime-preferences saves and returns new values
- Background task uses primary runtime when no runtime specified
- Background task preserves explicit runtime override (does not apply preference)
- RuntimePreferencesManager loads/saves/defaults correctly
- Context injection includes runtime preferences
"""

import json
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["API_SHARED_KEY"] = "test_key_168"
os.environ["APP_ENV"] = "DEV"
os.environ["API_PORT"] = "8099"


class TestRuntimePreferencesManager(unittest.TestCase):
    """Unit tests for the RuntimePreferencesManager class."""

    def setUp(self):
        import agent_manager

        self.tmp = tempfile.mktemp(suffix=".json")
        self.mgr = agent_manager.RuntimePreferencesManager(self.tmp)

    def tearDown(self):
        if os.path.exists(self.tmp):
            os.unlink(self.tmp)

    def test_default_primary(self):
        """Default primary runtime should be 'copilot'."""
        self.assertEqual(self.mgr.primary(), "copilot")

    def test_default_backup(self):
        """Default backup runtime should be 'claude'."""
        self.assertEqual(self.mgr.backup(), "claude")

    def test_get_returns_dict(self):
        """get() should return a dict with primary_runtime and backup_runtime keys."""
        prefs = self.mgr.get()
        self.assertIn("primary_runtime", prefs)
        self.assertIn("backup_runtime", prefs)

    def test_set_persists_to_disk(self):
        """set() should persist changes to the JSON file."""
        self.mgr.set("gemini", "opencode")
        self.assertTrue(os.path.exists(self.tmp))
        with open(self.tmp) as f:
            data = json.load(f)
        self.assertEqual(data["primary_runtime"], "gemini")
        self.assertEqual(data["backup_runtime"], "opencode")

    def test_set_updates_in_memory(self):
        """set() should immediately update in-memory values."""
        self.mgr.set("claude", "copilot")
        self.assertEqual(self.mgr.primary(), "claude")
        self.assertEqual(self.mgr.backup(), "copilot")

    def test_load_from_existing_file(self):
        """Manager should load existing preferences from disk on init."""
        import agent_manager

        data = {"primary_runtime": "opencode", "backup_runtime": "gemini"}
        with open(self.tmp, "w") as f:
            json.dump(data, f)
        mgr2 = agent_manager.RuntimePreferencesManager(self.tmp)
        self.assertEqual(mgr2.primary(), "opencode")
        self.assertEqual(mgr2.backup(), "gemini")

    def test_load_handles_corrupt_file(self):
        """Manager should fall back to defaults if file is corrupt."""
        import agent_manager

        with open(self.tmp, "w") as f:
            f.write("{ not valid json }")
        mgr2 = agent_manager.RuntimePreferencesManager(self.tmp)
        self.assertEqual(mgr2.primary(), "copilot")
        self.assertEqual(mgr2.backup(), "claude")

    def test_thread_safety(self):
        """Concurrent reads and writes should not raise exceptions."""
        errors = []

        def writer():
            try:
                for _ in range(20):
                    self.mgr.set("claude", "copilot")
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(20):
                    _ = self.mgr.get()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer) for _ in range(3)]
        threads += [threading.Thread(target=reader) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [], f"Thread safety errors: {errors}")


class TestRuntimePreferencesAPI(unittest.TestCase):
    """Tests for the /api/v1/runtime-preferences API endpoints."""

    @classmethod
    def setUpClass(cls):
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        import agent_manager

        cls._telegram_patch = patch.object(
            agent_manager,
            "_resolve_telegram_identity",
            side_effect=lambda identity: identity,
        )
        cls._telegram_patch.start()
        cls._send_pairing_patch = patch.object(
            agent_manager,
            "_send_pairing_code",
            return_value=True,
        )
        cls._send_pairing_patch.start()

        # Override pref file to a temp file so tests don't affect real config
        cls._tmp_pref = tempfile.mktemp(suffix="_168_test.json")
        cls._pref_patch = patch.object(
            agent_manager,
            "_runtime_pref_mgr",
            agent_manager.RuntimePreferencesManager(cls._tmp_pref),
        )
        cls._pref_patch.start()

        cls.app = agent_manager.create_api_app()
        cls.client = TestClient(cls.app)
        cls.auth = {"Authorization": "Bearer shared_test_key_168"}

    @classmethod
    def tearDownClass(cls):
        cls._telegram_patch.stop()
        cls._send_pairing_patch.stop()
        cls._pref_patch.stop()
        if os.path.exists(cls._tmp_pref):
            os.unlink(cls._tmp_pref)

    def test_get_returns_defaults(self):
        """GET /api/v1/runtime-preferences should return default primary/backup."""
        resp = self.client.get("/api/v1/runtime-preferences", headers=self.auth)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("primary_runtime", data)
        self.assertIn("backup_runtime", data)
        self.assertIn("available_runtimes", data)
        self.assertEqual(data["primary_runtime"], "copilot")
        self.assertEqual(data["backup_runtime"], "claude")

    def test_put_saves_preferences(self):
        """PUT /api/v1/runtime-preferences should save and return new values."""
        payload = {"primary_runtime": "gemini", "backup_runtime": "opencode"}
        resp = self.client.put(
            "/api/v1/runtime-preferences",
            json=payload,
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["primary_runtime"], "gemini")
        self.assertEqual(data["backup_runtime"], "opencode")
        self.assertEqual(data["status"], "saved")

    def test_put_persists_across_get(self):
        """After PUT, GET should return the new values."""
        payload = {"primary_runtime": "claude", "backup_runtime": "copilot"}
        self.client.put("/api/v1/runtime-preferences", json=payload, headers=self.auth)
        resp = self.client.get("/api/v1/runtime-preferences", headers=self.auth)
        data = resp.json()
        self.assertEqual(data["primary_runtime"], "claude")
        self.assertEqual(data["backup_runtime"], "copilot")

    def test_put_requires_auth(self):
        """PUT without valid auth should be rejected (401 or 403)."""
        payload = {"primary_runtime": "gemini", "backup_runtime": "opencode"}
        resp = self.client.put("/api/v1/runtime-preferences", json=payload)
        self.assertIn(resp.status_code, [401, 403])

    def test_get_requires_auth(self):
        """GET should require authentication (401 without valid auth)."""
        resp = self.client.get("/api/v1/runtime-preferences")
        self.assertIn(resp.status_code, [401, 403])

    def test_available_runtimes_in_response(self):
        """GET should include available_runtimes list."""
        resp = self.client.get("/api/v1/runtime-preferences", headers=self.auth)
        data = resp.json()
        self.assertIsInstance(data["available_runtimes"], list)


class TestBackgroundTaskRuntimeSelection(unittest.TestCase):
    """Tests that background tasks use runtime preferences correctly."""

    @classmethod
    def setUpClass(cls):
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        import agent_manager

        cls._telegram_patch = patch.object(
            agent_manager,
            "_resolve_telegram_identity",
            side_effect=lambda identity: identity,
        )
        cls._telegram_patch.start()
        cls._send_pairing_patch = patch.object(
            agent_manager,
            "_send_pairing_code",
            return_value=True,
        )
        cls._send_pairing_patch.start()

        cls._tmp_pref = tempfile.mktemp(suffix="_168_bg_test.json")
        cls._pref_mgr = agent_manager.RuntimePreferencesManager(cls._tmp_pref)
        cls._pref_patch = patch.object(
            agent_manager,
            "_runtime_pref_mgr",
            cls._pref_mgr,
        )
        cls._pref_patch.start()

        # Mock _execute_background_task to capture runtime used
        cls._captured_tasks = []

        async def _fake_run_bg(  # noqa: E306
            self_sm, task_id, session_id, prompt, agent, runtime, model, *a, **kw
        ):
            cls._captured_tasks.append(
                {"task_id": task_id, "runtime": runtime, "model": model}
            )

        cls._run_bg_patch = patch.object(
            agent_manager.SessionManager, "_execute_background_task", _fake_run_bg
        )
        cls._run_bg_patch.start()

        cls.app = agent_manager.create_api_app()
        cls.client = TestClient(cls.app)
        cls.auth = {"Authorization": "Bearer shared_test_key_168"}

    @classmethod
    def tearDownClass(cls):
        cls._telegram_patch.stop()
        cls._send_pairing_patch.stop()
        cls._pref_patch.stop()
        cls._run_bg_patch.stop()
        if os.path.exists(cls._tmp_pref):
            os.unlink(cls._tmp_pref)

    def test_primary_runtime_used_when_no_explicit_runtime(self):
        """Background task with no explicit runtime should use primary preference."""
        from unittest.mock import patch

        import agent_manager

        # Set primary to gemini
        self._pref_mgr.set("gemini", "claude")

        # Patch _compute_bg_task_defaults to return empty (no session inheritance)
        with patch.object(agent_manager, "_compute_bg_task_defaults", return_value={}):
            resp = self.client.post(
                "/api/v1/background-tasks",
                json={"prompt": "test task no runtime", "agent": "orchestrator"},
                headers=self.auth,
            )
        self.assertIn(resp.status_code, [200, 201, 202])
        # The task may be queued but runtime should be set based on preference
        task_data = resp.json()
        self.assertIn("runtime", task_data)
        self.assertEqual(task_data["runtime"], "gemini")

    def test_explicit_runtime_overrides_preference(self):
        """Background task with explicit runtime should NOT be overridden."""
        from unittest.mock import patch

        import agent_manager

        # Set primary to gemini, but explicitly request claude
        self._pref_mgr.set("gemini", "copilot")

        with patch.object(agent_manager, "_compute_bg_task_defaults", return_value={}):
            resp = self.client.post(
                "/api/v1/background-tasks",
                json={
                    "prompt": "test explicit override",
                    "agent": "orchestrator",
                    "runtime": "claude",
                },
                headers=self.auth,
            )
        self.assertIn(resp.status_code, [200, 201, 202])
        task_data = resp.json()
        self.assertIn("runtime", task_data)
        self.assertEqual(task_data["runtime"], "claude")


class TestContextInjection(unittest.TestCase):
    """Tests that runtime preferences are injected into session context."""

    @classmethod
    def setUpClass(cls):
        from unittest.mock import patch

        import agent_manager

        cls._tmp_pref = tempfile.mktemp(suffix="_168_ctx_test.json")
        cls._pref_mgr = agent_manager.RuntimePreferencesManager(cls._tmp_pref)
        cls._pref_mgr.set("opencode", "gemini")
        cls._pref_patch = patch.object(
            agent_manager,
            "_runtime_pref_mgr",
            cls._pref_mgr,
        )
        cls._pref_patch.start()

    @classmethod
    def tearDownClass(cls):
        cls._pref_patch.stop()
        if os.path.exists(cls._tmp_pref):
            os.unlink(cls._tmp_pref)

    def test_context_includes_runtime_preferences(self):
        """build_agent_context_prompt should include runtime preferences in context."""
        from unittest.mock import patch

        import agent_manager

        session_mgr = agent_manager.SessionManager.__new__(agent_manager.SessionManager)
        session_mgr.AGENTS = {
            "orchestrator": {"description": "Main orchestrator", "path": "/opt"},
            "devops": {"description": "DevOps agent", "path": "/opt"},
        }
        session_mgr._bg_task_mgr = None
        session_mgr._bg_identity = None
        session_mgr.session_map_file = None
        session_mgr.skill_repositories = []

        with patch.object(
            session_mgr, "load_session_data", return_value={}
        ), patch.object(session_mgr, "update_session_field", return_value=None):
            ctx = session_mgr.build_agent_context_prompt(
                prompt="hello",
                agent="orchestrator",
                channel="api",
                n8n_session_id="test-session-168",
            )

        self.assertIn("opencode", ctx)
        self.assertIn("gemini", ctx)
        self.assertIn("Primary runtime", ctx)


class TestRuntimePreferencesValidation(unittest.TestCase):
    """Tests that PUT /api/v1/runtime-preferences validates input."""

    @classmethod
    def setUpClass(cls):
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        import agent_manager

        cls._telegram_patch = patch.object(
            agent_manager,
            "_resolve_telegram_identity",
            side_effect=lambda identity: identity,
        )
        cls._telegram_patch.start()
        cls._send_pairing_patch = patch.object(
            agent_manager,
            "_send_pairing_code",
            return_value=True,
        )
        cls._send_pairing_patch.start()

        cls._tmp_pref = tempfile.mktemp(suffix="_168_val_test.json")
        cls._pref_patch = patch.object(
            agent_manager,
            "_runtime_pref_mgr",
            agent_manager.RuntimePreferencesManager(cls._tmp_pref),
        )
        cls._pref_patch.start()

        cls.app = agent_manager.create_api_app()
        cls.client = TestClient(cls.app)
        cls.auth = {"Authorization": "Bearer shared_test_key_168"}

    @classmethod
    def tearDownClass(cls):
        cls._telegram_patch.stop()
        cls._send_pairing_patch.stop()
        cls._pref_patch.stop()
        if os.path.exists(cls._tmp_pref):
            os.unlink(cls._tmp_pref)

    def test_invalid_primary_runtime_rejected(self):
        """PUT with invalid primary_runtime should return 422."""
        payload = {
            "primary_runtime": "<script>alert(1)</script>",
            "backup_runtime": "claude",
        }
        resp = self.client.put(
            "/api/v1/runtime-preferences",
            json=payload,
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 422)

    def test_invalid_backup_runtime_rejected(self):
        """PUT with invalid backup_runtime should return 422."""
        payload = {
            "primary_runtime": "copilot",
            "backup_runtime": "'; DROP TABLE sessions;--",
        }
        resp = self.client.put(
            "/api/v1/runtime-preferences",
            json=payload,
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 422)

    def test_valid_runtimes_accepted(self):
        """PUT with known-valid runtimes should succeed."""
        import agent_manager

        valid = agent_manager.get_available_runtimes()
        if len(valid) < 2:
            self.skipTest("Need at least 2 available runtimes")
        payload = {
            "primary_runtime": valid[0]["id"],
            "backup_runtime": valid[1]["id"],
        }
        resp = self.client.put(
            "/api/v1/runtime-preferences",
            json=payload,
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 200)


class TestBackupRuntimeFallback(unittest.TestCase):
    """Tests that backup_runtime is used when primary is empty."""

    def test_backup_used_when_primary_empty(self):
        """When primary returns '', backup should be used."""
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        import agent_manager

        tmp = tempfile.mktemp(suffix="_168_fallback.json")
        mgr = agent_manager.RuntimePreferencesManager(tmp)
        mgr.set("", "gemini")

        try:
            with patch.object(agent_manager, "_runtime_pref_mgr", mgr), patch.object(
                agent_manager,
                "get_default_runtime",
                return_value="copilot",
            ), patch.object(
                agent_manager,
                "_resolve_telegram_identity",
                side_effect=lambda x: x,
            ), patch.object(
                agent_manager,
                "_send_pairing_code",
                return_value=True,
            ), patch.object(
                agent_manager,
                "_compute_bg_task_defaults",
                return_value={},
            ):
                captured = []

                async def fake_bg(
                    self_sm,
                    task_id,
                    session_id,
                    prompt,
                    agent,
                    runtime,
                    model,
                    *a,
                    **kw,
                ):
                    captured.append(runtime)

                with patch.object(
                    agent_manager.SessionManager,
                    "_execute_background_task",
                    fake_bg,
                ):
                    app = agent_manager.create_api_app()
                    client = TestClient(app)
                    resp = client.post(
                        "/api/v1/background-tasks",
                        json={
                            "prompt": "test backup fallback",
                            "agent": "orchestrator",
                        },
                        headers={
                            "Authorization": "Bearer shared_test_key_168",
                        },
                    )
                self.assertIn(resp.status_code, [200, 201, 202])
                task_data = resp.json()
                self.assertIn("runtime", task_data)
                self.assertEqual(task_data["runtime"], "gemini")
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def test_backup_used_when_primary_unavailable(self):
        """Regression test for #168 BLOCKER: when primary is configured but
        unavailable (not installed), the backup runtime must be selected."""
        import tempfile
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        import agent_manager

        tmp = tempfile.mktemp(suffix="_168_fallback_unavailable.json")
        mgr = agent_manager.RuntimePreferencesManager(tmp)
        # Set primary to something definitely not installed; backup to a real one
        mgr.set("definitely-not-installed", "gemini")

        try:
            with patch.object(agent_manager, "_runtime_pref_mgr", mgr), patch.object(
                agent_manager,
                "check_runtime_available",
                side_effect=lambda rt: rt != "definitely-not-installed",
            ), patch.object(
                agent_manager,
                "get_default_runtime",
                return_value="copilot",
            ), patch.object(
                agent_manager,
                "_resolve_telegram_identity",
                side_effect=lambda x: x,
            ), patch.object(
                agent_manager,
                "_send_pairing_code",
                return_value=True,
            ), patch.object(
                agent_manager,
                "_compute_bg_task_defaults",
                return_value={},
            ):
                captured = []

                async def fake_bg(
                    self_sm,
                    task_id,
                    session_id,
                    prompt,
                    agent,
                    runtime,
                    model,
                    *a,
                    **kw,
                ):
                    captured.append(runtime)

                with patch.object(
                    agent_manager.SessionManager,
                    "_execute_background_task",
                    fake_bg,
                ):
                    app = agent_manager.create_api_app()
                    client = TestClient(app)
                    resp = client.post(
                        "/api/v1/background-tasks",
                        json={
                            "prompt": "test unavailable primary fallback",
                            "agent": "orchestrator",
                        },
                        headers={
                            "Authorization": "Bearer shared_test_key_168",
                        },
                    )
                self.assertIn(resp.status_code, [200, 201, 202])
                task_data = resp.json()
                self.assertIn("runtime", task_data)
                self.assertEqual(
                    task_data["runtime"],
                    "gemini",
                    f"Expected backup 'gemini' but got '{task_data['runtime']}'. "
                    "Primary 'definitely-not-installed' should be skipped.",
                )
        finally:
            import os

            if os.path.exists(tmp):
                os.unlink(tmp)

    def test_default_used_when_primary_unavailable_and_no_backup(self):
        """When primary is unavailable and no backup is set, use the default runtime."""
        import tempfile
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        import agent_manager

        tmp = tempfile.mktemp(suffix="_168_fallback_default.json")
        mgr = agent_manager.RuntimePreferencesManager(tmp)
        mgr.set("definitely-not-installed", "")

        try:
            with patch.object(agent_manager, "_runtime_pref_mgr", mgr), patch.object(
                agent_manager,
                "check_runtime_available",
                side_effect=lambda rt: rt != "definitely-not-installed",
            ), patch.object(
                agent_manager,
                "get_default_runtime",
                return_value="copilot",
            ), patch.object(
                agent_manager,
                "_resolve_telegram_identity",
                side_effect=lambda x: x,
            ), patch.object(
                agent_manager,
                "_send_pairing_code",
                return_value=True,
            ), patch.object(
                agent_manager,
                "_compute_bg_task_defaults",
                return_value={},
            ):
                captured = []

                async def fake_bg(
                    self_sm,
                    task_id,
                    session_id,
                    prompt,
                    agent,
                    runtime,
                    model,
                    *a,
                    **kw,
                ):
                    captured.append(runtime)

                with patch.object(
                    agent_manager.SessionManager,
                    "_execute_background_task",
                    fake_bg,
                ):
                    app = agent_manager.create_api_app()
                    client = TestClient(app)
                    resp = client.post(
                        "/api/v1/background-tasks",
                        json={
                            "prompt": "test unavailable primary no backup",
                            "agent": "orchestrator",
                        },
                        headers={
                            "Authorization": "Bearer shared_test_key_168",
                        },
                    )
                self.assertIn(resp.status_code, [200, 201, 202])
                task_data = resp.json()
                self.assertIn("runtime", task_data)
                self.assertEqual(
                    task_data["runtime"],
                    "copilot",
                    f"Expected default 'copilot' but got '{task_data['runtime']}'.",
                )
        finally:
            import os

            if os.path.exists(tmp):
                os.unlink(tmp)

    def test_default_used_when_both_primary_and_backup_unavailable(self):
        """Regression test for #168 MAJOR: when both primary AND backup runtimes are
        configured but unavailable, the code must fall back to the default runtime —
        NOT select the unavailable backup. Reproduction:
        primary='definitely-not-installed',
        backup='also-not-installed', default='copilot' -> must return 'copilot'."""
        import tempfile
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        import agent_manager

        tmp = tempfile.mktemp(suffix="_168_both_unavailable.json")
        mgr = agent_manager.RuntimePreferencesManager(tmp)
        mgr.set("definitely-not-installed", "also-not-installed")

        try:
            with patch.object(agent_manager, "_runtime_pref_mgr", mgr), patch.object(
                agent_manager,
                "check_runtime_available",
                side_effect=lambda rt: rt
                not in ("definitely-not-installed", "also-not-installed"),
            ), patch.object(
                agent_manager,
                "get_default_runtime",
                return_value="copilot",
            ), patch.object(
                agent_manager,
                "_resolve_telegram_identity",
                side_effect=lambda x: x,
            ), patch.object(
                agent_manager,
                "_send_pairing_code",
                return_value=True,
            ), patch.object(
                agent_manager,
                "_compute_bg_task_defaults",
                return_value={},
            ):
                captured = []

                async def fake_bg(
                    self_sm,
                    task_id,
                    session_id,
                    prompt,
                    agent,
                    runtime,
                    model,
                    *a,
                    **kw,
                ):
                    captured.append(runtime)

                with patch.object(
                    agent_manager.SessionManager,
                    "_execute_background_task",
                    fake_bg,
                ):
                    app = agent_manager.create_api_app()
                    client = TestClient(app)
                    resp = client.post(
                        "/api/v1/background-tasks",
                        json={
                            "prompt": "test both runtimes unavailable",
                            "agent": "orchestrator",
                        },
                        headers={
                            "Authorization": "Bearer shared_test_key_168",
                        },
                    )
                self.assertIn(resp.status_code, [200, 201, 202])
                task_data = resp.json()
                self.assertIn("runtime", task_data)
                self.assertEqual(
                    task_data["runtime"],
                    "copilot",
                    f"Expected default 'copilot' but got '{task_data['runtime']}'. "
                    "Both primary 'definitely-not-installed' and backup"
                    " 'also-not-installed' "
                    "are unavailable — must fall back to default 'copilot'.",
                )
        finally:
            import os

            if os.path.exists(tmp):
                os.unlink(tmp)


if __name__ == "__main__":
    unittest.main()
