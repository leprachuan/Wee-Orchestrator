"""Regression tests for issue #303: Disable/Enable runtimes via Settings panel checkboxes."""

import json
import pytest
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_manager import (
    DisabledRuntimesManager,
    get_disabled_runtimes_manager,
    get_available_runtimes,
    get_all_runtimes,
)


class TestDisabledRuntimesManager:
    """Test the DisabledRuntimesManager class."""

    def test_manager_initialization(self, tmp_path):
        """Test that the manager initializes correctly."""
        mgr = DisabledRuntimesManager(config_dir=str(tmp_path))
        assert mgr.get_disabled() == []
        assert not mgr.is_disabled("copilot")

    def test_disable_runtime(self, tmp_path):
        """Test disabling a runtime."""
        mgr = DisabledRuntimesManager(config_dir=str(tmp_path))
        
        # Disable a runtime
        assert mgr.disable("copilot") is True
        assert mgr.is_disabled("copilot")
        assert "copilot" in mgr.get_disabled()
        
        # Disabling already disabled runtime should return False
        assert mgr.disable("copilot") is False

    def test_enable_runtime(self, tmp_path):
        """Test enabling a runtime."""
        mgr = DisabledRuntimesManager(config_dir=str(tmp_path))
        
        # First disable, then enable
        mgr.disable("copilot")
        assert mgr.is_disabled("copilot")
        
        assert mgr.enable("copilot") is True
        assert not mgr.is_disabled("copilot")
        assert "copilot" not in mgr.get_disabled()
        
        # Enabling already enabled runtime should return False
        assert mgr.enable("copilot") is False

    def test_set_disabled_list(self, tmp_path):
        """Test setting the entire disabled list."""
        mgr = DisabledRuntimesManager(config_dir=str(tmp_path))
        
        disabled_list = ["copilot", "claude-sdk", "devin"]
        mgr.set_disabled(disabled_list)
        
        assert mgr.get_disabled() == sorted(disabled_list)
        assert mgr.is_disabled("copilot")
        assert mgr.is_disabled("claude-sdk")
        assert mgr.is_disabled("devin")
        assert not mgr.is_disabled("wee")

    def test_persistence(self, tmp_path):
        """Test that disabled runtimes persist across manager instances."""
        mgr1 = DisabledRuntimesManager(config_dir=str(tmp_path))
        mgr1.disable("copilot")
        mgr1.disable("claude-sdk")
        
        # Create new manager instance (should load from file)
        mgr2 = DisabledRuntimesManager(config_dir=str(tmp_path))
        assert mgr2.is_disabled("copilot")
        assert mgr2.is_disabled("claude-sdk")
        assert not mgr2.is_disabled("wee")

    def test_config_file_format(self, tmp_path):
        """Test that config file is saved in correct JSON format."""
        mgr = DisabledRuntimesManager(config_dir=str(tmp_path))
        mgr.set_disabled(["copilot", "claude"])
        
        config_file = tmp_path / "disabled_runtimes.json"
        assert config_file.exists()
        
        with open(config_file) as f:
            data = json.load(f)
        
        assert "disabled" in data
        assert sorted(data["disabled"]) == ["claude", "copilot"]

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
