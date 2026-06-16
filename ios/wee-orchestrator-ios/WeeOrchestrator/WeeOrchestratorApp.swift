import SwiftUI

@main
struct WeeOrchestratorApp: App {
    @State private var model = WeeAppModel()

    var body: some Scene {
        WindowGroup {
            AppRootView(model: model)
        }
    }
}
