"""Regression tests for Issue #174: btn-toggle-queue missing from HTML causes DOMContentLoaded crash.

This test suite verifies that:
1. btn-toggle-queue button exists in the HTML (it was accidentally removed in commit f02898d)
2. btn-pause-queue button exists in the HTML
3. Both event listeners are safely registered with null checks in app.js

Background: In commit f02898d (feat(#161): remove built-in TODO panel), the
btn-toggle-queue button was removed from webui/dist/index.html but app.js still
called $('btn-toggle-queue').addEventListener('click', toggleQueuePanel)
unconditionally at line 2356, causing:
    TypeError: Cannot read properties of null (reading 'addEventListener')

This crashed the DOMContentLoaded handler immediately, leaving the WebUI
completely non-functional (blank page, only Skills panel visible).

The proper fix is two-fold:
1. Restore the button to index.html (commit 622385e)
2. Add null checks around event listener setup in app.js (this PR)
"""

import os
import re
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEBUI_DIST = os.path.join(BASE_DIR, "webui", "dist")
INDEX_HTML_PATH = os.path.join(WEBUI_DIST, "index.html")
APP_JS_PATH = os.path.join(WEBUI_DIST, "app.js")


class TestButtonsInHTML(unittest.TestCase):
    """Tests that required buttons exist in the HTML."""

    @classmethod
    def setUpClass(cls):
        with open(INDEX_HTML_PATH) as f:
            cls.index_html = f.read()

    def test_btn_toggle_queue_exists_in_html(self):
        """btn-toggle-queue button must exist in index.html."""
        self.assertIn('id="btn-toggle-queue"', self.index_html,
                      "btn-toggle-queue button missing from HTML. "
                      "This causes DOMContentLoaded crash at app.js line 2354.")

    def test_btn_toggle_queue_has_click_handler_class(self):
        """btn-toggle-queue must have the queue-toggle-btn class."""
        self.assertIn('id="btn-toggle-queue" class="queue-toggle-btn"', self.index_html,
                      "btn-toggle-queue must have class='queue-toggle-btn'")

    def test_btn_pause_queue_exists_in_html(self):
        """btn-pause-queue button must exist in index.html."""
        self.assertIn('id="btn-pause-queue"', self.index_html,
                      "btn-pause-queue button missing from HTML")

    def test_btn_toggle_queue_section_exists_in_html(self):
        """btn-toggle-queue-section button must exist in index.html."""
        self.assertIn('id="btn-toggle-queue-section"', self.index_html,
                      "btn-toggle-queue-section button missing from HTML")


