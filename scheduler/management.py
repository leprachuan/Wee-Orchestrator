"""Task scheduler — embedded in Wee-Orchestrator.

Moved from /opt/skills/task-scheduler/shared_infrastructure.py.
Extended with update_job() and get_results() for the REST API.
Now supports AI-powered natural language to cron conversion via Claude.
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # Python < 3.9

try:
    from croniter import croniter
except ImportError:
    croniter = None

logger = logging.getLogger(__name__)


def _get_local_tz_name() -> str:
    """Return IANA timezone name from env, /etc/localtime, /etc/timezone, or UTC."""
    tz_env = os.environ.get("TZ", "").strip()
    if tz_env:
        return tz_env
    try:
        link = os.readlink("/etc/localtime")
        idx = link.find("zoneinfo/")
        if idx >= 0:
            return link[idx + len("zoneinfo/"):]
    except OSError:
        pass
    try:
        with open("/etc/timezone") as fh:
            name = fh.read().strip()
        if name and name not in ("Etc/UTC", "UTC"):
            return name
    except OSError:
        pass
    return "UTC"


def _get_local_tz():
    """Return DST-aware tzinfo for the server local timezone."""
    tz_name = _get_local_tz_name()
    if ZoneInfo is not None:
        try:
            return ZoneInfo(tz_name)
        except Exception:
            pass
    # Fallback: current fixed-offset (no future DST awareness)
    return datetime.now().astimezone().tzinfo


# ---------------------------------------------------------------------------
# Cron helpers
# ---------------------------------------------------------------------------


def is_valid_cron(expression: str) -> bool:
    """Check if a string is a valid cron expression (5 or 6 field)."""
    if croniter is None:
        return False
    try:
        croniter(expression)
        return True
    except (ValueError, KeyError, TypeError):
        return False


def cron_next_run(expression: str, _base_local: Optional[datetime] = None) -> Optional[str]:
    """Get next run time from a cron expression as UTC ISO string.

    Cron expressions are interpreted in the server local timezone so that
    "30 7 * * 1-5" means 7:30 AM local time, not 7:30 UTC.
    The returned ISO string is always UTC (Z suffix) for consistent storage.
    DST is handled automatically via the IANA timezone database.

    Args:
        expression: 5-field cron expression.
        _base_local: Optional tz-aware datetime to use as "now" (for testing).
    """
    # _base_local must carry tzinfo; that tzinfo becomes the local timezone.
    if croniter is None:
        return None
    try:
        if _base_local is not None:
            local_tz = _base_local.tzinfo
            now_local = _base_local
        else:
            local_tz = _get_local_tz()
            now_local = datetime.now(local_tz)
        now_naive = now_local.replace(tzinfo=None)
        cron = croniter(expression, now_naive)
        next_naive = cron.get_next(datetime)
        # Reattach local tz and convert to UTC for storage
        next_local = next_naive.replace(tzinfo=local_tz)
        next_utc = next_local.astimezone(timezone.utc)
        return next_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, KeyError, TypeError):
        return None


def cron_human_readable(expression: str) -> str:
    """Convert a cron expression to a rough human-readable description."""
    parts = expression.strip().split()
    if len(parts) < 5:
        return expression

    minute, hour, dom, month, dow = parts[:5]

    if expression == "* * * * *":
        return "every minute"
    if minute != "*" and hour != "*" and dom == "*" and month == "*" and dow == "*":
        return f"every day at {hour.zfill(2)}:{minute.zfill(2)} UTC"
    if minute != "*" and hour != "*" and dom == "*" and month == "*" and dow != "*":
        day_names = {
            "0": "Sun",
            "1": "Mon",
            "2": "Tue",
            "3": "Wed",
            "4": "Thu",
            "5": "Fri",
            "6": "Sat",
            "7": "Sun",
            "1-5": "weekdays",
            "0,6": "weekends",
        }
        day_label = day_names.get(dow, f"dow={dow}")
        return f"{day_label} at {hour.zfill(2)}:{minute.zfill(2)} UTC"
    if minute.startswith("*/") or hour.startswith("*/"):
        if minute.startswith("*/") and hour == "*":
            return f"every {minute[2:]} minutes"
        if hour.startswith("*/") and minute == "0":
            return f"every {hour[2:]} hours"
    return expression


# ---------------------------------------------------------------------------
# AI-powered schedule conversion
# ---------------------------------------------------------------------------

_SCHEDULE_CONVERSION_PROMPT = """Convert this natural language schedule description to a standard 5-field cron expression.

