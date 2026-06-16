import SwiftUI
import UIKit

struct SettingsView: View {
    @Bindable var model: WeeAppModel
    @State private var testResult: String?
    @State private var telegramIdentity = ""
    @State private var pairingCode = ""
    @State private var showManualToken = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                Text("Settings")
                    .font(.title3.weight(.bold))
                    .foregroundStyle(WeeTheme.textPrimary)

                telegramAuthSection
                settingsFields
                manualTokenSection

                HStack {
                    Button {
                        model.saveConfiguration()
                        testResult = "Saved"
                    } label: {
                        Label("Save", systemImage: "checkmark")
                    }
                    .buttonStyle(WeePrimaryButtonStyle())

                    Button {
                        Task {
                            model.saveConfiguration()
                            await model.refreshAll()
                            testResult = model.errorMessage == nil ? "Connected" : model.errorMessage
                        }
                    } label: {
                        Label("Test", systemImage: "network")
                    }
                    .buttonStyle(WeeGhostButtonStyle())
                }

                if let testResult {
                    Text(testResult)
                        .font(.caption)
                        .foregroundStyle(testResult == "Connected" || testResult == "Saved" ? WeeTheme.accent : WeeTheme.danger)
                }

                connectionSummary
            }
            .padding(16)
            .glassPanel()
            .padding(UIDevice.current.userInterfaceIdiom == .pad ? 0 : 12)
        }
        .scrollIndicators(.hidden)
        .onAppear {
            if telegramIdentity.isEmpty {
                telegramIdentity = model.configuration.identity
            }
        }
    }

    private var settingsFields: some View {
        VStack(spacing: 12) {
            FieldRow(title: "Backend URL") {
                TextField("https://host:8000", text: $model.configuration.baseURLString)
                    .textInputAutocapitalization(.never)
                    .keyboardType(.URL)
            }

            Toggle("Allow insecure TLS", isOn: $model.configuration.allowInsecureTLS)
                .tint(WeeTheme.accent)
                .foregroundStyle(WeeTheme.textPrimary)
        }
    }

    private var telegramAuthSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Label("Telegram Sign In", systemImage: "paperplane.circle.fill")
                    .font(.headline.weight(.semibold))
                    .foregroundStyle(WeeTheme.textPrimary)

                Spacer()

                StatusPill(
                    text: model.isAuthenticated ? "signed in" : "required",
                    color: model.isAuthenticated ? WeeTheme.accent : WeeTheme.gold
                )
            }

            if model.isAuthenticated {
                VStack(alignment: .leading, spacing: 8) {
                    Text(model.configuration.identity.isEmpty ? "Authenticated" : model.configuration.identity)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(WeeTheme.textPrimary)
                        .lineLimit(1)

                    Button {
                        model.signOut()
                        testResult = nil
                        pairingCode = ""
                    } label: {
                        Label("Sign Out", systemImage: "rectangle.portrait.and.arrow.right")
                    }
                    .buttonStyle(WeeGhostButtonStyle())
                }
            } else {
                FieldRow(title: "Telegram Username") {
                    TextField("@username", text: $telegramIdentity)
                        .textInputAutocapitalization(.never)
                        .textContentType(.username)
                        .autocorrectionDisabled()
                }

                Button {
                    Task {
                        await model.requestTelegramPairing(identity: telegramIdentity)
                    }
                } label: {
                    Label("Send Pairing Code", systemImage: "paperplane")
                }
                .buttonStyle(WeePrimaryButtonStyle())
                .disabled(model.isLoading || telegramIdentity.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)

                if model.authPairingIdentity != nil {
                    FieldRow(title: "Pairing Code") {
                        TextField("123456", text: $pairingCode)
                            .keyboardType(.numberPad)
                            .textContentType(.oneTimeCode)
                            .onChange(of: pairingCode) {
                                pairingCode = String(pairingCode.filter(\.isNumber).prefix(6))
                            }
                    }

                    Button {
                        Task {
                            await model.verifyTelegramPairing(code: pairingCode)
                            if model.isAuthenticated {
                                pairingCode = ""
                                testResult = "Connected"
                            }
                        }
                    } label: {
                        Label("Verify", systemImage: "checkmark.seal")
                    }
                    .buttonStyle(WeePrimaryButtonStyle())
                    .disabled(model.isLoading || pairingCode.count < 6)
                }
            }

            if let authStatus = model.authStatusMessage {
                Text(authStatus)
                    .font(.caption)
                    .foregroundStyle(authStatus.localizedCaseInsensitiveContains("signed in") || authStatus.localizedCaseInsensitiveContains("sent") ? WeeTheme.accent : WeeTheme.danger)
            }
        }
        .padding(13)
        .background(WeeTheme.sunken, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
    }

    private var manualTokenSection: some View {
        DisclosureGroup(isExpanded: $showManualToken) {
            VStack(spacing: 12) {
                FieldRow(title: "Bearer Token") {
                    SecureField("Token", text: $model.configuration.token)
                        .textInputAutocapitalization(.never)
                }

                FieldRow(title: "Identity") {
                    TextField("user identity", text: $model.configuration.identity)
                        .textInputAutocapitalization(.never)
                }

                FieldRow(title: "Channel") {
                    Picker("Channel", selection: $model.configuration.channel) {
                        Text("telegram").tag("telegram")
                        Text("webex").tag("webex")
                        Text("webui").tag("webui")
                    }
                    .pickerStyle(.segmented)
                }
            }
            .padding(.top, 10)
        } label: {
            Label("Advanced Token", systemImage: "key")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(WeeTheme.textSecondary)
        }
        .tint(WeeTheme.accent)
    }

    private var connectionSummary: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Connection")
                .font(.headline.weight(.semibold))
                .foregroundStyle(WeeTheme.textPrimary)

            HStack {
                StatusPill(text: model.health?.status ?? "unknown", color: model.health?.status == "ok" ? WeeTheme.accent : WeeTheme.gold)
                if let environment = model.health?.environment ?? model.appConfig?.appEnv {
                    StatusPill(text: environment, color: WeeTheme.gold)
                }
            }

            if let loaded = model.health?.agentsLoaded {
                Text("\(loaded) agents loaded")
                    .font(.caption)
                    .foregroundStyle(WeeTheme.textSecondary)
            }

            if let lastRefresh = model.lastRefresh {
                Text(lastRefresh.formatted(date: .omitted, time: .standard))
                    .font(.caption)
                    .foregroundStyle(WeeTheme.textMuted)
            }
        }
        .padding(13)
        .background(WeeTheme.sunken, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}

private struct FieldRow<Content: View>: View {
    let title: String
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(WeeTheme.textMuted)
                .textCase(.uppercase)

            content
                .textFieldStyle(.plain)
                .foregroundStyle(WeeTheme.textPrimary)
                .padding(.horizontal, 12)
                .padding(.vertical, 10)
                .background(WeeTheme.sunken, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(WeeTheme.glassStroke))
        }
    }
}
