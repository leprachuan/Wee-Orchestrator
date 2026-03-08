# n8n-copilot-shim Background Task Notifications & Muting Investigation

## Summary
The system uses a multi-layered notification architecture that routes background task completion notifications to WebUI (stored), Telegram, and WebEx channels. User muting preferences are tracked per-session and inherited across channels.

---

## 1. BACKGROUND TASK NOTIFICATIONS - HOW THEY'RE SENT

### Flow Diagram
```
Background Task Creation → Execution → Completion → _emit_bg_notification() 
    ↓                          ↓           ↓                ↓
create_background_task()   _run_background_task()  _emit_bg_notification()  notification_mgr.create_notification()
(Extract channel/identity)  (Execute task)         (Check notify flag)      (Route to channels)
```

### Key Files and Flow:

#### **1. Task Creation: `/api/v1/background-tasks` Endpoint**
**File:** `/opt/n8n-copilot-shim-dev/agent_manager.py:5571-5692`

```python
# Lines 5571-5592: Authenticate and extract user identity from headers
async def create_background_task(body: BackgroundTaskRequest, request: Request):
    user = await authenticate(
        request,
        authorization=request.headers.get("authorization"),
        x_user_identity=request.headers.get("x-user-identity"),  # User ID from channel
        x_auth_channel=request.headers.get("x-auth-channel"),     # "telegram" or "webex"
    )
    channel = user["channel"]
    identity = user["identity"]

# Lines 5670-5681: Determine notification preference (body override > session default > True)
notify_pref = body.notify
if notify_pref is None:
    session_pref = defaults.get("notification_preference", "all")
    notify_pref = (session_pref != "off")

# Pass notify_pref to background executor
loop.run_in_executor(
    bg_executor,
    _run_background_task,
    task_id, session_id, body.prompt, agent, runtime, model, channel, identity, bg_timeout,
    notify_pref  # <-- MUTING DECISION MADE HERE
)
```

#### **2. Task Execution & Notification Trigger**
**File:** `/opt/n8n-copilot-shim-dev/agent_manager.py:5494-5569`

When the task completes (successfully or with error):
```python
def _run_background_task(task_id, session_id, prompt, agent, runtime, model, 
                        channel, user_identity, timeout=None, notify=True):
    # ...execute copilot command...
    
    if result.returncode == 0:
        # Task succeeded
        _emit_bg_notification(task_id, prompt, "completed", channel, user_identity,
                            output_preview=final_output, error=None, notify=notify)  # Line 5551
    else:
        # Task failed
        _emit_bg_notification(task_id, prompt, "failed", channel, user_identity,
                            output_preview=None, error=error_msg, notify=notify)    # Line 5556
```

#### **3. Notification Emission: Decision Point**
**File:** `/opt/n8n-copilot-shim-dev/agent_manager.py:5473-5493`

```python
def _emit_bg_notification(task_id, prompt, status, channel, user_identity, 
                         output_preview=None, error=None, notify=True):
    """Emit a background task completion notification via notification_mgr."""
    if notification_mgr is None:
        return
    
    user_key = bg_task_mgr._user_key(channel, user_identity)
    notification_mgr.create_notification(
        task_id=task_id,
        description=prompt[:200],
        status=status,
        channel=channel,
        user_key=user_key,
        output_preview=output_preview,
        error=error,
        skip_external=not notify,  # <-- MUTE ENFORCEMENT: if notify=False, skip_external=True
    )
```

#### **4. Notification Manager: Channel Routing**
**File:** `/opt/n8n-copilot-shim-dev/notification_manager.py:53-99`

```python
def create_notification(self, task_id, description, status, channel, user_key, 
                       output_preview=None, error=None, skip_external=False):
    
    notification = {
        "notification_id": notif_id,
        "task_id": task_id,
        "channel": channel,
        "user_key": user_key,
        # ... other fields ...
    }
    
    # ALWAYS store for WebUI polling (regardless of mute status)
    with self._lock:
        notifications = self._load()
        notifications.append(notification)
        self._save(notifications)
    
    # Route to external channels ONLY if NOT muted (skip_external=False)
    if not skip_external:  # Lines 89-94
        print(f"[NotificationManager] Routing to external channel: {channel}")
        if channel and channel.lower() == "telegram":
            self._notify_telegram(notification)  # Line 92
        elif channel and channel.lower() == "webex":
            self._notify_webex(notification)     # Line 94
    else:
        print(f"[NotificationManager] Skipping external notification (skip_external=True)")
```

---

## 2. MUTING LOGIC

### Muting Mechanism:

