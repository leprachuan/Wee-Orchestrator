"""Regression test for issue #322: scheduled-task fallback runtime must stay
selected when reopening the edit form."""

import os
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS_PATH = os.path.join(BASE_DIR, "webui", "dist", "app.js")


class TestScheduledTaskFallbackRuntimeEditUi(unittest.TestCase):
    """Verify edit-mode fallback values are preserved through async dropdown loading."""

    @classmethod
    def setUpClass(cls):
        with open(APP_JS_PATH) as f:
            cls.app_js = f.read()

    def test_edit_form_passes_saved_fallback_values_into_wiring(self):
        self.assertIn(
            "fallbackRuntime: job?.fallback_runtime || ''",
            self.app_js,
        )
        self.assertIn(
            "fallbackModel: job?.fallback_model || ''",
            self.app_js,
        )

    def test_wire_job_form_uses_container_scoped_fallback_selects(self):
        self.assertIn(
            "const fbRtEl = container.querySelector('#sched-fallback-runtime');",
            self.app_js,
        )
        self.assertIn(
            "const fbModelEl = container.querySelector('#sched-fallback-model');",
            self.app_js,
        )

    def test_wire_job_form_populates_runtime_before_model_with_saved_values(self):
        self.assertIn(
            "populateFallbackRuntimeDropdown(fbRtEl, currentFallbackRuntime).then(() => {",
            self.app_js,
        )
        self.assertIn(
            "fbRtEl.value || currentFallbackRuntime",
            self.app_js,
        )
        self.assertIn(
            "currentFallbackModel",
            self.app_js,
        )


if __name__ == "__main__":
    unittest.main()
