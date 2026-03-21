"""Task scheduler — embedded in Wee-Orchestrator.

Moved from /opt/skills/task-scheduler/shared_infrastructure.py.
Extended with update_job() and get_results() for the REST API.
"""

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional


def parse_schedule_to_next_run(schedule: str) -> Optional[str]:
    """Parse schedule string and return ISO datetime string for next run."""
    schedule = re.sub(r"\s+", " ", schedule.lower().strip())
    now = datetime.utcnow()

    # Handle shorthand like "every minute", "every hour", "every second"
    shorthand_match = re.fullmatch(r"every (minute|hour|second|day|week)", schedule)
    if shorthand_match:
        unit = shorthand_match.group(1)
        if unit == "second":
            return (now + timedelta(seconds=1)).isoformat() + "Z"
        elif unit == "minute":
            return (now + timedelta(minutes=1)).isoformat() + "Z"
        elif unit == "hour":
            return (now + timedelta(hours=1)).isoformat() + "Z"
        elif unit == "day":
            return (now + timedelta(days=1)).isoformat() + "Z"
        elif unit == "week":
            return (now + timedelta(weeks=1)).isoformat() + "Z"

    interval_match = re.fullmatch(r"(in|every) (\d+) ([a-z]+)", schedule)
    if interval_match:
        mode, amount_str, unit = interval_match.groups()
        amount = int(amount_str)
        unit = unit.rstrip("s")
        unit = {"sec": "second", "min": "minute", "hr": "hour"}.get(unit, unit)

        if unit == "second":
            delta = timedelta(seconds=amount)
        elif unit == "minute":
            delta = timedelta(minutes=amount)
        elif unit == "hour":
            delta = timedelta(hours=amount)
        elif unit == "day":
            delta = timedelta(days=amount)
        elif unit == "week" and mode == "every":
            delta = timedelta(weeks=amount)
        else:
            return None

        return (now + delta).isoformat() + "Z"

    # Handle "in X minutes/hours/seconds/days" format
    daily_match = re.fullmatch(
        r"every day at (\d{1,2})(?::(\d{2}))?\s*(am|pm)?",
        schedule,
    )
    if daily_match:
        hour_str, minute_str, meridiem = daily_match.groups()
        hour = int(hour_str)
        minute = int(minute_str or "0")

        if minute > 59:
            return None

        if meridiem:
            if hour < 1 or hour > 12:
                return None
            if meridiem == "am":
                hour = 0 if hour == 12 else hour
            else:
                hour = 12 if hour == 12 else hour + 12
        elif hour > 23:
            return None

        next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)

        return next_run.isoformat() + "Z"

    return None


