"""
Simplified task scheduler executor daemon.

Runs as a systemd service, checking jobs.json every 1 second.
For each job that's ready to run:
- Executes via agent_manager.py
- Captures results
- Sends notification to the job creator via their original channel (Telegram or WebEx)
"""

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional

# Repo root is parent of scheduler/ directory (e.g. /opt/n8n-copilot-shim-dev)
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCHEDULER_BASE = _REPO_ROOT / ".task-scheduler"
_SCHEDULER_BASE.mkdir(parents=True, exist_ok=True)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(str(_SCHEDULER_BASE / "executor.log")),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Clock drift handling constants
# ---------------------------------------------------------------------------
_CLOCK_DRIFT_THRESHOLD = 30  # seconds — log warning when drift exceeds this
_MAX_CATCHUP_WINDOW = 3600  # 1 hour — skip (recalculate) runs older than this
_MIN_EXEC_INTERVAL_MONO = 10  # monotonic seconds — cooldown between same-job runs
_DRIFT_COMPENSATION_CAP = 600  # max backward-drift compensation (10 min)
_DRIFT_EVENT_HISTORY = 50  # keep last N drift events for diagnostics

_MAX_NOTIFICATION_LENGTH = 200


def _brief_notification(icon: str, job_name: str, verb: str) -> str:
    """Return a one-line notification capped at _MAX_NOTIFICATION_LENGTH."""
    msg = f"{icon} {job_name} — {verb}"
    if len(msg) > _MAX_NOTIFICATION_LENGTH:
        msg = msg[: _MAX_NOTIFICATION_LENGTH - 3] + "..."
    return msg


# Telegram connector for direct per-user delivery
sys.path.insert(0, str(_REPO_ROOT))
try:
    from telegram_connector import TelegramConnector as _TelegramConnector
except ImportError:
    _TelegramConnector = None

# WebEx connector for per-user delivery
try:
    from webex_connector import WebEXConnector as _WebEXConnector
except ImportError:
    _WebEXConnector = None

from scheduler.management import (
    cron_next_run,
    is_valid_cron,
    parse_schedule_to_next_run,
)


