"""
Test for issue #23 lock cleanup: Verify orphaned locks are cleaned up when sessions are TTL'd.
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, "/opt/n8n-copilot-shim-dev")


def test_lock_cleanup_on_ttl_eviction():
    """Test that per-session locks are cleaned up when sessions are evicted by TTL."""
    from agent_manager import SessionManager

    with tempfile.TemporaryDirectory() as tmpdir:
        session_map_file = Path(tmpdir) / "test-session-map.json"

        # Create a session manager with a very short TTL
        sm = SessionManager()
        sm.session_map_file = session_map_file
        sm.session_map_ttl = 2  # 2 seconds TTL for testing

        # Create initial sessions with old timestamps
        now = time.time()
        old_time = now - 10  # 10 seconds ago (will be evicted)
        recent_time = now - 0.5  # 0.5 seconds ago (will be kept)

        initial_data = {
            "old_session_1": {
                "session_id": "backend_old_1",
                "last_activity": old_time,
                "model": "gpt-4",
            },
            "old_session_2": {
                "session_id": "backend_old_2",
                "last_activity": old_time,
                "model": "gpt-4",
            },
            "recent_session": {
                "session_id": "backend_recent",
                "last_activity": recent_time,
                "model": "gpt-4",
            },
        }

        with open(session_map_file, "w") as f:
            json.dump(initial_data, f)

        # Get locks for all sessions (create them in _per_session_locks)
        lock1 = sm._get_per_session_lock("old_session_1")
        lock2 = sm._get_per_session_lock("old_session_2")
        lock3 = sm._get_per_session_lock("recent_session")

        # Verify locks exist
        assert "old_session_1" in sm._per_session_locks
        assert "old_session_2" in sm._per_session_locks
        assert "recent_session" in sm._per_session_locks
        initial_lock_count = len(sm._per_session_locks)
        print(f"  Initial lock count: {initial_lock_count} locks")

        # Load and prune the session map (simulating save_session_map_atomic)
        session_map = sm.load_session_map()
        pruned_map = sm._prune_session_map_ttl(session_map)

        # Verify sessions were pruned
        assert "old_session_1" not in pruned_map
        assert "old_session_2" not in pruned_map
        assert "recent_session" in pruned_map
        print(f"  After TTL pruning: {len(pruned_map)} sessions remain")

        # Verify locks for evicted sessions were cleaned up
        assert (
            "old_session_1" not in sm._per_session_locks
        ), "Lock for old_session_1 should be cleaned up"
        assert (
            "old_session_2" not in sm._per_session_locks
        ), "Lock for old_session_2 should be cleaned up"
        assert (
            "recent_session" in sm._per_session_locks
        ), "Lock for recent_session should remain"

        final_lock_count = len(sm._per_session_locks)
        print(f"  Final lock count: {final_lock_count} locks")
        print(f"  ✓ Cleaned up {initial_lock_count - final_lock_count} orphaned locks")

        assert (
            final_lock_count == 1
        ), f"Expected 1 lock remaining, got {final_lock_count}"


if __name__ == "__main__":
    print("Running issue #23 lock cleanup test...")
    try:
        test_lock_cleanup_on_ttl_eviction()
        print("\n✅ Lock cleanup test passed!")
        sys.exit(0)
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
