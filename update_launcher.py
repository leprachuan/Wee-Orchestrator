"""
update_launcher.py — Launch update_orchestrator.sh fully detached.

Uses systemd-run --scope so the child runs in its own transient cgroup,
completely outside the agent-manager service cgroup. This means it
survives when systemd kills the service during the restart cycle.
(setsid alone is not enough — systemd tracks all cgroup descendants.)
"""

import os
import subprocess
import time


def launch_update() -> int:
    """Launch update_orchestrator.sh fully detached — survives service restart.

    Returns the PID of the detached process.
    """
    repo_dir = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(repo_dir, 'update_orchestrator.sh')

    # Auto-detect environment from repo dir name (same logic as update_orchestrator.sh)
    is_dev = repo_dir.endswith('-dev')
    log = '/tmp/wee-update.log' if is_dev else '/tmp/wee-update-prod.log'

    unit_name = f"wee-update-{'dev' if is_dev else 'prod'}-{int(time.time())}"

    with open(log, 'w') as out:
        proc = subprocess.Popen(
            [
                'systemd-run',
                '--scope',
                f'--unit={unit_name}',
                '--',
                'bash',
                script,
            ],
            stdout=out,
            stderr=out,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
    return proc.pid
