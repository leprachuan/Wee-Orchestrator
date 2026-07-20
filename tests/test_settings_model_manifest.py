"""Tests for the /api/v1/settings/model-manifest endpoints."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["API_SHARED_KEY"] = "test_key_123"
os.environ["APP_ENV"] = "DEV"
os.environ["API_PORT"] = "8098"


class TestSettingsModelManifest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        import agent_manager

        cls.tmpdir = tempfile.TemporaryDirectory()
        cls.manifest_path = os.path.join(cls.tmpdir.name, "model-manifest.json")
        with open(cls.manifest_path, "w") as f:
            json.dump(
                {
                    "runtimes": {
                        "claude": ["sonnet", "haiku"],
                        "codex": ["gpt-5.5", "gpt-5.4"],
                        "wee": ["ollama/gemma4:e4b"],
                    }
                },
                f,
            )

        cls._manifest_patch = patch.object(
            agent_manager, "MODEL_MANIFEST_PATH", __import__("pathlib").Path(cls.manifest_path)
        )
        cls._manifest_patch.start()

        cls.app = agent_manager.create_api_app()
        cls.client = TestClient(cls.app)
        cls.shared_header = {"Authorization": "Bearer shared_test_key_123"}

    @classmethod
    def tearDownClass(cls):
        cls._manifest_patch.stop()
        cls.tmpdir.cleanup()

    def _read_manifest(self):
        with open(self.manifest_path) as f:
            return json.load(f)

    def test_get_returns_models_and_excludes_wee_from_available(self):
        resp = self.client.get(
            "/api/v1/settings/model-manifest?runtime=claude", headers=self.shared_header
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["runtime"], "claude")
        self.assertEqual(data["models"], ["sonnet", "haiku"])
        self.assertNotIn("wee", data["available_runtimes"])
        self.assertIn("claude", data["available_runtimes"])
        self.assertIn("codex", data["available_runtimes"])

    def test_get_wee_is_rejected(self):
        resp = self.client.get(
            "/api/v1/settings/model-manifest?runtime=wee", headers=self.shared_header
        )
        self.assertEqual(resp.status_code, 400)

    def test_get_unknown_runtime_404s(self):
        resp = self.client.get(
            "/api/v1/settings/model-manifest?runtime=nonexistent",
            headers=self.shared_header,
        )
        self.assertEqual(resp.status_code, 404)

    def test_put_normalizes_and_persists(self):
        resp = self.client.put(
            "/api/v1/settings/model-manifest",
            headers=self.shared_header,
            json={
                "runtime": "codex",
                "models": ["gpt-5.6", "  gpt-5.6-luna  ", "", "gpt-5.6", "gpt-5.5"],
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["models"], ["gpt-5.6", "gpt-5.6-luna", "gpt-5.5"])

        # Persisted to disk, and other runtimes untouched.
        on_disk = self._read_manifest()
        self.assertEqual(
            on_disk["runtimes"]["codex"], ["gpt-5.6", "gpt-5.6-luna", "gpt-5.5"]
        )
        self.assertEqual(on_disk["runtimes"]["claude"], ["sonnet", "haiku"])
        self.assertEqual(on_disk["runtimes"]["wee"], ["ollama/gemma4:e4b"])

    def test_put_wee_is_rejected(self):
        resp = self.client.put(
            "/api/v1/settings/model-manifest",
            headers=self.shared_header,
            json={"runtime": "wee", "models": ["something"]},
        )
        self.assertEqual(resp.status_code, 400)

    def test_put_empty_models_rejected(self):
        resp = self.client.put(
            "/api/v1/settings/model-manifest",
            headers=self.shared_header,
            json={"runtime": "claude", "models": ["   ", ""]},
        )
        self.assertEqual(resp.status_code, 400)

    def test_put_unconfigured_runtime_rejected(self):
        resp = self.client.put(
            "/api/v1/settings/model-manifest",
            headers=self.shared_header,
            json={"runtime": "nonexistent", "models": ["foo"]},
        )
        self.assertEqual(resp.status_code, 400)

    def test_issue442_put_leaves_no_stray_tmp_file_on_success(self):
        """PUT must write through a temp file and rename it into place,
        never leaving a `.model-manifest.json.*.tmp` artifact behind."""
        resp = self.client.put(
            "/api/v1/settings/model-manifest",
            headers=self.shared_header,
            json={"runtime": "codex", "models": ["gpt-5.5"]},
        )
        self.assertEqual(resp.status_code, 200)

        manifest_dir = os.path.dirname(self.manifest_path)
        leftovers = [
            name
            for name in os.listdir(manifest_dir)
            if name.startswith(".model-manifest.json.") and name.endswith(".tmp")
        ]
        self.assertEqual(leftovers, [])

    def test_issue442_put_failure_does_not_corrupt_existing_manifest(self):
        """If the atomic rename step fails, the manifest on disk must be left
        exactly as it was -- never a half-written file."""
        from unittest.mock import patch

        original_on_disk = self._read_manifest()

        with patch("agent_manager.os.replace", side_effect=OSError("simulated failure")):
            resp = self.client.put(
                "/api/v1/settings/model-manifest",
                headers=self.shared_header,
                json={"runtime": "codex", "models": ["gpt-5.5-broken"]},
            )
        self.assertEqual(resp.status_code, 500)

        # Untouched: still valid JSON, still the pre-failure content.
        self.assertEqual(self._read_manifest(), original_on_disk)

        manifest_dir = os.path.dirname(self.manifest_path)
        leftovers = [
            name
            for name in os.listdir(manifest_dir)
            if name.startswith(".model-manifest.json.") and name.endswith(".tmp")
        ]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