**Mute Flag Storage:** Session-level `notification_preference` field
- **"all"** = Notifications enabled (default)
- **"off"** = Notifications muted

### Mute Command: `/notifications [on|off|mute|current]`
**File:** `/opt/n8n-copilot-shim-dev/agent_manager.py:3985-4002`

```python
elif command == "/notifications":
    if not argument:
        argument = "current"
    
    if argument == "current":
        pref = session_data.get("notification_preference", "all")  # Line 3990
        status = "ON (All updates)" if pref == "all" else "OFF (WebUI only)"
        return f"🔔 **Background Notifications:** `{status}`"
    
    elif argument in ["on", "all"]:
        self.update_session_field(n8n_session_id, "notification_preference", "all")  # Line 3995
        return "✓ Background task notifications enabled for Telegram/WebEx."
    
    elif argument in ["off", "mute"]:
        self.update_session_field(n8n_session_id, "notification_preference", "off")  # Line 3999
        return "✓ Background task notifications muted for Telegram/WebEx (WebUI only)."
    else:
        return "Usage: `/notifications [on|off]` to toggle background task notifications."
```

### Mute Enforcement at Task Creation:
**File:** `/opt/n8n-copilot-shim-dev/agent_manager.py:5629-5675`

When creating a background task, the system searches for existing session data to inherit the `notification_preference`:

```python
# Lines 5606-5644: Search for matching sessions to inherit notification_preference
for n8n_sid, data in session_map.items():
    # ... matching logic ...
    if match_found:
        pref = data.get("notification_preference")  # Line 5630
        if pref:
            # Priority: if any matching session has it "off", use "off"
            if pref == "off" or not defaults.get("notification_preference"):
                defaults["notification_preference"] = pref  # Line 5634
                print(f"[API] Inherited notification_preference '{pref}' from session")

# Lines 5671-5675: Use inherited preference when creating task
notify_pref = body.notify  # Body can override
if notify_pref is None:
    session_pref = defaults.get("notification_preference", "all")
    notify_pref = (session_pref != "off")  # If "off", set notify_pref=False
```

### How Mute Is Enforced:

1. User runs `/notifications off` in a session
2. When user creates a background task (no `notify` override in request body)
3. System inherits `notification_preference="off"` from any matching session
4. Task created with `notify=False`
5. When task completes, `_emit_bg_notification()` called with `notify=False`
6. Notification manager receives `skip_external=True`
7. External (Telegram/WebEx) notifications are **skipped**
8. WebUI notification is **still stored** for polling

### Result:
- ✅ **WebUI:** Always receives notification (stored in JSON)
- ✅ **Telegram/WebEx:** Only if `notification_preference != "off"`
- ✅ **Inheritance:** Preference inherits from ANY matching session across channels

---

## 3. USER IDENTITY TRACKING FOR BACKGROUND TASKS

### Header-Based Identity Extraction:
**File:** `/opt/n8n-copilot-shim-dev/agent_manager.py:5571-5580`

```python
async def create_background_task(body: BackgroundTaskRequest, request: Request):
    user = await authenticate(
        request,
        authorization=request.headers.get("authorization"),
        x_user_identity=request.headers.get("x-user-identity"),  # User ID from caller
        x_auth_channel=request.headers.get("x-auth-channel"),     # "telegram", "webex", "webui"
    )
    
    channel = user["channel"]       # "telegram" or "webex"
    identity = user["identity"]    # User ID (chat_id for Telegram, email for WebEx)
```

### Header Usage in Background Task Flow:
1. **WebUI Session**: `X-User-Identity` = WebUI user identifier
2. **Telegram Connector**: `X-User-Identity` = Telegram chat_id, `X-Auth-Channel` = "telegram"
3. **WebEx Connector**: `X-User-Identity` = email/person_id, `X-Auth-Channel` = "webex"

**File:** `/opt/n8n-copilot-shim-dev/test_mute_integration.py:22-24`
```python
HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {AUTH_TOKEN}",
    "X-User-Identity": "test-user-mute",
    "X-Auth-Channel": "webex"
}
```

### User Key Construction:
**File:** `/opt/n8n-copilot-shim-dev/agent_manager.py:5480, 5811, 5827`

```python
user_key = bg_task_mgr._user_key(channel, user_identity)
# Result: "{channel}_{identity}" (e.g., "telegram_12345", "webex_user@domain.com")
```

This `user_key` is used to:
- Store task metadata
- Associate notifications with the correct user
- Enforce authorization (only user who created the task can view/manage it)

---

## 4. TESTS FOR BACKGROUND TASKS & NOTIFICATIONS

