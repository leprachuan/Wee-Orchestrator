"""
Regression coverage for issue #451: a Wee turn against Ollama that produces no
usable content must say *why*.

Root cause established on the dev server. The Copilot SDK sends a ~25 KB agent
prompt. Measured against Ollama on 192.168.1.101:

    gemma4:e4b              -> 1 character   (no num_ctx in its Modelfile)
    gemma4-e2b-128k:latest  -> 248 characters (num_ctx 131072)
    gpt-oss:64k             -> 321 characters
    nemotron-3-nano:128k    -> 208 characters

The request does not fit the model's allocated context, so Ollama has no room
left to generate and stops after roughly one token. It is *not* a transport
fault: a direct curl to the same /v1/chat/completions endpoint returns 65 SSE
chunks and the full answer.

The discriminator is the `num_ctx` parameter baked into the Modelfile, not the
architecture's context length — Ollama reports `gemma4.context_length = 131072`
for both the working and failing models, so only `num_ctx` distinguishes them.

The previous message ("Ollama Copilot SDK returned an unusably short response")
blamed response length, which sent this investigation down two dead ends.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wee_copilot_sdk import describe_degenerate_ollama_turn  # noqa: E402

PROMPT_CHARS = 25_021  # measured on the wire


class TestIssue451OllamaContextDiagnosis(unittest.TestCase):
    def test_missing_num_ctx_is_named_as_the_cause(self):
        message = describe_degenerate_ollama_turn("gemma4:e4b", PROMPT_CHARS, None)

        self.assertIn("gemma4:e4b", message)
        self.assertIn("num_ctx", message)
        self.assertIn("OLLAMA_CONTEXT_LENGTH", message, "must state how to fix it")
        self.assertNotIn(
            "unusably short",
            message,
            "must not blame response length, which is the symptom not the cause",
        )

    def test_small_num_ctx_is_reported_with_its_value(self):
        message = describe_degenerate_ollama_turn("gemma4:e4b", PROMPT_CHARS, 4096)

        self.assertIn("num_ctx=4096", message)
        self.assertIn("too small", message)

    def test_adequate_num_ctx_points_elsewhere_instead_of_misdiagnosing(self):
        """Don't assert a context problem when the context is fine."""
        message = describe_degenerate_ollama_turn("gpt-oss:64k", PROMPT_CHARS, 65_536)

        self.assertIn("num_ctx=65536", message)
        self.assertIn("adequate", message)
        self.assertIn("elsewhere", message)
        self.assertNotIn("too small", message)

    def test_prompt_size_is_always_included(self):
        """The prompt size is half the diagnosis; it must never be omitted."""
        for num_ctx in (None, 4096, 65_536):
            with self.subTest(num_ctx=num_ctx):
                message = describe_degenerate_ollama_turn("m", PROMPT_CHARS, num_ctx)
                self.assertIn(str(PROMPT_CHARS), message)

    def test_boundary_between_too_small_and_adequate(self):
        self.assertIn("too small", describe_degenerate_ollama_turn("m", 1, 16_383))
        self.assertIn("adequate", describe_degenerate_ollama_turn("m", 1, 16_384))


class TestNumCtxProbeIsDefensive(unittest.TestCase):
    """The probe runs on an already-failing path; it must never raise."""

    def test_unreachable_host_reports_probe_failure_not_a_verdict(self):
        from wee_copilot_sdk import _ollama_num_ctx_probe

        # Port 1 is reserved and never listening.
        self.assertEqual(
            _ollama_num_ctx_probe("http://127.0.0.1:1/v1", "any-model"), (False, None)
        )

    def test_blank_base_url_reports_probe_failure(self):
        from wee_copilot_sdk import _ollama_num_ctx_probe

        self.assertEqual(_ollama_num_ctx_probe("", "any-model"), (False, None))
        self.assertEqual(_ollama_num_ctx_probe("/v1", "any-model"), (False, None))


