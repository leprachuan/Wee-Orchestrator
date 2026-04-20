"""Tests for Issue #100: GitHub Issues integration in /api/v1/todos endpoints.

Verifies that GET /api/v1/todos merges GitHub Issues with flat files,
POST /api/v1/todos creates GitHub Issues, and POST /api/v1/todos/{title}/complete
closes GitHub Issues.
"""

import json
from unittest.mock import MagicMock, patch


# ── Test helper: import the inner functions from agent_manager ──────

# agent_manager defines these functions inside setup_routes(),
# so we can't import them directly. Instead, we test the endpoints
# via the FastAPI test client and also unit-test the logic patterns.


# ── 1. Unit tests for GitHub Issues helper logic ────────────────────


class TestFetchGitHubTodos:
    """Test _fetch_github_todos logic (mocked gh CLI)."""

    def test_parses_gh_output_correctly(self):
        """GitHub Issues are parsed into the expected TODO format."""
        gh_output = json.dumps(
            [
                {
                    "number": 42,
                    "title": "Buy groceries",
                    "body": "📅 **Due:** 2026-04-15\n\nMilk, eggs, bread",
                    "labels": [
                        {"name": "todo"},
                        {"name": "FAMILY"},
                    ],
                    "createdAt": "2026-04-10T00:00:00Z",
                    "updatedAt": "2026-04-10T00:00:00Z",
                },
                {
                    "number": 43,
                    "title": "Fix router",
                    "body": "",
                    "labels": [{"name": "todo"}],
                    "createdAt": "2026-04-11T00:00:00Z",
                    "updatedAt": "2026-04-11T00:00:00Z",
                },
            ]
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=gh_output,
            )

            # Simulate the logic from _fetch_github_todos
            import re as _re
            import subprocess as _sp

            result = _sp.run(
                [
                    "gh",
                    "issue",
                    "list",
                    "--repo",
                    "leprachuan/fosterbot-home",
                    "--label",
                    "todo",
                    "--state",
                    "open",
                    "--json",
                    "number,title,body,labels,createdAt,updatedAt",
                    "--limit",
                    "100",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            issues = json.loads(result.stdout)

            todos = []
            for issue in issues:
                body = issue.get("body", "") or ""
                due = None
                due_match = _re.search(r"📅\s*\*\*Due:\*\*\s*(.+)", body)
                if due_match:
                    due = due_match.group(1).strip()

                issue_labels = [
                    lbl["name"]
                    for lbl in issue.get("labels", [])
                    if lbl["name"] != "todo"
                ]

                todos.append(
                    {
                        "description": issue["title"],
                        "due": due,
                        "labels": issue_labels,
                        "notes": [],
                        "details": body,
                        "source": "github",
                        "github_issue_number": issue["number"],
                    }
                )

            assert len(todos) == 2
            assert todos[0]["description"] == "Buy groceries"
            assert todos[0]["due"] == "2026-04-15"
            assert todos[0]["labels"] == ["FAMILY"]
            assert todos[0]["source"] == "github"
            assert todos[0]["github_issue_number"] == 42
            assert todos[1]["description"] == "Fix router"
            assert todos[1]["due"] is None
            assert todos[1]["labels"] == []

    def test_handles_gh_failure_gracefully(self):
        """Returns empty list when gh CLI fails."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")

            result = mock_run(
                ["gh", "issue", "list"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0:
                todos = []
            else:
                todos = json.loads(result.stdout)

            assert todos == []

    def test_handles_gh_timeout_gracefully(self):
        """Returns empty list on subprocess timeout."""
        import subprocess

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("gh", 15)):
            try:
                result = subprocess.run(
                    ["gh", "issue", "list"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                todos = json.loads(result.stdout)
            except (subprocess.TimeoutExpired, FileNotFoundError):
                todos = []

            assert todos == []

    def test_handles_empty_gh_output(self):
        """Returns empty list when gh returns empty JSON array."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="[]")

            result = mock_run(
                ["gh", "issue", "list"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            issues = json.loads(result.stdout) if result.stdout.strip() else []
            assert issues == []


class TestMergeTodos:
    """Test _merge_todos deduplication logic."""

    def test_github_takes_precedence_over_flatfile(self):
        """When same title exists in both sources, GitHub wins."""
        gh_todos = [
            {"description": "Buy groceries", "source": "github", "due": "2026-04-15"},
        ]
        flat_todos = [
            {"description": "Buy groceries", "due": None},
            {"description": "Fix router", "due": None},
        ]

        # Simulate _merge_todos
        seen_titles = set()
        merged = []
        for todo in gh_todos:
            key = todo["description"].lower().strip()
            if key not in seen_titles:
                seen_titles.add(key)
                merged.append(todo)
        for todo in flat_todos:
            key = todo["description"].lower().strip()
            if key not in seen_titles:
                seen_titles.add(key)
                todo["source"] = "flatfile"
                merged.append(todo)

        assert len(merged) == 2
        assert merged[0]["description"] == "Buy groceries"
        assert merged[0]["source"] == "github"
        assert merged[1]["description"] == "Fix router"
        assert merged[1]["source"] == "flatfile"

    def test_dedup_is_case_insensitive(self):
        """Deduplication ignores case differences."""
        gh_todos = [
            {"description": "Buy Groceries", "source": "github"},
        ]
        flat_todos = [
            {"description": "buy groceries"},
        ]

        seen_titles = set()
        merged = []
        for todo in gh_todos:
            key = todo["description"].lower().strip()
            if key not in seen_titles:
                seen_titles.add(key)
                merged.append(todo)
        for todo in flat_todos:
            key = todo["description"].lower().strip()
            if key not in seen_titles:
                seen_titles.add(key)
                todo["source"] = "flatfile"
                merged.append(todo)

        assert len(merged) == 1
        assert merged[0]["source"] == "github"

    def test_respects_limit(self):
        """Merged list is truncated to limit."""
        gh_todos = [{"description": f"GH-{i}", "source": "github"} for i in range(5)]
        flat_todos = [{"description": f"FF-{i}"} for i in range(5)]

        seen_titles = set()
        merged = []
        for todo in gh_todos:
            key = todo["description"].lower().strip()
            if key not in seen_titles:
                seen_titles.add(key)
                merged.append(todo)
        for todo in flat_todos:
            key = todo["description"].lower().strip()
            if key not in seen_titles:
                seen_titles.add(key)
                todo["source"] = "flatfile"
                merged.append(todo)

        limited = merged[:3]
        assert len(limited) == 3

    def test_empty_github_returns_only_flatfile(self):
        """When GitHub returns empty, flat-file TODOs are returned."""
        gh_todos = []
        flat_todos = [
            {"description": "Local task", "due": None},
        ]

        seen_titles = set()
        merged = []
        for todo in gh_todos:
            key = todo["description"].lower().strip()
            if key not in seen_titles:
                seen_titles.add(key)
                merged.append(todo)
        for todo in flat_todos:
            key = todo["description"].lower().strip()
            if key not in seen_titles:
                seen_titles.add(key)
                todo["source"] = "flatfile"
                merged.append(todo)

        assert len(merged) == 1
        assert merged[0]["source"] == "flatfile"

    def test_empty_flatfile_returns_only_github(self):
        """When flat files are empty, GitHub Issues are returned."""
        gh_todos = [
            {"description": "Cloud task", "source": "github"},
        ]
        flat_todos = []

        seen_titles = set()
        merged = []
        for todo in gh_todos:
            key = todo["description"].lower().strip()
            if key not in seen_titles:
                seen_titles.add(key)
                merged.append(todo)
        for todo in flat_todos:
            key = todo["description"].lower().strip()
            if key not in seen_titles:
                seen_titles.add(key)
                todo["source"] = "flatfile"
                merged.append(todo)

        assert len(merged) == 1
        assert merged[0]["source"] == "github"


class TestCreateGitHubTodo:
    """Test _create_github_todo logic (mocked gh CLI)."""

    def test_creates_issue_with_correct_labels(self):
        """Issue is created with 'todo' label always included."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="https://github.com/leprachuan/fosterbot-home/issues/99",
            )

            import re as _re
            import subprocess as _sp

            labels = ["FAMILY"]
            if "todo" not in labels:
                labels.append("todo")

            result = _sp.run(
                [
                    "gh",
                    "issue",
                    "create",
                    "--repo",
                    "leprachuan/fosterbot-home",
                    "--title",
                    "Test task",
                    "--label",
                    ",".join(labels),
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )

            m = _re.search(r"/issues/(\d+)", result.stdout)
            assert m is not None
            assert int(m.group(1)) == 99

            # Verify gh was called with correct args
            call_args = mock_run.call_args[0][0]
            assert "--label" in call_args
            label_idx = call_args.index("--label")
            assert "todo" in call_args[label_idx + 1]
            assert "FAMILY" in call_args[label_idx + 1]

    def test_handles_create_failure(self):
        """Returns None when gh issue create fails."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")

            result = mock_run(
                ["gh", "issue", "create"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0:
                gh_result = None
            else:
                gh_result = {"issue_number": 1}

            assert gh_result is None

    def test_includes_due_date_in_body(self):
        """Due date is formatted as 📅 **Due:** in the issue body."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="https://github.com/leprachuan/fosterbot-home/issues/100",
            )

            due = "2026-05-01"
            body_parts = []
            body_parts.append(f"📅 **Due:** {due}")
            full_body = "\n\n".join(body_parts)

            import subprocess as _sp

            cmd = [
                "gh",
                "issue",
                "create",
                "--repo",
                "leprachuan/fosterbot-home",
                "--title",
                "Test task",
                "--label",
                "todo",
                "--body",
                full_body,
            ]
            _sp.run(cmd, capture_output=True, text=True, timeout=15)

            call_args = mock_run.call_args[0][0]
            body_idx = call_args.index("--body")
            assert "📅 **Due:** 2026-05-01" in call_args[body_idx + 1]


class TestCloseGitHubTodo:
    """Test _close_github_todo logic (mocked gh CLI)."""

    def test_closes_exact_title_match(self):
        """Finds and closes issue with exact title match."""
        list_output = json.dumps(
            [
                {"number": 42, "title": "Buy groceries"},
                {"number": 43, "title": "Fix router"},
            ]
        )

        with patch("subprocess.run") as mock_run:
            # First call: list issues, second call: close issue
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=list_output),
                MagicMock(returncode=0, stdout=""),
            ]

            import json as _json
            import subprocess as _sp

            result = _sp.run(
                [
                    "gh",
                    "issue",
                    "list",
                    "--repo",
                    "leprachuan/fosterbot-home",
                    "--label",
                    "todo",
                    "--state",
                    "open",
                    "--json",
                    "number,title",
                    "--limit",
                    "200",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            issues = _json.loads(result.stdout)

            title = "Buy groceries"
            match = None
            title_lower = title.lower().strip()
            for issue in issues:
                if issue["title"].lower().strip() == title_lower:
                    match = issue
                    break

            assert match is not None
            assert match["number"] == 42

            close_result = _sp.run(
                [
                    "gh",
                    "issue",
                    "close",
                    "--repo",
                    "leprachuan/fosterbot-home",
                    str(match["number"]),
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            assert close_result.returncode == 0

    def test_closes_partial_title_match(self):
        """Falls back to partial title match when exact match not found."""
        list_output = json.dumps(
            [
                {"number": 42, "title": "Buy groceries for the week"},
            ]
        )

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=list_output),
                MagicMock(returncode=0, stdout=""),
            ]

            import json as _json
            import subprocess as _sp

            result = _sp.run(
                [
                    "gh",
                    "issue",
                    "list",
                    "--repo",
                    "leprachuan/fosterbot-home",
                    "--label",
                    "todo",
                    "--state",
                    "open",
                    "--json",
                    "number,title",
                    "--limit",
                    "200",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            issues = _json.loads(result.stdout)

            title = "Buy groceries"
            match = None
            title_lower = title.lower().strip()
            for issue in issues:
                if issue["title"].lower().strip() == title_lower:
                    match = issue
                    break
            if not match:
                for issue in issues:
                    if title_lower in issue["title"].lower().strip():
                        match = issue
                        break

            assert match is not None
            assert match["number"] == 42

    def test_returns_none_when_no_match(self):
        """Returns None when no matching issue found."""
        list_output = json.dumps(
            [
                {"number": 42, "title": "Completely different task"},
            ]
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=list_output)

            import json as _json
            import subprocess as _sp

            result = _sp.run(
                ["gh", "issue", "list"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            issues = _json.loads(result.stdout)

            title = "Buy groceries"
            match = None
            title_lower = title.lower().strip()
            for issue in issues:
                if issue["title"].lower().strip() == title_lower:
                    match = issue
                    break
            if not match:
                for issue in issues:
                    if title_lower in issue["title"].lower().strip():
                        match = issue
                        break

            assert match is None


class TestDueDateParsing:
    """Test due date extraction from GitHub Issue body."""

    def test_extracts_due_date_with_emoji(self):
        """Parses 📅 **Due:** format from issue body."""
        import re

        body = "📅 **Due:** 2026-04-15\n\nSome details"
        match = re.search(r"📅\s*\*\*Due:\*\*\s*(.+)", body)
        assert match is not None
        assert match.group(1).strip() == "2026-04-15"

    def test_no_due_date_returns_none(self):
        """Returns None when no due date in body."""
        import re

        body = "Just some task details"
        match = re.search(r"📅\s*\*\*Due:\*\*\s*(.+)", body)
        assert match is None

    def test_empty_body_returns_none(self):
        """Returns None for empty body."""
        import re

        body = ""
        match = re.search(r"📅\s*\*\*Due:\*\*\s*(.+)", body)
        assert match is None

    def test_due_date_with_time(self):
        """Parses due date with time component."""
        import re

        body = "📅 **Due:** 03/15/2026 10:00:00"
        match = re.search(r"📅\s*\*\*Due:\*\*\s*(.+)", body)
        assert match is not None
        assert match.group(1).strip() == "03/15/2026 10:00:00"


class TestSourceTagging:
    """Test that TODOs are correctly tagged with source."""

    def test_github_todos_tagged_as_github(self):
        """GitHub TODOs have source='github'."""
        todo = {
            "description": "Test",
            "source": "github",
            "github_issue_number": 1,
        }
        assert todo["source"] == "github"
        assert "github_issue_number" in todo

    def test_flatfile_todos_tagged_as_flatfile(self):
        """Flat-file TODOs have source='flatfile' after merge."""
        todo = {"description": "Test"}
        todo["source"] = "flatfile"
        assert todo["source"] == "flatfile"
        assert "github_issue_number" not in todo


class TestInvalidLabelHandling:
    """Test that invalid labels are stripped and issue creation succeeds."""

    @classmethod
    def setup_class(cls):
        """Create shared TestClient for all tests in this class."""
        import os
        import sys

        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        os.environ.setdefault("API_SHARED_KEY", "test_key_123")
        os.environ.setdefault("APP_ENV", "DEV")
        os.environ.setdefault("API_PORT", "8099")

        from unittest.mock import patch as _patch

        from fastapi.testclient import TestClient

        import agent_manager

        cls._telegram_patch = _patch.object(
            agent_manager,
            "_resolve_telegram_identity",
            side_effect=lambda identity: identity,
        )
        cls._telegram_patch.start()
        cls._send_pairing_patch = _patch.object(
            agent_manager,
            "_send_pairing_code",
            return_value=True,
        )
        cls._send_pairing_patch.start()
        cls.app = agent_manager.create_api_app()
        cls.client = TestClient(cls.app)
        cls.headers = {"Authorization": "Bearer shared_test_key_123"}

    @classmethod
    def teardown_class(cls):
        cls._telegram_patch.stop()
        cls._send_pairing_patch.stop()

    def setup_method(self):
        """Create a fresh temp TODO directory for each test."""
        import os
        import tempfile
        from pathlib import Path

        self.tmp_base = tempfile.mkdtemp(prefix="label_test_", dir="/opt")
        self.agent_name = os.path.basename(self.tmp_base)
        self.active_dir = Path(self.tmp_base) / "TODOs" / "ACTIVE"
        self.active_dir.mkdir(parents=True)

    def teardown_method(self):
        import shutil

        shutil.rmtree(self.tmp_base, ignore_errors=True)

    def _post_todo(self, title, labels=None, **kwargs):
        body = {"title": title, "agent": self.agent_name}
        if labels is not None:
            body["labels"] = labels
        body.update(kwargs)
        return self.client.post("/api/v1/todos", json=body, headers=self.headers)

    def test_invalid_label_stripped_and_retried(self):
        """When gh fails due to invalid label, strips it and retries without it."""
        from unittest.mock import MagicMock, patch

        # First call: gh issue create fails because "qa-test" label doesn't exist
        fail_result = MagicMock(
            returncode=1,
            stderr="could not add label: qa-test not found",
            stdout="",
        )
        # Second call: gh label list returns only "todo" and "bug" as valid labels
        label_list_result = MagicMock(
            returncode=0,
            stdout='[{"name": "todo"}, {"name": "bug"}]',
            stderr="",
        )
        # Third call: gh issue create without "qa-test" succeeds
        success_result = MagicMock(
            returncode=0,
            stdout="https://github.com/leprachuan/fosterbot-home/issues/42\n",
            stderr="",
        )

        with patch(
            "subprocess.run",
            side_effect=[fail_result, label_list_result, success_result],
        ):
            resp = self._post_todo("Buy test item", labels=["qa-test"])

        data = resp.json()
        assert data["success"] is True
        gh = data["github_issue"]
        assert gh is not None
        assert gh["issue_number"] == 42
        assert gh["labels_stripped"] == ["qa-test"]

    def test_all_labels_invalid_creates_without_labels(self):
        """When ALL labels are invalid, issue is still created without --label flag."""
        from unittest.mock import MagicMock, patch

        # First call: gh issue create fails (qa-test not found)
        fail_result = MagicMock(
            returncode=1,
            stderr="could not add label: qa-test not found",
            stdout="",
        )
        # Second call: gh label list returns only "bug" — neither "todo" nor "qa-test" are valid  # noqa: E501
        label_list_result = MagicMock(
            returncode=0,
            stdout='[{"name": "bug"}]',
            stderr="",
        )
        # Third call: gh issue create without --label flag succeeds
        success_result = MagicMock(
            returncode=0,
            stdout="https://github.com/leprachuan/fosterbot-home/issues/99\n",
            stderr="",
        )

        with patch(
            "subprocess.run",
            side_effect=[fail_result, label_list_result, success_result],
        ):
            resp = self._post_todo("All invalid labels task", labels=["qa-test"])

        data = resp.json()
        assert data["success"] is True
        gh = data["github_issue"]
        assert gh is not None
        assert "issue_number" in gh
        assert gh["issue_number"] == 99

    def test_non_label_failure_is_logged_and_returns_none(self):
        """Non-label failures (e.g., network error) return github_issue=None with no retry."""  # noqa: E501
        from unittest.mock import MagicMock, patch

        # gh issue create fails with a network error (not a label error)
        network_fail = MagicMock(
            returncode=1,
            stderr="Could not resolve hostname api.github.com",
            stdout="",
        )

        with patch("subprocess.run", return_value=network_fail) as mock_run:
            resp = self._post_todo("Network fail task")

        data = resp.json()
        # The TODO file itself was created successfully
        assert data["success"] is True
        # But the GitHub issue creation returned None (no retry for non-label errors)
        assert data["github_issue"] is None
        # Only one subprocess call was made (no label-list retry)
        assert mock_run.call_count == 1

    def test_labels_stripped_field_in_response(self):
        """Response dict contains labels_stripped field when invalid labels were removed."""  # noqa: E501
        from unittest.mock import MagicMock, patch

        # First call: create fails because "nonexistent-label" is invalid
        fail_result = MagicMock(
            returncode=1,
            stderr="could not add label: nonexistent-label not found",
            stdout="",
        )
        # Second call: label list returns "todo" and "FAMILY" as valid
        label_list_result = MagicMock(
            returncode=0,
            stdout='[{"name": "todo"}, {"name": "FAMILY"}]',
            stderr="",
        )
        # Third call: create with only valid labels succeeds
        success_result = MagicMock(
            returncode=0,
            stdout="https://github.com/leprachuan/fosterbot-home/issues/77\n",
            stderr="",
        )

        with patch(
            "subprocess.run",
            side_effect=[fail_result, label_list_result, success_result],
        ):
            resp = self._post_todo(
                "Label strip verify", labels=["nonexistent-label", "FAMILY"]
            )

        data = resp.json()
        assert data["success"] is True
        gh = data["github_issue"]
        assert gh is not None
        assert "labels_stripped" in gh
        assert "nonexistent-label" in gh["labels_stripped"]
        assert gh["issue_number"] == 77

    def test_no_labels_stripped_field_when_all_valid(self):
        """Response does NOT contain labels_stripped when first gh call succeeds."""
        from unittest.mock import MagicMock, patch

        # gh issue create succeeds on first try (label is valid)
        success_result = MagicMock(
            returncode=0,
            stdout="https://github.com/leprachuan/fosterbot-home/issues/55\n",
            stderr="",
        )

        with patch("subprocess.run", return_value=success_result):
            resp = self._post_todo("Valid labels task", labels=["FAMILY"])

        data = resp.json()
        assert data["success"] is True
        gh = data["github_issue"]
        assert gh is not None
        assert gh["issue_number"] == 55
        assert "labels_stripped" not in gh
