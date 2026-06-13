import Foundation
import Combine

/// Persists connection settings for the Wee Orchestrator backend.
///
/// The base URL is stored in `UserDefaults` (non-sensitive). The bearer
/// token is stored in the Keychain since it grants API access.
final class SettingsStore: ObservableObject {
    @Published var baseURL: String {
        didSet { UserDefaults.standard.set(baseURL, forKey: Keys.baseURL) }
    }

    @Published var bearerToken: String {
        didSet { KeychainHelper.shared.save(bearerToken, for: Keys.bearerToken) }
    }

    @Published var useMockData: Bool {
        didSet { UserDefaults.standard.set(useMockData, forKey: Keys.useMockData) }
    }

    /// Trusts any TLS certificate presented by the backend. Intended only
    /// for local/dev hosts using self-signed certificates — never enable
    /// this for a backend reachable over an untrusted network.
    @Published var allowSelfSignedCertificates: Bool {
        didSet { UserDefaults.standard.set(allowSelfSignedCertificates, forKey: Keys.allowSelfSigned) }
    }

    private enum Keys {
        static let baseURL = "wee.baseURL"
        static let bearerToken = "wee.bearerToken"
        static let useMockData = "wee.useMockData"
        static let allowSelfSigned = "wee.allowSelfSignedCertificates"
    }

    init() {
        let storedURL = UserDefaults.standard.string(forKey: Keys.baseURL)
        self.baseURL = storedURL ?? "https://127.0.0.1:8000"

        self.bearerToken = KeychainHelper.shared.read(for: Keys.bearerToken) ?? ""

        // Default to mock data until the user supplies real connection details,
        // so the app is usable immediately after a fresh install.
        if UserDefaults.standard.object(forKey: Keys.useMockData) == nil {
            self.useMockData = true
        } else {
            self.useMockData = UserDefaults.standard.bool(forKey: Keys.useMockData)
        }

        self.allowSelfSignedCertificates = UserDefaults.standard.bool(forKey: Keys.allowSelfSigned)
    }

    var isConfigured: Bool {
        !baseURL.isEmpty && !bearerToken.isEmpty
    }
}
