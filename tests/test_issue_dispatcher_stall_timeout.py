"""Regression test for dispatcher stall timeout detection.

Issue: Work queue dispatcher had no timeout for stalled items.
If wee-qa got stuck and never completed, the dispatcher would infinitely
re-dispatch it, wasting resources and blocking new items.

This test ensures the timeout mechanism properly detects and escalates
stalled qa-review items after STALL_TIMEOUT_MINUTES.
"""

import pytest

pytest.skip(
    "dispatch_wee_dev_work_queue was replaced by dispatch_pipeline.py",
    allow_module_level=True,
)
