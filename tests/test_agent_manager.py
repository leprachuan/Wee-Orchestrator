#!/usr/bin/env python3
"""
Test suite for agent_manager.py

Tests cover:
- Session management and persistence
- Agent configuration loading
- Slash command parsing and execution
- Model resolution and switching
- Metadata stripping
- Runtime management
"""

import unittest
import tempfile
import json
import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# Add parent directory to path to import agent_manager
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_manager import SessionManager


class TestSessionManager(unittest.TestCase):
    """Test SessionManager initialization and configuration"""

    def setUp(self):
        """Create temporary directories and config files for testing"""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

        # Create a temporary agents.json
        self.agents_config = {
            "agents": [
                {
                    "name": "test_devops",
                    "description": "Test DevOps agent",
                    "path": "/tmp/test_devops"
                },
                {
                    "name": "test_projects",
                    "description": "Test Projects agent",
                    "path": "/tmp/test_projects"
                }
            ]
        }

        self.config_file = self.temp_path / "agents.json"
        with open(self.config_file, 'w') as f:
            json.dump(self.agents_config, f)

    def tearDown(self):
        """Clean up temporary files"""
        self.temp_dir.cleanup()

    def test_session_manager_initialization(self):
        """Test SessionManager initializes with proper directories"""
        manager = SessionManager(str(self.config_file))
        self.assertIsNotNone(manager.copilot_home)
        self.assertIsNotNone(manager.opencode_home)
        self.assertIsNotNone(manager.claude_home)

    def test_load_agents_config(self):
        """Test loading agents from config file"""
        manager = SessionManager(str(self.config_file))
        agents = manager.AGENTS

        self.assertIn('test_devops', agents)
        self.assertIn('test_projects', agents)
        self.assertEqual(agents['test_devops']['path'], "/tmp/test_devops")
        self.assertEqual(agents['test_projects']['description'], "Test Projects agent")

    def test_load_agents_config_missing_file(self):
        """Test handling of missing config file"""
        manager = SessionManager(str(self.temp_path / "nonexistent.json"))
        # Should return empty dict but not crash
        self.assertIsInstance(manager.AGENTS, dict)

    def test_load_agents_config_invalid_json(self):
        """Test handling of invalid JSON in config file"""
        bad_config = self.temp_path / "bad.json"
        with open(bad_config, 'w') as f:
            f.write("{invalid json")

        manager = SessionManager(str(bad_config))
        self.assertEqual(manager.AGENTS, {})


class TestSessionPersistence(unittest.TestCase):
    """Test session state persistence and management"""

    def setUp(self):
        """Create temporary session storage"""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

        # Create config
        self.agents_config = {
            "agents": [
                {"name": "test_devops", "description": "Test", "path": "/tmp/test"}
            ]
        }
        self.config_file = self.temp_path / "agents.json"
        with open(self.config_file, 'w') as f:
            json.dump(self.agents_config, f)

        # Mock home directory for session storage
        self.patcher = patch('agent_manager.Path.home')
        self.mock_home = self.patcher.start()
        self.mock_home.return_value = self.temp_path

    def tearDown(self):
        """Clean up patches and temp files"""
        self.patcher.stop()
        self.temp_dir.cleanup()

    def test_create_new_session(self):
        """Test creating a new session with default values"""
        manager = SessionManager(str(self.config_file))
        session_data = manager.get_or_create_session_data("test_session_1")

        self.assertIn("session_id", session_data)
        self.assertEqual(session_data["model"], "gpt-5-mini")
        self.assertEqual(session_data["agent"], "devops")
        self.assertEqual(session_data["runtime"], "copilot")
        self.assertTrue(session_data["is_new"])

    def test_resume_existing_session(self):
        """Test resuming an existing session"""
        manager = SessionManager(str(self.config_file))

        # Create first session
        session_1 = manager.get_or_create_session_data("test_session_2")
        original_id = session_1["session_id"]

        # Resume same session
        session_2 = manager.get_or_create_session_data("test_session_2")
        self.assertEqual(session_2["session_id"], original_id)
        self.assertFalse(session_2["is_new"])

    def test_update_session_field(self):
        """Test updating individual session fields"""
        manager = SessionManager(str(self.config_file))

        # Create session
        manager.get_or_create_session_data("test_session_3")

        # Update model
        manager.update_session_field("test_session_3", "model", "gpt-5")
        session_data = manager.get_or_create_session_data("test_session_3")
        self.assertEqual(session_data["model"], "gpt-5")

    def test_session_persistence_across_instances(self):
        """Test that sessions persist across SessionManager instances"""
        manager1 = SessionManager(str(self.config_file))
        session_1 = manager1.get_or_create_session_data("persistent_session")
        original_id = session_1["session_id"]

        # Create new manager instance
        manager2 = SessionManager(str(self.config_file))
        session_2 = manager2.get_or_create_session_data("persistent_session")
        self.assertEqual(session_2["session_id"], original_id)


