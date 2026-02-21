"""Task scheduler — embedded in Wee-Orchestrator.

Moved from /opt/skills/task-scheduler/shared_infrastructure.py.
Extended with update_job() and get_results() for the REST API.
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional


def parse_schedule_to_next_run(schedule: str) -> Optional[str]:
    """Parse schedule string and return ISO datetime string for next run."""
    schedule = schedule.lower().strip()
    now = datetime.utcnow()

    # Handle "in X minutes/hours/seconds/days" format
    if schedule.startswith("in "):
        parts = schedule[3:].split()
        if len(parts) >= 2:
            try:
                amount = int(parts[0])
                unit = parts[1].rstrip('s')

                if unit == "minute":
                    next_run = now + timedelta(minutes=amount)
                elif unit == "hour":
                    next_run = now + timedelta(hours=amount)
                elif unit == "second":
                    next_run = now + timedelta(seconds=amount)
                elif unit == "day":
                    next_run = now + timedelta(days=amount)
                else:
                    return None

                return next_run.isoformat() + "Z"
            except ValueError:
                pass

    # Handle "every X minutes/hours/days" format
    if schedule.startswith("every "):
        parts = schedule[6:].split()
        if len(parts) >= 2:
            try:
                amount = int(parts[0])
                unit = parts[1].rstrip('s')

                if unit == "minute":
                    next_run = now + timedelta(minutes=amount)
                elif unit == "hour":
                    next_run = now + timedelta(hours=amount)
                elif unit == "day":
                    next_run = now + timedelta(days=amount)
                else:
                    return None

                return next_run.isoformat() + "Z"
            except ValueError:
                pass

        # Handle "every day at HH:MM" or "every day at HHam/pm"
        if "at" in schedule:
            time_part = schedule.split("at")[1].strip()
            try:
                if "am" in time_part or "pm" in time_part:
                    time_obj = datetime.strptime(
                        time_part.replace("am", "").replace("pm", "").strip(), "%I"
                    ).time()
                    if "pm" in time_part and time_obj.hour != 12:
                        time_obj = time_obj.replace(hour=time_obj.hour + 12)
                    elif "am" in time_part and time_obj.hour == 12:
                        time_obj = time_obj.replace(hour=0)
                else:
                    time_obj = datetime.strptime(time_part, "%H:%M").time()

                next_run = now.replace(
                    hour=time_obj.hour, minute=time_obj.minute, second=0, microsecond=0
                )
                if next_run <= now:
                    next_run += timedelta(days=1)

                return next_run.isoformat() + "Z"
            except (ValueError, AttributeError):
                pass

    return None


class TaskScheduler:
    def __init__(self, config: Optional[Dict] = None):
        self.jobs_file = Path(os.getenv("SCHEDULER_JOBS_FILE", "/opt/.task-scheduler/jobs.json"))
        self.logs_dir = Path(os.getenv("SCHEDULER_LOGS_DIR", "/opt/.task-scheduler/logs/"))
        self.results_dir = Path(os.getenv("SCHEDULER_RESULTS_DIR", "/opt/.task-scheduler/results/"))
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
        return json.loads(self.jobs_file.read_text())

    def _save_jobs(self, data: Dict):
        """Save jobs to JSON."""
        self.jobs_file.write_text(json.dumps(data, indent=2))

    def schedule_task(
        self,
        name: str,
        schedule: str,
        agent: str = None,
        runtime: str = None,
        task: str = "",
        notify: bool = False,
        recurring: bool = True,
        created_by: Optional[Dict] = None,
    ) -> Dict:
        """Create a scheduled task.

        created_by: optional dict with keys 'identity', 'channel', and optionally 'username'.
        Stored in the job so the executor can send notifications back to the right person.
        """
        if agent is None:
            agent = os.getenv("SCHEDULER_DEFAULT_AGENT", "fosterbot")
        if runtime is None:
            runtime = os.getenv("SCHEDULER_DEFAULT_RUNTIME", "claude")

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
            "task": task,
            "schedule": schedule,
            "notify": notify,
            "recurring": recurring,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "next_run": next_run,
            "last_run": None,
            "enabled": True,
            "retries": 0,
            "created_by": created_by or {},
        }

        jobs["jobs"].append(job)
        self._save_jobs(jobs)
        self._log(job_id, f"Scheduled: {name} (next run: {next_run}, recurring: {recurring})")

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

        Allowed fields: name, schedule, agent, runtime, task, notify, recurring, enabled.
        If schedule changes, next_run is recalculated.
        """
        allowed = {"name", "schedule", "agent", "runtime", "task", "notify", "recurring", "enabled"}
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
