# Changelog

All notable changes to Wee Orchestrator are documented here.

## [Unreleased] — Dev Branch

### Added

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

### Deprecated

### Removed

### Security

---

## Previous Releases

(Historical releases documented when applicable)
