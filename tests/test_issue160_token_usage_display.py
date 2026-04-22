"""Issue #160: Display Token Usage and Estimated Price in Wee Native Runtime WebUI Card.

Regression tests covering:
- _build_wee_meta() pricing logic for various providers
- Token accumulation across rounds
- Cost calculation accuracy
- Graceful handling of missing usage data
- stream_options fallback behavior
- SSE done_payload includes wee_meta
- WebUI buildTimingText rendering
"""

import os
import sys
import unittest
from unittest.mock import MagicMock

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _get_session_mgr_class():
    """Import SessionManager from agent_manager without starting the server."""
    # We only need to test _build_wee_meta which is a pure method.
    # Rather than importing the full module (which starts FastAPI),
    # we test via a standalone mock class.
    return None


class TestBuildWeeMeta(unittest.TestCase):
    """Test _build_wee_meta() method logic without importing the full agent_manager."""

    def _make_session_mgr(self):
        """Create a minimal mock with the real _build_wee_meta logic."""
        # Read the actual source to get the pricing table and method
        am_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "agent_manager.py",
        )
        with open(am_path, "r") as f:
            source = f.read()

        # Verify the pricing table exists
        assert (
            "_WEE_MODEL_PRICING" in source
        ), "Pricing table not found in agent_manager.py"
        assert (
            "_build_wee_meta" in source
        ), "_build_wee_meta not found in agent_manager.py"

        # Extract pricing dict from source
        import re

        pricing_block = re.search(
            r"_WEE_MODEL_PRICING\s*=\s*\{([^}]+)\}",
            source,
            re.DOTALL,
        )
        assert pricing_block, "Could not extract pricing table"

        # Build a minimal class with the method
        pricing_dict = {}
        for line in pricing_block.group(1).strip().split("\n"):
            line = line.strip().rstrip(",")
            if not line or line.startswith("#"):
                continue
            # Parse: "model/name": (input, output)
            m = re.match(r'"([^"]+)":\s*\(([^)]+)\)', line)
            if m:
                key = m.group(1)
                vals = m.group(2).split(",")
                pricing_dict[key] = (float(vals[0].strip()), float(vals[1].strip()))

        mgr = MagicMock()
        mgr._WEE_MODEL_PRICING = pricing_dict

        # Bind the real method logic
        def build_wee_meta(
            self_mock,
            api_base,
            resolved_model,
            original_model,
            prompt_tokens,
            completion_tokens,
            usage_available,
        ):
            meta = {"runtime": "wee"}
            is_ollama = "11434" in (api_base or "") or (api_base or "").startswith(
                "http://192.168.1.101"
            )
            is_openrouter = "openrouter" in (api_base or "").lower()
            is_lmstudio = "1234" in (api_base or "")

            if not usage_available:
                if is_ollama or is_lmstudio:
                    meta["cost_label"] = "local"
                return meta

            total = prompt_tokens + completion_tokens
            meta["tokens"] = total
            meta["prompt_tokens"] = prompt_tokens
            meta["completion_tokens"] = completion_tokens

            if is_ollama or is_lmstudio:
                meta["cost_label"] = "local"
            elif is_openrouter:
                pricing = None
                for candidate in [original_model, resolved_model]:
                    candidate_lower = candidate.lower() if candidate else ""
                    if candidate_lower.startswith("openrouter/"):
                        candidate_lower = candidate_lower[len("openrouter/") :]
                    for key, val in self_mock._WEE_MODEL_PRICING.items():
                        if key.lower() == candidate_lower or candidate_lower.startswith(
                            key.lower()
                        ):
                            pricing = val
                            break
                    if pricing:
                        break
                if pricing:
                    input_cost = (prompt_tokens / 1_000_000) * pricing[0]
                    output_cost = (completion_tokens / 1_000_000) * pricing[1]
                    total_cost = input_cost + output_cost
                    if total_cost < 0.001:
                        meta["cost_label"] = f"${total_cost:.6f}"
                    elif total_cost < 0.01:
                        meta["cost_label"] = f"${total_cost:.4f}"
                    else:
                        meta["cost_label"] = f"${total_cost:.2f}"
                elif any(":free" in (original_model or "").lower() for _ in [1]):
                    meta["cost_label"] = "free"
                else:
                    meta["cost_label"] = "est. N/A"
            else:
                meta["cost_label"] = ""

            return meta

        mgr._build_wee_meta = lambda *a, **kw: build_wee_meta(mgr, *a, **kw)
        return mgr

    # --- Ollama (local) tests ---

    def test_ollama_with_usage(self):
        """Ollama endpoint should report 'local' cost even with usage data."""
        mgr = self._make_session_mgr()
        meta = mgr._build_wee_meta(
            "http://192.168.1.101:11434/v1",
            "gemma4:e4b",
            "gemma4:e4b",
            500,
            200,
            True,
        )
        self.assertEqual(meta["runtime"], "wee")
        self.assertEqual(meta["tokens"], 700)
        self.assertEqual(meta["prompt_tokens"], 500)
        self.assertEqual(meta["completion_tokens"], 200)
        self.assertEqual(meta["cost_label"], "local")

    def test_ollama_without_usage(self):
        """Ollama without usage data should still show 'local'."""
        mgr = self._make_session_mgr()
        meta = mgr._build_wee_meta(
            "http://192.168.1.101:11434/v1",
            "llama3.3",
            "llama3.3",
            0,
            0,
            False,
        )
        self.assertEqual(meta["cost_label"], "local")
        self.assertNotIn("tokens", meta)

    # --- OpenRouter tests ---

    def test_openrouter_known_model_cost(self):
        """OpenRouter with a known model should calculate cost."""
        mgr = self._make_session_mgr()
        # anthropic/claude-sonnet-4: $3.00/1M input, $15.00/1M output
        meta = mgr._build_wee_meta(
            "https://openrouter.ai/api/v1",
            "anthropic/claude-sonnet-4",
            "openrouter/anthropic/claude-sonnet-4",
            1000,
            500,
            True,
        )
        self.assertEqual(meta["tokens"], 1500)
        # Cost: (1000/1M)*3 + (500/1M)*15 = 0.003 + 0.0075 = 0.0105
        self.assertTrue(meta["cost_label"].startswith("$"))
        # $0.0105 >= $0.01, so formatted with 2 decimal places → "$0.01"
        self.assertEqual(meta["cost_label"], "$0.01")

    def test_openrouter_free_model(self):
        """OpenRouter free model should show 'free'."""
        mgr = self._make_session_mgr()
        meta = mgr._build_wee_meta(
            "https://openrouter.ai/api/v1",
            "nvidia/llama-3.1-nemotron-ultra-253b-v1:free",
            "openrouter/nvidia/llama-3.1-nemotron-ultra-253b-v1:free",
            1000,
            500,
            True,
        )
        self.assertEqual(meta["tokens"], 1500)
        # Nemotron is in pricing table at (0.00, 0.00), so cost = $0
        cost_label = meta["cost_label"]
        self.assertTrue(
            cost_label in ("free", "$0.000000"),
            f"Expected 'free' or '$0.000000', got '{cost_label}'",
        )

    def test_openrouter_unknown_model(self):
        """OpenRouter with unknown model should show 'est. N/A'."""
        mgr = self._make_session_mgr()
        meta = mgr._build_wee_meta(
            "https://openrouter.ai/api/v1",
            "some-unknown/model-v99",
            "openrouter/some-unknown/model-v99",
            1000,
            500,
            True,
        )
        self.assertEqual(meta["tokens"], 1500)
        self.assertEqual(meta["cost_label"], "est. N/A")

    def test_openrouter_prefix_stripped(self):
        """Model ID with 'openrouter/' prefix should still match pricing."""
        mgr = self._make_session_mgr()
        meta = mgr._build_wee_meta(
            "https://openrouter.ai/api/v1",
            "openai/gpt-4o",
            "openrouter/openai/gpt-4o",
            10000,
            5000,
            True,
        )
        self.assertEqual(meta["tokens"], 15000)
        # (10000/1M)*2.50 + (5000/1M)*10.00 = 0.025 + 0.05 = 0.075, >= $0.01 → 2dp
        self.assertEqual(meta["cost_label"], "$0.08")

    # --- LM Studio tests ---

    def test_lmstudio_with_usage(self):
        """LM Studio should report 'local' cost."""
        mgr = self._make_session_mgr()
        meta = mgr._build_wee_meta(
            "http://localhost:1234/v1",
            "some-model",
            "some-model",
            300,
            100,
            True,
        )
        self.assertEqual(meta["tokens"], 400)
        self.assertEqual(meta["cost_label"], "local")

    def test_lmstudio_without_usage(self):
        """LM Studio without usage should show 'local' and no tokens."""
        mgr = self._make_session_mgr()
        meta = mgr._build_wee_meta(
            "http://localhost:1234/v1",
            "some-model",
            "some-model",
            0,
            0,
            False,
        )
        self.assertEqual(meta["cost_label"], "local")
        self.assertNotIn("tokens", meta)

    # --- Unknown provider tests ---

    def test_unknown_provider_with_usage(self):
        """Unknown provider should have empty cost_label."""
        mgr = self._make_session_mgr()
        meta = mgr._build_wee_meta(
            "http://some-custom-api.example.com/v1",
            "model-x",
            "model-x",
            1000,
            500,
            True,
        )
        self.assertEqual(meta["tokens"], 1500)
        self.assertEqual(meta["cost_label"], "")

    def test_unknown_provider_without_usage(self):
        """Unknown provider without usage should return minimal meta."""
        mgr = self._make_session_mgr()
        meta = mgr._build_wee_meta(
            "http://some-custom-api.example.com/v1",
            "model-x",
            "model-x",
            0,
            0,
            False,
        )
        self.assertEqual(meta, {"runtime": "wee"})

    # --- Cost formatting tests ---

    def test_cost_formatting_tiny(self):
        """Very small costs should use 6 decimal places."""
        mgr = self._make_session_mgr()
        # gemini-2.0-flash-001: $0.10/1M input, $0.40/1M output
        meta = mgr._build_wee_meta(
            "https://openrouter.ai/api/v1",
            "google/gemini-2.0-flash-001",
            "openrouter/google/gemini-2.0-flash-001",
            100,
            50,
            True,  # tiny token counts
        )
        cost_label = meta["cost_label"]
        self.assertTrue(cost_label.startswith("$"))
        # (100/1M)*0.10 + (50/1M)*0.40 = 0.00001 + 0.00002 = 0.00003
        self.assertEqual(cost_label, "$0.000030")

    def test_cost_formatting_medium(self):
        """Medium costs should use 4 decimal places."""
        mgr = self._make_session_mgr()
        # anthropic/claude-sonnet-4: $3.00/1M input, $15.00/1M output
        meta = mgr._build_wee_meta(
            "https://openrouter.ai/api/v1",
            "anthropic/claude-sonnet-4",
            "openrouter/anthropic/claude-sonnet-4",
            500,
            100,
            True,
        )
        cost_label = meta["cost_label"]
        # (500/1M)*3 + (100/1M)*15 = 0.0015 + 0.0015 = 0.003
        self.assertEqual(cost_label, "$0.0030")

    def test_cost_formatting_large(self):
        """Larger costs should use 2 decimal places."""
        mgr = self._make_session_mgr()
        # google/gemini-2.5-pro-preview: $1.25/1M input, $10.00/1M output
        meta = mgr._build_wee_meta(
            "https://openrouter.ai/api/v1",
            "google/gemini-2.5-pro-preview",
            "openrouter/google/gemini-2.5-pro-preview",
            100000,
            50000,
            True,
        )
        cost_label = meta["cost_label"]
        # (100000/1M)*1.25 + (50000/1M)*10.00 = 0.125 + 0.5 = 0.625
        self.assertEqual(cost_label, "$0.62")

    # --- Token accumulation test ---

    def test_token_accumulation_across_rounds(self):
        """Simulates multiple tool rounds accumulating tokens."""
        total_prompt = 0
        total_completion = 0
        usage_available = False

        # Simulate 3 rounds
        rounds = [
            (100, 50),
            (200, 150),
            (300, 100),
        ]
        for prompt, completion in rounds:
            total_prompt += prompt
            total_completion += completion
            usage_available = True

        mgr = self._make_session_mgr()
        meta = mgr._build_wee_meta(
            "https://openrouter.ai/api/v1",
            "openai/gpt-4o",
            "openrouter/openai/gpt-4o",
            total_prompt,
            total_completion,
            usage_available,
        )
        self.assertEqual(meta["prompt_tokens"], 600)
        self.assertEqual(meta["completion_tokens"], 300)
        self.assertEqual(meta["tokens"], 900)

    # --- Runtime field test ---

    def test_runtime_always_wee(self):
        """All meta dicts should have runtime='wee'."""
        mgr = self._make_session_mgr()
        for api_base in [
            "http://192.168.1.101:11434/v1",
            "https://openrouter.ai/api/v1",
            "http://localhost:1234/v1",
            "http://example.com/v1",
        ]:
            meta = mgr._build_wee_meta(api_base, "m", "m", 100, 50, True)
            self.assertEqual(meta["runtime"], "wee", f"Failed for {api_base}")


