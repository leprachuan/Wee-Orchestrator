"""
Regression test for GitHub issue #23: Per-session locking prevents race conditions.

Issue description:
  Two simultaneous API requests for the same session both load n8n-session-map.json,
  modify independently, and last-write-wins. Causes lost session state and corrupted
  history.

Fix:
  Added per-session threading.Lock() to serialize modifications to individual
  sessions, plus cleanup when sessions are TTL'd to prevent memory leaks.
"""

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

# Add agent_manager to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_manager import SessionManager


def test_issue_23_per_session_locking():
    """Test that concurrent requests to the same session serialize correctly.

    Without per-session locks, concurrent updates can result in lost updates
    (last-write-wins). With locks, all updates are serialized and preserved.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Use temp directory for session map
        home_patch = patch.object(Path, "home", return_value=Path(tmpdir))
        with home_patch:
            manager = SessionManager()

            # Session ID for this test
            session_id = "test_session_123"

            # Initialize session with default data
            data = manager.get_or_create_session_data(session_id)
            assert data is not None

            # Test data to write
            test_values = {
                "field_a": "value_from_thread_a",
                "field_b": "value_from_thread_b",
                "field_c": "value_from_thread_c",
            }

            # Simulate concurrent updates from multiple threads
            errors = []

            def update_field(field_name, field_value):
                """Simulate a concurrent update to a session field."""
                try:
                    manager.update_session_field(session_id, field_name, field_value)
                except Exception as e:
                    errors.append((field_name, str(e)))

            threads = []
            for field_name, field_value in test_values.items():
                t = threading.Thread(
                    target=update_field, args=(field_name, field_value)
                )
                threads.append(t)

            # Start all threads simultaneously
            for t in threads:
                t.start()

            # Wait for all threads to complete
            for t in threads:
                t.join()

            # Verify no errors occurred
            assert not errors, f"Thread errors occurred: {errors}"

            # Verify all updates were preserved
            final_data = manager.load_session_data(session_id)
            assert final_data is not None
            for field_name, field_value in test_values.items():
                assert final_data.get(field_name) == field_value, (
                    f"Field {field_name} not preserved: "
                    f"expected {field_value}, got {final_data.get(field_name)}"
                )


def test_issue_23_per_session_lock_cleanup():
    """Test that per-session locks are cleaned up when sessions are TTL'd.

    Memory leak fix: orphaned Lock objects in _per_session_locks dict should
    be removed when sessions expire.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        home_patch = patch.object(Path, "home", return_value=Path(tmpdir))
        with home_patch:
            manager = SessionManager()

            # Create a real session that will stay
            manager.get_or_create_session_data("keep_session")

            # Create an old session entry manually and its lock
            session_map = manager.load_session_map()
            cutoff = time.time() - (manager.session_map_ttl + 3600)
            session_map["old_session"] = {
                "session_id": "old_backend_id",
                "last_activity": cutoff - 1000,  # Very old
            }

            # Manually create the lock for old_session
            manager._get_per_session_lock("old_session")

            # Verify locks were created
            before_lock_count = len(manager._per_session_locks)
            assert "old_session" in manager._per_session_locks
            assert "keep_session" in manager._per_session_locks
            assert (
                before_lock_count == 2
            ), f"Expected 2 locks before cleanup, got {before_lock_count}"

            # Trigger pruning by saving (which calls _prune_session_map_ttl)
            manager.save_session_map(session_map)

            # After pruning, the old session's lock should be removed
            after_lock_count = len(manager._per_session_locks)
            assert after_lock_count < before_lock_count, (
                f"Lock not cleaned up: before={before_lock_count}, "
                f"after={after_lock_count}"
            )

            # Verify the old session's lock was removed
            assert (
                "old_session" not in manager._per_session_locks
            ), "Old session lock should be removed"
            # Verify the kept session's lock remains
            assert (
                "keep_session" in manager._per_session_locks
            ), "Kept session lock should remain"


