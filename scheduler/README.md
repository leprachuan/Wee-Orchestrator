# Task Scheduler - Native Wee-Orchestrator Component

The task scheduler is a core component of Wee-Orchestrator responsible for scheduling and executing agent tasks on a recurring or one-time basis.

## Architecture

```
scheduler/
├── __init__.py          # Module exports
├── management.py        # TaskScheduler API (schedule, update, delete, list jobs)
├── executor.py          # APScheduler run loop (polls jobs, executes them)
└── run.py               # Entry point for systemd service
```

**Data Files** (isolated per environment):
- Production: `/opt/.task-scheduler/`
- Development: `/opt/.task-scheduler-dev/`

## Systemd Services

| Environment | Service | Status | Port |
|------------|---------|--------|------|
| **Production** | `task-scheduler-executor.service` | Active | (internal) |
| **Development** | `task-scheduler-executor-dev.service` | Active | (internal) |

## Quick Start

### Schedule a Task (from agent code or REST API)

```python
from scheduler.management import TaskScheduler

scheduler = TaskScheduler()

# Schedule a one-time task
scheduler.schedule_task(
    name="Daily Report",
    schedule="in 5 minutes",
    task="Generate daily summary report",
    agent="devops",
    runtime="claude",
    notify=True  # Send notification when done
)

# Schedule a recurring task
scheduler.schedule_task(
    name="Health Check",
    schedule="every day at 9am",
    task="Check infrastructure health",
    agent="devops",
    recurring=True
)
```

### Schedule String Formats

| Format | Example |
|--------|---------|
| In X time units | `in 5 minutes`, `in 2 hours`, `in 1 day` |
| Every X time units | `every 30 minutes`, `every 6 hours` |
| Daily at time | `every day at 9am`, `every day at 14:30` |

### Manage Jobs

```python
from scheduler.management import TaskScheduler

scheduler = TaskScheduler()

# List all jobs
jobs = scheduler.list_jobs()
print(jobs["result"])

# Get specific job
job = scheduler.get_job("daily-report")

# Update job
scheduler.update_job("daily-report", {"schedule": "every day at 10am"})

# Pause/resume job
scheduler.pause_job("daily-report")
scheduler.resume_job("daily-report")

# Delete job
scheduler.delete_job("daily-report")

# Get execution results
results = scheduler.get_results("daily-report", limit=10)
print(results["result"])  # Last 10 executions
```

## REST API

The scheduler is exposed via agent_manager.py REST API:

### POST `/scheduler/jobs`

Create a scheduled job.

**Request:**
```json
{
  "name": "Daily Report",
  "schedule": "every day at 9am",
  "agent": "devops",
  "runtime": "claude",
  "task": "Generate daily summary",
  "notify": true,
  "recurring": true
}
```

**Response:**
```json
{
  "success": true,
  "result": {
    "id": "daily-report",
    "name": "Daily Report",
    "next_run": "2026-02-24T09:00:00Z",
    "schedule": "every day at 9am",
    "enabled": true
  }
}
```

### GET `/scheduler/jobs`

List all jobs.

### GET `/scheduler/jobs/{job_id}`

Get a specific job.

### PUT `/scheduler/jobs/{job_id}`

Update a job.

### DELETE `/scheduler/jobs/{job_id}`

Delete a job.

### POST `/scheduler/jobs/{job_id}/pause`

Pause a job.

### POST `/scheduler/jobs/{job_id}/resume`

Resume a job.

### GET `/scheduler/jobs/{job_id}/results`

Get execution results for a job.

**Query Parameters:**
- `limit=20` - Number of results (default: 20)

## Environment Configuration

Configure defaults via environment variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `SCHEDULER_JOBS_FILE` | `/opt/.task-scheduler/jobs.json` | Jobs database |
| `SCHEDULER_LOGS_DIR` | `/opt/.task-scheduler/logs/` | Job logs |
| `SCHEDULER_RESULTS_DIR` | `/opt/.task-scheduler/results/` | Execution results (JSONL) |
| `SCHEDULER_DEFAULT_AGENT` | `fosterbot` | Default agent for tasks |
| `SCHEDULER_DEFAULT_RUNTIME` | `claude` | Default runtime for tasks |
| `SCHEDULER_RETRY_MAX` | `3` | Max retries on failure |

**Dev overrides** (set in `/etc/systemd/system/task-scheduler-executor-dev.service`):
- `SCHEDULER_JOBS_FILE=/opt/.task-scheduler-dev/jobs.json`
- `SCHEDULER_LOGS_DIR=/opt/.task-scheduler-dev/logs/`
- `SCHEDULER_RESULTS_DIR=/opt/.task-scheduler-dev/results/`

## Execution Flow

1. **Executor polls** every 1 second: reads `jobs.json`
2. **Identifies ready jobs**: `next_run <= now`
3. **Executes via agent_manager**: calls Python subprocess with agent/runtime/model
4. **Captures results**: stores in JSONL format in `results_dir/{job_id}.jsonl`
5. **Sends notification**: if `notify=true` and `created_by` provided (Telegram/WebEx)
6. **Updates job**: sets `last_run` and calculates next `next_run` for recurring tasks

## Results Format

Execution results stored in JSONL (one JSON object per line):

```json
{
  "timestamp": "2026-02-23T14:30:00Z",
  "job_id": "daily-report",
  "job_name": "Daily Report",
  "success": true,
  "output": "Report generated successfully...",
  "error": null,
  "duration_seconds": 23.45
}
```

## Troubleshooting

### Check Service Status

```bash
# Dev
sudo systemctl status task-scheduler-executor-dev.service

# Production
sudo systemctl status task-scheduler-executor.service
```

### View Logs

```bash
# Real-time logs
sudo journalctl -u task-scheduler-executor-dev.service -f

# Last 50 lines
sudo journalctl -u task-scheduler-executor-dev.service -n 50
```

### Run Diagnostics

```python
from scheduler.management import TaskScheduler

scheduler = TaskScheduler()
diag = scheduler.doctor()
print(diag["result"])
```

### Common Issues

| Issue | Solution |
|-------|----------|
| Jobs not running | Check if service is active: `systemctl status task-scheduler-executor-dev.service` |
| Service fails to start | Check logs: `journalctl -u task-scheduler-executor-dev.service` |
| Jobs file corrupted | Delete and recreate: `rm /opt/.task-scheduler-dev/jobs.json` |
| Results not saving | Check write permissions: `ls -la /opt/.task-scheduler-dev/results/` |

## Integration with Agent Manager

The scheduler is initialized lazily by agent_manager.py when needed:

```python
# In agent_manager.py
from scheduler.management import TaskScheduler

scheduler = TaskScheduler()  # Lazy initialization
```

The REST API exposes scheduler endpoints automatically:
- `POST /scheduler/jobs` - Create job
- `GET /scheduler/jobs` - List jobs
- `PUT /scheduler/jobs/{id}` - Update job
- `DELETE /scheduler/jobs/{id}` - Delete job

## Notes

- **Development isolation**: Dev uses separate directories and jobs.json to avoid interfering with production
- **Notifications**: If `notify=true` and job has `created_by` metadata, executor sends result back via original channel (Telegram/WebEx)
- **No APScheduler dependency**: Uses simple datetime polling instead of APScheduler for reliability and simplicity
- **Timezone**: All times are UTC internally, converted to local timezone for display when needed
