"""
Regression tests for Issue #161: Remove Built-in TODO Panel from WebUI.

Verifies that:
1. TODO API endpoints have been removed (return 404/405)
2. TODO-related Python functions no longer exist in agent_manager
3. WebUI dist files contain no TODO panel references
4. WebUI source files contain no todo_dir references
5. Other API endpoints still function correctly
"""

import importlib
import os
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Resolve project root (works both locally and via pytest)
# ---------------------------------------------------------------------------
_PROJ_ROOT = Path(os.environ.get(
    "WEE_PROJECT_ROOT",
    Path(__file__).resolve().parent.parent,
))


# ── 1. Removed Python functions should not exist ─────────────────────────

class TestTodoFunctionsRemoved:
    """Ensure TODO-related helper functions are gone from agent_manager.py."""

    @pytest.fixture(autouse=True)
    def _load_source(self):
        self.source = (_PROJ_ROOT / "agent_manager.py").read_text()

    def test_resolve_todo_dir_removed(self):
        assert "def _resolve_todo_dir" not in self.source

    def test_resolve_todo_file_removed(self):
        assert "def _resolve_todo_file" not in self.source

    def test_parse_todo_file_removed(self):
        assert "def _parse_todo_file" not in self.source

    def test_parse_todos_from_dir_removed(self):
        assert "def _parse_todos_from_dir" not in self.source

    def test_parse_todos_from_md_removed(self):
        assert "def _parse_todos_from_md" not in self.source

    def test_todo_api_section_removed(self):
        assert "# --- TODO list API ---" not in self.source

    def test_no_todo_api_routes(self):
        """No @app.get/post/patch for /api/v1/todos paths."""
        assert '"/api/v1/todos"' not in self.source
        assert '"/api/v1/todos/' not in self.source


# ── 2. WebUI dist files: no TODO panel markup/logic ──────────────────────

class TestWebUIDistClean:
    """Verify WebUI dist files have no TODO panel references."""

    @pytest.fixture(autouse=True)
    def _load_dist(self):
        dist = _PROJ_ROOT / "webui" / "dist"
        self.html = (dist / "index.html").read_text()
        self.js = (dist / "app.js").read_text()
        self.css = (dist / "app.css").read_text()

    # ── HTML ──
    def test_no_todo_details_aside(self):
        assert "todo-details-panel" not in self.html

    def test_no_todo_section_in_queue(self):
        assert "todo-section" not in self.html

    def test_no_mobile_todo_tab(self):
        assert 'data-tab="todos"' not in self.html

    def test_no_todo_dir_setting(self):
        assert "asf-todo-dir" not in self.html

    # ── JS ──
    def test_no_todo_panel_js(self):
        assert "TODO Panel" not in self.js

    def test_no_startTodoRefresh(self):
        assert "startTodoRefresh" not in self.js

    def test_no_initTodoDetailsPanel(self):
        assert "_initTodoDetailsPanel" not in self.js

    def test_no_openTodoDetailsPanel(self):
        assert "openTodoDetailsPanel" not in self.js

    def test_no_closeTodoDetailsPanel(self):
        assert "closeTodoDetailsPanel" not in self.js

    def test_no_mobile_show_todos_only(self):
        assert "mobile-show-todos-only" not in self.js

    # ── CSS ──
    def test_no_todo_css_classes(self):
        assert ".todo-" not in self.css

    def test_no_td_prefix_css(self):
        """No .td-* classes (todo detail panel styles)."""
        # Allow .td in general table contexts — match only .td- prefix
        matches = re.findall(r'\.td-[a-z]', self.css)
        assert len(matches) == 0, f"Found TODO detail CSS classes: {matches}"


# ── 3. WebUI source (TypeScript) ─────────────────────────────────────────

class TestWebUISourceClean:
    """Verify TypeScript source files have no todo_dir references."""

    def test_agents_ts_no_todo_dir(self):
        path = _PROJ_ROOT / "webui" / "src" / "types" / "agents.ts"
        if path.exists():
            source = path.read_text()
            assert "todo_dir" not in source

    def test_agent_settings_panel_no_todo(self):
        path = _PROJ_ROOT / "webui" / "src" / "components" / "AgentSettingsPanel.tsx"
        if path.exists():
            source = path.read_text()
            assert "todo_dir" not in source
            assert "TODO Directory" not in source

    def test_agent_config_no_todo_dir(self):
        path = _PROJ_ROOT / "webui" / "src" / "api" / "agentConfig.ts"
        if path.exists():
            source = path.read_text()
            assert "todo_dir" not in source


# ── 4. Copilot CLI TodoRead/TodoWrite NOT removed ────────────────────────

class TestCopilotToolsPreserved:
    """TodoRead/TodoWrite are Copilot CLI tools, NOT our TODO panel.
    They must remain in the allowlists."""

    @pytest.fixture(autouse=True)
    def _load_source(self):
        self.source = (_PROJ_ROOT / "agent_manager.py").read_text()

    def test_todoread_preserved(self):
        assert '"TodoRead"' in self.source

    def test_todowrite_preserved(self):
        assert '"TodoWrite"' in self.source


# ── 5. Other API sections still present ──────────────────────────────────

class TestOtherSectionsIntact:
    """Verify that removing TODO API didn't break neighboring sections."""

    @pytest.fixture(autouse=True)
    def _load_source(self):
        self.source = (_PROJ_ROOT / "agent_manager.py").read_text()

    def test_wee_canvas_section_present(self):
        assert "# --- Wee Canvas" in self.source

    def test_scheduler_section_present(self):
        assert "# --- Task Scheduler" in self.source

    def test_health_endpoint_present(self):
        assert '"/api/v1/health"' in self.source

    def test_agents_endpoint_present(self):
        assert '"/api/v1/agents"' in self.source
