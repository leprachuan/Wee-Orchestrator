# Kanban TODO Board and iOS Due Alerts Plan

Tracking issue: https://github.com/leprachuan/Wee-Orchestrator/issues/367

This issue is intentionally not labeled `wee-dev` while local development is in progress, so the dispatch pipeline does not compete with this thread.

## Goal

Move the `pot-o-skills/github-kanban-board` capability into Wee Orchestrator as a first-class API and UI surface, then expose it in the native iOS/iPadOS client. The board must support TODO-style due dates and one-shot due alerts.

## Source Material

- `pot-o-skills/github-kanban-board/SKILL.md`
- `pot-o-skills/github-kanban-board/scripts/kanban_server.py`
- `pot-o-skills/todo-tracker/todo_reminder.py`
- Existing Wee background tasks, scheduler, notifications, and auth endpoints in `agent_manager.py`
- Native iOS app in `/Volumes/DropSpot/Agents/wee-orchestrator-ios`

## Current Constraints

- Existing test `tests/test_issue161_todo_panel_removal.py` asserts the old built-in TODO panel and `/api/v1/todos` route should not return as that specific feature.
- The local backend currently still contains legacy `/api/v1/todos` code, so this project should avoid deepening that ambiguity.
- New work should use `/api/v1/kanban/*` and a Kanban-specific WebUI/iOS surface.
- GitHub issue #367 must remain without the `wee-dev` label until Foster explicitly asks the pipeline to take over.

## Backend Contract

Add a Kanban API namespace:

- `GET /api/v1/kanban/board`
  - Returns grouped columns: `todo`, `in-progress`, `ai-active`, `pending-review`, `done`.
  - Includes cards with `id`, `title`, `source`, `status`, `agent`, `priority`, `urgency`, `due`, `labels`, `details`, `url`, `created_at`, `updated_at`, `is_overdue`, `due_bucket`.
  - Supports filters: `agent`, `urgency`, `date_from`, `date_to`, `source`.
- `GET /api/v1/kanban/items/{item_id}`
  - Returns one item plus comments/notes when available.
- `POST /api/v1/kanban/items`
  - Creates a TODO/card. Initial implementation can use the existing local TODO folder flow; GitHub issue creation can follow if desired.
- `PATCH /api/v1/kanban/items/{item_id}`
  - Updates title/details/due/status/labels.
- `POST /api/v1/kanban/items/{item_id}/complete`
  - Moves the card to done/complete.
- `GET /api/v1/kanban/alerts`
  - Returns due/overdue alert candidates for the signed-in user/channel.
- `POST /api/v1/kanban/alerts/check`
  - Runs the reminder check once and records fired tiers.

## Alert Semantics

Reuse `todo-tracker/todo_reminder.py` behavior:

- Timed due dates: `1day`, `1hour`, `15min`, `now`, `overdue_1h`
- Date-only due dates: `1day`, `today`, `overdue`
- Each alert tier fires once per item.
- Store fired state in a JSON file such as `.kanban-alert-state.json`, configurable by env var.
- Alert delivery should flow through Wee notifications first, with Telegram/WebEx forwarding using existing channel config.
- iOS should show in-app due status and can later add native push/local notifications after the backend contract is stable.

## WebUI Plan

Add a Kanban view separate from the removed TODO panel:

- Sidebar button: `🗂 Kanban`
- Main panel with compact columns on desktop and stacked columns on mobile.
- Card badges for agent, due bucket, urgency, priority, and source.
- Detail panel for body/comments/notes.
- Status changes through explicit buttons first; drag/drop can follow.

## iOS/iPadOS Plan

Add a native Kanban tab:

- iPhone: stacked column picker with list cards.
- iPad: multi-column board.
- Card details sheet.
- Create/edit item sheet.
- Due alert section for due soon/overdue items.
- Use the existing Telegram auth/session token flow.
- Use the existing Wee visual language from the current native app.

## Testing Plan

Backend:

- Unit test status/label mapping from kanban skill fixtures.
- Unit test due bucket calculation and one-shot alert tier state.
- API test for authenticated `GET /api/v1/kanban/board`.
- API test for unauthenticated 401.

iOS:

- Build iPhone 17 simulator.
- Build iPad Pro 13-inch simulator.
- Manual simulator inspection for empty/auth/error states.

## Implementation Order

1. Extract Kanban card normalization into a small backend helper.
2. Add `/api/v1/kanban/board` read-only API.
3. Add backend tests for status/due mapping.
4. Add read-only iOS Kanban tab and models.
5. Add alert candidate API and state persistence.
6. Add create/update/complete operations.
7. Add WebUI Kanban panel.
8. Add iOS create/edit/detail interactions.
9. Verify backend tests and iPhone/iPad builds.

## Deferred

- Native iOS push notifications through APNs.
- Multi-repository Kanban source selection.
- Drag/drop syncing in the WebUI.
- Offline iOS cache and conflict resolution.
