"""Tests for model-manifest.json as the model-list source of truth."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_manager
from agent_manager import SessionManager


class TestModelManifestSource(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.config_file = self.temp_path / "agents.json"
        self.config_file.write_text(
            json.dumps(
                {
                    "agents": [
                        {
                            "name": "wee-dev",
                            "dispatch_config": {
                                "runtime": "copilot",
                                "model": "dispatch-default-model",
                            },
                        }
                    ]
                }
            )
        )
        self.manager = SessionManager(str(self.config_file))

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_manifest(self, runtimes):
        manifest_path = self.temp_path / "model-manifest.json"
        manifest_path.write_text(json.dumps({"runtimes": runtimes}))
        return manifest_path

    def test_manifest_models_drive_copilot_listing(self):
        manifest_path = self._write_manifest(
            {"copilot": ["manifest-claude", "manifest-gpt"]}
        )

        with patch.object(agent_manager, "MODEL_MANIFEST_PATH", manifest_path):
            result = self.manager.get_models_for_runtime("copilot")

        all_models = [m for group in result.values() for m in group]
        self.assertEqual(all_models, ["manifest-claude", "manifest-gpt"])

    def test_manifest_models_drive_api_labels(self):
        manifest_path = self._write_manifest({"codex": ["gpt-5.5"]})

        with patch.object(agent_manager, "MODEL_MANIFEST_PATH", manifest_path):
            result = self.manager.get_models_for_runtime("codex")
            label = self.manager._get_model_description("gpt-5.5", "codex")

        self.assertEqual(result, {"GPT Models": ["gpt-5.5"]})
        self.assertEqual(label, "GPT 5.5")

    def test_missing_manifest_falls_back_to_dispatch_default_for_runtime_default(self):
        missing_manifest = self.temp_path / "missing-model-manifest.json"

        with patch.object(agent_manager, "MODEL_MANIFEST_PATH", missing_manifest):
            result = self.manager._runtime_default_model("copilot")

        self.assertEqual(result, "dispatch-default-model")

    def test_manifest_runtime_alias_supports_copilot_sdk(self):
        manifest_path = self._write_manifest({"copilot": ["gpt-5.5"]})

        with patch.object(agent_manager, "MODEL_MANIFEST_PATH", manifest_path):
            result = self.manager.get_models_for_runtime("copilot-sdk")

        self.assertEqual(result, {"GPT Models": ["gpt-5.5"]})


if __name__ == "__main__":
    unittest.main()
