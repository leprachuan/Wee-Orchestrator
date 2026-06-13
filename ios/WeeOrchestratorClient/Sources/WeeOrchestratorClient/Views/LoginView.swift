import SwiftUI

/// Telegram/WebEx pairing-code login flow, equivalent to the WebUI's auth
/// overlay. Shown instead of the main app UI until `AuthStore.state` is
/// `.authenticated` (or `useMockData` is enabled).
struct LoginView: View {
    @EnvironmentObject private var authStore: AuthStore
    @EnvironmentObject private var settings: SettingsStore

    @State private var identity = ""
    @State private var channel: AuthChannel = .telegram
    @State private var code = ""
    @State private var showAdvanced = false

    var body: some View {
        ScrollView {
            VStack(spacing: 20) {
                header

                GlassCard {
                    VStack(alignment: .leading, spacing: 16) {
                        switch authStore.state {
                        case .loggedOut, .failed:
                            identityStep
                        case .codeSent(let codeSentIdentity, let codeSentChannel):
                            codeStep(identity: codeSentIdentity, channel: codeSentChannel)
                        case .expired:
                            expiredStep
                        case .authenticated:
                            EmptyView()
                        }

                        if case .failed(let message) = authStore.state {
                            Text(message)
                                .font(.footnote)
                                .foregroundStyle(WeeTheme.danger)
                        }
                    }
                }

                advancedSection
            }
            .padding(20)
        }
        .weeBackground()
        .scrollDismissesKeyboard(.interactively)
    }

    private var header: some View {
        VStack(spacing: 6) {
            Image(systemName: "paperplane.circle.fill")
                .font(.system(size: 44))
                .foregroundStyle(WeeTheme.accent)
            Text("Sign in to Wee Orchestrator")
                .font(.title2.bold())
                .foregroundStyle(WeeTheme.textPrimary)
            Text("Use the same Telegram pairing flow as the WebUI to authenticate this device.")
                .font(.footnote)
                .foregroundStyle(WeeTheme.textSecondary)
                .multilineTextAlignment(.center)
        }
        .padding(.top, 24)
    }

    // MARK: - Step 1: identity entry

    private var identityStep: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Channel")
                .font(.caption)
                .foregroundStyle(WeeTheme.textSecondary)
            Picker("Channel", selection: $channel) {
                ForEach(AuthChannel.allCases) { channel in
                    Text(channel.displayName).tag(channel)
                }
            }
            .pickerStyle(.segmented)

            Text(channel == .telegram ? "Telegram Username or ID" : "WebEx Email or ID")
                .font(.caption)
                .foregroundStyle(WeeTheme.textSecondary)
            TextField(channel == .telegram ? "@username" : "you@example.com", text: $identity)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .textFieldStyle(.roundedBorder)

            Button {
                Task { await authStore.requestCode(identity: identity, channel: channel) }
            } label: {
                if authStore.isRequestingCode {
                    ProgressView().frame(maxWidth: .infinity)
                } else {
                    Text("Send Pairing Code").frame(maxWidth: .infinity)
                }
            }
            .buttonStyle(.borderedProminent)
            .tint(WeeTheme.accent)
            .disabled(authStore.isRequestingCode || identity.trimmingCharacters(in: .whitespaces).isEmpty)

            Text("Send any message to the Wee Orchestrator bot first if it doesn't recognize your username.")
                .font(.caption2)
                .foregroundStyle(WeeTheme.textMuted)
        }
    }

    // MARK: - Step 2: code entry

    private func codeStep(identity codeIdentity: String, channel codeChannel: String) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Pending — Code Sent")
                .font(.caption.bold())
                .foregroundStyle(WeeTheme.accent)
            Text("A pairing code was sent via \(codeChannel.capitalized) to \(codeIdentity). Enter it below to finish signing in.")
                .font(.footnote)
                .foregroundStyle(WeeTheme.textSecondary)

            TextField("Pairing code", text: $code)
                .keyboardType(.numberPad)
                .textFieldStyle(.roundedBorder)

            Button {
                Task { await authStore.verifyCode(code) }
            } label: {
                if authStore.isVerifyingCode {
                    ProgressView().frame(maxWidth: .infinity)
                } else {
                    Text("Verify").frame(maxWidth: .infinity)
                }
            }
            .buttonStyle(.borderedProminent)
            .tint(WeeTheme.accent)
            .disabled(authStore.isVerifyingCode || code.trimmingCharacters(in: .whitespaces).isEmpty)

            Button("Back") {
                code = ""
                authStore.backToStart()
            }
            .font(.footnote)
            .foregroundStyle(WeeTheme.textSecondary)
        }
    }

    // MARK: - Expired

    private var expiredStep: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("Session Expired", systemImage: "exclamationmark.triangle.fill")
                .font(.caption.bold())
                .foregroundStyle(WeeTheme.danger)
            Text("Your session has expired. Sign in again to continue.")
                .font(.footnote)
                .foregroundStyle(WeeTheme.textSecondary)

            identityStep
        }
    }

    // MARK: - Advanced: manual bearer token fallback

    private var advancedSection: some View {
        GlassCard {
            DisclosureGroup("Advanced: Manual Token Entry", isExpanded: $showAdvanced) {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Base URL")
                        .font(.caption)
                        .foregroundStyle(WeeTheme.textSecondary)
                    TextField("https://192.168.1.100:8000", text: $settings.baseURL)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .textFieldStyle(.roundedBorder)

                    Text("Bearer Token")
                        .font(.caption)
                        .foregroundStyle(WeeTheme.textSecondary)
                    SecureField("shared_... or session_...", text: $settings.bearerToken)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .textFieldStyle(.roundedBorder)

                    Text("For trusted dev backends only: bypasses the Telegram/WebEx pairing flow with a pre-issued token.")
                        .font(.caption2)
                        .foregroundStyle(WeeTheme.textMuted)
                }
                .padding(.top, 8)
            }
            .font(.subheadline)
            .foregroundStyle(WeeTheme.textPrimary)
            .tint(WeeTheme.accent)
        }
        .onChange(of: settings.baseURL) { _, _ in authStore.refreshClient() }
    }
}
