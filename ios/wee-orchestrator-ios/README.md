# Wee Orchestrator iOS

Native SwiftUI client for Wee Orchestrator. The app mirrors the WebUI's dark glass surface, emerald/gold accent system, and compact chat/task/agent workflows while adapting the layout for iPhone and iPad.

## Current Laptop Status

This folder contains a generated Xcode project and initial app source. Xcode 26.5 and the iOS 26.5 simulator runtime are installed. The laptop still has Apple Command Line Tools selected globally because changing the active developer directory requires the Mac admin password:

```sh
xcode-select -p
# /Library/Developer/CommandLineTools
```

Either run builds with `DEVELOPER_DIR`:

```sh
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer \
  /Applications/Xcode.app/Contents/Developer/usr/bin/xcodebuild \
  -project WeeOrchestrator.xcodeproj \
  -scheme WeeOrchestrator \
  -destination 'platform=iOS Simulator,name=iPhone 17,OS=26.5' \
  build
```

Or switch the global developer directory once:

```sh
sudo xcode-select -s /Applications/Xcode.app/Contents/Developer
xcodebuild -project WeeOrchestrator.xcodeproj -scheme WeeOrchestrator -destination 'platform=iOS Simulator,name=iPhone 17,OS=26.5' build
```

## Backend Configuration

Settings are entered in-app:

- Backend URL, for example `https://100.124.186.75:8000` or the dev host URL.
- Telegram sign-in flow matching the WebUI:
  - Enter Telegram username.
  - Send pairing code.
  - Enter the 6-digit code delivered in Telegram.
  - The returned `session_...` bearer token is stored in Keychain.
- Manual bearer-token entry remains available under Advanced Token.
- Insecure TLS toggle for local/self-signed development endpoints. It defaults on for the current `100.124.186.75` Wee backend certificate and can be turned off in Settings.

No API tokens or secrets are stored in repository files.

## Initial Capabilities

- Health check and feature flag refresh.
- Agent list.
- Session create and chat execute calls.
- Background task create, list, and detail calls.
- Mock/offline state for UI development when the backend is unreachable.
- Responsive iPhone tab layout and iPad split layout.

## Verified Locally

- Built for `iPhone 17` on iOS 26.5.
- Built for `iPad Pro 13-inch (M5)` on iOS 26.5.
- Installed and launched on both simulators.
- Verified backend health reaches `https://100.124.186.75:8000` and reports `ok` / `PROD`.

## Issue

Tracked as `leprachuan/Wee-Orchestrator#371`.
