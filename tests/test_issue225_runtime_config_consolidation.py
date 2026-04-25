"""
Regression test for Issue #225: Runtime Config Consolidation

Tests that the agent configuration UI correctly consolidates runtime
configuration into a single section with Primary + Fallback runtime/model pairs,
eliminating duplicate sections.

Regression: The old UI had both "⚡ Runtime Config" and "🔄 Runtime Preferences"
sections, creating ambiguity about which was authoritative.

Expected: Single "🔄 Runtime Config" section with Primary and Fallback subsections.
"""

import json
import unittest
from pathlib import Path


class TestIssue225RuntimeConsolidation(unittest.TestCase):
    """
    Verify that duplicate runtime config sections have been consolidated.
    """

    @classmethod
    def setUpClass(cls):
        """Load source and dist files for inspection."""
        project_root = Path(__file__).parent.parent
        
        # Load the React source component
        cls.react_component_path = project_root / "webui/src/components/AgentSettingsPanel.tsx"
        if cls.react_component_path.exists():
            with open(cls.react_component_path, 'r') as f:
                cls.react_source = f.read()
        else:
            cls.react_source = None
        
        # Load the built dist HTML
        cls.dist_html_path = project_root / "webui/dist/index.html"
        if cls.dist_html_path.exists():
            with open(cls.dist_html_path, 'r') as f:
                cls.dist_html = f.read()
        else:
            cls.dist_html = None

    def test_react_source_has_consolidated_section(self):
        """React source should have one 'Runtime Config' section with fallback fields."""
        if not self.react_source:
            self.skipTest("React source not found")
        
        # Should have Primary and Fallback subsections
        self.assertIn('Primary', self.react_source, 
                     "React source should mention 'Primary' runtime section")
        self.assertIn('Fallback', self.react_source,
                     "React source should mention 'Fallback' runtime section")
        
        # Should have fallback_runtime and fallback_model fields
        self.assertIn('fallback_runtime', self.react_source,
                     "React source should have fallback_runtime field")
        self.assertIn('fallback_model', self.react_source,
                     "React source should have fallback_model field")
        
        # Should NOT have duplicate "Runtime Preferences" section
        self.assertNotIn('Runtime Preferences', self.react_source,
                        "React source should NOT have separate 'Runtime Preferences' section")

    def test_react_source_no_duplicate_runtime_sections(self):
        """React source should not have separate runtime preferences section."""
        if not self.react_source:
            self.skipTest("React source not found")
        
        # Count occurrences of runtime preferences (should be 0)
        count_prefs = self.react_source.count('Runtime Preferences')
        self.assertEqual(count_prefs, 0,
                        f"Should have no 'Runtime Preferences' section, found {count_prefs}")

    def test_dist_html_no_duplicate_runtime_sections(self):
        """Built HTML should have single consolidated runtime section."""
        if not self.dist_html:
            self.skipTest("Dist HTML not found")
        
        # Should NOT have the old separate "Runtime Preferences" section
        self.assertNotIn('Runtime Preferences', self.dist_html,
                        "HTML should NOT have separate 'Runtime Preferences' section")
        
        # Should have exactly one section-title containing "Runtime Config"
        count_runtime_config_titles = self.dist_html.count('<div class="asf-section-title">🔄 Runtime Config')
        self.assertEqual(count_runtime_config_titles, 1,
                        f"HTML should have exactly 1 'Runtime Config' section title, found {count_runtime_config_titles}")

    def test_dist_html_has_fallback_fields(self):
        """Built HTML should include fallback runtime and model fields."""
        if not self.dist_html:
            self.skipTest("Dist HTML not found")
        
        # Should have fallback-runtime and fallback-model fields
        self.assertIn('asf-fallback-runtime', self.dist_html,
                     "HTML should have fallback-runtime field")
        self.assertIn('asf-fallback-model', self.dist_html,
                     "HTML should have fallback-model field")
        
        # Should have "Primary" and "Fallback" subsection labels
        self.assertIn('Primary', self.dist_html,
                     "HTML should label primary runtime section")
        self.assertIn('Fallback', self.dist_html,
                     "HTML should label fallback runtime section")

    def test_dist_html_fallback_fields_are_optional(self):
        """Fallback fields should allow empty/None values (optional)."""
        if not self.dist_html:
            self.skipTest("Dist HTML not found")
        
        # Fallback runtime select should have "None" option
        self.assertIn('value="">None</option>', self.dist_html,
                     "Fallback runtime select should have 'None' option")

    def test_dist_html_no_lingering_rp_id(self):
        """HTML should not have old 'runtime-prefs-section' or 'rp-*' IDs."""
        if not self.dist_html:
            self.skipTest("Dist HTML not found")
        
        # Should not have old section ID or button IDs
        self.assertNotIn('runtime-prefs-section', self.dist_html,
                        "HTML should not have old 'runtime-prefs-section' ID")
        self.assertNotIn('id="rp-', self.dist_html,
                        "HTML should not have old 'rp-*' field IDs")

    def test_dist_html_consistent_id_naming(self):
        """HTML should use consistent 'asf-*' naming for runtime fields."""
        if not self.dist_html:
            self.skipTest("Dist HTML not found")
        
        # All runtime/fallback fields should use asf- prefix
        required_ids = [
            'asf-runtime',
            'asf-model',
            'asf-fallback-runtime',
            'asf-fallback-model',
            'asf-max-concurrent',
        ]
        for field_id in required_ids:
            self.assertIn(f'id="{field_id}"', self.dist_html,
                         f"HTML should have consistent field ID: {field_id}")


if __name__ == '__main__':
    unittest.main()
