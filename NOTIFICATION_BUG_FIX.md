# Critical Bug Fix: Background Task Notification Preference Bypass

## Summary

Fixed a critical bug where background task notifications were sent to WebEX and Telegram even when users explicitly disabled notifications via the `/notifications off` command.

## The Bug

### Symptom
- User runs `/notifications off` to disable background task notifications
- User creates a background task from WebUI
- Notifications still appear on WebEX/Telegram despite being disabled

### Root Cause

The bug was in the `create_background_task` endpoint in `agent_manager.py` (lines ~5590-5650):

1. **Wrong Session ID Type**: The code tried to load session data using a **Copilot session_id** (from `history_mgr.get_sessions()`), but `SessionManager.load_session_data()` expects an **n8n_session_id**
   - Copilot session_ids are internal to the Copilot CLI
   - n8n_session_ids are the keys in the session_map
   - These are NOT the same thing

2. **Missing Identity Storage**: The session_map never stored user identity, so there was no way to correlate:
   - WebEX sessions with their user
   - WebUI background task requests with that same user
   - The user's notification preference across channels

3. **Broken Lookup Logic**: Even with aggressive string matching, the code couldn't find the right sessions because:
   - Session keys (n8n_session_ids) are arbitrary UUIDs or short codes
   - They don't contain user identity information
   - There was no persistent mapping of user → sessions

## The Fix

### Changes Made

#### 1. Store User Identity in Sessions
- Modified `SessionManager.get_or_create_session_data()` to accept an optional `identity` parameter
- When sessions are created or accessed via API, the user's identity is now stored
- This enables finding all sessions for a given user across all channels

```python
def get_or_create_session_data(self, n8n_session_id: str, identity: Optional[str] = None) -> Dict:
    # ... creates session and stores identity if provided
    if identity:
        default_data["identity"] = identity
```

#### 2. Update API Endpoints to Pass Identity
All API endpoints now pass the user's identity when creating/accessing sessions:

```python
# Create session endpoint
session_mgr.get_or_create_session_data(session_id, identity=user["identity"])

# Execute session endpoint
session_mgr.get_or_create_session_data(session_id, identity=user["identity"])

# Stream session endpoint
session_mgr.update_session_field(session_id, "identity", user["identity"])
```

#### 3. Fix Background Task Notification Lookup
Changed from broken session_id lookup to correct identity-based matching:

**Before (BROKEN):**
```python
# Tries to use Copilot session_id as n8n_session_id — WRONG!
latest_sid = current_sessions[0].get("session_id", "")
sd = session_mgr.load_session_data(latest_sid)  # Returns None!
```

**After (FIXED):**
```python
# Search session_map for sessions belonging to this user (by identity)
for n8n_sid, data in session_map.items():
    sid_identity = data.get("identity")
    if sid_identity == identity:  # Match by actual user
        # Extract notification preference
        pref = data.get("notification_preference")
        if pref == "off":
            skip_notifications = True
```

#### 4. Respect Preference Across Channels
- Prioritize preferences from same channel
- Fall back to other channels if needed
- If user disabled notifications on ANY channel, respect that globally
- This ensures a user disabling notifications on WebEX won't be surprised by WebUI background tasks

### Test Coverage

Added comprehensive tests in `test_notification_fix.py`:

1. **Test 1: Preference Storage**
   - Verify identity is stored when creating sessions
   - Verify preferences persist across updates

2. **Test 2: Preference Inheritance** 
   - User disables notifications on WebEX
   - Background task on WebUI finds and respects that preference

3. **Test 3: Multi-Channel Inheritance**
   - Create sessions on Telegram, WebEX, and WebUI
   - Disable on Telegram
   - Verify WebUI respects the disabled preference

4. **Critical Bug Scenario** (in `test_critical_bug_fix.py`)
   - Exact reproduction of the reported bug
   - Verifies the fix works end-to-end

## How Notifications Work Now

### Notification Flow with the Fix

```
User in WebEX: /notifications off
  └─ Stored: webex_session[notification_preference] = "off"
             webex_session[identity] = "user@example.com"

Later: User creates background task from WebUI
  └─ Backend receives: channel="webui", identity="user@example.com"
  
  └─ Search session_map:
     - Find all sessions with identity="user@example.com"
     - Check notification_preference in each
     - If any has "off", use that
     
  └─ Background task creation:
     - notify_pref = "off" (inherited from WebEX)
     - skip_external = True (don't send to Telegram/WebEX)
     
  └─ Notification Manager:
     - Stores notification for WebUI (user can see it)
     - Skips sending to Telegram/WebEX (skip_external=True)
```

## Affected Code Paths

### Changed Files
- `agent_manager.py` (main fix)

### Key Functions Modified
1. `SessionManager.get_or_create_session_data()` - Now accepts identity
2. `SessionManager._get_or_create_session_data_unlocked()` - Stores identity
3. `create_api_app()` → `create_session()` - Passes identity
4. `create_api_app()` → `execute_session()` - Passes identity and ensures persistence
5. `create_api_app()` → `stream_session()` - Ensures identity is persisted
6. `create_api_app()` → `create_background_task()` - Fixed preference lookup (MAIN FIX)

### Functions that Call These
- All API endpoints that create or execute sessions
- Background task creation endpoint

## Verification

### Automated Tests
```bash
python3 test_notification_fix.py           # 3 unit tests
python3 test_critical_bug_fix.py          # Bug scenario test
```

### Manual Verification
1. Create a WebEX session
2. Run `/notifications off`
3. Create a background task from WebUI
4. Verify no notification appears on WebEX/Telegram
5. Verify notification appears in WebUI (it should always be stored there)

## Breaking Changes
**None.** The changes are backward compatible:
- Sessions without identity still work (identity is optional)
- The notification preference lookup is more reliable but uses same storage
- API contracts unchanged

## Future Improvements
1. Consider per-channel notification preferences if users want different settings
2. Add a global user preference store (not per-session) for simpler lookups
3. Add UI option to manage notification preferences centrally

## Summary
This fix ensures that when users explicitly disable notifications via `/notifications off`, background task notifications respect that preference regardless of which channel creates the task. The fix works by:

1. Storing user identity with sessions
2. Using identity-based lookup instead of broken session_id lookup
3. Finding all user sessions and checking their preferences
4. Respecting "off" preference globally across channels

The fix is minimal, focused, and doesn't break any existing functionality while solving a critical UX bug.
