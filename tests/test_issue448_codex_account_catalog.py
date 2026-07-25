"""
Regression tests for issue #448: the Codex catalog collapsed to a single
"default" entry whenever the local Codex CLI was authenticated with a ChatGPT
account, so codex was the only runtime offering no model choice at all.

Measured against Codex CLI v0.144.1 with ChatGPT auth:
  * `codex exec -m gpt-5.6-luna ...`  -> works
  * `codex exec -m gpt-5.6 ...`       -> 400 invalid_request_error
      "The 'gpt-5.6' model is not supported when using Codex with a ChatGPT
       account."

So rejecting *unsupported* models is correct; suppressing the supported ones is
not. The account's own list lives in ~/.codex/models_cache.json, which is what
the CLI itself consults.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_SHARED_KEY", "test_key_448")
os.environ.setdefault("APP_ENV", "DEV")
os.environ.setdefault("API_PORT", "9448")

from agent_manager import SessionManager  # noqa: E402


# Mirrors the real cache shape, including the internal hidden entry.
CACHE_FIXTURE = {
    "fetched_at": "2026-07-25T00:27:11.151102Z",
    "client_version": "0.146.0",
    "models": [
        {"slug": "gpt-5.6-sol", "display_name": "GPT-5.6-Sol", "visibility": "list"},
        {"slug": "gpt-5.6-terra", "display_name": "GPT-5.6-Terra", "visibility": "list"},
        {"slug": "gpt-5.6-luna", "display_name": "GPT-5.6-Luna", "visibility": "list"},
        {"slug": "gpt-5.5", "display_name": "GPT-5.5", "visibility": "list"},
        {"slug": "gpt-5.4", "display_name": "GPT-5.4", "visibility": "list"},
        {"slug": "gpt-5.4-mini", "display_name": "GPT-5.4-Mini", "visibility": "list"},
        # Internal: must never be offered to users.
        {"slug": "codex-auto-review", "display_name": "Codex Auto Review", "visibility": "hide"},
    ],
}

LISTABLE = [
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
]


def _make_sm():
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump({"agents": []}, tmp)
    tmp.close()
    return SessionManager(tmp.name)


class TestIssue448CodexAccountCatalog(unittest.TestCase):
    def setUp(self):
        self.sm = _make_sm()
        self.home = tempfile.mkdtemp()
        codex_dir = Path(self.home) / ".codex"
        codex_dir.mkdir(parents=True, exist_ok=True)
        self.cache_path = codex_dir / "models_cache.json"
        self.cache_path.write_text(json.dumps(CACHE_FIXTURE), encoding="utf-8")

    def test_account_models_read_from_cache_excluding_hidden(self):
        with patch.object(Path, "home", return_value=Path(self.home)):
            models = self.sm._codex_account_models()

        self.assertEqual(models, LISTABLE)
        self.assertNotIn(
            "codex-auto-review",
            models,
            'visibility="hide" entries are internal and must not be offered',
        )

    def test_catalog_offers_account_models_not_just_default(self):
        """The core regression: catalog must not collapse to ["default"]."""
        with patch.object(Path, "home", return_value=Path(self.home)), patch.object(
            self.sm, "_codex_uses_chatgpt_account", return_value=True
        ):
            catalog = self.sm.fetch_codex_models()

        offered = catalog["Codex CLI"]
        self.assertNotEqual(
            offered, ["default"], "catalog still collapses to default (issue #448)"
        )
        self.assertEqual(offered[0], "default", '"default" should remain selectable first')
        for slug in LISTABLE:
            self.assertIn(slug, offered, f"account model {slug} missing from catalog")

    def test_catalog_falls_back_to_default_when_cache_unreadable(self):
        self.cache_path.unlink()
        with patch.object(Path, "home", return_value=Path(self.home)), patch.object(
            self.sm, "_codex_uses_chatgpt_account", return_value=True
        ):
            catalog = self.sm.fetch_codex_models()

        self.assertEqual(catalog["Codex CLI"], ["default"])

    def test_malformed_cache_is_tolerated(self):
        self.cache_path.write_text("{not json", encoding="utf-8")
        with patch.object(Path, "home", return_value=Path(self.home)):
            self.assertIsNone(self.sm._codex_account_models())

    def test_supported_model_is_not_silently_replaced_by_default(self):
        """A model the account carries must reach the CLI, or selecting it does nothing."""
        source = __import__("inspect").getsource(self.sm.run_codex)
        self.assertIn(
            "_codex_account_models",
            source,
            "run_codex must consult the account model list before overriding "
            "a selected model, otherwise the #448 catalog fix is defeated by "
            "the guard blanking every model",
        )


if __name__ == "__main__":
    unittest.main()