### Unit Test: Mute Functionality
**File:** `/opt/n8n-copilot-shim-dev/test_mute_fix.py` (110 lines)

Tests:
1. `/notifications current` — Show current status (default "all")
2. `/notifications on` — Enable notifications
3. `/notifications off` — Mute notifications
4. `/notifications mute` — Alias for off
5. Session data verification — Check `notification_preference` stored correctly

Example:
```python
# Line 33: Initialize session
session_info = am.initialize_session(n8n_session_id, "Hello", "webex", "test-user")

# Line 38: Check current status
result = am.process_command(n8n_session_id, "/notifications current", "webex", "test-user")
assert "Background Notifications" in result

# Line 47: Enable notifications
result = am.process_command(n8n_session_id, "/notifications on", "webex", "test-user")
assert "enabled" in result.lower()

# Line 60: Disable notifications
result = am.process_command(n8n_session_id, "/notifications off", "webex", "test-user")
assert "muted" in result.lower()

# Line 90-93: Verify session data
session_data = am.load_session_data(n8n_session_id)
notification_pref = session_data.get("notification_preference")
assert notification_pref == "off"
```

### Integration Test: Mute via API
**File:** `/opt/n8n-copilot-shim-dev/test_mute_integration.py` (160 lines)

Tests `/notifications` command via HTTP API:
1. Initialize session
2. Get current notification status
3. Disable notifications (`/notifications off`)
4. Verify mute status (`/notifications current`)
5. Enable notifications (`/notifications on`)
6. Test `mute` alias

Uses headers:
```python
HEADERS = {
    "X-User-Identity": "test-user-mute",
    "X-Auth-Channel": "webex"
}
```

---

## 5. NOTIFICATION SENDING CODE THAT BYPASSES MUTE CHECKS

### **CRITICAL FINDING: Direct Telegram/WebEx Calls (Outside notification_manager)**

These calls **DO NOT respect mute settings**:

#### 1. Pairing Code Delivery
**File:** `/opt/n8n-copilot-shim-dev/agent_manager.py:4392-4430`

```python
def _send_pairing_code(channel: str, identity: str, code: str) -> None:
    """Best-effort delivery of a pairing code via the appropriate connector."""
    # Lines 4396-4407: Direct Telegram send (BYPASSES notification_manager)
    if channel == "telegram":
        from telegram_connector import TelegramConnector
        config_path = os.path.join(script_dir, "telegram_config.json")
        with open(config_path) as f:
            cfg = json.load(f)
        connector = TelegramConnector(cfg)
        connector.send_message(int(identity), 
            f"Your pairing code is: {code}\nIt expires in 5 minutes.")
    
    # Lines 4408-4428: Direct WebEx API call (BYPASSES notification_manager)
    elif channel == "webex":
        import requests
        payload = {"toPersonEmail": identity, "text": msg, "markdown": msg}
        requests.post(
            "https://webexapis.com/v1/messages",
            headers={"Authorization": f"Bearer {token}", ...},
            json=payload,
            timeout=10,
        )
```

**Impact:** Pairing codes are sent directly without mute checks. This is **intentional** (pairing is not a "notification").

#### 2. Task Scheduler Notifications (executor.py)
**File:** `/opt/n8n-copilot-shim-dev/scheduler/executor.py:172-247`

The scheduler executor sends notifications **directly via Telegram/WebEx connectors**, NOT through notification_manager:

```python
def _notify_creator(self, job: Dict, message: str) -> bool:
    """Send notification to the user who created the job."""
    created_by = job.get("created_by", {})  # Line 179
    channel = created_by.get("channel", "")
    identity = created_by.get("identity", "")
    
    if channel == "telegram":
        return self._send_telegram_to(identity, message, job_id)  # Line 189
    elif channel == "webex":
        return self._send_webex_to(identity, message, job_id)     # Line 191

def _send_telegram_to(self, chat_id: str, message: str, job_id: str) -> bool:
    # Lines 197-221: Direct Telegram send
    connector = _TelegramConnector(token, config_file=str(config_path))
    connector.send_message(int(chat_id), message)  # Line 215 - DIRECT SEND

def _send_webex_to(self, email: str, message: str, job_id: str) -> bool:
    # Lines 223-247: Direct WebEx send
    connector = _WebEXConnector(token, config_file=str(config_path))
    connector.send_message(email, message)  # Line 241 - DIRECT SEND
```

**Note:** The scheduler job includes `notify=True/False` flag in the job definition. It DOES check this flag before calling `_notify_creator()`, but there's **no mute preference checking** — if `notify=True` in the job, it sends regardless of user's mute preference.