class TaskSchedulerExecutor:
    """Execute scheduled jobs from jobs.json."""

    def __init__(self):
        # Scheduler data lives inside the repo at .task-scheduler/
        # Respect env-var overrides so executor + API always use the same paths
        self.repo_root = _REPO_ROOT
        scheduler_base = _SCHEDULER_BASE

        self.jobs_file = Path(
            os.getenv("SCHEDULER_JOBS_FILE", str(scheduler_base / "jobs.json"))
        )
        self.logs_dir = Path(
            os.getenv("SCHEDULER_LOGS_DIR", str(scheduler_base / "logs/"))
        )
        self.results_dir = Path(
            os.getenv("SCHEDULER_RESULTS_DIR", str(scheduler_base / "results/"))
        )
        self.config_file = self.repo_root / "agents.json"
        self.data_dir = scheduler_base

        self.jobs_file.parent.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)

        self._check_stale_checkpoints()

        # Clock drift detection — compare wall clock vs monotonic clock
        self._last_check_wall = time.time()
        self._last_check_mono = time.monotonic()
        # Per-job monotonic timestamp of last execution (prevents double-exec
        # when a backward clock jump causes _calculate_next_run to re-schedule
        # into an already-executed time slot).
        self._job_last_exec_mono: Dict[str, float] = {}
        # Wall-clock debt: accumulated backward drift not yet recovered.
        # Applied as compensation in _is_job_ready so backward jumps don't
        # silently skip jobs that were due before the clock regressed.
        self._wall_clock_debt: float = 0.0
        self._drift_events: list = []  # recent (timestamp, drift_secs) tuples
        self._drift_recovered_count: int = 0  # jobs recovered via compensation

    def _detect_clock_drift(self) -> float:
        """Compare wall-clock delta with monotonic delta to detect drift.

        Returns drift in seconds (positive = forward jump, negative = backward).
        Updates internal tracking timestamps and wall-clock debt for backward
        drift compensation (Issue #71).
        """
        now_wall = time.time()
        now_mono = time.monotonic()

        wall_delta = now_wall - self._last_check_wall
        mono_delta = now_mono - self._last_check_mono

        drift = wall_delta - mono_delta

        self._last_check_wall = now_wall
        self._last_check_mono = now_mono

        if abs(drift) > _CLOCK_DRIFT_THRESHOLD:
            direction = "forward" if drift > 0 else "backward"
            logger.warning(
                f"System clock drift detected: {drift:+.1f}s ({direction}) — "
                f"wall Δ={wall_delta:.1f}s vs mono Δ={mono_delta:.1f}s"
            )

            # Track event for diagnostics
            self._drift_events.append(
                (datetime.now(timezone.utc).isoformat(), round(drift, 1))
            )
            if len(self._drift_events) > _DRIFT_EVENT_HISTORY:
                self._drift_events = self._drift_events[-_DRIFT_EVENT_HISTORY:]

            # Update wall-clock debt for backward drift compensation
            if drift < 0:
                # Backward jump: increase debt (capped)
                self._wall_clock_debt = min(
                    self._wall_clock_debt + abs(drift),
                    _DRIFT_COMPENSATION_CAP,
                )
                logger.warning(
                    f"Wall-clock debt increased to {self._wall_clock_debt:.1f}s "
                    f"(backward drift compensation active)"
                )
            else:
                # Forward drift reduces debt (clock catching up via NTP slew)
                if self._wall_clock_debt > 0:
                    old_debt = self._wall_clock_debt
                    self._wall_clock_debt = max(0.0, self._wall_clock_debt - drift)
                    logger.info(
                        f"Wall-clock debt reduced {old_debt:.1f}s → "
                        f"{self._wall_clock_debt:.1f}s (forward drift recovery)"
                    )

        return drift

    def _load_jobs(self) -> Dict:
        """Load jobs from JSON."""
        if not self.jobs_file.exists():
            return {"jobs": []}
        try:
            return json.loads(self.jobs_file.read_text())
        except (json.JSONDecodeError, FileNotFoundError):
            logger.error(f"Failed to load jobs from {self.jobs_file}")
            return {"jobs": []}

    def _save_jobs(self, data: Dict):
        """Save jobs to JSON atomically (write tmp + rename)."""
        import tempfile

        try:
            tmp_fd, tmp_path = tempfile.mkstemp(
                dir=str(self.jobs_file.parent), suffix=".tmp"
            )
            with os.fdopen(tmp_fd, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, str(self.jobs_file))
        except Exception as e:
            logger.error(f"Failed to save jobs: {e}")
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _check_stale_checkpoints(self):
        """Check for stale checkpoint files on startup (from crashed executions)."""
        if not self.data_dir.exists():
            return
        for checkpoint_file in self.data_dir.glob(".checkpoint_*.json"):
            try:
                checkpoint = json.loads(checkpoint_file.read_text())
                job_id = checkpoint.get("job_id", "unknown")
                started_at = checkpoint.get("started_at", "unknown")
                pid = checkpoint.get("pid", 0)
                logger.warning(
                    f"Found stale checkpoint for job {job_id} (started: {started_at}, pid: {pid})"
                )
                checkpoint_file.unlink()
            except Exception as e:
                logger.error(
                    f"Failed to process stale checkpoint {checkpoint_file}: {e}"
                )

    def _write_checkpoint(self, job_id: str):
        """Write checkpoint file before starting execution."""
        checkpoint_file = self.data_dir / f".checkpoint_{job_id}.json"
        checkpoint = {
            "job_id": job_id,
            "started_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "pid": os.getpid(),
        }
        try:
            checkpoint_file.write_text(json.dumps(checkpoint))
        except Exception as e:
            logger.error(f"Failed to write checkpoint for job {job_id}: {e}")

    def _clear_checkpoint(self, job_id: str):
        """Clear checkpoint file after execution completes or fails."""
        checkpoint_file = self.data_dir / f".checkpoint_{job_id}.json"
        try:
            if checkpoint_file.exists():
                checkpoint_file.unlink()
        except Exception as e:
            logger.error(f"Failed to clear checkpoint for job {job_id}: {e}")

    def _log_job(self, job_id: str, message: str):
        """Log job execution to job-specific log file."""
        log_file = self.logs_dir / f"{job_id}.log"
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        try:
            with open(log_file, "a") as f:
                f.write(f"[{timestamp}] {message}\n")
        except Exception as e:
            logger.error(f"Failed to log job {job_id}: {e}")

    def _save_result(
        self,
        job_id: str,
        job_name: str,
        success: bool,
        output: str = "",
        error: str = "",
    ):
        """Save full execution result to results database.

        Creates a JSON file with complete execution details for auditing and analysis.
        """
        result_file = self.results_dir / f"{job_id}.jsonl"
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        result = {
            "timestamp": timestamp,
            "job_id": job_id,
            "job_name": job_name,
            "success": success,
            "output": output[:5000] if output else "",  # Keep first 5000 chars
            "error": error[:5000] if error else "",  # Keep first 5000 chars
        }

        try:
            # Append to JSONL file (one JSON object per line)
            with open(result_file, "a") as f:
                f.write(json.dumps(result) + "\n")
        except Exception as e:
            logger.error(f"Failed to save result for job {job_id}: {e}")

    def _notify_creator(self, job: Dict, message: str) -> bool:
        """Send notification to the user who created the job, via their original channel.

        Reads job["created_by"] = {"identity": ..., "channel": "telegram"|"webex", "username": ...}
        Falls back to logging a warning if the channel is unknown or connectors are unavailable.
        """
        job_id = job.get("id", "unknown")
        created_by = job.get("created_by", {})
        channel = created_by.get("channel", "")
        identity = created_by.get("identity", "")

        if not channel or not identity:
            logger.warning(
                f"Job {job_id}: no creator channel/identity stored, cannot notify"
            )
            self._log_job(job_id, "Notification skipped: no created_by info")
            return False

        if channel == "telegram":
            return self._send_telegram_to(identity, message, job_id)
        elif channel == "webex":
            return self._send_webex_to(identity, message, job_id)
        else:
            logger.warning(f"Job {job_id}: unknown notification channel '{channel}'")
            self._log_job(job_id, f"Notification skipped: unknown channel '{channel}'")
            return False

    def _send_telegram_to(self, chat_id: str, message: str, job_id: str) -> bool:
        """Send a Telegram message directly to a specific chat_id (numeric string)."""
        try:
            if not _TelegramConnector:
                logger.warning(
                    "TelegramConnector not available, skipping Telegram notification"
                )
                self._log_job(
                    job_id, "Telegram notification skipped: connector unavailable"
                )
                return False

            script_dir = Path("/opt/n8n-copilot-shim")
            config_path = script_dir / "telegram_config.json"
            with open(config_path) as f:
                cfg = json.load(f)
            token = cfg.get("token") or os.getenv("TELEGRAM_BOT_TOKEN", "")
            if not token:
                logger.warning("No Telegram bot token configured")
                return False

            connector = _TelegramConnector(token, config_file=str(config_path))
            connector.send_message(int(chat_id), message)
            self._log_job(job_id, f"Telegram notification sent to chat_id={chat_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to send Telegram notification to {chat_id}: {e}")
            self._log_job(job_id, f"Telegram notification failed: {e}")
            return False

    def _send_webex_to(self, email: str, message: str, job_id: str) -> bool:
        """Send a WebEx message to a specific user by email."""
        try:
            if not _WebEXConnector:
                logger.warning(
                    "WebEXConnector not available, skipping WebEx notification"
                )
                self._log_job(
                    job_id, "WebEx notification skipped: connector unavailable"
                )
                return False

            script_dir = Path("/opt/n8n-copilot-shim")
            config_path = script_dir / "webex_config.json"
            with open(config_path) as f:
                cfg = json.load(f)
            token = cfg.get("bot_token") or os.getenv("WEBEX_BOT_TOKEN", "")
            if not token:
                logger.warning("No WebEx bot token configured")
                return False

            connector = _WebEXConnector(token, config_file=str(config_path))
            connector.send_message(email, message)
            self._log_job(job_id, f"WebEx notification sent to {email}")
            return True
        except Exception as e:
            logger.error(f"Failed to send WebEx notification to {email}: {e}")
            self._log_job(job_id, f"WebEx notification failed: {e}")
            return False

    def _execute_task(self, job: Dict) -> Optional[str]:
        """
        Execute a job in either AI mode (via agent_manager.py) or command mode (direct shell).

        Modes:
        - 'ai' (default): Execute via LLM agent through agent_manager.py
        - 'command': Execute as direct shell/python command

        Returns the execution result/output, or None if failed.
        """
        job_id = job["id"]
        mode = job.get("mode", "ai")  # Default to AI mode

        # Validate: command-mode tasks should not have LLM runtime/model fields.
        # Having them is a misconfiguration — command mode always runs the shell
        # command directly and never invokes an LLM (see issue #78).
        if mode == "command":
            llm_fields = [f for f in ("runtime", "model") if job.get(f)]
            if llm_fields:
                logger.warning(
                    f"Job {job_id} has mode='command' but also sets {llm_fields}. "
                    "These fields are ignored for command-mode tasks. "
                    "Remove them from the job config to silence this warning."
                )
            return self._execute_command_mode(job)
        else:
            return self._execute_ai_mode(job)

    def _execute_ai_mode(self, job: Dict) -> Optional[str]:
        """Execute job via LLM agent (agent_manager.py)."""
        job_id = job["id"]
        agent = job.get("agent", os.getenv("SCHEDULER_DEFAULT_AGENT", "orchestrator"))
        runtime = job.get("runtime", os.getenv("SCHEDULER_DEFAULT_RUNTIME", "claude"))
        task = job.get("task", "")
        notify = job.get("notify", False)
        timeout = int(
            job.get("timeout") or os.getenv("SCHEDULER_DEFAULT_TIMEOUT", "300")
        )

        # Create session ID
        session_id = f"scheduled-{job_id}-{int(time.time())}"

        # Pick a sensible default model per runtime
        _default_models = {
            "claude": "sonnet",
            "copilot": "gpt-4.1",
            "gemini": "gemini-1.5-pro",
            "opencode": "gpt-4o",
        }
        model = job.get("model") or _default_models.get(runtime, "sonnet")

        # Use repo_root to find agent_manager.py (already set in __init__)
        agent_manager_path = self.repo_root / "agent_manager.py"

        # Resolve permission mode from job
        perm_mode = job.get("permission_mode", "restricted")
        cmd = [
            "python3",
            agent_manager_path,
            "--config",
            str(self.config_file),
            "--agent",
            agent,
            "--runtime",
            runtime,
            "--model",
            model,
        ]
        if perm_mode in ("elevated", "restricted", "sandboxed"):
            cmd.extend(["--mode", perm_mode])
        cmd.extend([task, session_id])

        logger.info(
            f"[AI Mode] Executing job {job_id}: agent={agent}, runtime={runtime}, model={model}, task={task[:60]}..."
        )
        self._log_job(
            job_id,
            f"Executing (AI mode): agent={agent}, runtime={runtime}, model={model}, session={session_id}",
        )

        self._write_checkpoint(job_id)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ, "COMMAND_TIMEOUT": str(timeout)},
            )

            if result.returncode == 0:
                output = result.stdout.strip()
                self._log_job(job_id, f"Execution succeeded")
                self._save_result(job_id, job["name"], success=True, output=output)
                logger.info(f"Job {job_id} completed successfully")

                if notify:
                    self._notify_creator(
                        job,
                        _brief_notification("✅", job["name"], "done"),
                    )

                return output
            else:
                error_msg = result.stderr or result.stdout
                self._log_job(job_id, f"Execution failed: {error_msg[:200]}")
                self._save_result(job_id, job["name"], success=False, error=error_msg)
                logger.error(f"Job {job_id} failed with code {result.returncode}")

                if notify:
                    self._notify_creator(
                        job,
                        _brief_notification("❌", job["name"], "failed"),
                    )

                return None

        except subprocess.TimeoutExpired:
            timeout_mins = timeout / 60
            self._log_job(job_id, f"Execution timed out ({timeout_mins:.1f} minutes)")
            self._save_result(
                job_id,
                job["name"],
                success=False,
                error=f"Execution timed out ({timeout_mins:.1f} minutes)",
            )
            logger.error(f"Job {job_id} execution timed out")

            if notify:
                self._notify_creator(
                    job,
                    _brief_notification("⏱️", job["name"], "timed out"),
                )
            return None

        except Exception as e:
            error_str = str(e)
            self._log_job(job_id, f"Exception: {error_str}")
            self._save_result(job_id, job["name"], success=False, error=error_str)
            logger.error(f"Failed to execute job {job_id}: {e}")

            if job.get("notify"):
                self._notify_creator(
                    job,
                    _brief_notification("⚠️", job["name"], "error"),
                )
            return None
        finally:
            self._clear_checkpoint(job_id)

    def _execute_command_mode(self, job: Dict) -> Optional[str]:
        """Execute job as direct shell/python command (no LLM)."""
        job_id = job["id"]
        task = job.get("task", "")
        notify = job.get("notify", False)
        working_dir = job.get("working_dir", "/opt")
        timeout = int(
            job.get("timeout") or os.getenv("SCHEDULER_DEFAULT_TIMEOUT", "300")
        )

        logger.info(
            f"[Command Mode] Executing job {job_id}: working_dir={working_dir}, task={task[:60]}..."
        )
        self._log_job(
            job_id,
            f"Executing (command mode): working_dir={working_dir}, cmd={task[:120]}",
        )

        self._write_checkpoint(job_id)
        try:
            result = subprocess.run(
                task,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=True,
                cwd=working_dir,
            )

            if result.returncode == 0:
                output = result.stdout.strip()
                self._log_job(job_id, f"Command executed successfully")
                self._save_result(job_id, job["name"], success=True, output=output)
                logger.info(f"Job {job_id} (command mode) completed successfully")

                if notify:
                    self._notify_creator(
                        job,
                        _brief_notification("✅", job["name"], "done"),
                    )

                return output
            else:
                error_msg = (
                    result.stderr
                    or result.stdout
                    or f"Command failed with exit code {result.returncode}"
                )
                self._log_job(job_id, f"Command failed: {error_msg[:200]}")
                self._save_result(job_id, job["name"], success=False, error=error_msg)
                logger.error(
                    f"Job {job_id} (command mode) failed with code {result.returncode}"
                )

                if notify:
                    self._notify_creator(
                        job,
                        _brief_notification("❌", job["name"], "failed"),
                    )

                return None

        except subprocess.TimeoutExpired:
            timeout_mins = timeout / 60
            self._log_job(job_id, f"Execution timed out ({timeout_mins:.1f} minutes)")
            self._save_result(
                job_id,
                job["name"],
                success=False,
                error=f"Execution timed out ({timeout_mins:.1f} minutes)",
            )
            logger.error(f"Job {job_id} execution timed out")

            if job.get("notify"):
                self._notify_creator(
                    job,
                    _brief_notification("⏱️", job["name"], "timed out"),
                )
            return None

        except Exception as e:
            error_str = str(e)
            self._log_job(job_id, f"Exception: {error_str}")
            self._save_result(job_id, job["name"], success=False, error=error_str)
            logger.error(f"Failed to execute job {job_id}: {e}")

            if job.get("notify"):
                self._notify_creator(
                    job,
                    _brief_notification("⚠️", job["name"], "error"),
                )
            return None
        finally:
            self._clear_checkpoint(job_id)

    def _is_job_ready(self, job: Dict) -> bool:
        """Check if a job is ready to execute (enabled and time has passed).

        Handles clock drift by:
        - Using wall-clock debt compensation so backward clock jumps don't
          silently skip jobs that were due before the regression (Issue #71).
        - Using monotonic cooldown to prevent double-execution after
          backward clock jumps.
        - Detecting stale next_run (past MAX_CATCHUP_WINDOW) and returning
          ``False`` so that ``_recalculate_stale_jobs`` can fix it instead.
        """
        if not job.get("enabled", True):
            return False

        next_run_str = job.get("next_run")
        if not next_run_str:
            return False

        try:
            next_run = datetime.fromisoformat(
                next_run_str.replace("Z", "+00:00")
            ).replace(tzinfo=None)
            now = datetime.now(timezone.utc).replace(tzinfo=None)

            # Apply wall-clock debt compensation: if the clock jumped backward,
            # compensated_now represents what time it "should" be. This prevents
            # jobs from being silently skipped after a backward NTP adjustment.
            compensated_now = now
            drift_compensated = False
            if self._wall_clock_debt > 0:
                compensated_now = now + timedelta(seconds=self._wall_clock_debt)

            if next_run > compensated_now:
                return False

            # If the job is only ready because of drift compensation, log it
            if next_run > now and next_run <= compensated_now:
                drift_compensated = True

            job_id = job.get("id", "")

            # Monotonic cooldown — prevents double-execution when a backward
            # clock jump causes _calculate_next_run to schedule the same slot.
            last_exec_mono = self._job_last_exec_mono.get(job_id)
            if last_exec_mono is not None:
                elapsed_mono = time.monotonic() - last_exec_mono
                if elapsed_mono < _MIN_EXEC_INTERVAL_MONO:
                    return False

            # Stale run guard — recurring jobs whose next_run is extremely old
            # should be rescheduled, not executed with stale context.
            overdue_seconds = (compensated_now - next_run).total_seconds()
            if overdue_seconds > _MAX_CATCHUP_WINDOW and job.get("recurring", True):
                logger.warning(
                    f"Job {job_id} next_run is {overdue_seconds:.0f}s overdue "
                    f"(>{_MAX_CATCHUP_WINDOW}s catchup window) — "
                    f"will recalculate instead of executing stale run"
                )
                return False

            if drift_compensated:
                gap = (next_run - now).total_seconds()
                logger.info(
                    f"Job {job_id} recovered via backward-drift compensation "
                    f"(next_run {gap:.0f}s in future, debt={self._wall_clock_debt:.1f}s)"
                )
                self._drift_recovered_count += 1
            elif overdue_seconds > _CLOCK_DRIFT_THRESHOLD:
                logger.info(
                    f"Job {job_id} is {overdue_seconds:.0f}s overdue — "
                    f"executing catchup run"
                )

            return True
        except (ValueError, TypeError) as exc:
            logger.warning(
                f"Invalid next_run format for job "
                f"{job.get('id', '?')}: {next_run_str!r} ({exc})"
            )
            return False

    def _calculate_next_run(self, job: Dict) -> Optional[str]:
        """Calculate next run time using cron (preferred) or schedule string fallback."""
        # Prefer cron expression (set during job creation via AI or deterministic conversion)
        cron_expr = job.get("cron") if isinstance(job, dict) else None
        if cron_expr and is_valid_cron(cron_expr):
            next_run = cron_next_run(cron_expr)
            if next_run:
                return next_run

        # Fallback to schedule string for legacy/one-time jobs
        schedule = job.get("schedule", "") if isinstance(job, dict) else job
        if schedule:
            next_run = parse_schedule_to_next_run(schedule)
            if next_run:
                return next_run

        logger.warning(f"Could not calculate next run for job")
        return None

    def _recalculate_stale_jobs(self, data: Dict) -> Dict[str, Dict]:
        """Recalculate next_run for recurring jobs past the catchup window.

        Returns a dict of ``{job_id: updates}`` for jobs whose ``next_run``
        is so far in the past that executing them would be stale.  Instead
        of running them, we advance ``next_run`` to the next future slot.
        """
        modified: Dict[str, Dict] = {}
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        for job in data.get("jobs", []):
            if not job.get("enabled", True):
                continue
            if not job.get("recurring", True):
                continue

            next_run_str = job.get("next_run")
            if not next_run_str:
                continue

            try:
                next_run = datetime.fromisoformat(
                    next_run_str.replace("Z", "+00:00")
                ).replace(tzinfo=None)
                overdue = (now - next_run).total_seconds()

                if overdue > _MAX_CATCHUP_WINDOW:
                    job_id = job["id"]
                    new_next = self._calculate_next_run(job)
                    if new_next:
                        modified[job_id] = {"next_run": new_next}
                        logger.warning(
                            f"Job {job_id} was {overdue:.0f}s overdue "
                            f"(>{_MAX_CATCHUP_WINDOW}s window) — "
                            f"advanced next_run to {new_next}"
                        )
                    else:
                        modified[job_id] = {"enabled": False}
                        logger.warning(
                            f"Job {job_id} stale and could not recalculate — "
                            f"disabling"
                        )
            except (ValueError, TypeError) as exc:
                logger.warning(
                    f"Could not parse next_run for job "
                    f"{job.get('id', '?')}: {job.get('next_run')!r} ({exc})"
                )
                continue

        return modified

    def get_drift_diagnostics(self) -> Dict:
        """Return current drift handling state for diagnostics."""
        return {
            "wall_clock_debt_seconds": round(self._wall_clock_debt, 1),
            "drift_compensation_active": self._wall_clock_debt > 0,
            "drift_recovered_jobs": self._drift_recovered_count,
            "recent_drift_events": self._drift_events[-10:],
            "compensation_cap_seconds": _DRIFT_COMPENSATION_CAP,
        }

    def check_and_execute(self):
        """Check for ready jobs and execute them.

        On each cycle, drift between the wall clock and monotonic clock is
        measured.  Wall-clock debt from backward jumps is tracked and used
        to compensate job readiness checks (Issue #71).  Stale recurring
        jobs (past the catchup window) get their ``next_run`` advanced
        without executing.  A per-job monotonic cooldown prevents
        double-execution after backward clock jumps.
        """
        # --- clock drift detection ---
        drift = self._detect_clock_drift()

        if self._wall_clock_debt > 0:
            logger.debug(
                f"Drift compensation active: debt={self._wall_clock_debt:.1f}s, "
                f"recovered={self._drift_recovered_count} jobs"
            )

        data = self._load_jobs()

        # --- recalculate stale recurring jobs (forward jump / long outage) ---
        stale_updates = self._recalculate_stale_jobs(data)

        # Track which jobs were modified so we only write back when necessary
        modified_jobs: Dict[str, Dict] = {}
        modified_jobs.update(stale_updates)

        for job in data.get("jobs", []):
            if not self._is_job_ready(job):
                continue

            job_id = job["id"]
            logger.info(f"Job ready: {job_id}")

            # Record monotonic execution time BEFORE executing to close any
            # race window on backward clock jumps.
            self._job_last_exec_mono[job_id] = time.monotonic()

            # Execute the job
            result = self._execute_task(job)

            # Update job record
            now = datetime.now(timezone.utc)
            updates = {"last_run": now.isoformat().replace("+00:00", "Z")}

            # Handle recurring vs one-time jobs
            recurring = job.get("recurring", True)  # Default: recurring

            if recurring:
                # Recurring job - calculate next run
                next_run = self._calculate_next_run(job)
                if next_run:
                    updates["next_run"] = next_run
                else:
                    # If we can't calculate next run, disable the job
                    updates["enabled"] = False
                    self._log_job(job_id, "Could not calculate next run, disabling job")
            else:
                # One-time job - disable after running
                updates["enabled"] = False
                self._log_job(job_id, "One-time job completed, disabling")

            modified_jobs[job_id] = updates

        # Only write if we actually modified something; re-read fresh data
        # to avoid overwriting concurrent API changes (e.g. deletes)
        if modified_jobs:
            fresh_data = self._load_jobs()
            for job in fresh_data.get("jobs", []):
                if job["id"] in modified_jobs:
                    job.update(modified_jobs[job["id"]])
            self._save_jobs(fresh_data)

    def run(self):
        """Main executor loop - runs forever, checking every 1 second."""
        logger.info("Task scheduler executor started")

        try:
            while True:
                try:
                    self.check_and_execute()
                    time.sleep(1)  # Check every 1 second
                except Exception as e:
                    logger.error(f"Error in execution loop: {e}")
                    time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Task scheduler executor stopped")
            sys.exit(0)


def main():
    """Entry point."""
    executor = TaskSchedulerExecutor()
    executor.run()


if __name__ == "__main__":
    main()
