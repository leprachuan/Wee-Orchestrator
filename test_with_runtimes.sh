#!/bin/bash
# Test script to run the full test suite with runtime tests enabled
# Usage: ./test_with_runtimes.sh

export TEST_WITH_RUNTIMES=1
python3 tests/test_agent_manager.py "$@"
