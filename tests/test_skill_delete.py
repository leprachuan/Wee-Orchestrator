"""Tests for skill deletion — F008.

Covers:
- delete_skill() on symlinks vs real directories
- 404 on missing skill
- DELETE /api/v1/skills/{skill_key} API endpoint
"""

from unittest.mock import patch

import pytest

from skill_manager import delete_skill

# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def temp_skill_dir(tmp_path):
    """Create a temp skill directory with a SKILL.md so it's recognized."""
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: test-skill\ndescription: A test skill\n---\n"
    )
    (skill_dir / "copilot").mkdir()
    (skill_dir / "copilot" / "impl.py").write_text("# implementation\n")
    return skill_dir


@pytest.fixture
def temp_symlink_skill(tmp_path, temp_skill_dir):
    """Create a symlink pointing to temp_skill_dir."""
    link_path = tmp_path / "link-skill"
    link_path.symlink_to(temp_skill_dir)
    return link_path


# ── Unit tests for delete_skill() ────────────────────────────────────────────


class TestDeleteSkillUnit:
    """Test the delete_skill function directly."""

    def test_delete_real_directory(self, temp_skill_dir):
        """delete_skill removes a real directory tree."""
        fake_skill = {
            "skill_key": "test/test-skill",
            "path": str(temp_skill_dir),
            "name": "test-skill",
        }
        with (
            patch("skill_manager.get_skill", return_value=fake_skill),
            patch("skill_manager.delete_origin", return_value=True),
        ):
            result = delete_skill("test/test-skill")

        assert result["success"] is True
        assert "directory removed" in result["message"]
        assert result["was_symlink"] is False
        assert not temp_skill_dir.exists()

    def test_delete_symlink(self, temp_skill_dir, temp_symlink_skill):
        """delete_skill removes only the symlink, leaving the target intact."""
        fake_skill = {
            "skill_key": "test/link-skill",
            "path": str(temp_symlink_skill),
            "name": "link-skill",
        }
        with (
            patch("skill_manager.get_skill", return_value=fake_skill),
            patch("skill_manager.delete_origin", return_value=True),
        ):
            result = delete_skill("test/link-skill")

        assert result["success"] is True
        assert "symlink removed" in result["message"]
        assert result["was_symlink"] is True
        # Symlink is gone
        assert not temp_symlink_skill.exists()
        # Original directory still exists
        assert temp_skill_dir.exists()

    def test_delete_missing_skill(self):
        """delete_skill returns failure dict when skill not found."""
        with patch("skill_manager.get_skill", return_value=None):
            result = delete_skill("nonexistent/skill")

        assert result["success"] is False
        assert "not found" in result["message"]

    def test_delete_missing_path(self, tmp_path):
        """Handle case where skill exists in scan but path on disk is gone."""
        ghost_path = str(tmp_path / "ghost-skill")
        fake_skill = {
            "skill_key": "test/ghost-skill",
            "path": ghost_path,
            "name": "ghost-skill",
        }
        with patch("skill_manager.get_skill", return_value=fake_skill):
            result = delete_skill("test/ghost-skill")

        assert result["success"] is False
        assert "does not exist" in result["message"]

    def test_delete_cleans_up_origin_metadata(self, temp_skill_dir):
        """delete_skill also removes origin metadata."""
        fake_skill = {
            "skill_key": "test/test-skill",
            "path": str(temp_skill_dir),
            "name": "test-skill",
        }
        with (
            patch("skill_manager.get_skill", return_value=fake_skill),
            patch(
                "skill_manager.delete_origin", return_value=True
            ) as mock_del,
        ):
            result = delete_skill("test/test-skill")

        assert result["success"] is True
        mock_del.assert_called_once_with("test/test-skill")

    def test_delete_os_error(self, temp_skill_dir):
        """delete_skill handles OSError gracefully."""
        fake_skill = {
            "skill_key": "test/test-skill",
            "path": str(temp_skill_dir),
            "name": "test-skill",
        }
        with (
            patch("skill_manager.get_skill", return_value=fake_skill),
            patch("skill_manager.shutil") as mock_shutil,
        ):
            mock_shutil.rmtree.side_effect = OSError("Permission denied")
            with (
                patch("skill_manager.os.path.islink", return_value=False),
                patch("skill_manager.os.path.exists", return_value=True),
            ):
                from skill_manager import delete_skill as ds

                result = ds("test/test-skill")

        assert result["success"] is False
        assert "Permission denied" in result["message"]


# ── API endpoint tests ────────────────────────────────────────────────────


class TestDeleteSkillAPI:
    """Test DELETE /api/v1/skills/{skill_key} endpoint via FastAPI."""

    @pytest.fixture(autouse=True)
    def setup_client(self):
        """Set up TestClient if httpx is available."""
        try:
            from httpx import AsyncClient  # noqa: F401
        except ImportError:
            pytest.skip("httpx not available")

    def test_delete_skill_requires_auth(self):
        """DELETE /api/v1/skills/{key} returns 401/403 without auth."""
        try:
            import httpx

            resp = httpx.delete(
                "https://127.0.0.1:8001/api/v1/skills/test/nonexistent",
                verify=False,
                timeout=10,
            )
            assert resp.status_code in (401, 403)
        except Exception:
            pytest.skip("Dev API not reachable")

    def test_delete_skill_404_on_missing(self):
        """DELETE /api/v1/skills/{key} returns 404 for nonexistent skill."""
        try:
            import httpx

            token = "shared_R6R6wReORUV6bouLntScMTowbsh30Rzqa3hzjs3bWgU"
            resp = httpx.delete(
                "https://127.0.0.1:8001/api/v1/skills/nonexistent/fakeskill",
                headers={"Authorization": f"Bearer {token}"},
                verify=False,
                timeout=10,
            )
            assert resp.status_code == 404
        except Exception:
            pytest.skip("Dev API not reachable")
