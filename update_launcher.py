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
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "update_orchestrator.sh")
    log = "/tmp/wee-update.log"

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