class TaskScheduler:
    def __init__(self, config: Optional[Dict] = None):
        # Scheduler data lives inside the repo at .task-scheduler/
        # Derive repo root from this file's location: scheduler/management.py → repo root
        repo_root = Path(__file__).resolve().parent.parent
        base_dir = str(repo_root / ".task-scheduler")
        
        self.jobs_file = Path(os.getenv("SCHEDULER_JOBS_FILE", f"{base_dir}/jobs.json"))
        self.logs_dir = Path(os.getenv("SCHEDULER_LOGS_DIR", f"{base_dir}/logs/"))
        self.results_dir = Path(os.getenv("SCHEDULER_RESULTS_DIR", f"{base_dir}/results/"))
        self.max_retries = int(os.getenv("SCHEDULER_RETRY_MAX", 3))

        self.jobs_file.parent.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self._init_jobs_file()

    def _init_jobs_file(self):
        """Initialize jobs.json if it doesn't exist."""
        if not self.jobs_file.exists():
            self.jobs_file.write_text(json.dumps({"jobs": []}, indent=2))

    def _load_jobs(self) -> Dict:
        """Load jobs from JSON."""
        if not self.jobs_file.exists():
            return {"jobs": []}
        try:
            content = self.jobs_file.read_text()
            if not content.strip():
                return {"jobs": []}
            return json.loads(content)
        except (json.JSONDecodeError, FileNotFoundError):
            return {"jobs": []}

    def _save_jobs(self, data: Dict):
        """Save jobs to JSON atomically (write tmp + rename)."""
        import tempfile
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(self.jobs_file.parent), suffix=".tmp"
        )
        try:
            with os.fdopen(tmp_fd, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, str(self.jobs_file))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def schedule_task(
        self,
        name: str,
        schedule: str,
        agent: str = None,
        runtime: str = None,
        model: str = None,
        mode: str = None,
        task: str = "",
        notify: bool = False,
        recurring: bool = True,
        working_dir: str = None,
        created_by: Optional[Dict] = None,
        timeout: int = None,
    ) -> Dict:
        """Create a scheduled task.

        Args:
            name: Task name
            schedule: Schedule string (e.g. "every day at 9am", "in 5 minutes")
            agent: Agent name (default: orchestrator, or SCHEDULER_DEFAULT_AGENT env var)
            runtime: Runtime (default: claude)
            model: Model override
            mode: Execution mode - 'ai' (LLM-based, default) or 'command' (direct shell)
            task: Task description or shell command
            notify: Send notification when done
            recurring: Whether task repeats
            working_dir: Working directory for command mode (default: /opt)
            created_by: Optional dict with identity, channel, username for notifications
            timeout: Execution timeout in seconds (default: 300)

        Examples:
            # AI mode (via LLM agent)
            scheduler.schedule_task(
                name="Daily Report",
                schedule="every day at 9am",
                mode="ai",
                task="Generate daily summary",
                agent="devops"
            )

            # Command mode (direct execution)
            scheduler.schedule_task(
                name="Backup DB",
                schedule="every day at 2am",
                mode="command",
                task="bash /opt/scripts/backup.sh",
                working_dir="/opt"
            )
        """
        if agent is None:
            agent = os.getenv("SCHEDULER_DEFAULT_AGENT", "orchestrator")
        if runtime is None:
            runtime = os.getenv("SCHEDULER_DEFAULT_RUNTIME", "claude")
        if working_dir is None:
            working_dir = "/opt"

        jobs = self._load_jobs()
        job_id = name.lower().replace(" ", "-")

        # Prevent duplicate IDs
        existing_ids = {j["id"] for j in jobs["jobs"]}
        base_id = job_id
        counter = 2
        while job_id in existing_ids:
            job_id = f"{base_id}-{counter}"
            counter += 1

        next_run = parse_schedule_to_next_run(schedule)

        job = {
            "id": job_id,
            "name": name,
            "agent": agent,
            "runtime": runtime,
            "model": model,
            "mode": mode or "ai",
            "task": task,
            "schedule": schedule,
            "working_dir": working_dir,
            "notify": notify,
            "recurring": recurring,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "next_run": next_run,
            "last_run": None,
            "enabled": True,
            "retries": 0,
            "created_by": created_by or {},
            "timeout": timeout,
        }

        jobs["jobs"].append(job)
        self._save_jobs(jobs)
        self._log(job_id, f"Scheduled: {name} (next run: {next_run}, recurring: {recurring}, mode: {mode or 'ai'})")

        return {"success": True, "result": job, "message": f"Task '{name}' scheduled for {next_run}"}

    def list_jobs(self) -> Dict:
        """List all scheduled jobs."""
        jobs = self._load_jobs()
        return {"success": True, "result": jobs["jobs"], "message": f"Found {len(jobs['jobs'])} jobs"}

    def get_job(self, job_id: str) -> Dict:
        """Get a single job by ID."""
        jobs = self._load_jobs()
        job = next((j for j in jobs["jobs"] if j["id"] == job_id), None)
        if job:
            return {"success": True, "result": job, "message": "Job found"}
        return {"success": False, "message": f"Job '{job_id}' not found"}

    def update_job(self, job_id: str, updates: Dict) -> Dict:
        """Update fields of an existing job.

        Allowed fields: name, schedule, agent, runtime, task, notify, recurring, enabled, mode, model, working_dir.
        If schedule changes, next_run is recalculated.
        """
        allowed = {"name", "schedule", "agent", "runtime", "task", "notify", "recurring", "enabled", "mode", "model", "working_dir", "timeout"}
        invalid = set(updates.keys()) - allowed
        if invalid:
            return {"success": False, "message": f"Unknown fields: {', '.join(invalid)}"}

        jobs = self._load_jobs()
        for job in jobs["jobs"]:
            if job["id"] == job_id:
                # If schedule changed, recalculate next_run
                if "schedule" in updates and updates["schedule"] != job["schedule"]:
                    new_next = parse_schedule_to_next_run(updates["schedule"])
                    job["next_run"] = new_next

                job.update(updates)
                self._save_jobs(jobs)
                self._log(job_id, f"Updated: {list(updates.keys())}")
                return {"success": True, "result": job, "message": f"Job '{job_id}' updated"}

        return {"success": False, "message": f"Job '{job_id}' not found"}

    def pause_job(self, job_id: str) -> Dict:
        """Pause a job."""
        jobs = self._load_jobs()
        for job in jobs["jobs"]:
            if job["id"] == job_id:
                job["enabled"] = False
                self._save_jobs(jobs)
                self._log(job_id, "Job paused")
                return {"success": True, "message": f"Job '{job_id}' paused"}
        return {"success": False, "message": f"Job '{job_id}' not found"}

    def resume_job(self, job_id: str) -> Dict:
        """Resume a job."""
        jobs = self._load_jobs()
        for job in jobs["jobs"]:
            if job["id"] == job_id:
                job["enabled"] = True
                self._save_jobs(jobs)
                self._log(job_id, "Job resumed")
                return {"success": True, "message": f"Job '{job_id}' resumed"}
        return {"success": False, "message": f"Job '{job_id}' not found"}

    def delete_job(self, job_id: str) -> Dict:
        """Delete a job."""
        jobs = self._load_jobs()
        original_count = len(jobs["jobs"])
        jobs["jobs"] = [j for j in jobs["jobs"] if j["id"] != job_id]

        if len(jobs["jobs"]) < original_count:
            self._save_jobs(jobs)
            self._log(job_id, "Job deleted")
            return {"success": True, "message": f"Job '{job_id}' deleted"}
        return {"success": False, "message": f"Job '{job_id}' not found"}

    def run_job(self, job_id: str) -> Dict:
        """Mark a job's last_run timestamp, recalculate next_run, and return job for immediate execution."""
        jobs = self._load_jobs()
        job = next((j for j in jobs["jobs"] if j["id"] == job_id), None)
        if not job:
            return {"success": False, "message": f"Job '{job_id}' not found"}
        job["last_run"] = datetime.utcnow().isoformat() + "Z"
        # Recalculate next_run based on the cron/schedule expression
        schedule = job.get("schedule", "")
        if schedule and job.get("recurring", True):
            new_next = parse_schedule_to_next_run(schedule)
            if new_next:
                job["next_run"] = new_next
                self._log(job_id, f"Next run recalculated: {new_next}")
        self._save_jobs(jobs)
        self._log(job_id, "Manual run triggered via API (run-now)")
        return {"success": True, "result": job, "message": f"Job '{job_id}' triggered for immediate execution"}

    def get_logs(self, job_id: str) -> Dict:
        """Get scheduler logs for a job."""
        log_file = self.logs_dir / f"{job_id}.log"
        if log_file.exists():
            return {"success": True, "result": log_file.read_text(), "message": "Logs retrieved"}
        return {"success": False, "message": f"No logs found for job '{job_id}'"}

    def get_results(self, job_id: str, limit: int = 20) -> Dict:
        """Get execution results for a job (newest first).

        Results are stored in JSONL format at results_dir/{job_id}.jsonl.
        Each line is a JSON object with: timestamp, job_id, job_name, success, output, error.
        """
        results_file = self.results_dir / f"{job_id}.jsonl"
        if not results_file.exists():
            return {"success": True, "result": [], "message": "No results yet"}

        lines = results_file.read_text().strip().splitlines()
        records = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(records) >= limit:
                break

        return {
            "success": True,
            "result": records,
            "message": f"Found {len(records)} results (limit {limit})",
        }

    def save_result(self, job_id: str, job_name: str, success: bool, output: str = "", error: str = "") -> Dict:
        """Save execution result to job results file (JSONL format)."""
        result_file = self.results_dir / f"{job_id}.jsonl"
        timestamp = datetime.utcnow().isoformat() + "Z"
        result = {"timestamp": timestamp, "job_id": job_id, "job_name": job_name, "success": success, "output": output[:5000] if output else "", "error": error[:5000] if error else ""}
        try:
            with open(result_file, "a") as f:
                f.write(json.dumps(result) + "\n")
            return {"success": True, "message": f"Result saved for job {job_id}"}
        except Exception as e:
            return {"success": False, "message": f"Failed to save result: {e}"}

    def doctor(self) -> Dict:
        """Diagnostic tool."""
        issues = []
        fixes = []

        if not self.jobs_file.exists():
            issues.append("Jobs file not found")
            fixes.append(f"Create: {self.jobs_file}")

        if not self.logs_dir.exists():
            issues.append("Logs directory not found")
            fixes.append(f"Create: {self.logs_dir}")

        if not self.results_dir.exists():
            issues.append("Results directory not found")
            fixes.append(f"Create: {self.results_dir}")

        try:
            test_file = self.logs_dir / ".test"
            test_file.write_text("test")
            test_file.unlink()
        except PermissionError:
            issues.append(f"Cannot write to logs directory: {self.logs_dir}")
            fixes.append(f"Fix permissions: chmod 755 {self.logs_dir}")

        try:
            jobs = self._load_jobs()
            all_jobs = jobs.get("jobs", [])
            job_count = len(all_jobs)
            enabled_count = sum(1 for j in all_jobs if j.get("enabled"))
        except json.JSONDecodeError:
            issues.append("jobs.json is corrupted")
            fixes.append("Restore from backup or recreate jobs.json")
            job_count = 0
            enabled_count = 0

        # Check if executor service is running
        executor_running = False
        try:
            import subprocess
            result = subprocess.run(
                ["systemctl", "is-active", "task-scheduler-executor.service"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            executor_running = result.stdout.strip() == "active"
        except Exception:
            pass

        if not executor_running:
            issues.append("task-scheduler-executor.service is not running")
            fixes.append("sudo systemctl start task-scheduler-executor.service")

        return {
            "success": len(issues) == 0,
            "result": {
                "issues": issues,
                "fixes": fixes,
                "jobs_count": job_count,
                "enabled_count": enabled_count,
                "executor_running": executor_running,
                "logs_dir": str(self.logs_dir),
                "results_dir": str(self.results_dir),
                "jobs_file": str(self.jobs_file),
            },
            "message": "Diagnostics complete",
        }

    def _log(self, job_id: str, message: str):
        """Log to job-specific log file."""
        log_file = self.logs_dir / f"{job_id}.log"
        timestamp = datetime.utcnow().isoformat() + "Z"
        with open(log_file, "a") as f:
            f.write(f"[{timestamp}] {message}\n")
