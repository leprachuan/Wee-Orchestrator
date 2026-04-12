"""Regression tests for Issue #133: Add wee runtime icon to WebUI.

Verifies the wee runtime icon is properly wired into the WebUI runtime
switcher across all display locations (badges, dropdown, slash commands).
"""

import os
import re
import unittest
import xml.etree.ElementTree as ET

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEBUI_DIST = os.path.join(BASE_DIR, "webui", "dist")
SVG_PATH = os.path.join(WEBUI_DIST, "assets", "runtime-icons", "wee.svg")
APP_JS_PATH = os.path.join(WEBUI_DIST, "app.js")


class TestWeeIconSvgAsset(unittest.TestCase):
    """SVG asset checks for the wee runtime icon."""

    def test_wee_svg_file_exists(self):
        """wee.svg must exist at the expected path."""
        self.assertTrue(os.path.exists(SVG_PATH), f"SVG missing at {SVG_PATH}")

    def test_wee_svg_is_valid_xml(self):
        """wee.svg must be valid XML with an <svg> root element."""
        tree = ET.parse(SVG_PATH)
        root = tree.getroot()
        # ElementTree prefixes namespace — strip it for comparison
        tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
        self.assertEqual(tag, "svg", f"Expected svg root, got {root.tag}")

    def test_wee_svg_has_viewbox(self):
        """wee.svg must have a viewBox attribute for scaling to 14x14px."""
        tree = ET.parse(SVG_PATH)
        root = tree.getroot()
        # Check with and without namespace prefix
        attribs = {k.split("}")[-1] if "}" in k else k: v for k, v in root.attrib.items()}
        self.assertIn("viewBox", attribs, "SVG must have viewBox for correct 14x14px rendering")

    def test_wee_svg_file_size(self):
        """wee.svg should be a reasonable size (50B–4KB)."""
        size = os.path.getsize(SVG_PATH)
        self.assertGreater(size, 50, f"SVG seems too small ({size}B)")
        self.assertLess(size, 4096, f"SVG seems too large ({size}B) for an icon")

    def test_wee_svg_no_hardcoded_white_colors(self):
        """wee.svg must not hardcode white fills (breaks dark/light theming)."""
        with open(SVG_PATH) as f:
            content = f.read().lower()
        for bad_fill in ['fill="white"', "fill='white'", 'fill="#fff"', 'fill="#ffffff"']:
            self.assertNotIn(bad_fill, content, f"SVG hardcodes white fill: {bad_fill}")

    def test_wee_svg_no_hardcoded_black_colors(self):
        """wee.svg must not hardcode black fills (breaks icon color CSS filters)."""
        with open(SVG_PATH) as f:
            content = f.read().lower()
        for bad_fill in ['fill="black"', "fill='black'", 'fill="#000"', 'fill="#000000"']:
            self.assertNotIn(bad_fill, content, f"SVG hardcodes black fill: {bad_fill}")


class TestRuntimeIconsMap(unittest.TestCase):
    """Tests that RUNTIME_ICONS map in app.js includes wee."""

    @classmethod
    def setUpClass(cls):
        with open(APP_JS_PATH) as f:
            cls.app_js = f.read()

    def test_runtime_icons_object_exists(self):
        """RUNTIME_ICONS constant must be defined in app.js."""
        self.assertIn("RUNTIME_ICONS", self.app_js)

    def test_runtime_icons_has_wee_entry(self):
        """RUNTIME_ICONS must map 'wee' to wee.svg path."""
        pattern = r"wee\s*:\s*['\"][^'\"]*runtime-icons/wee\.svg['\"]"
        self.assertRegex(
            self.app_js,
            pattern,
            "RUNTIME_ICONS must have: wee: '/ui/assets/runtime-icons/wee.svg'"
        )

    def test_runtime_icons_wee_uses_ui_path(self):
        """wee icon path must use /ui/ prefix (not /assets/ directly)."""
        self.assertIn("/ui/assets/runtime-icons/wee.svg", self.app_js)

    def test_other_runtimes_still_present(self):
        """Existing runtime icon mappings must not be broken by wee addition."""
        for runtime in ("claude", "copilot", "gemini", "opencode", "devin", "cursor"):
            self.assertIn(f"'{runtime}'", self.app_js, f"Runtime '{runtime}' missing from app.js")

    def test_runtime_icon_html_function_renders_img(self):
        """runtimeIconHTML function must generate an <img> tag for icon rendering."""
        # Check the function exists and returns an img tag
        self.assertIn("runtimeIconHTML", self.app_js)
        # Verify img element with size params
        self.assertIn('width="${size}"', self.app_js)
        self.assertIn('height="${size}"', self.app_js)


