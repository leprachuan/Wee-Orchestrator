import Foundation

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
}

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
