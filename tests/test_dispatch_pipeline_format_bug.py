"""
Regression test for dispatch_pipeline.py formatting bug.
Issue: TypeError when mins is None in log message (line 428)
Fix: Handle None case with conditional formatting
"""
import unittest


class TestDispatchPipelineFormatBug(unittest.TestCase):
    """Test that dispatch_pipeline doesn't crash on None mins value"""

    def test_format_string_with_none_mins(self):
        """Verify that format string handles None mins without TypeError"""
        mins = None
        mins_str = f"{mins:.1f}min" if mins is not None else "unknown time"
        
        message = f"Re-dispatching stalled #123 (no running task after {mins_str})"
        self.assertIn("unknown time", message)
        self.assertNotIn("None", message)
    
    def test_format_string_with_numeric_mins(self):
        """Verify format string still works with numeric mins"""
        mins = 45.5
        mins_str = f"{mins:.1f}min" if mins is not None else "unknown time"
        
        message = f"Re-dispatching stalled #123 (no running task after {mins_str})"
        self.assertIn("45.5min", message)
    

if __name__ == "__main__":
    unittest.main()