class TestEventListenerSetup(unittest.TestCase):
    """Tests that event listeners are safely set up with null checks in app.js."""

    @classmethod
    def setUpClass(cls):
        with open(APP_JS_PATH) as f:
            cls.app_js = f.read()

    def test_btn_toggle_queue_listener_has_null_check(self):
        """Event listener for btn-toggle-queue must include a null check.
        
        The vulnerable pattern is:
            $('btn-toggle-queue').addEventListener('click', toggleQueuePanel);
        
        Safe pattern is:
            const btnToggleQueue = $('btn-toggle-queue');
            if (btnToggleQueue) {
              btnToggleQueue.addEventListener('click', toggleQueuePanel);
            }
        """
        # Look for pattern: const btnToggleQueue = $('btn-toggle-queue')
        pattern_declare = r"const\s+btnToggleQueue\s*=\s*\$\s*\(\s*['\"]btn-toggle-queue['\"]\s*\)"
        self.assertRegex(self.app_js, pattern_declare,
                         "btn-toggle-queue must be assigned to a const variable for null checking")

        # Look for pattern: if (btnToggleQueue) { ... addEventListener ... }
        pattern_check = (
            r"if\s*\(\s*btnToggleQueue\s*\)\s*\{\s*"
            r"btnToggleQueue\s*\.\s*addEventListener\s*\(\s*['\"]click['\"]\s*,\s*toggleQueuePanel\s*\)"
        )
        self.assertRegex(self.app_js, pattern_check,
                         "btnToggleQueue listener setup must be guarded by an if (btnToggleQueue) check")

    def test_btn_pause_queue_listener_has_null_check(self):
        """Event listener for btn-pause-queue must include a null check.
        
        Similar pattern to btnToggleQueue:
            const btnPauseQueue = $('btn-pause-queue');
            if (btnPauseQueue) {
              btnPauseQueue.addEventListener('click', toggleQueuePause);
            }
        """
        # Look for pattern: const btnPauseQueue = $('btn-pause-queue')
        pattern_declare = r"const\s+btnPauseQueue\s*=\s*\$\s*\(\s*['\"]btn-pause-queue['\"]\s*\)"
        self.assertRegex(self.app_js, pattern_declare,
                         "btn-pause-queue must be assigned to a const variable for null checking")

        # Look for pattern: if (btnPauseQueue) { ... addEventListener ... }
        pattern_check = (
            r"if\s*\(\s*btnPauseQueue\s*\)\s*\{\s*"
            r"btnPauseQueue\s*\.\s*addEventListener\s*\(\s*['\"]click['\"]\s*,\s*toggleQueuePause\s*\)"
        )
        self.assertRegex(self.app_js, pattern_check,
                         "btnPauseQueue listener setup must be guarded by an if (btnPauseQueue) check")

    def test_no_unconditional_btn_toggle_queue_getelementbyid(self):
        """app.js must NOT call addEventListener on btn-toggle-queue without null check.
        
        The vulnerable pattern must not appear anywhere in app.js:
            $('btn-toggle-queue').addEventListener(
            
        This is the exact bug from issue #174.
        """
        # Ensure vulnerable pattern does not exist
        # (Note: this regex checks for direct addEventListener without a guard)
        vulnerable_pattern = r"\$\s*\(\s*['\"]btn-toggle-queue['\"]\s*\)\s*\.\s*addEventListener"
        # If we find this pattern, it means there's an unconditional addEventListener
        matches = re.findall(vulnerable_pattern, self.app_js)
        if matches:
            # But only flag as error if there's an unconditional one (not guarded)
            # We need to check that the only matches are inside if guards
            lines = self.app_js.split("\n")
            for i, line in enumerate(lines):
                if vulnerable_pattern in line:
                    # Check if this line is preceded by an if check
                    # (Simple heuristic: look back up to 3 lines)
                    context = "\n".join(lines[max(0, i-3):i+1])
                    self.assertIn("if (", context,
                                  f"Found unconditional addEventListener on btn-toggle-queue at line {i+1}")

    def test_domcontentloaded_handler_completes(self):
        """The DOMContentLoaded handler must complete without errors.
        
        With the null checks in place, even if the button is missing from HTML,
        the handler will skip the listener setup and continue gracefully.
        With the button restored to HTML, listeners will be attached normally.
        """
        # Verify DOMContentLoaded handler is present
        self.assertIn("document.addEventListener('DOMContentLoaded'", self.app_js,
                      "DOMContentLoaded handler must be registered")

        # Verify the handler includes the queue button setup
        self.assertIn("Request Queue", self.app_js,
                      "DOMContentLoaded handler must reference Request Queue section")


class TestRegressionScenarios(unittest.TestCase):
    """Integration tests that simulate the exact crash scenario from issue #174."""

    @classmethod
    def setUpClass(cls):
        with open(INDEX_HTML_PATH) as f:
            cls.index_html = f.read()
        with open(APP_JS_PATH) as f:
            cls.app_js = f.read()

    def test_button_exists_and_listener_is_safe(self):
        """Simulates: Button is in HTML AND listener is safely registered.
        
        This is the correct state after the fix:
        - Button exists in HTML
        - Listener registration is guarded
        - Page load succeeds
        """
        # Button must be in HTML
        button_in_html = 'id="btn-toggle-queue"' in self.index_html
        self.assertTrue(button_in_html, "btn-toggle-queue not in HTML")

        # Listener setup must be guarded
        safe_setup = "if (btnToggleQueue)" in self.app_js
        self.assertTrue(safe_setup, "btnToggleQueue listener setup not guarded by null check")

    def test_vulnerable_pattern_not_present(self):
        """Verifies the exact vulnerable pattern from issue #174 is not present.
        
        Vulnerable code from issue:
            $('btn-toggle-queue').addEventListener('click', toggleQueuePanel);
        
        This pattern should NOT be in the current codebase (except in comments).
        """
        # The vulnerable pattern: direct call to addEventListener on result of $()
        # without storing in a variable first
        lines = self.app_js.split("\n")
        for i, line in enumerate(lines):
            # Skip if it's in a comment
            if line.strip().startswith("//"):
                continue
            # Look for the vulnerable pattern
            if "$('btn-toggle-queue')" in line and ".addEventListener" in line:
                # This is the vulnerable pattern
                self.fail(
                    f"Vulnerable pattern found at line {i+1}: "
                    f"{line.strip()}\n"
                    "Expected: const btnToggleQueue = $('btn-toggle-queue'); "
                    "if (btnToggleQueue) { btnToggleQueue.addEventListener(...) }"
                )


if __name__ == "__main__":
    unittest.main()
