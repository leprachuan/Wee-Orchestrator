"""Regression tests for Issue #31: structured logging — replace print() with logger calls.

Verifies:
1. _configure_logging() sets up root logger handlers correctly
2. LOG_LEVEL env var is respected
3. LOG_FORMAT=json produces JSON output via _JsonFormatter
4. No diagnostic print() calls remain in agent_manager (only 4 intentional CLI output ones)
"""
import importlib
import json
import logging
import os
import re
import sys
from io import StringIO
from unittest.mock import patch
import pathlib


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import_am():
    """Import agent_manager fresh (bypasses module cache to re-run configure)."""
    if 'agent_manager' in sys.modules:
        del sys.modules['agent_manager']
    import pathlib; sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
    return importlib.import_module('agent_manager')


def _reset_root_logger():
    root = logging.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)
    root.setLevel(logging.WARNING)  # reset to default


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestIssue31StructuredLogging:
    """Issue #31: convert print() to structured logging."""

    def test_issue_31_configure_logging_adds_handler(self):
        """_configure_logging() must add at least one handler to the root logger."""
        _reset_root_logger()
        am = _import_am()
        am._configure_logging()
        root = logging.getLogger()
        assert len(root.handlers) >= 1, (
            "_configure_logging() did not add any handlers to root logger"
        )

    def test_issue_31_default_level_is_info(self):
        """Default LOG_LEVEL should be INFO."""
        _reset_root_logger()
        env = {k: v for k, v in os.environ.items() if k != 'LOG_LEVEL'}
        with patch.dict(os.environ, env, clear=True):
            am = _import_am()
            am._configure_logging()
        root = logging.getLogger()
        assert root.level == logging.INFO, (
            f"Expected root logger level INFO, got {logging.getLevelName(root.level)}"
        )

    def test_issue_31_log_level_env_var_debug(self):
        """LOG_LEVEL=DEBUG should set root logger to DEBUG level."""
        _reset_root_logger()
        with patch.dict(os.environ, {'LOG_LEVEL': 'DEBUG'}, clear=False):
            am = _import_am()
            am._configure_logging()
        root = logging.getLogger()
        assert root.level == logging.DEBUG, (
            f"Expected DEBUG level when LOG_LEVEL=DEBUG, got {logging.getLevelName(root.level)}"
        )

    def test_issue_31_log_level_env_var_warning(self):
        """LOG_LEVEL=WARNING should set root logger to WARNING level."""
        _reset_root_logger()
        with patch.dict(os.environ, {'LOG_LEVEL': 'WARNING'}, clear=False):
            am = _import_am()
            am._configure_logging()
        root = logging.getLogger()
        assert root.level == logging.WARNING, (
            f"Expected WARNING, got {logging.getLevelName(root.level)}"
        )

    def test_issue_31_invalid_log_level_falls_back_to_info(self):
        """Invalid LOG_LEVEL should fall back to INFO."""
        _reset_root_logger()
        with patch.dict(os.environ, {'LOG_LEVEL': 'NONSENSE'}, clear=False):
            am = _import_am()
            am._configure_logging()
        root = logging.getLogger()
        assert root.level == logging.INFO, (
            f"Expected INFO fallback for invalid LOG_LEVEL, got {logging.getLevelName(root.level)}"
        )

    def test_issue_31_json_format_produces_valid_json(self):
        """LOG_FORMAT=json handler should emit valid JSON records."""
        _reset_root_logger()
        stream = StringIO()
        with patch.dict(os.environ, {'LOG_FORMAT': 'json', 'LOG_LEVEL': 'DEBUG'}, clear=False):
            am = _import_am()
            am._configure_logging()

        # Replace the last handler with one writing to our StringIO
        root = logging.getLogger()
        # Find a handler that uses _JsonFormatter
        json_handler = None
        for h in root.handlers:
            if isinstance(h.formatter, am._JsonFormatter):
                json_handler = h
                break
        assert json_handler is not None, "_JsonFormatter handler not found after LOG_FORMAT=json"

        # Add a StringIO handler with the same formatter
        test_handler = logging.StreamHandler(stream)
        test_handler.setFormatter(am._JsonFormatter())
        root.addHandler(test_handler)
        root.setLevel(logging.DEBUG)

        logger = logging.getLogger('test_issue_31')
        logger.info("test structured logging message")

        output = stream.getvalue().strip()
        assert output, "No output from JSON logger"
        record = json.loads(output)
        assert 'timestamp' in record or 'time' in record or 'asctime' in record or 'ts' in record, (
            "JSON log record missing timestamp field"
        )
        assert 'level' in record or 'levelname' in record, (
            "JSON log record missing level field"
        )
        assert 'message' in record or 'msg' in record, (
            "JSON log record missing message field"
        )

    def test_issue_31_no_diagnostic_print_to_stderr(self):
        """agent_manager.py must have no diagnostic print(..., file=sys.stderr) calls.

        Only the 4 intentional CLI-output print(output) calls at the end of main()
        are allowed; those use plain print(output) with no file= kwarg.
        """
        with open(pathlib.Path(__file__).parent.parent / 'agent_manager.py') as f:
            source = f.read()

        # Find all print() calls with file=sys.stderr
        # This pattern catches print(..., file=sys.stderr) diagnostic calls
        matches = re.findall(r'\bprint\s*\(.*?file\s*=\s*sys\.stderr', source, re.DOTALL)
        assert len(matches) == 0, (
            f"Found {len(matches)} print(..., file=sys.stderr) calls that should "
            f"be converted to logger calls: {matches[:3]}"
        )

    def test_issue_31_logger_object_exists(self):
        """agent_manager module must have a logger object."""
        am = _import_am()
        assert hasattr(am, 'logger'), "agent_manager missing 'logger' attribute"
        assert isinstance(am.logger, logging.Logger), (
            f"Expected Logger instance, got {type(am.logger)}"
        )

    def test_issue_31_configure_logging_idempotent(self):
        """Calling _configure_logging() twice must not duplicate handlers."""
        _reset_root_logger()
        am = _import_am()
        am._configure_logging()
        count_after_first = len(logging.getLogger().handlers)
        am._configure_logging()
        count_after_second = len(logging.getLogger().handlers)
        assert count_after_second == count_after_first, (
            f"Handler count grew from {count_after_first} to {count_after_second} on second call"
        )

    def test_issue_31_json_formatter_class_exists(self):
        """_JsonFormatter class must be importable from agent_manager."""
        am = _import_am()
        assert hasattr(am, '_JsonFormatter'), "agent_manager missing '_JsonFormatter' class"
        assert issubclass(am._JsonFormatter, logging.Formatter), (
            "_JsonFormatter must subclass logging.Formatter"
        )


# Allow running directly
if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
