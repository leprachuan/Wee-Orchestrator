import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var appState: AppState
    @EnvironmentObject private var settings: SettingsStore
    @EnvironmentObject private var authStore: AuthStore

    @State private var isTesting = false
    @State private var testResult: String?
    @State private var testSucceeded = false

    var body: some View {
        Form {
            if authStore.state == .authenticated {
                Section("Account") {
                    if let identity = authStore.storedIdentity {
                        LabeledContent("Signed in as", value: authStore.storedUsername.map { "@\($0)" } ?? identity)
                    }
                    if let channel = authStore.storedChannel {
                        LabeledContent("Channel", value: channel.capitalized)
                    }
                    Button("Sign Out", role: .destructive) {
                        authStore.logout()
                    }
                }
            }

            Section("Backend") {
                TextField("Base URL", text: $settings.baseURL)
                    .keyboardType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()

                SecureField("Bearer Token", text: $settings.bearerToken)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()

                Toggle("Allow self-signed certificates", isOn: $settings.allowSelfSignedCertificates)
            }

            Section {
                Toggle("Use mock data", isOn: $settings.useMockData)
            } footer: {
                Text("When enabled, the app shows sample data instead of calling the backend. Useful for offline development.")
            }

            Section {
                Button(action: testConnection) {
                    if isTesting {
                        ProgressView()
                            .frame(maxWidth: .infinity)
                    } else {
                        Text("Test Connection")
                            .frame(maxWidth: .infinity)
                    }
                }
                .disabled(isTesting || settings.baseURL.isEmpty)

                if let testResult {
                    Text(testResult)
                        .font(.footnote)
                        .foregroundStyle(testSucceeded ? WeeTheme.accent : WeeTheme.danger)
                }
            }

            Section("About") {
                LabeledContent("App", value: "Wee Orchestrator (iOS starter)")
                LabeledContent("API Base Path", value: "/api/v1")
            }
        }
        .navigationTitle("Settings")
        .scrollContentBackground(.hidden)
        .weeBackground()
        .onChange(of: settings.baseURL) { _, _ in
            appState.refreshClient()
            authStore.refreshClient()
        }
        .onChange(of: settings.bearerToken) { _, _ in appState.refreshClient() }
        .onChange(of: settings.allowSelfSignedCertificates) { _, _ in appState.refreshClient() }
        .onChange(of: settings.useMockData) { _, _ in
            Task { await appState.loadAll() }
        }
    }

    private func testConnection() {
        isTesting = true
        testResult = nil
        Task {
            defer { isTesting = false }
            let wasMock = settings.useMockData
            settings.useMockData = false
            appState.refreshClient()
            await appState.loadAll()
            settings.useMockData = wasMock

            if let error = appState.errorMessage {
                testResult = error
                testSucceeded = false
            } else if appState.health != nil {
                testResult = "Connected — backend is healthy."
                testSucceeded = true
            } else {
                testResult = "No response from backend."
                testSucceeded = false
            }
        }
    }
}
