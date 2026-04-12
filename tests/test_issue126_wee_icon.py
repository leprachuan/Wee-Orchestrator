"""Regression tests for Issue #126: Wee runtime icon in runtime switcher."""

import os
import re
import unittest
import xml.etree.ElementTree as ET

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEBUI_DIST = os.path.join(BASE_DIR, "webui", "dist")
SVG_PATH = os.path.join(WEBUI_DIST, "assets", "runtime-icons", "wee.svg")
APP_JS_PATH = os.path.join(WEBUI_DIST, "app.js")


class TestWeeSvgFile(unittest.TestCase):
    """Tests for the wee.svg icon file."""

    def test_wee_svg_exists(self):
        """wee.svg must exist in the runtime-icons directory."""
        self.assertTrue(os.path.isfile(SVG_PATH), f"Missing: {SVG_PATH}")

    def test_wee_svg_valid_xml(self):
        """wee.svg must be valid XML."""
        tree = ET.parse(SVG_PATH)
        root = tree.getroot()
        self.assertTrue(root.tag.endswith("svg"), f"Root should be svg, got {root.tag}")

    def test_wee_svg_has_viewbox(self):
        """wee.svg must have a viewBox for proper scaling."""
        tree = ET.parse(SVG_PATH)
        root = tree.getroot()
        self.assertIn("viewBox", root.attrib, "SVG must have viewBox")

    def test_wee_svg_reasonable_size(self):
        """wee.svg should be under 2KB (consistent with other icons)."""
        size = os.path.getsize(SVG_PATH)
        self.assertLess(size, 2048, f"SVG is {size}B, should be under 2KB")
        self.assertGreater(size, 50, f"SVG is only {size}B, seems too small")

    def test_wee_svg_no_hardcoded_white_fill(self):
        """wee.svg should not hardcode white fill colors."""
        with open(SVG_PATH) as f:
            content = f.read().lower()
        for bad in ['fill="white"', 'fill="#fff"', 'fill="#ffffff"']:
            self.assertNotIn(bad, content, f"SVG has hardcoded fill: {bad}")


class TestAppJsWeeIntegration(unittest.TestCase):
    """Tests that app.js properly references the wee icon."""

    @classmethod
    def setUpClass(cls):
        with open(APP_JS_PATH) as f:
            cls.app_js = f.read()

    def test_runtime_icons_has_wee(self):
        """RUNTIME_ICONS map must include a wee entry pointing to the SVG."""
        pattern = r"wee\s*:\s*['\"].*runtime-icons/wee\.svg['\"]"
        self.assertRegex(self.app_js, pattern)

    def test_fallback_runtime_list_has_wee(self):
        """Fallback runtime list must include a wee entry."""
        self.assertIn("/runtime set wee", self.app_js)

    def test_runtime_icon_html_function_exists(self):
        """runtimeIconHTML function must exist to render icon img tags."""
        self.assertIn("runtimeIconHTML", self.app_js)


class TestRuntimesApiIncludesWee(unittest.TestCase):
    """Tests that the backend /runtimes API includes wee."""

    def test_get_available_runtimes_includes_wee(self):
        """get_available_runtimes() must return a wee entry."""
        import sys
        sys.path.insert(0, BASE_DIR)
        from agent_manager import get_available_runtimes

        runtimes = get_available_runtimes()
        ids = [r["id"] for r in runtimes]
        self.assertIn("wee", ids)

    def test_wee_runtime_has_icon_emoji(self):
        """Wee runtime entry should include an icon field."""
        import sys
        sys.path.insert(0, BASE_DIR)
        from agent_manager import get_available_runtimes

        runtimes = get_available_runtimes()
        wee = next((r for r in runtimes if r["id"] == "wee"), None)
        self.assertIsNotNone(wee)
        self.assertIn("icon", wee, "Wee runtime should have an icon field")


if __name__ == "__main__":
    unittest.main()
