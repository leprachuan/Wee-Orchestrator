# Wee Orchestrator — iOS Client (Starter)

A SwiftUI starter client for [Wee Orchestrator](https://github.com/leprachuan/Wee-Orchestrator),
mirroring the visual language of the existing WebUI (dark "Emerald"
glassmorphism theme) with responsive layouts for iPhone and iPad.

This is an early starter: it covers the API surface needed for chat,
health checks, agent listing, background task status/creation, and
connection configuration.

## Features

- **Chat** — transcript view backed by `/api/v1/sessions/*` and
  `/api/v1/history/sessions/*`. The header agent name opens an agent picker
  that updates the active session's agent (via `/agent set`) or the agent
  used for the next new session. A "New Chat" action clears the transcript
  and starts a fresh session on next send. Previous sessions can be browsed
  and reopened from a sheet.
- **Dashboard** — backend health, service status (telegram/webex/api/scheduler).
- **Agents** — list of configured agents with runtime/model.
- **Tasks** — background task list, detail view with recent output, and a
  form to create new background tasks (`POST /api/v1/background-tasks`).
- **Settings** — configurable backend base URL, bearer token (stored in the
  Keychain), a "use mock data" toggle for offline development, and an
  option to trust self-signed TLS certificates for local/dev backends.
- **iPad/iPhone layouts** — `NavigationSplitView` sidebar on iPad (regular
  width), tab bar on iPhone (compact width).
- **Mock/offline mode** — enabled by default so the app is browsable
  immediately without a configured backend.

## Project layout

```
ios/WeeOrchestratorClient/
├── project.yml                 # XcodeGen project definition
├── Sources/WeeOrchestratorClient/
│   ├── WeeOrchestratorApp.swift # App entry point
│   ├── Models/Models.swift      # Codable API response types
│   ├── Networking/APIClient.swift
│   ├── Stores/
│   │   ├── AppState.swift       # Observable app data + mock fallback
│   │   ├── ChatStore.swift       # Chat session/transcript/agent state
│   │   ├── SettingsStore.swift  # Base URL / token / mock toggle
│   │   └── KeychainHelper.swift # Bearer token storage
│   ├── Theme/Theme.swift         # Colors/components matching the WebUI theme
│   ├── Mock/MockData.swift       # Sample data for offline mode
│   └── Views/
│       ├── RootView.swift
│       ├── ChatView.swift
│       ├── DashboardView.swift
│       ├── AgentsView.swift
│       ├── BackgroundTasksView.swift
│       ├── BackgroundTaskDetailView.swift
│       ├── NewTaskView.swift
│       └── SettingsView.swift
└── Tests/WeeOrchestratorClientTests/
    └── ChatStoreTests.swift      # Chat session/agent/new-chat regression tests
```

## Building locally

Requires Xcode 15+ (iOS 17 SDK) on macOS.

### Option A — XcodeGen (recommended)

1. Install [XcodeGen](https://github.com/yonaskolb/XcodeGen): `brew install xcodegen`
2. From `ios/WeeOrchestratorClient/`, run:
   ```sh
   xcodegen generate
   open WeeOrchestratorClient.xcodeproj
   ```
3. Select the `WeeOrchestratorClient` scheme and an iPhone or iPad simulator,
   then Run (`Cmd+R`).

### Option B — Manual Xcode project

1. In Xcode, create a new project: **App**, Interface: **SwiftUI**,
   Language: **Swift**, name it `WeeOrchestratorClient`.
2. Delete the generated `ContentView.swift` and default app file.
3. Drag the contents of `Sources/WeeOrchestratorClient/` into the project,
   keeping the folder structure (ensure "Copy items if needed" is checked).
4. In the target's **Info** tab, add an **App Transport Security Settings**
   entry with **Allow Arbitrary Loads** set to **YES** (only needed if your
   backend uses HTTP or a self-signed certificate during development).
5. Build and run on an iPhone or iPad simulator.

## Configuring the backend connection

Open the **Settings** tab/section in the app:

- **Base URL** — e.g. `https://192.168.1.100:8000` (the dev host's API).
- **Bearer Token** — an API token accepted by `/api/v1/*` endpoints
  (e.g. a `shared_<key>` or `session_<token>` value). Stored in the iOS
  Keychain, not `UserDefaults`.
- **Allow self-signed certificates** — enable for dev backends using
  self-signed TLS certs (mirrors the `curl -k` pattern used elsewhere in
  this project). Leave disabled for production backends with valid certs.
- **Use mock data** — on by default. Turn off once Base URL and Bearer
  Token are set, then tap **Test Connection** to verify
  `GET /api/v1/health` succeeds.

## API endpoints used

| Purpose | Endpoint |
| --- | --- |
| Health | `GET /api/v1/health` |
| Agents | `GET /api/v1/agents` |
| Service status | `GET /api/v1/service-status` |
| List background tasks | `GET /api/v1/background-tasks` |
| Background task detail | `GET /api/v1/background-tasks/{task_id}` |
| Create background task | `POST /api/v1/background-tasks` |

All authenticated requests send `Authorization: Bearer <token>` and
`X-Auth-Channel: ios` headers, matching the auth scheme implemented in
`agent_manager.py`.

## Validation performed

- Verified against the live `/api/v1` route definitions in `agent_manager.py`
  (health, agents, service-status, background-tasks endpoints and their
  response shapes).
- Reviewed `webui/dist/app.css` and `webui/dist/themes.css` for the default
  "Emerald" theme's colors (`#3ecf8e` accent, dark glass background) used in
  `Theme.swift`.
- Swift sources were not compiled in this environment (no macOS/Xcode
  toolchain available on the dev host). Build and run in Xcode to verify
  compilation before relying on this starter.

## Next steps (not in scope for this starter)

- Chat/session UI (`/api/v1/sessions/*`, streaming).
- Scheduler UI (`/api/v1/scheduler/*`).
- Notifications, secrets manager, theme picker parity with the WebUI.
- Pairing-code login flow (`/api/v1/auth/request-pairing` /
  `verify-pairing`) instead of manual token entry.
