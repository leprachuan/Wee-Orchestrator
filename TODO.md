# TODO - Feature Roadmap

Feature backlog and improvements for the Telegram bot orchestrator system.

## High Priority (Blocking Issues)

- [ ] **Fix task scheduler Telegram notifications timeout**
  - Task scheduler currently delegates to agent_manager for Telegram notifications, causing 30-second timeouts
  - Root cause: `scheduler_executor.py` `_send_telegram` method spawns Claude session instead of using Telegram API directly
  - Solution: Use `/opt/skills/telegram-notify/shared_infrastructure.py` TelegramNotifier or call Telegram API directly
  - Files: `/opt/skills/task-scheduler/scheduler_executor.py`

## Medium Priority (Reliability & UX)

- [ ] **Voice message transcription support**
  - Allow users to send voice messages to the bot
  - Implementation:
    - Detect voice messages in Telegram updates (check for "voice" key)
    - Download .ogg audio files from Telegram using getFile API
    - Integrate speech-to-text (OpenAI Whisper, Google Speech-to-Text, or local Whisper)
    - Transcribe audio to text and pass to agent as normal message
    - Return transcription confidence and allow agent to ask for clarification if needed

- [ ] **Session management cleanup**
  - Prevent orphaned child processes in telegram_connector.py systemd service
  - Background: Feb 14 incident - systemd service spawned orphaned child processes causing:
    - Duplicate message processing
    - Concurrent session state mutations
    - Anthropic API tool_use/tool_result protocol violations
  - Solution: Review process spawning logic and add safeguards to prevent background processes

- [ ] **Rate limiting and quota management**
  - Implement per-user rate limiting to prevent abuse/runaway costs
  - Track API usage per user
  - Implement daily/monthly quotas if needed
  - Graceful degradation when over quota
  - Admin commands to check usage stats

- [ ] **Health checks and monitoring dashboard**
  - Periodically check bot is responsive
  - Track uptime/downtime
  - Monitor API error rates
  - Optional web dashboard to view stats
  - Alerts on failures

## Low Priority (Nice to Have)

- [ ] **Video/video message support**
  - Similar to voice messages, add support for:
    - Video files (mp4, etc.) - download and describe/analyze
    - Video notes (short round videos) - similar to voice, extract and process
    - Could extract frames for image analysis or transcribe audio track

- [ ] **Add location/geolocation message support**
  - Handle Telegram location messages
  - Extract latitude/longitude
  - Optionally reverse geocode to address
  - Pass to agents for location-aware queries
  - Could enable smart home queries like "what's the weather where I am"

- [ ] **Rich message formatting improvements**
  - Support Telegram inline buttons for quick actions
  - Format tables/structured data better
  - Add collapsible sections for long responses
  - Better code syntax highlighting in markdown
  - Message threading/reactions if possible

- [ ] **Message persistence and history**
  - Store messages in local database or file
  - Search conversation history
  - Export conversations
  - Privacy-aware (allow users to opt-out of history)

- [ ] **Telegram group chat support**
  - Extend bot to work in group chats
  - Mention bot in group (@lipkeyhomebot message)
  - Separate conversation per group
  - Admin-only agent switching in groups
  - Group-specific settings/permissions

## Related Issues

- Feb 14, 2026: Fixed duplicate message responses caused by orphaned telegram_connector child process
- Task scheduler timeouts causing Anthropic API protocol violations (tool_use without tool_result blocks)
