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

    def test_unreachable_host_returns_none(self):
        from wee_copilot_sdk import _ollama_num_ctx

        # Port 1 is reserved and never listening.
        self.assertIsNone(_ollama_num_ctx("http://127.0.0.1:1/v1", "any-model"))

    def test_blank_base_url_returns_none(self):
        from wee_copilot_sdk import _ollama_num_ctx

        self.assertIsNone(_ollama_num_ctx("", "any-model"))
        self.assertIsNone(_ollama_num_ctx("/v1", "any-model"))


if __name__ == "__main__":
    unittest.main()