def test_issue_23_atomic_writes():
    """Test that session map writes are atomic (no partial writes on crash).

    Uses tempfile + shutil.move pattern to ensure atomicity.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        home_patch = patch.object(Path, "home", return_value=Path(tmpdir))
        with home_patch:
            manager = SessionManager()

            # Create a session
            session_id = "test_atomic"
            data = manager.get_or_create_session_data(session_id)

            # Modify it
            manager.update_session_field(session_id, "test_key", "test_value")

            # Verify file exists and is valid JSON
            session_map_file = manager.session_map_file
            assert session_map_file.exists(), "Session map file should exist"

            # Load and verify content
            with open(session_map_file, "r") as f:
                content = json.load(f)
                assert session_id in content
                assert content[session_id].get("test_key") == "test_value"


def test_issue_23_high_concurrency():
    """Test high concurrency: 100 threads racing on same session.

    Ensures no data corruption, lost updates, or deadlocks under heavy
    concurrent load.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        home_patch = patch.object(Path, "home", return_value=Path(tmpdir))
        with home_patch:
            manager = SessionManager()

            session_id = "high_concurrency_test"
            manager.get_or_create_session_data(session_id)

            # Track operations
            operations = []
            errors = []

            def worker(thread_id, op_count):
                """Each thread performs multiple operations."""
                try:
                    for i in range(op_count):
                        field_name = f"thread_{thread_id}_op_{i}"
                        field_value = f"value_{thread_id}_{i}"
                        manager.update_session_field(
                            session_id, field_name, field_value
                        )
                        operations.append((thread_id, i, field_name, field_value))
                except Exception as e:
                    errors.append((thread_id, str(e)))

            # Spawn 100 threads, each doing 10 operations
            threads = []
            for thread_id in range(100):
                t = threading.Thread(target=worker, args=(thread_id, 10))
                threads.append(t)

            # Start all threads
            for t in threads:
                t.start()

            # Wait for completion
            for t in threads:
                t.join()

            # Verify no errors
            assert not errors, f"Errors in high concurrency test: {errors}"

            # Verify all operations were saved (1000 total)
            final_data = manager.load_session_data(session_id)
            assert final_data is not None
            assert (
                len(final_data) >= 1000
            ), f"Expected at least 1000 fields, got {len(final_data)}"


if __name__ == "__main__":
    test_issue_23_per_session_locking()
    print("✓ test_issue_23_per_session_locking passed")

    test_issue_23_per_session_lock_cleanup()
    print("✓ test_issue_23_per_session_lock_cleanup passed")

    test_issue_23_atomic_writes()
    print("✓ test_issue_23_atomic_writes passed")

    test_issue_23_high_concurrency()
    print("✓ test_issue_23_high_concurrency passed")

    print("\n✅ All issue #23 regression tests passed!")


def test_issue_23_per_session_locking():
    """Test that concurrent requests to the same session serialize correctly.

    Without per-session locks, concurrent updates can result in lost updates
    (last-write-wins). With locks, all updates are serialized and preserved.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Use temp directory for session map
        home_patch = patch.object(Path, "home", return_value=Path(tmpdir))
        with home_patch:
            manager = SessionManager()

            # Session ID for this test
            session_id = "test_session_123"

            # Initialize session with default data
            data = manager.get_or_create_session_data(session_id)
            assert data is not None

            # Test data to write
            test_values = {
                "field_a": "value_from_thread_a",
                "field_b": "value_from_thread_b",
                "field_c": "value_from_thread_c",
            }

            # Simulate concurrent updates from multiple threads
            errors = []

            def update_field(field_name, field_value):
                """Simulate a concurrent update to a session field."""
                try:
                    manager.update_session_field(session_id, field_name, field_value)
                except Exception as e:
                    errors.append((field_name, str(e)))

            threads = []
            for field_name, field_value in test_values.items():
                t = threading.Thread(
                    target=update_field, args=(field_name, field_value)
                )
                threads.append(t)

            # Start all threads simultaneously
            for t in threads:
                t.start()

            # Wait for all threads to complete
            for t in threads:
                t.join()

            # Verify no errors occurred
            assert not errors, f"Thread errors occurred: {errors}"

            # Verify all updates were preserved
            final_data = manager.load_session_data(session_id)
            assert final_data is not None
            for field_name, field_value in test_values.items():
                assert final_data.get(field_name) == field_value, (
                    f"Field {field_name} not preserved: "
                    f"expected {field_value}, got {final_data.get(field_name)}"
                )


def test_issue_23_per_session_lock_cleanup():
    """Test that per-session locks are cleaned up when sessions are TTL'd.

    Memory leak fix: orphaned Lock objects in _per_session_locks dict should
    be removed when sessions expire.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        home_patch = patch.object(Path, "home", return_value=Path(tmpdir))
        with home_patch:
            manager = SessionManager()

            # Create a real session that will stay
            manager.get_or_create_session_data("keep_session")

            # Create an old session entry manually and its lock
            session_map = manager.load_session_map()
            cutoff = time.time() - (manager.session_map_ttl + 3600)
            session_map["old_session"] = {
                "session_id": "old_backend_id",
                "last_activity": cutoff - 1000,  # Very old
            }

            # Manually create the lock for old_session
            manager._get_per_session_lock("old_session")

            # Verify locks were created
            before_lock_count = len(manager._per_session_locks)
            assert "old_session" in manager._per_session_locks
            assert "keep_session" in manager._per_session_locks
            assert (
                before_lock_count == 2
            ), f"Expected 2 locks before cleanup, got {before_lock_count}"

            # Trigger pruning by saving (which calls _prune_session_map_ttl)
            manager.save_session_map(session_map)

            # After pruning, the old session's lock should be removed
            after_lock_count = len(manager._per_session_locks)
            assert after_lock_count < before_lock_count, (
                f"Lock not cleaned up: before={before_lock_count}, "
                f"after={after_lock_count}"
            )

            # Verify the old session's lock was removed
            assert (
                "old_session" not in manager._per_session_locks
            ), "Old session lock should be removed"
            # Verify the kept session's lock remains
            assert (
                "keep_session" in manager._per_session_locks
            ), "Kept session lock should remain"