class TestShortRepliesAreNotDiscardedWhenTheContextIsFine(unittest.TestCase):
    """The `len < 4` rule discarded correct answers: "391", "OK", "42", "Yes".

    Observed on dev: with gpt-oss:64k (num_ctx=65536, i.e. ample) the question
    "What is 17 times 23? Reply with only the number." produced the correct
    "391" and was rejected as unusable.
    """

    def test_short_reply_is_degenerate_only_when_context_explains_it(self):
        from wee_copilot_sdk import short_ollama_reply_is_degenerate as degenerate

        # No num_ctx / too small -> the reply is breakage.
        self.assertTrue(degenerate(None))
        self.assertTrue(degenerate(2048))
        self.assertTrue(degenerate(4096))
        # Ample context -> a terse reply is plausibly the real answer.
        self.assertFalse(degenerate(65_536))
        self.assertFalse(degenerate(131_072))

    def test_boundary_matches_the_documented_threshold(self):
        from wee_copilot_sdk import (
            ADEQUATE_NUM_CTX,
            short_ollama_reply_is_degenerate as degenerate,
        )

        self.assertTrue(degenerate(ADEQUATE_NUM_CTX - 1))
        self.assertFalse(degenerate(ADEQUATE_NUM_CTX))


class TestPreflightAvoidsWastingATurn(unittest.TestCase):
    """#421/#451: catch an unusable context before spending the turn."""

    def setUp(self):
        import wee_copilot_sdk

        self.mod = wee_copilot_sdk
        self.mod._NUM_CTX_CACHE.clear()

    def test_probe_result_is_cached_per_model(self):
        calls = []

        def fake(base_url, model):
            calls.append((base_url, model))
            return (True, 4096)

        original = self.mod._ollama_num_ctx_probe
        self.mod._ollama_num_ctx_probe = fake
        try:
            for _ in range(3):
                self.assertEqual(
                    self.mod.ollama_context_probe("http://h/v1", "m"), (True, 4096)
                )
            self.assertEqual(len(calls), 1, "must probe once per model, not per turn")
            self.mod.ollama_context_probe("http://h/v1", "other")
            self.assertEqual(len(calls), 2)
        finally:
            self.mod._ollama_num_ctx_probe = original

    def test_failed_probe_is_cached_so_a_dead_host_is_probed_once(self):
        calls = []

        def fake(base_url, model):
            calls.append(1)
            return (False, None)

        original = self.mod._ollama_num_ctx_probe
        self.mod._ollama_num_ctx_probe = fake
        try:
            self.assertEqual(
                self.mod.ollama_context_probe("http://dead/v1", "m"), (False, None)
            )
            self.mod.ollama_context_probe("http://dead/v1", "m")
            self.assertEqual(len(calls), 1)
        finally:
            self.mod._ollama_num_ctx_probe = original

    def test_model_declaring_no_num_ctx_is_refused_not_waved_through(self):
        """The regression this tri-state exists to prevent.

        gemma4:e4b declares no num_ctx, so Ollama uses its small default and the
        turn degenerates. A probe that merely returned None made that
        indistinguishable from "host unreachable", so pre-flight failed open and
        the user received assembled fragments ("AIAs") instead of a diagnosis.
        """
        from wee_copilot_sdk import short_ollama_reply_is_degenerate as degenerate

        probed, num_ctx = (True, None)  # asked successfully; no num_ctx declared
        self.assertTrue(probed and degenerate(num_ctx))

        probed, num_ctx = (False, None)  # could not ask -> must fail open
        self.assertFalse(probed and degenerate(num_ctx))

    def test_preflight_is_wired_in_before_the_turn_and_fails_open(self):
        import inspect

        source = inspect.getsource(self.mod.execute_wee_copilot_async)
        preflight_at = source.index("ollama_context_probe")
        send_at = source.index("send_and_wait(prompt")
        self.assertLess(
            preflight_at, send_at, "the check must run before the turn is spent"
        )
        # Fails open: only a known-bad num_ctx raises.
        self.assertIn("probed and short_ollama_reply_is_degenerate", source)


if __name__ == "__main__":
    unittest.main()