class TestSlashCommands(unittest.TestCase):
    """Test slash command parsing and execution"""

    def setUp(self):
        """Set up test manager"""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

        self.agents_config = {
            "agents": [
                {"name": "test_devops", "description": "Test DevOps", "path": "/tmp/test"}
            ]
        }
        self.config_file = self.temp_path / "agents.json"
        with open(self.config_file, 'w') as f:
            json.dump(self.agents_config, f)

        self.patcher = patch('agent_manager.Path.home')
        self.mock_home = self.patcher.start()
        self.mock_home.return_value = self.temp_path

        self.manager = SessionManager(str(self.config_file))

    def tearDown(self):
        """Clean up"""
        self.patcher.stop()
        self.temp_dir.cleanup()

    def test_parse_slash_command(self):
        """Test parsing slash commands"""
        cmd, arg = self.manager.parse_slash_command("/help")
        self.assertEqual(cmd, "/help")
        self.assertIsNone(arg)

        cmd, arg = self.manager.parse_slash_command("/model set gpt-5")
        self.assertEqual(cmd, "/model")
        self.assertEqual(arg, "set gpt-5")

        cmd, arg = self.manager.parse_slash_command("regular prompt")
        self.assertIsNone(cmd)
        self.assertIsNone(arg)

    def test_help_command(self):
        """Test /help command"""
        result = self.manager.execute("/help", "test_session")
        self.assertIn("Available Commands", result)
        self.assertIn("/runtime", result)
        self.assertIn("/model", result)
        self.assertIn("/agent", result)

    def test_runtime_list_command(self):
        """Test /runtime list command"""
        result = self.manager.execute("/runtime list", "test_session")
        self.assertIn("copilot", result)
        self.assertIn("opencode", result)
        self.assertIn("claude", result)

    def test_runtime_current_command(self):
        """Test /runtime current command"""
        result = self.manager.execute("/runtime current", "test_session")
        self.assertIn("copilot", result)  # Default runtime

    def test_runtime_set_command(self):
        """Test /runtime set command"""
        result = self.manager.execute("/runtime set opencode", "test_session")
        self.assertIn("opencode", result)

        # Verify it was actually set
        session_data = self.manager.get_or_create_session_data("test_session")
        self.assertEqual(session_data["runtime"], "opencode")

    def test_agent_list_command(self):
        """Test /agent list command"""
        result = self.manager.execute("/agent list", "test_session")
        self.assertIn("test_devops", result)
        self.assertIn("Test DevOps", result)

    def test_agent_set_command(self):
        """Test /agent set command"""
        result = self.manager.execute("/agent set test_devops", "test_session")
        self.assertIn("test_devops", result)

        session_data = self.manager.get_or_create_session_data("test_session")
        self.assertEqual(session_data["agent"], "test_devops")

    def test_agent_set_invalid(self):
        """Test /agent set with invalid agent"""
        result = self.manager.execute("/agent set nonexistent", "test_session")
        self.assertIn("Unknown agent", result)

    def test_session_reset_command(self):
        """Test /session reset command"""
        # Create a session first
        self.manager.get_or_create_session_data("test_session_reset")

        # Reset it
        result = self.manager.execute("/session reset", "test_session_reset")
        self.assertIn("reset", result.lower())

        # Verify it was reset (next call should create new with is_new=True)
        new_session = self.manager.get_or_create_session_data("test_session_reset")
        self.assertTrue(new_session["is_new"])


