"""Tests for session-start memory injection (memory_context.py)."""
import subprocess
import sys
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add parent dir to path so we can import memory_context
sys.path.insert(0, "/opt/n8n-copilot-shim-dev")


class TestGetMemoryContext:
    def test_returns_empty_when_script_missing(self, tmp_path):
        from memory_context import get_memory_context, MEMORY_INJECT_SCRIPT
        with patch("memory_context.MEMORY_INJECT_SCRIPT", tmp_path / "nonexistent.py"):
            result = get_memory_context()
        assert result == ""

    def test_returns_stdout_from_script(self):
        mock_result = MagicMock()
        mock_result.stdout = "[MEMORY CONTEXT]\nsome fact\n[END MEMORY CONTEXT]"
        from memory_context import MEMORY_INJECT_SCRIPT
        with patch("memory_context.MEMORY_INJECT_SCRIPT") as mock_script:
            mock_script.exists.return_value = True
            with patch("subprocess.run", return_value=mock_result) as mock_run:
                from memory_context import get_memory_context
                result = get_memory_context()
        assert "some fact" in result

    def test_returns_empty_on_subprocess_exception(self):
        from memory_context import MEMORY_INJECT_SCRIPT
        with patch("memory_context.MEMORY_INJECT_SCRIPT") as mock_script:
            mock_script.exists.return_value = True
            with patch("subprocess.run", side_effect=Exception("timeout")):
                from memory_context import get_memory_context
                result = get_memory_context()
        assert result == ""

    def test_truncates_oversized_output(self, tmp_path):
        """When output exceeds MAX_MEMORY_CHARS, falls back to MEMORY.md only."""
        core_file = tmp_path / "MEMORY.md"
        core_file.write_text("core facts only")
        mock_result = MagicMock()
        mock_result.stdout = "x" * 5000  # Over MAX_MEMORY_CHARS=4000
        with patch("memory_context.MEMORY_INJECT_SCRIPT") as mock_script:
            mock_script.exists.return_value = True
            with patch("subprocess.run", return_value=mock_result):
                with patch("memory_context.Path") as mock_path_cls:
                    mock_mem_path = MagicMock()
                    mock_mem_path.exists.return_value = True
                    mock_mem_path.read_text.return_value = "core facts only"
                    mock_path_cls.return_value = mock_mem_path
                    from memory_context import get_memory_context
                    result = get_memory_context()
        assert "core facts only" in result


class TestPrependMemory:
    def test_prepends_memory_to_prompt(self):
        from memory_context import prepend_memory
        result = prepend_memory("do the task", "[MEMORY CONTEXT]\nfact\n[END MEMORY CONTEXT]")
        assert result.startswith("[MEMORY CONTEXT]")
        assert "do the task" in result
        assert result.index("[MEMORY CONTEXT]") < result.index("do the task")

    def test_returns_prompt_unchanged_when_empty_context(self):
        from memory_context import prepend_memory
        result = prepend_memory("do the task", "")
        assert result == "do the task"

    def test_returns_prompt_unchanged_when_none_context(self):
        from memory_context import prepend_memory
        result = prepend_memory("do the task", None)
        assert result == "do the task"


class TestDetectCompaction:
    def test_detects_compaction_signal(self):
        from memory_context import detect_compaction
        assert detect_compaction("I don't have context about previous sessions")
        assert detect_compaction("As a new session, I'm starting fresh")
        assert detect_compaction("I don't have access to previous conversations")

    def test_no_compaction_on_normal_response(self):
        from memory_context import detect_compaction
        assert not detect_compaction("Here is the result of your task.")
        assert not detect_compaction("")
        assert not detect_compaction(None)

    def test_case_insensitive(self):
        from memory_context import detect_compaction
        assert detect_compaction("I DON'T HAVE CONTEXT ABOUT anything")
