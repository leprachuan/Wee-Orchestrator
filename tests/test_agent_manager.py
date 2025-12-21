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

Environment Variables:
- TEST_WITH_RUNTIMES: Set to "1" or "true" to run real runtime tests.
  These tests require CLI tools (copilot, opencode, claude, gemini, codex)
  to be installed and available in PATH. Default: disabled (safe for CI/CD)
"""

import unittest
import tempfile
import json
import os
import sys
import shutil
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# Add parent directory to path to import agent_manager
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_manager import SessionManager

# Check if we should run tests that require actual CLI runtimes
ENABLE_RUNTIME_TESTS = os.environ.get('TEST_WITH_RUNTIMES', '').lower() in ('1', 'true', 'yes')

# Helper to skip tests based on runtime availability
def requires_runtime(*runtimes):
    """Decorator to skip test if runtime is not available or tests are disabled"""
    def decorator(test_func):
        def wrapper(*args, **kwargs):
            if not ENABLE_RUNTIME_TESTS:
                return unittest.skip("Runtime tests disabled. Set TEST_WITH_RUNTIMES=1 to enable.")(test_func)(*args, **kwargs)
            
            for runtime in runtimes:
                if not shutil.which(runtime):
                    return unittest.skip(f"Runtime '{runtime}' not found in PATH")(test_func)(*args, **kwargs)
            
            return test_func(*args, **kwargs)
        return wrapper
    return decorator

def has_runtime(runtime):
    """Check if a runtime CLI tool is available"""
    return shutil.which(runtime) is not None


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
        self.assertEqual(session_data["agent"], "orchestrator")
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

    def test_bash_command_pwd(self):
        """Test bash command execution with !pwd"""
        result = self.manager.execute("!pwd", "test_bash_session")
        # Should return current working directory
        self.assertIsNotNone(result)
        self.assertNotIn("Error", result)

    def test_bash_command_echo(self):
        """Test bash command execution with !echo"""
        result = self.manager.execute("!echo 'Hello World'", "test_bash_session")
        self.assertIn("Hello World", result)

    def test_bash_command_ls(self):
        """Test bash command execution with !ls"""
        result = self.manager.execute("!ls", "test_bash_session")
        # Should list files in current directory
        self.assertIsNotNone(result)
        self.assertNotIn("Error", result)

    def test_bash_command_empty(self):
        """Test bash command with empty command"""
        result = self.manager.execute("!", "test_bash_session")
        self.assertIn("Error", result)
        self.assertIn("No command provided", result)

    def test_bash_command_nonexistent(self):
        """Test bash command with nonexistent command"""
        result = self.manager.execute("!nonexistentcommand12345", "test_bash_session")
        # Should contain an error or stderr output
        self.assertIsNotNone(result)

    def test_bash_command_exit_code(self):
        """Test bash command that returns non-zero exit code"""
        result = self.manager.execute("!false", "test_bash_session")
        # Should indicate failure
        self.assertIn("exit code", result.lower())


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


class TestRealRuntimeExecution(unittest.TestCase):
    """Test actual CLI runtime execution (requires runtimes to be installed)
    
    These tests are only run when TEST_WITH_RUNTIMES=1 environment variable is set.
    This allows safe CI/CD runs without requiring all runtimes to be available.
    """

    def setUp(self):
        """Set up test environment with real home directories"""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        
        # Create test agent directories
        self.test_agent_dir = self.temp_path / "test_agent"
        self.test_agent_dir.mkdir(parents=True, exist_ok=True)
        
        # Create agents config
        self.agents_config = {
            "agents": [
                {
                    "name": "test_agent",
                    "description": "Test agent for runtime execution",
                    "path": str(self.test_agent_dir)
                }
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

    @requires_runtime('copilot')
    def test_copilot_simple_prompt(self):
        """Test executing a simple prompt with Copilot CLI"""
        self.manager.execute("/runtime set copilot", "test_copilot")
        
        # Execute a simple prompt
        result = self.manager.execute("Say hello", "test_copilot")
        
        # Should return something (not error)
        self.assertIsNotNone(result)
        self.assertGreater(len(result), 0)
        # Should not be an error message
        self.assertNotIn("Error:", result)

    @requires_runtime('opencode')
    def test_opencode_simple_prompt(self):
        """Test executing a simple prompt with OpenCode CLI"""
        self.manager.execute("/runtime set opencode", "test_opencode")
        
        # Execute a simple prompt
        result = self.manager.execute("Say hello", "test_opencode")
        
        # Should return something (not error)
        self.assertIsNotNone(result)
        self.assertGreater(len(result), 0)
        # Should not be an error message
        self.assertNotIn("Error:", result)

    @requires_runtime('claude')
    def test_claude_simple_prompt(self):
        """Test executing a simple prompt with Claude CLI"""
        self.manager.execute("/runtime set claude", "test_claude")
        
        # Execute a simple prompt
        result = self.manager.execute("Say hello", "test_claude")
        
        # Should return something (not error)
        self.assertIsNotNone(result)
        self.assertGreater(len(result), 0)
        # Should not be an error message
        self.assertNotIn("Error:", result)

    @requires_runtime('gemini')
    def test_gemini_simple_prompt(self):
        """Test executing a simple prompt with Gemini CLI"""
        self.manager.execute("/runtime set gemini", "test_gemini")
        
        # Execute a simple prompt
        result = self.manager.execute("Say hello", "test_gemini")
        
        # Should return something (not error)
        self.assertIsNotNone(result)
        self.assertGreater(len(result), 0)
        # Should not be an error message
        self.assertNotIn("Error:", result)

    @requires_runtime('codex')
    def test_codex_simple_prompt(self):
        """Test executing a simple prompt with CODEX CLI"""
        self.manager.execute("/runtime set codex", "test_codex")
        
        # Execute a simple prompt
        result = self.manager.execute("Say hello", "test_codex")
        
        # Should return something (not error)
        self.assertIsNotNone(result)
        self.assertGreater(len(result), 0)
        # Should not be an error message
        self.assertNotIn("Error:", result)


class TestRuntimeIntegration(unittest.TestCase):
    """Test runtime switching and session management with real CLIs"""

    def setUp(self):
        """Set up test environment"""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        
        self.test_agent_dir = self.temp_path / "test_agent"
        self.test_agent_dir.mkdir(parents=True, exist_ok=True)
        
        self.agents_config = {
            "agents": [
                {
                    "name": "test_agent",
                    "description": "Test agent",
                    "path": str(self.test_agent_dir)
                }
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

    @requires_runtime('copilot')
    def test_runtime_switching_with_copilot(self):
        """Test switching between runtimes with actual Copilot available"""
        # Start with copilot
        self.manager.execute("/runtime set copilot", "test_switch")
        session_data = self.manager.get_or_create_session_data("test_switch")
        self.assertEqual(session_data["runtime"], "copilot")
        
        # Switch to another runtime if available
        if has_runtime('claude'):
            self.manager.execute("/runtime set claude", "test_switch")
            session_data = self.manager.get_or_create_session_data("test_switch")
            self.assertEqual(session_data["runtime"], "claude")

    @requires_runtime('copilot', 'opencode')
    def test_multi_runtime_session_isolation(self):
        """Test that switching between runtimes maintains session isolation"""
        # Create two different sessions with different runtimes
        self.manager.execute("/runtime set copilot", "copilot_session")
        self.manager.execute("/runtime set opencode", "opencode_session")
        
        # Verify they are isolated
        copilot_data = self.manager.get_or_create_session_data("copilot_session")
        opencode_data = self.manager.get_or_create_session_data("opencode_session")
        
        self.assertEqual(copilot_data["runtime"], "copilot")
        self.assertEqual(opencode_data["runtime"], "opencode")
        self.assertNotEqual(copilot_data["session_id"], opencode_data["session_id"])

    @requires_runtime('copilot')
    def test_session_resumption_with_real_cli(self):
        """Test that sessions can be created and tracked with real CLI"""
        # Execute a prompt to create a real session
        result = self.manager.execute("List current directory files", "test_resume")
        
        # Session should have been created
        session_data = self.manager.get_or_create_session_data("test_resume")
        self.assertIn("session_id", session_data)
        self.assertIsNotNone(session_data["session_id"])
        
        # Result should not be empty
        self.assertGreater(len(result), 0)

    @requires_runtime('copilot')
    def test_metadata_stripping_with_real_output(self):
        """Test that metadata stripping works with real CLI output"""
        # Execute a simple prompt
        result = self.manager.execute("Print 'Hello'", "test_metadata")
        
        # Should not contain CLI metadata markers
        self.assertNotIn("Total usage est:", result)
        self.assertNotIn("Total duration", result)
        
        # Should contain actual response
        self.assertGreater(len(result), 0)


class TestCapabilityDiscovery(unittest.TestCase):
    """Test dynamic capability discovery feature"""

    def setUp(self):
        """Set up test environment"""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        
        self.agents_config = {
            "agents": [
                {
                    "name": "infrastructure",
                    "description": "Infrastructure and DevOps management",
                    "path": "/tmp/infra"
                },
                {
                    "name": "data_science",
                    "description": "Data analysis and ML experimentation",
                    "path": "/tmp/data_sci"
                },
                {
                    "name": "documentation",
                    "description": "Knowledge and documentation management",
                    "path": "/tmp/docs"
                }
            ]
        }
        self.config_file = self.temp_path / "agents.json"
        with open(self.config_file, 'w') as f:
            json.dump(self.agents_config, f)
        
        self.manager = SessionManager(str(self.config_file))

    def tearDown(self):
        """Clean up"""
        self.temp_dir.cleanup()

    def test_capabilities_command(self):
        """Test /capabilities command displays all agents"""
        result = self.manager.execute("/capabilities", "test_cap")
        
        self.assertIn("Orchestrator Capabilities", result)
        self.assertIn("infrastructure", result)
        self.assertIn("data_science", result)
        self.assertIn("documentation", result)
        self.assertIn("Infrastructure and DevOps", result)

    def test_capabilities_include_descriptions(self):
        """Test that capabilities include agent descriptions"""
        result = self.manager.execute("/capabilities", "test_cap")
        
        self.assertIn("Infrastructure and DevOps management", result)
        self.assertIn("Data analysis and ML experimentation", result)
        self.assertIn("Knowledge and documentation management", result)

    def test_capabilities_dynamic_discovery(self):
        """Test that capabilities are dynamically discovered from agents.json"""
        capabilities = self.manager.get_capabilities()
        
        # Should include all agents
        self.assertIn("infrastructure", capabilities)
        self.assertIn("data_science", capabilities)
        self.assertIn("documentation", capabilities)
        
        # Should include their descriptions
        self.assertIn("Infrastructure and DevOps", capabilities)

    def test_capabilities_empty_agents(self):
        """Test capabilities when no agents are configured"""
        empty_config = {
            "agents": []
        }
        config_file = self.temp_path / "empty.json"
        with open(config_file, 'w') as f:
            json.dump(empty_config, f)
        
        manager = SessionManager(str(config_file))
        result = manager.execute("/capabilities", "test_empty")
        
        self.assertIn("No agents", result)

    def test_help_includes_capabilities_command(self):
        """Test that /help includes the /capabilities command"""
        result = self.manager.execute("/help", "test_help")
        
        self.assertIn("/capabilities", result)
        self.assertIn("Show what the orchestrator can help with", result)


class TestQueryTracking(unittest.TestCase):
    """Test query tracking functionality for /status and /cancel commands"""

    def setUp(self):
        """Set up test environment"""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        
        self.agents_config = {
            "agents": [
                {"name": "test_agent", "description": "Test agent", "path": "/tmp/test"}
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

    def test_track_running_query(self):
        """Test tracking a running query"""
        self.manager.track_running_query("test_session", 12345, "copilot", "test_agent", "test prompt")
        
        query = self.manager.get_running_query("test_session")
        self.assertIsNotNone(query)
        self.assertEqual(query["pid"], 12345)
        self.assertEqual(query["runtime"], "copilot")
        self.assertEqual(query["agent"], "test_agent")
        self.assertIn("test prompt", query["prompt"])

    def test_update_query_output(self):
        """Test updating query output"""
        self.manager.track_running_query("test_session", 12345, "copilot", "test_agent", "test prompt")
        self.manager.update_query_output("test_session", "Some output from the query")
        
        query = self.manager.get_running_query("test_session")
        self.assertIn("Some output", query["last_output"])

    def test_clear_running_query(self):
        """Test clearing a running query"""
        self.manager.track_running_query("test_session", 12345, "copilot", "test_agent", "test prompt")
        self.manager.clear_running_query("test_session")
        
        query = self.manager.get_running_query("test_session")
        self.assertIsNone(query)

    def test_status_command_no_running_query(self):
        """Test /status when no query is running"""
        result = self.manager.execute("/status", "test_session")
        self.assertIn("No running query", result)

    def test_cancel_command_no_running_query(self):
        """Test /cancel when no query is running"""
        result = self.manager.execute("/cancel", "test_session")
        self.assertIn("No running query to cancel", result)

    @patch('agent_manager.SessionManager.is_process_running')
    def test_status_command_with_running_query(self, mock_is_running):
        """Test /status with a running query"""
        mock_is_running.return_value = True
        
        # Track a fake running query
        self.manager.track_running_query("test_session", 12345, "copilot", "test_agent", "test prompt for query")
        self.manager.update_query_output("test_session", "Recent output from the agent")
        
        result = self.manager.execute("/status", "test_session")
        self.assertIn("Query Running", result)
        self.assertIn("12345", result)  # PID
        self.assertIn("copilot", result)  # Runtime
        self.assertIn("test_agent", result)  # Agent
        self.assertIn("Recent output", result)  # Last output

    @patch('agent_manager.SessionManager.is_process_running')
    @patch('agent_manager.SessionManager.kill_process')
    def test_cancel_command_with_running_query(self, mock_kill, mock_is_running):
        """Test /cancel with a running query"""
        mock_is_running.return_value = True
        mock_kill.return_value = True
        
        # Track a fake running query
        self.manager.track_running_query("test_session", 12345, "copilot", "test_agent", "test prompt")
        
        result = self.manager.execute("/cancel", "test_session")
        self.assertIn("Cancelled running query", result)
        self.assertIn("12345", result)  # PID
        
        # Verify tracking was cleared
        query = self.manager.get_running_query("test_session")
        self.assertIsNone(query)

    def test_help_includes_status_and_cancel(self):
        """Test that /help includes /status and /cancel commands"""
        result = self.manager.execute("/help", "test_session")
        
        self.assertIn("/status", result)
        self.assertIn("/cancel", result)
        self.assertIn("Check status of running query", result)
        self.assertIn("Cancel running query", result)


if __name__ == '__main__':
    # Print test configuration info
    if ENABLE_RUNTIME_TESTS:
        print("\n=== TEST CONFIGURATION ===")
        print("✓ Runtime tests ENABLED (TEST_WITH_RUNTIMES=1)")
        print("\nAvailable runtimes:")
        for runtime in ['copilot', 'opencode', 'claude', 'gemini', 'codex']:
            available = "✓ AVAILABLE" if has_runtime(runtime) else "✗ NOT FOUND"
            print(f"  {runtime:12} {available}")
        print("========================\n")
    else:
        print("\n=== TEST CONFIGURATION ===")
        print("✓ Runtime tests DISABLED (safe for CI/CD)")
        print("  To enable: set TEST_WITH_RUNTIMES=1")
        print("========================\n")
    
    unittest.main()
