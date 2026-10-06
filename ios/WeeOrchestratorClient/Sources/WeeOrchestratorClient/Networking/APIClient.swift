import Foundation

extension Notification.Name {
    /// Posted by `APIClient` whenever a request receives a `401` response,
    /// so `AuthStore` can clear the stored session and return to the login
    /// flow (mirrors the WebUI's `apiRequest` 401 handling).
    static let weeUnauthorized = Notification.Name("WeeOrchestratorUnauthorized")
}

enum APIError: LocalizedError {
    case invalidURL
    case invalidResponse
    case http(status: Int, message: String?)
    case decoding(Error)

    var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "Invalid backend URL. Check Settings."
        case .invalidResponse:
            return "Unexpected response from server."
        case .http(let status, let message):
            return "Server returned \(status)\(message.map { ": \($0)" } ?? "")"
        case .decoding(let error):
            return "Failed to decode response: \(error.localizedDescription)"
        }
    }
}

/// Talks to the Wee Orchestrator `/api/v1` REST API.
///
/// Dev backends often run on a self-signed TLS certificate (mirroring the
/// `curl -k` examples used by the project's tooling), so this client
/// optionally trusts all certificates when `allowSelfSignedCertificates`
/// is enabled in Settings. Only enable that for trusted local/dev hosts.
final class APIClient: NSObject {
    private let settings: SettingsStore
    private lazy var session: URLSession = {
        URLSession(configuration: .default, delegate: self, delegateQueue: nil)
    }()

    init(settings: SettingsStore) {
        self.settings = settings
    }

    private func makeRequest(path: String, method: String = "GET", body: Data? = nil) throws -> URLRequest {
        guard let url = URL(string: settings.baseURL.trimmingTrailingSlash + path) else {
            throw APIError.invalidURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("Bearer \(settings.bearerToken)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("ios", forHTTPHeaderField: "X-Auth-Channel")
        request.httpBody = body
        return request
    }

    private func send<T: Decodable>(_ request: URLRequest) async throws -> T {
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw APIError.invalidResponse
        }
        guard (200..<300).contains(http.statusCode) else {
            let message = String(data: data, encoding: .utf8)
            if http.statusCode == 401 {
                NotificationCenter.default.post(name: .weeUnauthorized, object: nil)
            }
            throw APIError.http(status: http.statusCode, message: message)
        }
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw APIError.decoding(error)
        }
    }

    func fetchHealth() async throws -> HealthStatus {
        let request = try makeRequest(path: "/api/v1/health")
        return try await send(request)
    }

    func fetchAgents() async throws -> [Agent] {
        let request = try makeRequest(path: "/api/v1/agents")
        let response: AgentsResponse = try await send(request)
        return response.agents
    }

    func fetchServiceStatus() async throws -> ServiceStatusResponse {
        let request = try makeRequest(path: "/api/v1/service-status")
        return try await send(request)
    }

    func fetchBackgroundTasks() async throws -> [BackgroundTaskSummary] {
        let request = try makeRequest(path: "/api/v1/background-tasks")
        let response: BackgroundTasksResponse = try await send(request)
        return response.tasks
    }

    func fetchBackgroundTask(taskId: String) async throws -> BackgroundTaskDetail {
        let request = try makeRequest(path: "/api/v1/background-tasks/\(taskId)")
        return try await send(request)
    }

    func createBackgroundTask(_ body: CreateBackgroundTaskRequest) async throws -> CreateBackgroundTaskResponse {
        let encoded = try JSONEncoder().encode(body)
        let request = try makeRequest(path: "/api/v1/background-tasks", method: "POST", body: encoded)
        return try await send(request)
    }

    // MARK: - Scheduler

    func fetchScheduledJobs() async throws -> [ScheduledJob] {
        let request = try makeRequest(path: "/api/v1/scheduler/jobs")
        let response: SchedulerJobsResponse = try await send(request)
        return response.result
    }

    // MARK: - Chat

    func fetchChatSessions() async throws -> [ChatSession] {
        let request = try makeRequest(path: "/api/v1/history/sessions")
        let response: ChatSessionsResponse = try await send(request)
        return response.sessions
    }

    func fetchChatSessionMessages(sessionId: String) async throws -> [ChatMessage] {
        let request = try makeRequest(path: "/api/v1/history/sessions/\(sessionId)/messages")
        let response: ChatSessionMessagesResponse = try await send(request)
        return response.messages
    }

    func createChatSession(agent: String?, model: String? = nil, runtime: String? = nil) async throws -> CreateChatSessionResponse {
        let body = CreateChatSessionRequest(sessionId: nil, agent: agent, model: model, runtime: runtime)
        let encoded = try JSONEncoder().encode(body)
        let request = try makeRequest(path: "/api/v1/sessions/create", method: "POST", body: encoded)
        return try await send(request)
    }

