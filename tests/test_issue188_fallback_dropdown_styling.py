"""Regression tests for Issue #188: Fallback Runtime/Model dropdowns must use
the same glass-input glass-select styling as the primary Runtime/Model
selects."""

import os
import re
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS_PATH = os.path.join(BASE_DIR, "webui", "dist", "app.js")


class TestFallbackDropdownStyling(unittest.TestCase):
    """Verify fallback Runtime/Model selects carry the glassmorphism CSS classes."""

    @classmethod
    def setUpClass(cls):
        with open(APP_JS_PATH) as f:
            cls.app_js = f.read()

    def _get_fallback_select_html(self, element_id):
        """Extract the opening <select ...> tag for a given element id."""
        pattern = rf'<select[^>]+id="{re.escape(element_id)}"[^>]*>'
        match = re.search(pattern, self.app_js)
        self.assertIsNotNone(
            match, f'Could not find <select id="{element_id}"> in app.js'
        )
        return match.group(0)

    def test_fallback_runtime_has_glass_input_class(self):
        """sched-fallback-runtime select must have glass-input CSS class."""
        tag = self._get_fallback_select_html("sched-fallback-runtime")
        self.assertIn(
            "glass-input",
            tag,
            "sched-fallback-runtime <select> missing 'glass-input' class",
        )

    def test_fallback_runtime_has_glass_select_class(self):
        """sched-fallback-runtime select must have glass-select CSS class."""
        tag = self._get_fallback_select_html("sched-fallback-runtime")
        self.assertIn(
            "glass-select",
            tag,
            "sched-fallback-runtime <select> missing 'glass-select' class",
        )

    def test_fallback_model_has_glass_input_class(self):
        """sched-fallback-model select must have glass-input CSS class."""
        tag = self._get_fallback_select_html("sched-fallback-model")
        self.assertIn(
            "glass-input",
            tag,
            "sched-fallback-model <select> missing 'glass-input' class",
        )

    def test_fallback_model_has_glass_select_class(self):
        """sched-fallback-model select must have glass-select CSS class."""
        tag = self._get_fallback_select_html("sched-fallback-model")
        self.assertIn(
            "glass-select",
            tag,
            "sched-fallback-model <select> missing 'glass-select' class",
        )

    def test_primary_runtime_still_has_glass_classes(self):
        """Primary runtime select must still carry glass-input glass-select
        (no regression)."""
        # Primary runtime uses name="runtime" with data-current attribute
        pattern = r'<select class="glass-input glass-select" name="runtime"'
        self.assertRegex(
            self.app_js,
            pattern,
            "Primary runtime <select> lost its glass-input glass-select",
        )

    def test_primary_model_still_has_glass_classes(self):
        """Primary model select must still carry glass-input glass-select
        (no regression)."""
        pattern = r'<select class="glass-input glass-select" name="model"'
        self.assertRegex(
            self.app_js,
            pattern,
            "Primary model <select> lost its glass-input glass-select",
        )

    def test_fallback_runtime_class_order_matches_primary(self):
        """Fallback runtime class attribute should match primary pattern."""
        fb_tag = self._get_fallback_select_html("sched-fallback-runtime")
        self.assertIn(
            'class="glass-input glass-select"',
            fb_tag,
            "sched-fallback-runtime class order incorrect",
        )

    def test_fallback_model_class_order_matches_primary(self):
        """Fallback model class attribute should match primary pattern."""
        fb_tag = self._get_fallback_select_html("sched-fallback-model")
        self.assertIn(
            'class="glass-input glass-select"',
            fb_tag,
            "sched-fallback-model class order incorrect",
        )


if __name__ == "__main__":
    unittest.main()
