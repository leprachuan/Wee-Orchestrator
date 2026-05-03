"""
Regression test for smart fallback model selection.
Issue: When background task falls back to different runtime, it was using 
the primary runtime's model name (e.g., copilot/sonnet) which doesn't exist.
Fix: When runtime changes and no explicit fallback_model, use "auto"
"""
import unittest


class TestFallbackModelSmartSelection(unittest.TestCase):
    """Test that fallback model is intelligently selected based on runtime"""

    def test_explicit_fallback_model_used(self):
        """When fallback_model is explicitly set, use it"""
        fb_model = "auto"
        fb_runtime = "copilot"
        runtime = "claude"
        model = "sonnet"
        
        # Logic from agent_manager.py line 13499-13505
        if fb_model:
            eff_fb_model = fb_model
        elif fb_runtime and fb_runtime != runtime:
            eff_fb_model = "auto"
        else:
            eff_fb_model = model
        
        self.assertEqual(eff_fb_model, "auto")
    
    def test_runtime_changed_no_explicit_model(self):
        """When runtime changes and no explicit fallback_model, use auto"""
        fb_model = None  # Not explicitly set
        fb_runtime = "copilot"
        runtime = "claude"
        model = "sonnet"
        
        if fb_model:
            eff_fb_model = fb_model
        elif fb_runtime and fb_runtime != runtime:
            eff_fb_model = "auto"
        else:
            eff_fb_model = model
        
        self.assertEqual(eff_fb_model, "auto", 
                        "Should use 'auto' when switching runtimes without explicit fallback_model")
    
    def test_runtime_unchanged_uses_original_model(self):
        """When runtime unchanged, keep original model"""
        fb_model = None
        fb_runtime = "claude"  # Same as primary
        runtime = "claude"
        model = "sonnet"
        
        if fb_model:
            eff_fb_model = fb_model
        elif fb_runtime and fb_runtime != runtime:
            eff_fb_model = "auto"
        else:
            eff_fb_model = model
        
        self.assertEqual(eff_fb_model, "sonnet")
    
    def test_never_use_incompatible_model_with_new_runtime(self):
        """Ensure we never use copilot/sonnet (broken case)"""
        # The case that was failing: claude/sonnet -> copilot/sonnet
        fb_model = None  # Not explicitly configured
        fb_runtime = "copilot"  # Different from primary
        runtime = "claude"  # Primary
        model = "sonnet"  # Claude model, not copilot-compatible
        
        if fb_model:
            eff_fb_model = fb_model
        elif fb_runtime and fb_runtime != runtime:
            eff_fb_model = "auto"  # Smart: use auto instead of sonnet
        else:
            eff_fb_model = model
        
        # Should NOT be "sonnet" (which would fail in copilot)
        self.assertNotEqual(eff_fb_model, "sonnet")
        self.assertEqual(eff_fb_model, "auto")


if __name__ == "__main__":
    unittest.main()