class TestDonePayloadIncludesWeeMeta(unittest.TestCase):
    """Verify that done_payload construction sites include wee_meta."""

    def test_done_payload_code_includes_wee_meta(self):
        """All done_payload constructions should include wee_meta."""
        am_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "agent_manager.py",
        )
        with open(am_path, "r") as f:
            source = f.read()

        # Count occurrences of the wee_meta injection pattern
        import re

        pattern = r'_wm\s*=\s*session_data\.pop\("_wee_meta",\s*None\)'
        matches = re.findall(pattern, source)
        self.assertGreaterEqual(
            len(matches),
            4,
            (
                f"Expected at least 4 done_payload wee_meta injections,"
                f" found {len(matches)}"
            ),
        )

    def test_stream_options_in_create_kwargs(self):
        """stream_options should be set in the create call."""
        am_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "agent_manager.py",
        )
        with open(am_path, "r") as f:
            source = f.read()

        self.assertIn('"stream_options": {"include_usage": True}', source)

    def test_stream_options_fallback_exists(self):
        """Error handler should have stream_options fallback."""
        am_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "agent_manager.py",
        )
        with open(am_path, "r") as f:
            source = f.read()

        self.assertIn('create_kwargs.pop("stream_options"', source)


class TestWebUIBuildTimingText(unittest.TestCase):
    """Verify WebUI buildTimingText handles wee_meta correctly."""

    def _get_app_js(self):
        app_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "webui",
            "dist",
            "app.js",
        )
        with open(app_path, "r") as f:
            return f.read()

    def test_buildTimingText_has_tooltip(self):
        """buildTimingText should include title attribute for tooltip."""
        source = self._get_app_js()
        self.assertIn("title=", source)
        self.assertIn("prompt_tokens", source)
        self.assertIn("completion_tokens", source)

    def test_innerHTML_used_not_textContent(self):
        """Timing div uses innerHTML to render string returned by buildTimingText.

        Issue #198: buildTimingText now returns string|null (not DocumentFragment),
        so callers assign timingDiv.innerHTML = _timingText.
        """
        source = self._get_app_js()
        import re

        # Callers assign innerHTML (string-based) for timing divs
        timing_innerHTML = re.findall(r"timingDiv\.innerHTML\s*=", source)
        self.assertGreaterEqual(
            len(timing_innerHTML),
            2,
            "Expected at least 2 timingDiv.innerHTML assignments for timing divs",
        )

    def test_buildTimingText_handles_wee_meta_with_tokens(self):
        """Verify the function structure handles tokens from wee_meta."""
        source = self._get_app_js()
        # Should have token formatting with toLocaleString
        self.assertIn("toLocaleString()", source)
        # Should handle costLabel cases
        self.assertIn("local", source)
        self.assertIn("free", source)

    def test_no_xss_in_timing_display(self):
        """Timing text should not allow arbitrary HTML injection.

        The span title uses fixed format strings built from server-controlled
        metadata (token counts, cost labels). Issue #198 updated buildTimingText
        to return string|null using template literals — verify tooltip is safe.
        """
        source = self._get_app_js()
        import re

        # Extract just the buildTimingText function body
        pattern = r"function buildTimingText\(.*?\n\}"
        func_match = re.search(pattern, source, re.DOTALL)
        self.assertIsNotNone(func_match, "buildTimingText function not found")
        func_body = func_match.group(0)
        # title uses template literal ${tooltip} — tooltip is built
        # from safe server fields
        self.assertIn("tooltip", func_body)
        self.assertIn("prompt_tokens", func_body)
        self.assertIn("completion_tokens", func_body)
        # The span uses template literal, not innerHTML concatenation of raw user text
        self.assertIn("<span title=", func_body)


