"""
Regression test for Issue #315: Test import errors block weekly release

This test ensures that:
1. Test collection completes without import errors
2. No references to deleted test files exist in runtime
3. Deleted functions from dispatch_pipeline don't cause failures
"""

import subprocess
import sys


def test_pytest_collection_succeeds():
    """Ensure pytest can collect all tests without import errors."""
    result = subprocess.run(
        ["python3", "-m", "pytest", "--collect-only", "-q"],
        cwd="/opt/n8n-copilot-shim-dev",
        capture_output=True,
        text=True,
    )
    # Check for successful collection (should mention collected N items)
    assert "collected" in result.stdout, f"Collection failed: {result.stdout} {result.stderr}"
    # Check for no import errors
    assert "ImportError" not in result.stderr, f"Import errors detected: {result.stderr}"
    assert "ERROR" not in result.stderr or "ERRORS" not in result.stdout, f"Collection errors: {result.stderr}"
    # Should have thousands of tests (roughly 3200+)
    assert result.returncode == 0, f"Collection returned non-zero: {result.returncode}"


def test_no_stale_test_references():
    """Ensure no references to deleted dispatcher stall timeout test exist."""
    result = subprocess.run(
        ["grep", "-r", "test_issue_dispatcher_stall_timeout", "/opt/n8n-copilot-shim-dev"],
        capture_output=True,
        text=True,
    )
    # Should find nothing (grep returns 1 when no match found)
    if result.returncode == 0:
        # Found references - check if they're in pytest cache or other safe locations
        lines = result.stdout.strip().split('\n')
        for line in lines:
            # Exclude this test file and pytest cache references
            if ".pytest_cache" not in line and "test_issue_315_test_import_errors.py" not in line:
                raise AssertionError(f"Found reference to deleted test file: {line}")


def test_dispatch_pipeline_has_required_functions():
    """Ensure dispatch_pipeline.py has the functions tests expect."""
    sys.path.insert(0, "/opt/n8n-copilot-shim-dev/scripts")
    import dispatch_pipeline as dp
    
    # Verify key functions exist
    assert hasattr(dp, "dispatch_via_api"), "dispatch_via_api not found in dispatch_pipeline"
    assert hasattr(dp, "_load_api_key"), "_load_api_key not found in dispatch_pipeline"
    assert callable(dp.dispatch_via_api), "dispatch_via_api is not callable"
    assert callable(dp._load_api_key), "_load_api_key is not callable"


if __name__ == "__main__":
    test_pytest_collection_succeeds()
    test_no_stale_test_references()
    test_dispatch_pipeline_has_required_functions()
    print("All regression tests passed!")