class TestRuntimeSlashCommands(unittest.TestCase):
    """Tests that /runtime slash commands include wee."""

    @classmethod
    def setUpClass(cls):
        with open(APP_JS_PATH) as f:
            cls.app_js = f.read()

    def test_slash_runtime_set_wee_exists(self):
        """Slash command '/runtime set wee' must be in the runtime switcher options."""
        self.assertIn("/runtime set wee", self.app_js)

    def test_wee_slash_command_uses_icon_html(self):
        """Wee entry in slash commands must include the icon via runtimeIconHTML."""
        pattern = r"runtimeIconHTML\(['\"]wee['\"][^)]*\)[^;]*wee"
        self.assertRegex(
            self.app_js,
            pattern,
            "Wee slash command entry must render icon with runtimeIconHTML('wee')"
        )

    def test_all_runtimes_have_slash_commands(self):
        """All runtimes (including wee) must have /runtime set entries."""
        for runtime in ("claude", "copilot", "gemini", "opencode", "wee"):
            self.assertIn(f"/runtime set {runtime}", self.app_js)


class TestBackendWeeRuntime(unittest.TestCase):
    """Tests that the backend properly includes wee in runtime metadata."""

    def test_get_available_runtimes_includes_wee(self):
        """get_available_runtimes() must return wee as an available runtime."""
        import sys
        sys.path.insert(0, BASE_DIR)
        from agent_manager import get_available_runtimes

        runtimes = get_available_runtimes()
        runtime_ids = [r["id"] for r in runtimes]
        self.assertIn("wee", runtime_ids, f"wee not in runtimes: {runtime_ids}")

    def test_wee_runtime_has_icon_field(self):
        """Wee runtime entry must have an icon field for badge rendering."""
        import sys
        sys.path.insert(0, BASE_DIR)
        from agent_manager import get_available_runtimes

        runtimes = get_available_runtimes()
        wee = next((r for r in runtimes if r["id"] == "wee"), None)
        self.assertIsNotNone(wee, "wee runtime entry not found")
        self.assertIn("icon", wee, "wee runtime must have icon field")
        self.assertTrue(wee["icon"], "wee icon field must not be empty")

    def test_wee_runtime_has_label(self):
        """Wee runtime entry must have a label field."""
        import sys
        sys.path.insert(0, BASE_DIR)
        from agent_manager import get_available_runtimes

        runtimes = get_available_runtimes()
        wee = next((r for r in runtimes if r["id"] == "wee"), None)
        self.assertIsNotNone(wee, "wee runtime entry not found")
        self.assertIn("label", wee, "wee runtime must have a label")


class TestIconConsistency(unittest.TestCase):
    """Tests that icon usage is consistent across app.js."""

    @classmethod
    def setUpClass(cls):
        with open(APP_JS_PATH) as f:
            cls.app_js = f.read()

    def test_no_stray_wee_svg_references(self):
        """All wee.svg references in app.js must use the canonical path."""
        # Find all wee.svg references
        refs = re.findall(r"['\"][^'\"]*wee\.svg['\"]", self.app_js)
        for ref in refs:
            self.assertIn("/ui/assets/runtime-icons/wee.svg", ref,
                          f"Non-canonical wee.svg path: {ref}")

    def test_icon_file_matches_registry(self):
        """wee.svg file on disk must match the path registered in RUNTIME_ICONS."""
        # Verify the file at the path implied by the registry
        # Registry uses '/ui/assets/runtime-icons/wee.svg' served from webui/dist
        actual_path = os.path.join(WEBUI_DIST, "assets", "runtime-icons", "wee.svg")
        self.assertTrue(os.path.isfile(actual_path),
                        f"wee.svg not found at expected path: {actual_path}")


if __name__ == "__main__":
    unittest.main()