class TestMetadataStripping(unittest.TestCase):
    """Test metadata stripping from CLI output"""

    def setUp(self):
        """Set up test manager"""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

        self.agents_config = {"agents": []}
        self.config_file = self.temp_path / "agents.json"
        with open(self.config_file, 'w') as f:
            json.dump(self.agents_config, f)

        self.manager = SessionManager(str(self.config_file))

    def tearDown(self):
        """Clean up"""
        self.temp_dir.cleanup()

    def test_strip_thinking_tags(self):
        """Test removing <think> tags"""
        text = "Start\n<think>internal reasoning</think>\nEnd"
        result = self.manager.strip_thinking_tags(text)
        self.assertNotIn("<think>", result)
        self.assertNotIn("</think>", result)
        self.assertIn("Start", result)
        self.assertIn("End", result)

    def test_strip_copilot_metadata(self):
        """Test stripping Copilot metadata"""
        text = "Output here\nTotal usage est: 100 tokens\nTotal duration: 5s"
        result = self.manager.strip_metadata(text, 'copilot')
        self.assertIn("Output here", result)
        self.assertNotIn("Total usage", result)
        self.assertNotIn("Total duration", result)

    def test_strip_opencode_metadata(self):
        """Test stripping OpenCode metadata"""
        text = "█████ OpenCode Banner\nActual output\nTokens used: 50"
        result = self.manager.strip_metadata(text, 'opencode')
        self.assertIn("Actual output", result)
        self.assertNotIn("Tokens used", result)

    def test_strip_claude_metadata(self):
        """Test Claude metadata handling (should pass through)"""
        text = "Claude output\nSome response"
        result = self.manager.strip_metadata(text, 'claude')
        self.assertIn("Claude output", result)
        self.assertIn("Some response", result)


class TestModelResolution(unittest.TestCase):
    """Test model name resolution and switching"""

    def setUp(self):
        """Set up test manager"""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

        self.agents_config = {"agents": []}
        self.config_file = self.temp_path / "agents.json"
        with open(self.config_file, 'w') as f:
            json.dump(self.agents_config, f)

        self.manager = SessionManager(str(self.config_file))

    def tearDown(self):
        """Clean up"""
        self.temp_dir.cleanup()

    def test_get_claude_model_by_alias(self):
        """Test resolving Claude models by alias"""
        result = self.manager.get_model_from_name("sonnet", "claude")
        self.assertEqual(result, "sonnet")

        result = self.manager.get_model_from_name("haiku", "claude")
        self.assertEqual(result, "haiku")

    def test_get_claude_model_by_full_name(self):
        """Test resolving Claude models by full name"""
        result = self.manager.get_model_from_name("claude-sonnet-4.5", "claude")
        self.assertEqual(result, "sonnet")

    def test_get_invalid_model(self):
        """Test resolving non-existent model"""
        result = self.manager.get_model_from_name("nonexistent-model", "claude")
        self.assertIsNone(result)

    @patch.object(SessionManager, 'fetch_copilot_models')
    def test_get_copilot_model_exact_match(self, mock_fetch):
        """Test exact model name matching for Copilot"""
        mock_fetch.return_value = {
            'GPT Models': ['gpt-5', 'gpt-4'],
            'Other': ['gemini-pro']
        }

        result = self.manager.get_model_from_name("gpt-5", "copilot")
        self.assertEqual(result, "gpt-5")

    @patch.object(SessionManager, 'fetch_copilot_models')
    def test_get_copilot_model_substring_match(self, mock_fetch):
        """Test substring matching for Copilot models"""
        mock_fetch.return_value = {
            'GPT Models': ['gpt-5.2', 'gpt-4'],
            'Other': ['gemini-pro']
        }

        result = self.manager.get_model_from_name("5.2", "copilot")
        self.assertEqual(result, "gpt-5.2")


