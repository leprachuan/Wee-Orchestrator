import XCTest
@testable import WeeOrchestratorClient

/// In-memory fake of `AuthAPI` so `AuthStore` can be tested without a
/// network connection or backend.
private final class FakeAuthAPI: AuthAPI {
    var requestPairingResult: Result<PairingRequestResponse, Error> = .success(
        PairingRequestResponse(message: "sent", expiresIn: 300, identityResolved: "123456789")
    )
    var verifyPairingResult: Result<PairingVerificationResponse, Error> = .success(
        PairingVerificationResponse(
            token: "session_abc123",
            expiresIn: 3600,
            absoluteExpiresIn: 86400,
            identity: "123456789",
            channel: "telegram",
            username: "foster"
        )
    )

    private(set) var requestPairingCalls: [(identity: String, channel: String)] = []
    private(set) var verifyPairingCalls: [(code: String, identity: String)] = []

    func requestPairing(identity: String, channel: String) async throws -> PairingRequestResponse {
        requestPairingCalls.append((identity: identity, channel: channel))
        return try requestPairingResult.get()
    }

    func verifyPairing(code: String, identity: String) async throws -> PairingVerificationResponse {
        verifyPairingCalls.append((code: code, identity: identity))
        return try verifyPairingResult.get()
    }
}

private struct FakeError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

@MainActor
final class AuthStoreTests: XCTestCase {
    /// Regression test for #363: signing in via the Telegram pairing flow
    /// should request a code, verify it, persist the returned bearer token
    /// to Keychain (via `SettingsStore.bearerToken`), and move the store to
    /// `.authenticated` so authenticated API calls (send/background-task)
    /// use the stored token instead of requiring manual entry.
    func testTelegramPairingFlowStoresTokenAndAuthenticates() async {
        let defaults = makeIsolatedDefaults()
        let settings = SettingsStore()
        settings.bearerToken = ""
        let api = FakeAuthAPI()
        let store = AuthStore(settings: settings, client: api, defaults: defaults)

        XCTAssertEqual(store.state, .loggedOut)

        await store.requestCode(identity: "@foster", channel: .telegram)
        XCTAssertEqual(api.requestPairingCalls.count, 1)
        XCTAssertEqual(api.requestPairingCalls.first?.identity, "@foster")
        XCTAssertEqual(api.requestPairingCalls.first?.channel, "telegram")
        XCTAssertEqual(store.state, .codeSent(identity: "123456789", channel: "telegram"))

        await store.verifyCode("000000")
        XCTAssertEqual(api.verifyPairingCalls.count, 1)
        XCTAssertEqual(api.verifyPairingCalls.first?.code, "000000")
        XCTAssertEqual(api.verifyPairingCalls.first?.identity, "123456789")

        XCTAssertEqual(store.state, .authenticated)
        XCTAssertEqual(settings.bearerToken, "session_abc123")
        XCTAssertEqual(store.storedIdentity, "123456789")
        XCTAssertEqual(store.storedChannel, "telegram")
        XCTAssertEqual(store.storedUsername, "foster")

        // Cleanup so this test doesn't leave a token in the shared Keychain
        // entry used by other tests/the app.
        settings.bearerToken = ""
    }

    /// A failed pairing-code request surfaces an error state instead of
    /// silently doing nothing.
    func testFailedPairingRequestShowsFailedState() async {
        let defaults = makeIsolatedDefaults()
        let settings = SettingsStore()
        settings.bearerToken = ""
        let api = FakeAuthAPI()
        api.requestPairingResult = .failure(FakeError(message: "Telegram user @foster not found."))
        let store = AuthStore(settings: settings, client: api, defaults: defaults)

        await store.requestCode(identity: "@foster", channel: .telegram)

        XCTAssertEqual(store.state, .failed("Telegram user @foster not found."))
    }

    /// Manually entering a bearer token in Settings (the "advanced
    /// fallback") moves the store directly to `.authenticated`, and
    /// clearing it returns to `.loggedOut`.
    func testManualTokenEntryIsAdvancedFallback() async {
        let defaults = makeIsolatedDefaults()
        let settings = SettingsStore()
        settings.bearerToken = ""
        let api = FakeAuthAPI()
        let store = AuthStore(settings: settings, client: api, defaults: defaults)
        XCTAssertEqual(store.state, .loggedOut)

        settings.bearerToken = "shared_manual_token"
        try? await Task.sleep(nanoseconds: 10_000_000)
        XCTAssertEqual(store.state, .authenticated)

        settings.bearerToken = ""
        try? await Task.sleep(nanoseconds: 10_000_000)
        XCTAssertEqual(store.state, .loggedOut)
    }

    /// A `401` from any API call (posted via `.weeUnauthorized`) clears the
    /// stored token and moves an authenticated session to `.expired`, so
    /// the UI can prompt the user to sign in again.
    func testUnauthorizedNotificationExpiresSession() async {
        let defaults = makeIsolatedDefaults()
        let settings = SettingsStore()
        settings.bearerToken = "session_will_expire"
        defaults.set(Date().addingTimeInterval(3600).timeIntervalSince1970, forKey: AuthStore.Keys.tokenExpiresAt)
        let api = FakeAuthAPI()
        let store = AuthStore(settings: settings, client: api, defaults: defaults)
        XCTAssertEqual(store.state, .authenticated)

        NotificationCenter.default.post(name: .weeUnauthorized, object: nil)
        try? await Task.sleep(nanoseconds: 10_000_000)

        XCTAssertEqual(store.state, .expired)
        XCTAssertEqual(settings.bearerToken, "")
    }

    private func makeIsolatedDefaults() -> UserDefaults {
        let name = "AuthStoreTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: name)!
        addTeardownBlock { defaults.removePersistentDomain(forName: name) }
        return defaults
    }
}
