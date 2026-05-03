"""
update_launcher.py — Launch update_orchestrator.sh fully detached.

The child process runs in a new session (setsid) so it survives when
systemd kills the Wee Orchestrator service during the restart cycle.
"""

import os
import subprocess


def launch_update() -> int:
    """Launch update_orchestrator.sh fully detached — survives service restart.

    Returns the PID of the detached process.
    """
    repo_dir = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(repo_dir, "update_orchestrator.sh")
    
    # Auto-detect environment from repo dir name (same logic as update_orchestrator.sh)
    is_dev = repo_dir.endswith("-dev")
    log = "/tmp/wee-update.log" if is_dev else "/tmp/wee-update-prod.log"

    with open(log, "w") as out:
        proc = subprocess.Popen(
            ["setsid", "bash", script],
            stdout=out,
            stderr=out,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    return proc.pid