Schedule: "{schedule}"

Rules:
- Return ONLY the cron expression, nothing else
- Use 5-field format: minute hour day-of-month month day-of-week
- Use 0-6 for day-of-week (0=Sunday)
- Assume UTC timezone
- "every minute" = * * * * *
- "every hour" = 0 * * * *
- "every day at 9am" = 0 9 * * *
- "every weekday at 9am" = 0 9 * * 1-5
- "every Monday at 8am" = 0 8 * * 1
- "every 5 minutes" = */5 * * * *
- "every 6 hours" = 0 */6 * * *
- "twice daily at 9am and 5pm" = 0 9,17 * * *
- "every Sunday at noon" = 0 12 * * 0
- "1st of every month at midnight" = 0 0 1 * *
- "every 30 seconds" cannot be expressed in cron, use: * * * * * (every minute as closest)

Return ONLY the cron expression. No explanation, no backticks, no quotes."""


def convert_schedule_with_ai(schedule: str, api_key: str = None) -> Optional[str]:
    """Use Claude to convert natural language schedule to cron expression.

    Returns the cron expression string, or None if conversion fails.
    """
    try:
        import anthropic
    except ImportError:
        logger.debug("anthropic SDK not available for schedule conversion")
        return None

    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        logger.debug("No ANTHROPIC_API_KEY available for schedule conversion")
        return None

    try:
        client = anthropic.Anthropic(api_key=key)
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=50,
            messages=[
                {
                    "role": "user",
                    "content": _SCHEDULE_CONVERSION_PROMPT.format(schedule=schedule),
                }
            ],
        )
        result = response.content[0].text.strip()
        # Clean up: remove backticks, quotes, extra whitespace
        result = result.strip("`'\"\n ")
        # Validate it's actually a cron expression
        if is_valid_cron(result):
            return result
        logger.warning(f"AI returned invalid cron expression: {result!r}")
        return None
    except Exception as e:
        logger.warning(f"AI schedule conversion failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Deterministic fallback parser (regex-based)
# ---------------------------------------------------------------------------

_DAY_MAP = {
    "sunday": 0,
    "sun": 0,
    "monday": 1,
    "mon": 1,
    "tuesday": 2,
    "tue": 2,
    "tues": 2,
    "wednesday": 3,
    "wed": 3,
    "thursday": 4,
    "thu": 4,
    "thurs": 4,
    "friday": 5,
    "fri": 5,
    "saturday": 6,
    "sat": 6,
}


def _parse_time(time_str: str) -> Optional[Tuple[int, int]]:
    """Parse time like '9am', '14:30', '9:00pm', 'noon', 'midnight'."""
    time_str = time_str.strip().lower()
    if time_str in ("noon", "12pm", "12:00pm"):
        return (12, 0)
    if time_str in ("midnight", "12am", "12:00am"):
        return (0, 0)

    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", time_str)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or "0")
    meridiem = m.group(3)

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
    return (hour, minute)


def convert_schedule_deterministic(schedule: str) -> Optional[str]:
    """Convert natural language schedule to cron using regex patterns.

    Returns cron expression or None if not recognized.
    """
    s = re.sub(r"\s+", " ", schedule.lower().strip())

    # Direct cron passthrough: if it already looks like cron, return it
    if re.match(
        r"^[\d\*\/,\-]+\s+[\d\*\/,\-]+\s+[\d\*\/,\-]+\s+[\d\*\/,\-]+\s+[\d\*\/,\-]+$", s
    ):
        return s

    # "every minute"
    if s in ("every minute", "every 1 minute", "every 1 minutes"):
        return "* * * * *"
    # "every hour"
    if s in ("every hour", "every 1 hour", "every 1 hours", "hourly"):
        return "0 * * * *"
    # "daily" / "every day" (no time specified → midnight)
    if s in ("daily", "every day"):
        return "0 0 * * *"
    # "weekly"
    if s in ("weekly", "every week"):
        return "0 0 * * 0"

    # "every N minutes/hours"
    m = re.fullmatch(r"every (\d+) (minute|minutes|min|mins)", s)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 59:
            return f"*/{n} * * * *"
    m = re.fullmatch(r"every (\d+) (hour|hours|hr|hrs)", s)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 23:
            return f"0 */{n} * * *"

    # "every day at TIME"
    m = re.fullmatch(r"every day at (.+)", s)
    if m:
        t = _parse_time(m.group(1))
        if t:
            return f"{t[1]} {t[0]} * * *"

    # "every weekday at TIME" / "weekdays at TIME"
    m = re.fullmatch(r"(?:every )?weekday(?:s)? at (.+)", s)
    if m:
        t = _parse_time(m.group(1))
        if t:
            return f"{t[1]} {t[0]} * * 1-5"

    # "every weekend at TIME"
    m = re.fullmatch(r"(?:every )?weekend(?:s)? at (.+)", s)
    if m:
        t = _parse_time(m.group(1))
        if t:
            return f"{t[1]} {t[0]} * * 0,6"

    # "every DAYNAME at TIME"
    m = re.fullmatch(r"every (\w+) at (.+)", s)
    if m:
        day = _DAY_MAP.get(m.group(1))
        if day is not None:
            t = _parse_time(m.group(2))
            if t:
                return f"{t[1]} {t[0]} * * {day}"

    # "twice daily at TIME and TIME"
    m = re.fullmatch(r"twice daily at (.+) and (.+)", s)
    if m:
        t1 = _parse_time(m.group(1))
        t2 = _parse_time(m.group(2))
        if t1 and t2 and t1[1] == t2[1]:
            return f"{t1[1]} {t1[0]},{t2[0]} * * *"
        if t1 and t2:
            return f"{t1[1]} {t1[0]} * * *"  # approximate: use first time's minute

    # "every N days at TIME"
    m = re.fullmatch(r"every (\d+) days? at (.+)", s)
    if m:
        n = int(m.group(1))
        t = _parse_time(m.group(2))
        if t and 1 <= n <= 28:
            return f"{t[1]} {t[0]} */{n} * *"

    # "every N weeks" / "every N weeks at TIME"
    m = re.fullmatch(r"every (\d+) weeks?(?:\s+at\s+(.+))?", s)
    if m:
        n = int(m.group(1))
        time_str = m.group(2)
        t = _parse_time(time_str) if time_str else (0, 0)
        if t:
            days = n * 7
            if days <= 28:
                return f"{t[1]} {t[0]} */{days} * *"
            else:
                return f"{t[1]} {t[0]} 1 */{n} *"

    # "1st/15th of every month at TIME"
    m = re.fullmatch(r"(?:the )?(\d{1,2})(?:st|nd|rd|th) of every month at (.+)", s)
    if m:
        dom = int(m.group(1))
        t = _parse_time(m.group(2))
        if t and 1 <= dom <= 31:
            return f"{t[1]} {t[0]} {dom} * *"

    return None


# ---------------------------------------------------------------------------
# One-time schedule helpers (for "in X minutes" style)
# ---------------------------------------------------------------------------


def parse_one_time_schedule(schedule: str) -> Optional[str]:
    """Parse one-time schedule strings like 'in 5 minutes' to ISO datetime."""
    s = re.sub(r"\s+", " ", schedule.lower().strip())
    now = datetime.utcnow()

    m = re.fullmatch(
        r"in (\d+) (second|seconds|sec|secs|minute|minutes|min|mins|hour|hours|hr|hrs|day|days)",
        s,
    )
    if m:
        amount = int(m.group(1))
        unit = m.group(2).rstrip("s")
        unit = {"sec": "second", "min": "minute", "hr": "hour"}.get(unit, unit)
        deltas = {
            "second": timedelta(seconds=amount),
            "minute": timedelta(minutes=amount),
            "hour": timedelta(hours=amount),
            "day": timedelta(days=amount),
        }
        delta = deltas.get(unit)
        if delta:
            return (now + delta).isoformat() + "Z"
    return None


# ---------------------------------------------------------------------------
# Unified conversion: AI → deterministic fallback
# ---------------------------------------------------------------------------


def convert_schedule(schedule: str, use_ai: bool = True) -> Dict:
    """Convert a natural language schedule to cron format.

    Returns dict with:
        cron: str or None - the cron expression
        next_run: str or None - ISO datetime of next run
        human_readable: str - human-readable description
        method: str - "ai", "deterministic", "one_time", or "failed"
        original: str - the original schedule string
    """
    schedule = schedule.strip()

    # 1. If already a valid cron expression, use directly
    if is_valid_cron(schedule):
        return {
            "cron": schedule,
            "next_run": cron_next_run(schedule),
            "human_readable": cron_human_readable(schedule),
            "method": "passthrough",
            "original": schedule,
        }

    # 2. Check for one-time schedules ("in 5 minutes")
    one_time = parse_one_time_schedule(schedule)
    if one_time:
        return {
            "cron": None,
            "next_run": one_time,
            "human_readable": schedule,
            "method": "one_time",
            "original": schedule,
        }

    # 3. Try deterministic conversion first (fast, no API call)
    det_cron = convert_schedule_deterministic(schedule)
    if det_cron and is_valid_cron(det_cron):
        return {
            "cron": det_cron,
            "next_run": cron_next_run(det_cron),
            "human_readable": cron_human_readable(det_cron),
            "method": "deterministic",
            "original": schedule,
        }

    # 4. Try AI conversion (Claude)
    if use_ai:
        ai_cron = convert_schedule_with_ai(schedule)
        if ai_cron:
            return {
                "cron": ai_cron,
                "next_run": cron_next_run(ai_cron),
                "human_readable": cron_human_readable(ai_cron),
                "method": "ai",
                "original": schedule,
            }

    # 5. Failed
    return {
        "cron": None,
        "next_run": None,
        "human_readable": schedule,
        "method": "failed",
        "original": schedule,
    }


# ---------------------------------------------------------------------------
# Legacy compatibility wrapper
# ---------------------------------------------------------------------------


def parse_schedule_to_next_run(schedule: str) -> Optional[str]:
    """Legacy wrapper: parse schedule string and return ISO datetime for next run.

    Now uses croniter for cron expressions and the unified converter for natural language.
    """
    schedule = schedule.strip()

    # If it has a cron field stored, use croniter directly
    if is_valid_cron(schedule):
        return cron_next_run(schedule)

    # Try unified conversion (without AI for backward compat in executor hot path)
    result = convert_schedule(schedule, use_ai=False)
    return result.get("next_run")


# ===================================================================
# TaskScheduler class
# ===================================================================


class TaskScheduler:
    def __init__(self, config: Optional[Dict] = None):
        repo_root = Path(__file__).resolve().parent.parent
        base_dir = str(repo_root / ".task-scheduler")

        self.jobs_file = Path(os.getenv("SCHEDULER_JOBS_FILE", f"{base_dir}/jobs.json"))
        self.logs_dir = Path(os.getenv("SCHEDULER_LOGS_DIR", f"{base_dir}/logs/"))
        self.results_dir = Path(
            os.getenv("SCHEDULER_RESULTS_DIR", f"{base_dir}/results/")
        )
        self.max_retries = int(os.getenv("SCHEDULER_RETRY_MAX", 3))

        self.jobs_file.parent.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self._init_jobs_file()

        # Run migration on first load
        self._migrate_jobs_to_cron()

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

    def _migrate_jobs_to_cron(self):
        """Migrate existing jobs: add cron field if missing."""
        jobs = self._load_jobs()
        migrated = 0
        for job in jobs.get("jobs", []):
            if "cron" not in job:
                schedule = job.get("schedule", "")
                result = convert_schedule(schedule, use_ai=True)
                job["cron"] = result.get("cron")
                if result.get("cron") and result.get("next_run"):
                    job["next_run"] = result["next_run"]
                    self._log(
                        job["id"],
                        f"Migrated schedule \"{schedule}\" → cron \"{result['cron']}\" (method: {result['method']})",
                    )
                elif result.get("method") == "one_time":
                    self._log(job["id"], f'One-time schedule "{schedule}" kept as-is')
                else:
                    self._log(
                        job["id"],
                        f"Could not convert schedule \"{schedule}\" to cron (method: {result['method']})",
                    )
                migrated += 1
        if migrated > 0:
            self._save_jobs(jobs)
            logger.info(f"Migrated {migrated} jobs to include cron field")

    def _calculate_next_run(self, job: Dict) -> Optional[str]:
        """Calculate next run time using cron field (preferred) or schedule string."""
        cron_expr = job.get("cron")
        if cron_expr and is_valid_cron(cron_expr):
            return cron_next_run(cron_expr)

        # Fallback to schedule string for one-time or legacy jobs
        schedule = job.get("schedule", "")
        if schedule:
            return parse_schedule_to_next_run(schedule)
        return None

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
        permission_mode: str = None,
        cron: str = None,
    ) -> Dict:
        """Create a scheduled task.

        Args:
            name: Task name
            schedule: Schedule string (e.g. "every day at 9am", "in 5 minutes")
            agent: Agent name (default: orchestrator)
            runtime: Runtime (default: claude)
            model: Model override
            mode: "ai" (LLM-based, default) or "command" (direct shell)
            task: Task description or shell command
            notify: Send notification when done
            recurring: Whether task repeats
            working_dir: Working directory for command mode
            created_by: Optional dict with identity, channel, username
            timeout: Execution timeout in seconds
            permission_mode: Permission level (elevated/restricted/sandboxed)
            cron: Pre-validated cron expression (skips conversion if provided)
        """
        if agent is None:
            agent = os.getenv("SCHEDULER_DEFAULT_AGENT", "orchestrator")
        if runtime is None:
            runtime = os.getenv("SCHEDULER_DEFAULT_RUNTIME", "claude")
        if working_dir is None:
            working_dir = "/opt"

        jobs = self._load_jobs()
        job_id = name.lower().replace(" ", "-")

        existing_ids = {j["id"] for j in jobs["jobs"]}
        base_id = job_id
        counter = 2
        while job_id in existing_ids:
            job_id = f"{base_id}-{counter}"
            counter += 1

        # Convert schedule to cron if not already provided
        if cron and is_valid_cron(cron):
            cron_expr = cron
            next_run = cron_next_run(cron)
        else:
            conversion = convert_schedule(schedule, use_ai=True)
            cron_expr = conversion.get("cron")
            next_run = conversion.get("next_run")

        job = {
            "id": job_id,
            "name": name,
            "agent": agent,
            "runtime": runtime,
            "model": model,
            "mode": mode or "ai",
            "permission_mode": permission_mode or "restricted",
            "task": task,
            "schedule": schedule,
            "cron": cron_expr,
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
        cron_info = f", cron: {cron_expr}" if cron_expr else ""
        self._log(
            job_id,
            f"Scheduled: {name} (next run: {next_run}, recurring: {recurring}, mode: {mode or 'ai'}{cron_info})",
        )

        return {
            "success": True,
            "result": job,
            "message": f"Task '{name}' scheduled for {next_run}",
        }

    def list_jobs(self) -> Dict:
        """List all scheduled jobs."""
        jobs = self._load_jobs()
        return {
            "success": True,
            "result": jobs["jobs"],
            "message": f"Found {len(jobs['jobs'])} jobs",
        }

    def get_job(self, job_id: str) -> Dict:
        """Get a single job by ID."""
        jobs = self._load_jobs()
        job = next((j for j in jobs["jobs"] if j["id"] == job_id), None)
        if job:
            return {"success": True, "result": job, "message": "Job found"}
        return {"success": False, "message": f"Job '{job_id}' not found"}

    def update_job(self, job_id: str, updates: Dict) -> Dict:
        """Update fields of an existing job."""
        allowed = {
            "name",
            "schedule",
            "agent",
            "runtime",
            "task",
            "notify",
            "recurring",
            "enabled",
            "mode",
            "model",
            "working_dir",
            "timeout",
            "cron",
        }
        invalid = set(updates.keys()) - allowed
        if invalid:
            return {
                "success": False,
                "message": f"Unknown fields: {', '.join(invalid)}",
            }

        jobs = self._load_jobs()
        for job in jobs["jobs"]:
            if job["id"] == job_id:
                # If schedule changed, reconvert to cron
                if "schedule" in updates and updates["schedule"] != job.get("schedule"):
                    conversion = convert_schedule(updates["schedule"], use_ai=True)
                    job["cron"] = conversion.get("cron")
                    job["next_run"] = conversion.get("next_run")
                elif "cron" in updates:
                    cron_val = updates.pop("cron")
                    if cron_val and is_valid_cron(cron_val):
                        job["cron"] = cron_val
                        job["next_run"] = cron_next_run(cron_val)

                job.update(updates)
                self._save_jobs(jobs)
                self._log(job_id, f"Updated: {list(updates.keys())}")
                return {
                    "success": True,
                    "result": job,
                    "message": f"Job '{job_id}' updated",
                }

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
                # Recalculate next_run on resume
                next_run = self._calculate_next_run(job)
                if next_run:
                    job["next_run"] = next_run
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
        if job.get("recurring", True):
            new_next = self._calculate_next_run(job)
            if new_next:
                job["next_run"] = new_next
                self._log(job_id, f"Next run recalculated: {new_next}")
        self._save_jobs(jobs)
        self._log(job_id, "Manual run triggered via API (run-now)")
        return {
            "success": True,
            "result": job,
            "message": f"Job '{job_id}' triggered for immediate execution",
        }

    def get_logs(self, job_id: str) -> Dict:
        """Get scheduler logs for a job."""
        log_file = self.logs_dir / f"{job_id}.log"
        if log_file.exists():
            return {
                "success": True,
                "result": log_file.read_text(),
                "message": "Logs retrieved",
            }
        return {"success": False, "message": f"No logs found for job '{job_id}'"}

    def get_results(self, job_id: str, limit: int = 20) -> Dict:
        """Get execution results for a job (newest first)."""
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

    def save_result(
        self,
        job_id: str,
        job_name: str,
        success: bool,
        output: str = "",
        error: str = "",
    ) -> Dict:
        """Save execution result to job results file (JSONL format)."""
        result_file = self.results_dir / f"{job_id}.jsonl"
        timestamp = datetime.utcnow().isoformat() + "Z"
        result = {
            "timestamp": timestamp,
            "job_id": job_id,
            "job_name": job_name,
            "success": success,
            "output": output[:5000] if output else "",
            "error": error[:5000] if error else "",
        }
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

        # Check croniter availability
        if croniter is None:
            issues.append("croniter library not installed")
            fixes.append("pip install croniter")

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

        # Check AI conversion availability
        ai_available = False
        try:
            import anthropic

            key = os.environ.get("ANTHROPIC_API_KEY", "")
            ai_available = bool(key)
        except ImportError:
            pass

        return {
            "success": len(issues) == 0,
            "result": {
                "issues": issues,
                "fixes": fixes,
                "jobs_count": job_count,
                "enabled_count": enabled_count,
                "executor_running": executor_running,
                "croniter_available": croniter is not None,
                "ai_conversion_available": ai_available,
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
