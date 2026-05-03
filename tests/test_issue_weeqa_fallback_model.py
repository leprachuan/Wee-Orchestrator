"""
Regression test for wee-qa fallback model issue.
Issue: wee-qa agent falls back to copilot runtime with model="claude-sonnet-4.6"
       which is not recognized by copilot CLI (expects "auto" or copilot-specific models)
Fix: Change wee-qa fallback_model to "auto" so it's compatible with copilot runtime
"""
import json
import unittest


class TestWeeQAFallbackModel(unittest.TestCase):
    """Test that wee-qa fallback config uses compatible model names"""

    def test_weeqa_fallback_model_is_auto(self):
        """Verify wee-qa fallback_model is 'auto' not 'claude-sonnet-4.6'"""
        with open('/opt/n8n-copilot-shim/agents.json', 'r') as f:
            agents = json.load(f)
        
        # Find wee-qa agent
        weeqa = next((a for a in agents['agents'] if a['name'] == 'wee-qa'), None)
        self.assertIsNotNone(weeqa, "wee-qa agent not found")
        
        # Verify fallback config
        self.assertEqual(
            weeqa.get('fallback_runtime'), 'copilot',
            "wee-qa should have copilot as fallback runtime"
        )
        self.assertEqual(
            weeqa.get('fallback_model'), 'auto',
            "wee-qa should have 'auto' as fallback_model (not claude-sonnet-4.6)"
        )
    
    def test_all_agents_copilot_fallback_use_compatible_models(self):
        """Verify all agents with copilot fallback use compatible model names"""
        with open('/opt/n8n-copilot-shim/agents.json', 'r') as f:
            agents = json.load(f)
        
        for agent in agents['agents']:
            if agent.get('fallback_runtime') == 'copilot':
                model = agent.get('fallback_model')
                # Model should be 'auto' or start with a copilot-compatible name (e.g., gpt-, claude-haiku, etc.)
                # NOT: claude-sonnet, claude-opus which are Claude-specific
                self.assertTrue(
                    model in ['auto'] or model.startswith(('gpt-', 'claude-haiku')),
                    f"Agent '{agent['name']}' has incompatible fallback_model='{model}' for copilot runtime"
                )


if __name__ == "__main__":
    unittest.main()