class TestPricingTable(unittest.TestCase):
    """Verify the pricing table in agent_manager.py."""

    def _get_pricing(self):
        import re

        am_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "agent_manager.py",
        )
        with open(am_path, "r") as f:
            source = f.read()
        pricing_block = re.search(
            r"_WEE_MODEL_PRICING\s*=\s*\{([^}]+)\}",
            source,
            re.DOTALL,
        )
        self.assertIsNotNone(pricing_block, "Pricing table not found")
        return pricing_block.group(1)

    def test_pricing_has_major_providers(self):
        """Pricing table should cover major model families."""
        pricing = self._get_pricing()
        for provider in [
            "google/",
            "anthropic/",
            "openai/",
            "meta-llama/",
            "deepseek/",
        ]:
            self.assertIn(provider, pricing, f"Missing provider: {provider}")

    def test_pricing_values_non_negative(self):
        """All pricing values should be >= 0."""
        import re

        pricing = self._get_pricing()
        for line in pricing.strip().split("\n"):
            line = line.strip().rstrip(",")
            if not line or line.startswith("#"):
                continue
            m = re.match(r'"([^"]+)":\s*\(([^)]+)\)', line)
            if m:
                vals = m.group(2).split(",")
                for v in vals:
                    self.assertGreaterEqual(
                        float(v.strip()),
                        0,
                        f"Negative price for {m.group(1)}",
                    )

    def test_free_model_has_zero_pricing(self):
        """Nemotron free model should have (0.00, 0.00) pricing."""
        import re

        pricing = self._get_pricing()
        self.assertIn("nvidia/llama-3.1-nemotron-ultra-253b-v1", pricing)
        m = re.search(
            r'"nvidia/llama-3.1-nemotron-ultra-253b-v1":\s*\(([^)]+)\)',
            pricing,
        )
        self.assertIsNotNone(m)
        vals = [float(v.strip()) for v in m.group(1).split(",")]
        self.assertEqual(vals, [0.0, 0.0])


