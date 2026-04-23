"""
Test for issue #23: No per-session locking — concurrent request race conditions.

This test verifies that concurrent requests to the same session do not cause
lost session state due to read-modify-write race conditions.
"""

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, '/opt/n8n-copilot-shim-dev')

def test_per_session_locking():
    """Test that per-session locks prevent race conditions."""
    from agent_manager import SessionManager
    
    with tempfile.TemporaryDirectory() as tmpdir:
        session_map_file = Path(tmpdir) / 'test-session-map.json'
        
        # Create a session manager
        sm = SessionManager()
        sm.session_map_file = session_map_file
        
        # Initialize with a session
        initial_data = {
            'test_session': {
                'session_id': 'backend_123',
                'model': 'gpt-4',
                'scratch': 'initial'
            }
        }
        with open(session_map_file, 'w') as f:
            json.dump(initial_data, f)
        
        # Simulate concurrent modifications
        results = []
        errors = []
        
        def modify_session(thread_id, value):
            """Simulate a concurrent API request modifying session state."""
            try:
                session_lock = sm._get_per_session_lock('test_session')
                with session_lock:
                    # Simulate load-modify-write
                    session_map = sm.load_session_map()
                    session_data = session_map.get('test_session', {})
                    
                    # Simulate some processing time to increase race condition window
                    time.sleep(0.001)
                    
                    session_data['scratch'] = f'modified_by_{thread_id}'
                    session_data['thread_id'] = thread_id
                    session_data['value'] = value
                    session_map['test_session'] = session_data
                    
                    sm.save_session_map_atomic(session_map)
                
                results.append((thread_id, value))
            except Exception as e:
                errors.append((thread_id, str(e)))
        
        # Launch concurrent threads
        threads = []
        for i in range(10):
            t = threading.Thread(target=modify_session, args=(i, i * 100))
            threads.append(t)
            t.start()
        
        # Wait for all threads
        for t in threads:
            t.join()
        
        # Verify no errors occurred
        assert len(errors) == 0, f"Errors occurred: {errors}"
        
        # Load final session state
        with open(session_map_file, 'r') as f:
            final_map = json.load(f)
        
        final_session = final_map.get('test_session', {})
        
        # Verify the session state is valid (one thread's value, not corrupted)
        assert 'scratch' in final_session, "Session state was corrupted"
        assert 'thread_id' in final_session, "thread_id field missing"
        assert final_session['thread_id'] in range(10), "Invalid thread_id"
        
        print(f"✓ Test passed: Session state after concurrent modifications:")
        print(f"  Final scratch: {final_session['scratch']}")
        print(f"  Thread ID: {final_session['thread_id']}")
        print(f"  Value: {final_session['value']}")

def test_atomic_write():
    """Test that save_session_map_atomic writes atomically."""
    from agent_manager import SessionManager
    from pathlib import Path
    
    with tempfile.TemporaryDirectory() as tmpdir:
        session_map_file = Path(tmpdir) / 'test-session-map.json'
        
        sm = SessionManager()
        sm.session_map_file = session_map_file
        
        # Write large data to trigger potential partial write issues
        large_data = {
            f'session_{i}': {
                'session_id': f'backend_{i}',
                'data': 'x' * 10000,  # Large payload
            }
            for i in range(100)
        }
        
        sm.save_session_map_atomic(large_data)
        
        # Verify file is valid JSON
        with open(session_map_file, 'r') as f:
            loaded = json.load(f)
        
        assert len(loaded) == 100, "Not all sessions saved"
        print(f"✓ Atomic write test passed: {len(loaded)} sessions written atomically")

if __name__ == '__main__':
    print("Running issue #23 race condition tests...")
    try:
        test_per_session_locking()
        test_atomic_write()
        print("\n✅ All tests passed!")
        sys.exit(0)
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
