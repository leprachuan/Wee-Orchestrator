#!/usr/bin/env python3
"""
Task Scheduler Executor Entry Point

Runs as systemd service: task-scheduler-executor.service
Polls jobs.json every second and executes ready jobs.

Usage: python3 -m scheduler.run
"""

import sys
import os

# Ensure parent repo is in path for imports
sys.path.insert(0, '/opt/n8n-copilot-shim-dev')

from scheduler.executor import TaskSchedulerExecutor

if __name__ == "__main__":
    try:
        executor = TaskSchedulerExecutor()
        executor.run()
    except KeyboardInterrupt:
        print("\nScheduler stopped.")
        sys.exit(0)
    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        sys.exit(1)
