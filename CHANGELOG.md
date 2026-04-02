# Changelog

All notable changes to Wee Orchestrator are documented here.

## [Unreleased] — Dev Branch

### Added

#### F016: Telegram Slash Command Registration with BotFather
- **Status**: ✅ QA Approved (commit 3cdf77a on dev)
- **Commit**: 3cdf77a
- Automatically registers Telegram slash commands with BotFather for UI autocomplete
- `register_bot_commands()` method in TelegramConnector dynamically pulls command list from SessionManager
- Builds valid Telegram BotCommand payload; calls `setMyCommands` API on startup
- Added `/registercommands` handler for manual refresh if commands change
- Startup registration happens before polling begins
- **Testing**: 15 new tests covering payload construction, API calls, error handling
- **QA baseline**: 674 tests pass (9 skipped), flake8 clean, all services operational
- **Minor cleanup**: Removed unused imports (json, PropertyMock, pytest); fixed 10 E501 line-length violations
- **Ready for deployment**: No breaking changes, backwards compatible, no new config required

#### F024: Brief One-Line Notifications for Background & Scheduled Tasks
- **Status**: ✅ QA Approved (commit 0c0bb3f on dev)
- **Commit**: 0c0bb3f
- External notifications (Telegram, WebEx) now deliver single-line format instead of verbose multi-line messages
- Format: `✅ task_id done — description` (max 200 chars, auto-truncated with ellipsis)
- Full output/error details remain in WebUI notification dict (output_preview[:500], error[:500])
- Changes:
  - `_format_notification_message()` in notification_manager.py rewritten for single-line output
  - Added `_MAX_NOTIFICATION_LENGTH=200` constant to both notification_manager.py and scheduler/executor.py
  - Added `_brief_notification()` helper in executor.py
  - Replaced 8 verbose multi-line notification strings across scheduler AI mode (4) and command mode (4)
- Testing: 25 new tests in test_brief_notifications.py covering format, truncation, length validation, and WebUI preservation
- QA baseline: 659 tests pass (0 new failures), 4 pre-existing flake8 issues unchanged, all dev services active
- **Ready for deployment**: No breaking changes, backwards compatible, no new config required

#### F019: WebUI Secrets Manager — Store Secrets via UI
- **Status**: ✅ QA Approved (commit 44df840 on dev; F020-F023 follow-up bugs approved)
- **Commit**: b4efcdc (main feature); 44df840 (UX bug fixes)
- Backend: GET/POST/DELETE /api/v1/secrets endpoints delegating to secret_tool.py
- WebUI: Secrets nav panel with masked input form and secrets list
- Security: DELETE name regex validates input; sensitive values never returned by API
- **QA fixes (F020-F023)**: Cache-bust updated (v=20260401secrets1); emojis corrected (🔐💾👁); toggle state indicator added with ARIA; error feedback persistence + screen reader support
- 24/24 F019-F023 tests pass; 580+ total tests pass; flake8 clean
- **Ready for deployment**: No breaking changes, backwards compatible

#### F017: In-Thread Background Task Notifications
- **Status**: ✅ QA Approved (546 tests pass, 0 failures)
- **Commit**: 35c3f71
- Real-time background task status updates delivered in-thread to user messages
- Users now see task lifecycle (queued → running → complete) directly in conversation, not just in ⚡ Tasks panel
- Supports Telegram, WebEx, and other connected channels
- Enhanced notification_manager routing with context/thread preservation
- New `bg_events` SQLite table for event lifecycle tracking
- **Non-blocking QA observations**:
  - No automatic TTL cleanup for orphaned bg_events entries (safe; manageable database size)
  - shownBgBanners state resets on page reload (UX polish in upcoming release)
- **Ready for deployment**: No database schema setup needed, no config changes, backwards compatible

### Changed

### Fixed