    func executeChatSession(sessionId: String, query: String) async throws -> ExecuteSessionResponse {
        let body = ExecuteSessionRequest(query: query)
        let encoded = try JSONEncoder().encode(body)
        let request = try makeRequest(path: "/api/v1/sessions/\(sessionId)/execute", method: "POST", body: encoded)
        return try await send(request)
    }

    // MARK: - Auth (Telegram/WebEx pairing)

    /// Requests a one-time pairing code be sent to the user via the given
    /// channel (e.g. their Telegram DM), mirroring the WebUI's
    /// "Send Pairing Code" step. No bearer token is required for this call.
    func requestPairing(identity: String, channel: String) async throws -> PairingRequestResponse {
        let body = PairingRequestBody(identity: identity, channel: channel)
        let encoded = try JSONEncoder().encode(body)
        let request = try makeRequest(path: "/api/v1/auth/request-pairing", method: "POST", body: encoded)
        return try await send(request)
    }

    /// Exchanges a pairing code for a per-device session bearer token,
    /// mirroring the WebUI's "Verify" step. No bearer token is required for
    /// this call. `deviceName`/`platform` are display-only metadata shown
    /// back in the device-token management UI.
    func verifyPairing(code: String, identity: String, deviceName: String? = nil, platform: String? = nil) async throws -> PairingVerificationResponse {
        let body = PairingVerificationBody(code: code, identity: identity, deviceName: deviceName, platform: platform)
        let encoded = try JSONEncoder().encode(body)
        let request = try makeRequest(path: "/api/v1/auth/verify-pairing", method: "POST", body: encoded)
        return try await send(request)
    }

    // MARK: - Device token management

    /// Lists the caller's own long-lived device tokens/sessions. Server
    /// enforces that only the authenticated identity's own devices are
    /// returned.
    func fetchDeviceTokens() async throws -> [DeviceToken] {
        let request = try makeRequest(path: "/api/v1/auth/devices")
        let response: DeviceTokensResponse = try await send(request)
        return response.devices
    }

    /// Revokes one of the caller's own device tokens by id.
    func revokeDeviceToken(tokenId: String) async throws {
        let request = try makeRequest(path: "/api/v1/auth/devices/\(tokenId)", method: "DELETE")
        let _: RevokeDeviceResponse = try await send(request)
    }

    /// Signs the caller out of every device, including the one making the
    /// call.
    func revokeAllDeviceTokens() async throws -> Int {
        let request = try makeRequest(path: "/api/v1/auth/devices/revoke-all", method: "POST")
        let response: RevokeAllDevicesResponse = try await send(request)
        return response.revokedCount
    }
}

private struct RevokeDeviceResponse: Decodable {
    let revoked: String
}

private struct RevokeAllDevicesResponse: Decodable {
    let revokedCount: Int

    enum CodingKeys: String, CodingKey {
        case revokedCount = "revoked_count"
    }
}

/// Network operations needed by `AppState`, abstracted so tests can supply
/// a fake implementation without hitting the network.
protocol AppStateAPI {
    func fetchHealth() async throws -> HealthStatus
    func fetchAgents() async throws -> [Agent]
    func fetchServiceStatus() async throws -> ServiceStatusResponse
    func fetchBackgroundTasks() async throws -> [BackgroundTaskSummary]
    func fetchScheduledJobs() async throws -> [ScheduledJob]
    func fetchBackgroundTask(taskId: String) async throws -> BackgroundTaskDetail
    func createBackgroundTask(_ body: CreateBackgroundTaskRequest) async throws -> CreateBackgroundTaskResponse
}

extension APIClient: AppStateAPI {}

protocol AuthAPI {
    func requestPairing(identity: String, channel: String) async throws -> PairingRequestResponse
    func verifyPairing(code: String, identity: String, deviceName: String?, platform: String?) async throws -> PairingVerificationResponse
}

extension APIClient: AuthAPI {}

protocol DeviceManagementAPI {
    func fetchDeviceTokens() async throws -> [DeviceToken]
    func revokeDeviceToken(tokenId: String) async throws
    func revokeAllDeviceTokens() async throws -> Int
}

extension APIClient: DeviceManagementAPI {}

extension APIClient: URLSessionDelegate {
    func urlSession(
        _ session: URLSession,
        didReceive challenge: URLAuthenticationChallenge,
        completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        guard settings.allowSelfSignedCertificates,
              challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              let serverTrust = challenge.protectionSpace.serverTrust
        else {
            completionHandler(.performDefaultHandling, nil)
            return
        }
        completionHandler(.useCredential, URLCredential(trust: serverTrust))
    }
}

private extension String {
    var trimmingTrailingSlash: String {
        hasSuffix("/") ? String(dropLast()) : self
    }
}