**Lines 321-334 (Task completion in AI mode):**
```python
if result.returncode == 0:
    output = result.stdout.strip() if result.stdout else ""
    # ... save result ...
    
    if notify:  # Line 321: Checks job's notify flag
        notification_text = f"✅ Job Completed: {job['name']}\n..."
        self._notify_creator(job, notification_text)  # Line 323 - SENDS
```

---

## 6. NOTIFICATION MANAGER - ARCHITECTURE

### Files:
- **notification_manager.py** (236 lines)
- **agent_manager.py** (background task endpoints)

### Notification Storage:
**File:** `/opt/n8n-copilot-shim-dev/notification_manager.py:20-30`

```python
_NOTIF_FILE = os.path.join(os.path.expanduser("~"), ".copilot", "notifications.json")
_MAX_NOTIFICATIONS = 200

class NotificationManager:
    def __init__(self, notif_file: str = _NOTIF_FILE):
        self._path = notif_file
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
```

Notifications stored at: `~/.copilot/notifications.json`

### Notification Structure:
**Lines 66-77:**
```python
notification = {
    "notification_id": notif_id,
    "task_id": task_id,
    "description": description,
    "status": status,          # "completed" or "failed"
    "channel": channel,        # "webui", "telegram", "webex", etc.
    "user_key": user_key,      # "{channel}_{identity}"
    "output_preview": output_preview[:500],
    "error": error[:500],
    "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "read": False,
}
```

### Muting Logic in notification_manager:
**Lines 79-96:**
```python
# ALWAYS store for WebUI polling (regardless of mute status)
with self._lock:
    notifications = self._load()
    notifications.append(notification)
    self._save(notifications)

# Route to external channels ONLY if NOT muted
if not skip_external:  # skip_external = not notify
    print(f"[NotificationManager] Routing to external channel: {channel}")
    if channel.lower() == "telegram":
        self._notify_telegram(notification)
    elif channel.lower() == "webex":
        self._notify_webex(notification)
else:
    print(f"[NotificationManager] Skipping external notification (skip_external=True)")
```

---

## 7. KEY EQUATIONS & DECISION FLOWS

### Notification Delivery Decision Tree:

```
Background Task Completes
    ↓
_emit_bg_notification(notify=True/False)  ← Determined at task creation
    ↓
notification_mgr.create_notification(skip_external=not notify)
    ↓
    ├─→ WebUI: ALWAYS store in ~/.copilot/notifications.json
    │
    └─→ External (Telegram/WebEx):
        ├─→ If skip_external=False: SEND
        └─→ If skip_external=True: SKIP
```

### Mute Preference Inheritance:

```
Task Creation Request (body.notify=None)
    ↓
Resolve defaults from existing sessions:
    ├─→ Search for sessions matching user identity across ALL channels
    ├─→ If ANY session has notification_preference="off": inherit "off"
    └─→ Else: default to "all"
    ↓
Determine notify flag:
    ├─→ If body.notify is explicitly set: use it
    └─→ Else: use (session_pref != "off")
    ↓
Pass notify flag to _run_background_task()
```

---

## 8. SUMMARY TABLE

| Component | Location | Handles Mute? | Details |
|-----------|----------|---------------|---------|
| **Task Creation** | agent_manager.py:5571-5692 | ✅ Yes | Extracts X-User-Identity/X-Auth-Channel, checks session for notification_preference |
| **Task Execution** | agent_manager.py:5494-5569 | ✅ Yes | Passes notify flag through execution, calls _emit_bg_notification with notify |
| **Notification Emission** | agent_manager.py:5473-5493 | ✅ Yes | Converts notify→skip_external, passes to notification_manager |
| **Notification Manager** | notification_manager.py:53-99 | ✅ Yes | Always stores WebUI, skips external if skip_external=True |
| **Pairing Code Delivery** | agent_manager.py:4392-4430 | ❌ No | Direct connector calls, intentional (pairing ≠ notification) |
| **Scheduler Notifications** | scheduler/executor.py:172-247 | ⚠️ Partial | Checks job's notify flag, but NO session mute preference lookup |

---

## KEY FINDINGS

1. **Muting Works for Background Tasks**: ✅ Fully implemented via notification_preference session field
2. **User Identity Flow**: ✅ X-User-Identity/X-Auth-Channel headers properly flow through task creation and execution
3. **Notification Routing**: ✅ notification_manager properly enforces mute at external channel level while always storing WebUI
4. **Inheritance**: ✅ Mute preference inherited from ANY matching session across channels
5. **Bypass Risk**: ⚠️ Scheduler notifications bypass mute checks (but this may be intentional)