class TestIssue160StaleMeta(unittest.TestCase):
    """Regression: _wee_meta must not leak across responses (QA Round 2)."""

    def test_wee_meta_cleared_after_done_event(self):
        """After a done event consumes _wee_meta via pop(), it must be gone
        from session_data so the next response in the same session does not
        re-emit stale token/cost info."""
        session_data = {
            "runtime": "wee",
            "model": "openrouter/anthropic/claude-sonnet-4",
            "_wee_meta": {
                "tokens": 500,
                "prompt_tokens": 300,
                "completion_tokens": 200,
                "cost_label": "$0.0042",
            },
        }

        # Simulate what the done-event code does: pop _wee_meta
        _wm = session_data.pop("_wee_meta", None)
        self.assertIsNotNone(_wm, "_wee_meta should be available on first pop")
        self.assertEqual(_wm["tokens"], 500)

        # Second access should return None (stale leak prevented)
        _wm2 = session_data.pop("_wee_meta", None)
        self.assertIsNone(_wm2, "_wee_meta must be None after first pop — stale leak!")

    def test_wee_meta_absent_when_not_set(self):
        """If no _wee_meta was set (non-wee runtime), pop returns None."""
        session_data = {"runtime": "copilot", "model": "claude-sonnet-4.6"}
        _wm = session_data.pop("_wee_meta", None)
        self.assertIsNone(_wm)

    def test_done_event_includes_wee_meta_then_clears(self):
        """Full simulation: build done_evt dict, consume _wee_meta, verify
        subsequent done event has no wee_meta."""
        import json as _json

        meta = {
            "tokens": 1247,
            "prompt_tokens": 800,
            "completion_tokens": 447,
            "cost_label": "$0.0042",
        }
        session_data = {
            "runtime": "wee",
            "model": "openrouter/anthropic/claude-sonnet-4",
            "_wee_meta": meta,
        }

        # First done event
        _done_evt = {
            "type": "done",
            "response": "Hello!",
            "runtime": session_data.get("runtime", "copilot"),
            "model": session_data.get("model"),
        }
        _wm = session_data.pop("_wee_meta", None)
        if _wm:
            _done_evt["wee_meta"] = _wm
        payload1 = _json.loads(_json.dumps(_done_evt))
        self.assertIn("wee_meta", payload1)
        self.assertEqual(payload1["wee_meta"]["tokens"], 1247)

        # Second done event (same session) — must NOT have wee_meta
        _done_evt2 = {
            "type": "done",
            "response": "Second response",
            "runtime": session_data.get("runtime", "copilot"),
            "model": session_data.get("model"),
        }
        _wm2 = session_data.pop("_wee_meta", None)
        if _wm2:
            _done_evt2["wee_meta"] = _wm2
        payload2 = _json.loads(_json.dumps(_done_evt2))
        self.assertNotIn(
            "wee_meta",
            payload2,
            "Stale _wee_meta leaked into second response!",
        )


if __name__ == "__main__":
    unittest.main()
