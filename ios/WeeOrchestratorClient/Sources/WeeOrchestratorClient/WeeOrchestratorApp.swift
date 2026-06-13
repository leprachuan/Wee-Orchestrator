import SwiftUI

@main
struct WeeOrchestratorApp: App {
    @StateObject private var settings: SettingsStore
    @StateObject private var appState: AppState

    init() {
        let settings = SettingsStore()
        _settings = StateObject(wrappedValue: settings)
        _appState = StateObject(wrappedValue: AppState(settings: settings))
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(settings)
                .environmentObject(appState)
                .preferredColorScheme(.dark)
        }
    }
}