class TestAgentSwitching(unittest.TestCase):
    """Test agent switching functionality"""

    def setUp(self):
        """Set up test manager"""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

        self.agents_config = {
            "agents": [
                {"name": "agent1", "description": "Agent 1", "path": "/tmp/agent1"},
                {"name": "agent2", "description": "Agent 2", "path": "/tmp/agent2"}
            ]
        }
        self.config_file = self.temp_path / "agents.json"
        with open(self.config_file, 'w') as f:
            json.dump(self.agents_config, f)

        self.patcher = patch('agent_manager.Path.home')
        self.mock_home = self.patcher.start()
        self.mock_home.return_value = self.temp_path

        self.manager = SessionManager(str(self.config_file))

    def tearDown(self):
        """Clean up"""
        self.patcher.stop()
        self.temp_dir.cleanup()

    def test_set_agent_success(self):
        """Test successfully switching agents"""
        result = self.manager.set_agent("test_session", "agent1")
        self.assertIn("agent1", result)

        session_data = self.manager.get_or_create_session_data("test_session")
        self.assertEqual(session_data["agent"], "agent1")

    def test_set_agent_nonexistent(self):
        """Test switching to non-existent agent"""
        result = self.manager.set_agent("test_session", "nonexistent")
        self.assertIn("Unknown agent", result)

    def test_set_agent_generates_new_session_id(self):
        """Test that switching agents generates new backend session ID"""
        session_data_1 = self.manager.get_or_create_session_data("test_session")
        original_id = session_data_1["session_id"]

        self.manager.set_agent("test_session", "agent1")

        session_data_2 = self.manager.get_or_create_session_data("test_session")
        self.assertNotEqual(session_data_2["session_id"], original_id)


class TestSessionExistence(unittest.TestCase):
    """Test session existence checking"""

    def setUp(self):
        """Set up test manager with mocked directories"""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

        self.agents_config = {"agents": []}
        self.config_file = self.temp_path / "agents.json"
        with open(self.config_file, 'w') as f:
            json.dump(self.agents_config, f)

        self.patcher = patch('agent_manager.Path.home')
        self.mock_home = self.patcher.start()
        self.mock_home.return_value = self.temp_path

        self.manager = SessionManager(str(self.config_file))

    def tearDown(self):
        """Clean up"""
        self.patcher.stop()
        self.temp_dir.cleanup()

    def test_copilot_session_exists(self):
        """Test checking if Copilot session exists"""
        # Session should not exist initially
        result = self.manager.session_exists("test_id", "copilot")
        self.assertFalse(result)

        # Create a dummy session file
        session_file = self.manager.session_state_dir / "test_id.jsonl"
        session_file.parent.mkdir(parents=True, exist_ok=True)
        session_file.write_text("{}")

        # Now it should exist
        result = self.manager.session_exists("test_id", "copilot")
        self.assertTrue(result)

    def test_invalid_runtime_session_check(self):
        """Test checking session for invalid runtime"""
        result = self.manager.session_exists("test_id", "invalid")
        self.assertFalse(result)


