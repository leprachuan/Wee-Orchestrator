"""
Regression tests for issue #423: expose session context usage in the WebUI.

The macOS client already renders a context ring (issue #31). The API has long
returned the data — `/api/v1/sessions/{id}/status` includes `wee_context_usage`
with `context_percent`, `current_context_tokens` and `context_window` — but the
WebUI never read it.

The WebUI is hand-written vanilla ES2020 with no build step, so these tests scan
the shipped files. That is deliberate: the failure mode is silent (the pill
simply never appears), so a source guard is what keeps the wiring from being
dropped in a future edit.
"""

import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO_ROOT / "webui" / "dist" / "index.html"
APP_JS = REPO_ROOT / "webui" / "dist" / "app.js"
APP_CSS = REPO_ROOT / "webui" / "dist" / "app.css"


class TestIssue423WebUIContextRing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for path in (INDEX_HTML, APP_JS, APP_CSS):
            if not path.exists():  # pragma: no cover - layout guard
                raise unittest.SkipTest(f"WebUI asset missing: {path}")
        cls.html = INDEX_HTML.read_text(encoding="utf-8")
        cls.js = APP_JS.read_text(encoding="utf-8")
        cls.css = APP_CSS.read_text(encoding="utf-8")

    def test_markup_has_the_context_pill_and_ring(self):
        self.assertIn('id="meta-context"', self.html)
        self.assertIn('id="ctx-ring-fill"', self.html)
        self.assertIn('id="meta-context-label"', self.html)

    def test_pill_starts_hidden(self):
        """Only some runtimes report usage; a visible 0% would be a lie."""
        match = re.search(r'<span id="meta-context"[^>]*>', self.html)
        self.assertIsNotNone(match, "context pill markup not found")
        self.assertIn("hidden", match.group(0))

    def test_renderer_is_wired_into_the_status_update(self):
        self.assertIn("function updateContextRing(", self.js)
        self.assertIn("updateContextRing(data?.wee_context_usage)", self.js)

        # Must be called from updateSessionMeta, which runs on every status poll.
        meta_at = self.js.index("function updateSessionMeta(data) {")
        call_at = self.js.index("updateContextRing(data?.wee_context_usage)")
        self.assertGreater(call_at, meta_at)

    def test_hides_itself_when_the_runtime_reports_nothing(self):
        self.assertIn("pill.hidden = true", self.js)

    def test_percent_falls_back_to_token_counts(self):
        """A partially populated payload should still render truthfully."""
        self.assertIn("context_percent", self.js)
        self.assertIn("current_context_tokens", self.js)
        self.assertIn("context_window", self.js)

    def test_ring_geometry_matches_between_js_and_css(self):
        """A mismatched circumference silently renders the wrong arc length."""
        js_match = re.search(r"CTX_RING_CIRCUMFERENCE\s*=\s*([0-9.]+)", self.js)
        self.assertIsNotNone(js_match, "circumference constant missing from app.js")
        css_match = re.search(r"stroke-dasharray:\s*([0-9.]+)", self.css)
        self.assertIsNotNone(css_match, "stroke-dasharray missing from app.css")

        self.assertAlmostEqual(
            float(js_match.group(1)),
            float(css_match.group(1)),
            places=2,
            msg="app.js circumference and app.css stroke-dasharray must agree",
        )

    def test_escalation_thresholds_are_present(self):
        self.assertIn('data-level="warn"', self.css)
        self.assertIn('data-level="danger"', self.css)
        self.assertIn("'danger'", self.js)
        self.assertIn("'warn'", self.js)

    def test_api_still_returns_the_field_the_webui_reads(self):
        """Guards the contract from the other side."""
        source = (REPO_ROOT / "agent_manager.py").read_text(encoding="utf-8")
        self.assertIn('result["wee_context_usage"]', source)
        self.assertIn('"context_percent"', source)


if __name__ == "__main__":
    unittest.main()
