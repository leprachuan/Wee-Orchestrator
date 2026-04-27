"""Tests for themes API endpoints (F025 enhanced)."""

import os
import sys

import pytest

os.environ.setdefault("API_SHARED_KEY", "test_key_123")
os.environ.setdefault("APP_ENV", "DEV")
os.environ.setdefault("API_PORT", "8099")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

AUTH_HEADER = {"Authorization": "Bearer shared_test_key_123"}


@pytest.fixture(scope="module")
def client():
    from agent_manager import create_api_app

    app = create_api_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def themes_dir(tmp_path):
    """Create a temporary themes directory with test themes."""
    d = tmp_path / "themes"
    d.mkdir()
    (d / "ocean-breeze.css").write_text(
        '[data-theme="ocean-breeze"] { --accent: #0ea5e9; }'
    )
    (d / "forest.css").write_text('[data-theme="forest"] { --accent: #22c55e; }')
    (d / "custom.css.template").write_text("/* template */")
    (d / ".hidden.css").write_text("/* hidden */")
    return d


class TestListThemes:
    """GET /api/v1/themes."""

    def test_list_themes_returns_builtins(self, client):
        r = client.get("/api/v1/themes", headers=AUTH_HEADER)
        assert r.status_code == 200
        data = r.json()
        assert "themes" in data
        assert "count" in data
        names = [t["name"] for t in data["themes"]]
        assert "emerald" in names
        assert "midnight" in names
        assert "sunrise" in names
        assert "cyberpunk" in names
        assert data["count"] >= 4

    def test_list_themes_builtin_flag(self, client):
        r = client.get("/api/v1/themes", headers=AUTH_HEADER)
        data = r.json()
        for theme in data["themes"]:
            if theme["name"] in ("emerald", "midnight", "sunrise", "cyberpunk"):
                assert theme["builtin"] is True

    def test_list_themes_requires_auth(self, client):
        r = client.get("/api/v1/themes")
        assert r.status_code == 401

    def test_list_themes_includes_custom(self, client, themes_dir):
        with patch("agent_manager._themes_dir", themes_dir):
            r = client.get("/api/v1/themes", headers=AUTH_HEADER)
            data = r.json()
            names = [t["name"] for t in data["themes"]]
            assert "ocean-breeze" in names
            assert "forest" in names

    def test_list_themes_excludes_hidden_and_template(self, client, themes_dir):
        with patch("agent_manager._themes_dir", themes_dir):
            r = client.get("/api/v1/themes", headers=AUTH_HEADER)
            data = r.json()
            names = [t["name"] for t in data["themes"]]
            assert ".hidden" not in names
            assert "custom.css" not in names

    def test_custom_themes_not_builtin(self, client, themes_dir):
        with patch("agent_manager._themes_dir", themes_dir):
            r = client.get("/api/v1/themes", headers=AUTH_HEADER)
            data = r.json()
            for theme in data["themes"]:
                if theme["name"] in ("ocean-breeze", "forest"):
                    assert theme["builtin"] is False

    def test_custom_theme_label_formatting(self, client, themes_dir):
        with patch("agent_manager._themes_dir", themes_dir):
            r = client.get("/api/v1/themes", headers=AUTH_HEADER)
            data = r.json()
            ocean = next(t for t in data["themes"] if t["name"] == "ocean-breeze")
            assert ocean["label"] == "Ocean Breeze"


class TestCustomThemeCSSInListing:
    """Custom theme CSS is included in the themes list response (B01 fix)."""

    def test_custom_theme_includes_css(self, client, themes_dir):
        """CSS content is embedded in the listing so JS can cache and inject it."""
        with patch("agent_manager._themes_dir", themes_dir):
            r = client.get("/api/v1/themes", headers=AUTH_HEADER)
            data = r.json()
            ocean = next(t for t in data["themes"] if t["name"] == "ocean-breeze")
            assert "css" in ocean
            assert "--accent: #0ea5e9" in ocean["css"]

    def test_builtin_themes_have_no_css_field(self, client):
        """Built-in themes ship as static files — no css field in listing."""
        r = client.get("/api/v1/themes", headers=AUTH_HEADER)
        data = r.json()
        for theme in data["themes"]:
            if theme["builtin"]:
                assert "css" not in theme or theme.get("css") is None

    def test_css_endpoint_removed(self, client, themes_dir):
        """GET /api/v1/themes/{name}/css endpoint must not exist (B01 fix)."""
        with patch("agent_manager._themes_dir", themes_dir):
            r = client.get("/api/v1/themes/ocean-breeze/css")
            assert r.status_code == 404

    def test_path_traversal_via_name_rejected(self, client, themes_dir):
        """Theme name regex blocks traversal attempts at the listing level."""
        from agent_manager import _THEME_NAME_RE

        assert not _THEME_NAME_RE.match("../etc/passwd")
        assert not _THEME_NAME_RE.match("..%2fetc%2fpasswd")
        assert not _THEME_NAME_RE.match("bad name!")


class TestThemeNameRegex:
    """Validate _THEME_NAME_RE pattern directly."""

    def test_valid_names(self):
        from agent_manager import _THEME_NAME_RE

        valid = [
            "emerald",
            "midnight",
            "ocean-breeze",
            "my_theme",
            "my.theme",
            "CamelCase",
            "theme123",
            "a",
        ]
        for name in valid:
            assert _THEME_NAME_RE.match(name), f"Should match: {name}"

    def test_invalid_names(self):
        from agent_manager import _THEME_NAME_RE

        invalid = [
            "",
            ".hidden",
            "-start",
            "_start",
            "a" * 65,
            "../traversal",
            "name with space",
        ]
        for name in invalid:
            assert not _THEME_NAME_RE.match(name), f"Should reject: {name}"


class TestThemesDirectory:
    """Verify webui/themes/ directory structure."""

    def test_themes_dir_exists(self):
        themes = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "webui",
            "themes",
        )
        assert os.path.isdir(themes), "webui/themes/ must exist"

    def test_template_exists(self):
        tpl = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "webui",
            "themes",
            "custom.css.template",
        )
        assert os.path.isfile(tpl), "custom.css.template must exist"

    def test_leprachaun_theme_exists(self):
        css = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "webui",
            "themes",
            "leprachaun-glassmorphism.css",
        )
        assert os.path.isfile(css)

    def test_leprachaun_theme_has_data_theme(self):
        css = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "webui",
            "themes",
            "leprachaun-glassmorphism.css",
        )
        content = open(css).read()
        assert '[data-theme="leprachaun-glassmorphism"]' in content
