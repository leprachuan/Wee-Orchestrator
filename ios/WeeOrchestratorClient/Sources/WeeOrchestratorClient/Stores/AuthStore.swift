import Foundation
import Combine

/// State of the Telegram/WebEx pairing login flow, mirroring the WebUI's
/// auth overlay states (`IDLE` / `CODE_SENT` / `LOGGED_IN`) plus explicit
/// expired/failed states for the iOS UI.
enum AuthState: Equatable {
    /// No valid session token stored; show the identity entry step.
    case loggedOut
    /// A pairing code was sent to `identity` via `channel`; show the code
    /// entry step.
    case codeSent(identity: String, channel: String)
    /// A valid session token is stored and in use.
    case authenticated
    /// A previously valid session token has expired; the user must log in
    /// again.
    case expired
    /// The last request/verify step failed with `message`.
    case failed(String)
}

/// Drives the Telegram/WebEx pairing login flow and persists the resulting
/// session token (Keychain, via `SettingsStore.bearerToken`) and identity
/// metadata (UserDefaults, non-sensitive).
///
/// A manually-entered bearer token (the "advanced fallback" in Settings) is
/// also recognized: setting `settings.bearerToken` directly moves the state
/// to `.authenticated`, and clearing it moves the state back to `.loggedOut`.
@MainActor
final class AuthStore: ObservableObject {
    @Published private(set) var state: AuthState
    @Published var isRequestingCode = false
    @Published var isVerifyingCode = false

    let settings: SettingsStore
    private var client: AuthAPI

    /// The identity returned by `request-pairing` (e.g. resolved numeric
    /// Telegram user id), used as the `identity` for `verify-pairing`.
    private var pendingIdentity: String?

    private var cancellables = Set<AnyCancellable>()

    enum Keys {
        static let identity = "wee.auth.identity"
        static let channel = "wee.auth.channel"
        static let username = "wee.auth.username"
        static let tokenExpiresAt = "wee.auth.tokenExpiresAt"
    }

    init(settings: SettingsStore, client: AuthAPI? = nil, defaults: UserDefaults = .standard) {
        self.settings = settings
        self.client = client ?? APIClient(settings: settings)
        self.defaults = defaults

        if settings.bearerToken.isEmpty {
            state = .loggedOut
        } else if let expiresAt = defaults.object(forKey: Keys.tokenExpiresAt) as? Double,
                  Date().timeIntervalSince1970 > expiresAt {
            state = .expired
        } else {
            state = .authenticated
        }

        // React to a manually-entered/cleared bearer token in Settings
        // (the "advanced fallback"). `dropFirst` skips the value emitted
        // immediately on subscription, which we've already accounted for
        // above.
        settings.$bearerToken
            .dropFirst()
            .removeDuplicates()
            .sink { [weak self] token in
                guard let self else { return }
                if token.isEmpty {
                    self.clearStoredIdentity()
                    self.state = .loggedOut
                } else if self.state != .authenticated {
                    self.state = .authenticated
                }
            }
            .store(in: &cancellables)

        // A 401 from any API call means the stored token is no longer
        // valid (expired or revoked) — return to the login flow.
        NotificationCenter.default
            .publisher(for: .weeUnauthorized)
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in self?.handleUnauthorized() }
            .store(in: &cancellables)
    }

    private let defaults: UserDefaults

    /// Rebuilds the API client, e.g. after Settings (base URL) change.
    func refreshClient() {
        client = APIClient(settings: settings)
    }

    var storedIdentity: String? { defaults.string(forKey: Keys.identity) }
    var storedChannel: String? { defaults.string(forKey: Keys.channel) }
    var storedUsername: String? { defaults.string(forKey: Keys.username) }

    /// Step 1: request a pairing code be sent via `channel`.
    func requestCode(identity: String, channel: AuthChannel) async {
        let trimmed = identity.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            state = .failed("Enter your \(channel.displayName) username or ID first.")
            return
        }

        isRequestingCode = true
        defer { isRequestingCode = false }

        do {
            let response = try await client.requestPairing(identity: trimmed, channel: channel.rawValue)
            let resolved = response.identityResolved ?? trimmed.trimmingCharacters(in: CharacterSet(charactersIn: "@"))
            pendingIdentity = resolved
            state = .codeSent(identity: resolved, channel: channel.rawValue)
        } catch {
            state = .failed(error.localizedDescription)
        }
    }

    /// Step 2: exchange the pairing code for a session token.
    func verifyCode(_ code: String) async {
        let trimmed = code.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            state = .failed("Enter the pairing code sent to you.")
            return
        }
        guard let identity = pendingIdentity else {
            state = .loggedOut
            return
        }

        isVerifyingCode = true
        defer { isVerifyingCode = false }

        do {
            let response = try await client.verifyPairing(code: trimmed, identity: identity)
            settings.bearerToken = response.token
            defaults.set(response.identity, forKey: Keys.identity)
            defaults.set(response.channel, forKey: Keys.channel)
            if let username = response.username {
                defaults.set(username, forKey: Keys.username)
            } else {
                defaults.removeObject(forKey: Keys.username)
            }
            let expiresAt = Date().timeIntervalSince1970 + Double(response.absoluteExpiresIn)
            defaults.set(expiresAt, forKey: Keys.tokenExpiresAt)
            pendingIdentity = nil
            state = .authenticated
        } catch {
            state = .failed(error.localizedDescription)
        }
    }

    /// Returns to the identity-entry step (e.g. "Use a different account"
    /// or "Back" from the code-entry step).
    func backToStart() {
        pendingIdentity = nil
        state = .loggedOut
    }

    /// Clears the stored session and returns to the login flow.
    func logout() {
        settings.bearerToken = ""
        clearStoredIdentity()
        pendingIdentity = nil
        state = .loggedOut
    }

    /// Called when an API request returns `401`: the stored token is no
    /// longer valid.
    func handleUnauthorized() {
        guard state == .authenticated else { return }
        settings.bearerToken = ""
        state = .expired
    }

    private func clearStoredIdentity() {
        defaults.removeObject(forKey: Keys.identity)
        defaults.removeObject(forKey: Keys.channel)
        defaults.removeObject(forKey: Keys.username)
        defaults.removeObject(forKey: Keys.tokenExpiresAt)
    }
}