class TestGeminiSupport(unittest.TestCase):
    """Test Gemini CLI support"""

    def setUp(self):
        """Set up test environment"""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

        self.agents_config = {
            "agents": [
                {"name": "test_devops", "description": "Test DevOps", "path": "/tmp/test"}
            ]
        }
        self.config_file = self.temp_path / "agents.json"
        with open(self.config_file, 'w') as f:
            json.dump(self.agents_config, f)

        self.patcher = patch('agent_manager.Path.home')
        self.mock_home = self.patcher.start()
        self.mock_home.return_value = self.temp_path

        self.manager = SessionManager(str(self.config_file))

    def tearDown(self):
        """Clean up"""
        self.patcher.stop()
        self.temp_dir.cleanup()

    def test_gemini_models_defined(self):
        """Test that Gemini models are properly defined"""
        self.assertIn('Google Models', self.manager.GEMINI_MODELS)
        models = self.manager.GEMINI_MODELS['Google Models']
        self.assertGreater(len(models), 0)
        
        # Check structure of first model
        model_id, desc, aliases = models[0]
        self.assertIsInstance(model_id, str)
        self.assertIsInstance(desc, str)
        self.assertIsInstance(aliases, list)

    def test_runtime_list_includes_gemini(self):
        """Test that /runtime list includes gemini"""
        result = self.manager.execute("/runtime list", "test_session")
        self.assertIn("gemini", result.lower())

    def test_runtime_set_gemini(self):
        """Test switching to Gemini runtime"""
        result = self.manager.execute("/runtime set gemini", "test_session")
        self.assertIn("gemini", result.lower())
        
        # Verify it was set
        session_data = self.manager.get_or_create_session_data("test_session")
        self.assertEqual(session_data["runtime"], "gemini")
        self.assertEqual(session_data["model"], "gemini-1.5-flash")

    def test_get_gemini_model_by_name(self):
        """Test resolving Gemini models by name"""
        result = self.manager.get_model_from_name("gemini-1.5-flash", "gemini")
        self.assertEqual(result, "gemini-1.5-flash")
        
        result = self.manager.get_model_from_name("gemini-pro", "gemini")
        self.assertEqual(result, "gemini-pro")

    def test_get_gemini_model_by_alias(self):
        """Test resolving Gemini models by alias"""
        result = self.manager.get_model_from_name("flash-1.5", "gemini")
        self.assertEqual(result, "gemini-1.5-flash")
        
        result = self.manager.get_model_from_name("pro-1.5", "gemini")
        self.assertEqual(result, "gemini-1.5-pro")

    def test_gemini_session_directory_created(self):
        """Test that Gemini session directory is created"""
        gemini_session_dir = self.temp_path / ".gemini" / "sessions"
        self.assertTrue(gemini_session_dir.exists())

    def test_gemini_session_exists(self):
        """Test session_exists for Gemini runtime"""
        # Create a fake session file
        gemini_session_dir = self.temp_path / ".gemini" / "sessions"
        test_session_file = gemini_session_dir / "test-session-123.json"
        test_session_file.write_text('{}')
        
        result = self.manager.session_exists("test-session-123", "gemini")
        self.assertTrue(result)
        
        # Test non-existent session
        result = self.manager.session_exists("nonexistent-session", "gemini")
        self.assertFalse(result)

    def test_model_list_gemini(self):
        """Test /model list command for Gemini runtime"""
        # Switch to Gemini runtime
        self.manager.execute("/runtime set gemini", "test_session")
        
        # List models
        result = self.manager.execute("/model list", "test_session")
        self.assertIn("gemini", result.lower())
        self.assertIn("Google Models", result)

    def test_model_set_gemini(self):
        """Test /model set command for Gemini runtime"""
        # Switch to Gemini runtime
        self.manager.execute("/runtime set gemini", "test_session")
        
        # Set a specific model
        result = self.manager.execute('/model set "gemini-1.5-pro"', "test_session")
        self.assertIn("gemini-1.5-pro", result)
        
        # Verify it was set
        session_data = self.manager.get_or_create_session_data("test_session")
        self.assertEqual(session_data["model"], "gemini-1.5-pro")


if __name__ == '__main__':
    unittest.main()