def test_issue_23_atomic_writes():
    """Test that session map writes are atomic (no partial writes on crash).

    Uses tempfile + shutil.move pattern to ensure atomicity.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        home_patch = patch.object(Path, "home", return_value=Path(tmpdir))
        with home_patch:
            manager = SessionManager()

            # Create a session
            session_id = "test_atomic"
            data = manager.get_or_create_session_data(session_id)

            # Modify it
            manager.update_session_field(session_id, "test_key", "test_value")

            # Verify file exists and is valid JSON
            session_map_file = manager.session_map_file
            assert session_map_file.exists(), "Session map file should exist"

            # Load and verify content
            with open(session_map_file, "r") as f:
                content = json.load(f)
                assert session_id in content
                assert content[session_id].get("test_key") == "test_value"


def test_issue_23_high_concurrency():
    """Test high concurrency: 100 threads racing on same session.

    Ensures no data corruption, lost updates, or deadlocks under heavy
    concurrent load.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        home_patch = patch.object(Path, "home", return_value=Path(tmpdir))
        with home_patch:
            manager = SessionManager()

            session_id = "high_concurrency_test"
            manager.get_or_create_session_data(session_id)

            # Track operations
            operations = []
            errors = []

            def worker(thread_id, op_count):
                """Each thread performs multiple operations."""
                try:
                    for i in range(op_count):
                        field_name = f"thread_{thread_id}_op_{i}"
                        field_value = f"value_{thread_id}_{i}"
                        manager.update_session_field(
                            session_id, field_name, field_value
                        )
                        operations.append((thread_id, i, field_name, field_value))
                except Exception as e:
                    errors.append((thread_id, str(e)))

            # Spawn 100 threads, each doing 10 operations
            threads = []
            for thread_id in range(100):
                t = threading.Thread(target=worker, args=(thread_id, 10))
                threads.append(t)

            # Start all threads
            for t in threads:
                t.start()

            # Wait for completion
            for t in threads:
                t.join()

            # Verify no errors
            assert not errors, f"Errors in high concurrency test: {errors}"

            # Verify all operations were saved (1000 total)
            final_data = manager.load_session_data(session_id)
            assert final_data is not None
            assert (
                len(final_data) >= 1000
            ), f"Expected at least 1000 fields, got {len(final_data)}"


if __name__ == "__main__":
    test_issue_23_per_session_locking()
    print("✓ test_issue_23_per_session_locking passed")

    test_issue_23_per_session_lock_cleanup()
    print("✓ test_issue_23_per_session_lock_cleanup passed")

    test_issue_23_atomic_writes()
    print("✓ test_issue_23_atomic_writes passed")

    test_issue_23_high_concurrency()
    print("✓ test_issue_23_high_concurrency passed")

    print("\n✅ All issue #23 regression tests passed!")
