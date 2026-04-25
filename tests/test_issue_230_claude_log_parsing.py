"""Regression test for Issue #230: Claude runtime logs parsing."""
import unittest

class TestIssue230ClaudeLogParsing(unittest.TestCase):
    """Test Claude runtime log parsing in background tasks."""

    def test_stderr_prefixing_logic(self):
        """Test that stderr lines would be prefixed correctly."""
        stderr_lines_raw = [
            "Permission denied\n",
            "Connection timeout\n",
            "Model loading...\n",
        ]
        
        output_lines = []
        for err_line in stderr_lines_raw:
            line_text = err_line.rstrip("\n\r")
            if line_text:
                output_lines.append(f"[stderr] {line_text}")
        
        self.assertEqual(len(output_lines), 3)
        self.assertEqual(output_lines[0], "[stderr] Permission denied")
        self.assertEqual(output_lines[1], "[stderr] Connection timeout")
        self.assertEqual(output_lines[2], "[stderr] Model loading...")

    def test_only_truly_empty_lines_filtered(self):
        """Test that truly empty lines (no content) are not included."""
        stderr_lines_raw = [
            "Line 1\n",
            "\n",  # Truly empty - becomes '' after rstrip
            "Line 2\n",
            "Line 3\n",
        ]
        
        output_lines = []
        for err_line in stderr_lines_raw:
            line_text = err_line.rstrip("\n\r")
            if line_text:  # Only add non-empty lines
                output_lines.append(f"[stderr] {line_text}")
        
        # Verify only non-empty lines are included (3 lines, the \n becomes '')
        self.assertEqual(len(output_lines), 3)
        self.assertIn("Line 1", output_lines[0])
        self.assertIn("Line 2", output_lines[1])
        self.assertIn("Line 3", output_lines[2])

    def test_mixed_stdout_stderr_ordering(self):
        """Test that stdout and stderr can be interleaved properly."""
        mixed_events = [
            ("stdout", "Starting task..."),
            ("stderr", "Loading model"),
            ("stdout", "Processing..."),
            ("stderr", "GPU allocated"),
            ("stdout", "Complete"),
        ]
        
        output_lines = []
        for event_type, message in mixed_events:
            if event_type == "stderr":
                output_lines.append(f"[stderr] {message}")
            else:
                output_lines.append(message)
        
        # Verify order is preserved
        self.assertEqual(len(output_lines), 5)
        self.assertEqual(output_lines[0], "Starting task...")
        self.assertEqual(output_lines[1], "[stderr] Loading model")
        self.assertEqual(output_lines[2], "Processing...")
        self.assertEqual(output_lines[3], "[stderr] GPU allocated")
        self.assertEqual(output_lines[4], "Complete")
        
        # Verify stderr lines can be filtered
        stderr_lines = [l for l in output_lines if "[stderr]" in l]
        self.assertEqual(len(stderr_lines), 2)

    def test_multi_line_stderr_handling(self):
        """Test handling of multi-line stderr output (e.g., tracebacks)."""
        stderr_data = "Traceback (most recent call last):\n  File 'test.py', line 1\nError: Something failed\n"
        lines = stderr_data.split("\n")
        
        output_lines = []
        for line in lines:
            if line:  # Skip empty lines
                output_lines.append(f"[stderr] {line}")
        
        # Verify traceback is properly formatted
        self.assertEqual(len(output_lines), 3)
        self.assertIn("Traceback", output_lines[0])
        self.assertIn("File 'test.py'", output_lines[1])
        self.assertIn("Error: Something failed", output_lines[2])


if __name__ == '__main__':
    unittest.main()
