# AoA Task Queue Design

## Why Valkey?

**Chosen**: [Valkey](https://valkey.io/) — the official community fork of Redis.

### Selection Criteria

| Candidate | Redis-Compatible | RAM (idle) | Cross-Process | Install Size | Decision |
|-----------|:---:|:---:|:---:|:---:|:---:|
| **Valkey** | ✅ | ~4 MB | ✅ | ~30 MB | ✅ **Selected** |
| KeyDB | ✅ | ~6 MB | ✅ | ~50 MB | Good, but no Ubuntu 24.04 apt package |
| DragonflyDB | ✅ | ~30 MB | ✅ | ~150 MB | Too large for LXC with limited disk |
| Redis OSS | ✅ | ~5 MB | ✅ | ~40 MB | License concerns post-7.4 |
| SQLite WAL | ❌ | 0 | ✅ | 0 | Already in use; no pub/sub, requires polling |
| asyncio.Queue | ❌ | 0 | ❌ | 0 | In-process only, not cross-process |
| diskcache | ❌ | ~1 MB | ✅ | ~5 MB | Not Redis-compatible |

**Why Valkey wins**:
- Available in Ubuntu 24.04 LTS via `apt install valkey-server` — no PPAs needed
- Only 4 MB RAM idle — perfect for an LXC container with limited resources
- Full Redis protocol compatibility — any Redis client library works
- Official Redis fork maintained by Linux Foundation with active community
- Already enabled + started as systemd service on install

---

## Architecture

```
┌─────────────────────┐     ┌─────────────────┐     ┌──────────────────────┐
│  agent_manager.py   │     │   Valkey (6379)  │     │   AoA Daemon         │
│                     │     │                  │     │   (daemon.py)        │
│  POST /background   │────►│  LPUSH           │     │                      │
│  or @mention        │     │  {agent}:tasks   │◄────│  BRPOP (blocking)    │
│                     │     │                  │     │                      │
│  _route_to_aoa()    │     │  task:{id} hash  │     │  ┌─ThreadPool────┐   │
│  bg_task handler    │     │  {agent}:running │     │  │ Worker 1      │   │
│                     │     │                  │     │  │ Worker 2      │   │
└─────────────────────┘     └─────────────────┘     │  │ ...           │   │
                                                     │  │ Worker N      │   │
         ┌──────────────┐                            │  └───────────────┘   │
         │  SQLite DB   │◄───────────────────────────│                      │
         │  (persist)   │   read full task + update   │  max_concurrent      │
         └──────────────┘   status on complete        │  enforced via        │
                                                     │  Valkey counter      │
                                                     └──────────────────────┘
```

### Data Flow

1. **Task Creation** (agent_manager.py):
   - Task is inserted into SQLite (persistence, as before)
   - Task ID is pushed to Valkey `LPUSH {agent}:tasks {task_id}`
   - Lightweight metadata cached in Valkey `HSET task:{task_id} ...` (24h TTL)
   - Pub/sub notification sent on `aoa:new_task:{agent}` channel

2. **Task Pickup** (daemon.py):
   - Daemon calls `BRPOP` across all enabled agent queues (blocking, 2s timeout)
   - Instantly wakes when a task arrives — no 2-second polling delay
   - Checks `{agent}:running` counter against `max_concurrent`
   - If under limit: increments counter, fetches full task from SQLite, dispatches worker
   - If at limit: task stays in queue (re-pushed), checked on next iteration

3. **Task Completion** (daemon.py worker):
   - Updates SQLite with result/error (as before)
   - Decrements `{agent}:running` counter in Valkey
   - Removes `task:{task_id}` metadata from Valkey

4. **Fallback** (Valkey unavailable):
   - Every queue operation returns a sentinel value (None, -1, False)
   - Daemon detects this and falls back to existing SQLite polling (2s interval)
   - agent_manager.py skips Valkey push — SQLite insert alone is sufficient
   - System degrades gracefully with no data loss

### Per-Agent Queue Keys

| Key Pattern | Type | Purpose |
|-------------|------|---------|
| `{agent}:tasks` | List | FIFO task queue (LPUSH/BRPOP) |
| `{agent}:running` | String (int) | Running task counter (INCR/DECR) |
| `task:{task_id}` | Hash | Cached task metadata (24h TTL) |
| `aoa:new_task:{agent}` | Pub/Sub channel | Wake-up notification |

### Concurrency Control

Each agent has a `max_concurrent` limit from `agents.json`. Enforcement:

```
pop task_id from {agent}:tasks
read running = GET {agent}:running
if running < max_concurrent:
    INCR {agent}:running
    spawn worker(task_id)
else:
    RPUSH {agent}:tasks task_id   # re-queue for later
    sleep briefly
```

On worker completion:
```
DECR {agent}:running
```

Periodic reconciliation syncs the Valkey counter with actual SQLite `running` count
to prevent drift from crashes or missed decrements.

---

## Operations

### Service

```bash
# Status
systemctl status valkey-server

# Restart
systemctl restart valkey-server

# Logs
journalctl -u valkey-server -f
```

### CLI Inspection

```bash
# Ping
valkey-cli ping

# View all agent queues
valkey-cli keys "*:tasks"

# Check pending tasks for an agent
valkey-cli llen "testbot:tasks"
valkey-cli lrange "testbot:tasks" 0 -1

# Check running count
valkey-cli get "testbot:running"

# View task metadata
valkey-cli hgetall "task:<task_id>"

# Queue stats (all agents)
valkey-cli keys "*:running"

# Flush a queue (emergency)
valkey-cli del "testbot:tasks"
valkey-cli set "testbot:running" 0

# Monitor real-time commands
valkey-cli monitor
```

### Configuration

Valkey config: `/etc/valkey/valkey.conf`

Key settings for AoA use case (in-memory queue, no persistence needed):

```conf
# Disable persistence (tasks already in SQLite)
save ""
appendonly no

# Memory limit (keep it tight for LXC)
maxmemory 64mb
maxmemory-policy allkeys-lru

# Listen only on localhost
bind 127.0.0.1
port 6379
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VALKEY_HOST` | `127.0.0.1` | Valkey server host |
| `VALKEY_PORT` | `6379` | Valkey server port |
| `VALKEY_DB` | `0` | Valkey database number |
| `VALKEY_PASSWORD` | *(none)* | Optional authentication password |

### Monitoring

The `/api/v1/aoa/queue-stats` endpoint returns real-time queue statistics:

```json
{
  "valkey": {"status": "healthy", "valkey_version": "7.2.x", "used_memory_human": "4.1M"},
  "queues": {
    "testbot": {"pending": 2, "running": 1},
    "devops-dev": {"pending": 0, "running": 0}
  }
}
```

---

## Failure Modes

| Scenario | Behavior |
|----------|----------|
| Valkey down at startup | Daemon logs warning, uses SQLite polling |
| Valkey crashes mid-operation | Auto-reconnect on next operation; tasks safe in SQLite |
| Daemon crashes with tasks running | On restart, reconciliation resets `{agent}:running` counters from SQLite |
| Task pushed to Valkey but not SQLite | Cannot happen — SQLite insert happens first |
| Orphaned Valkey metadata | 24h TTL auto-expires; `reconcile()` cleans up |
