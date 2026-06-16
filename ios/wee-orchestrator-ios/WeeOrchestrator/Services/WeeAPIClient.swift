import Foundation

enum WeeAPIError: LocalizedError {
    case invalidBaseURL
    case invalidResponse
    case httpStatus(Int, String)

    var errorDescription: String? {
        switch self {
        case .invalidBaseURL:
            "Invalid backend URL."
        case .invalidResponse:
            "The backend returned an invalid response."
        case .httpStatus(let status, let message):
            "HTTP \(status): \(message)"
        }
    }
}

final class InsecureSessionDelegate: NSObject, URLSessionDelegate {
    func urlSession(
        _ session: URLSession,
        didReceive challenge: URLAuthenticationChallenge
    ) async -> (URLSession.AuthChallengeDisposition, URLCredential?) {
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              let trust = challenge.protectionSpace.serverTrust else {
            return (.performDefaultHandling, nil)
        }
        return (.useCredential, URLCredential(trust: trust))
    }
}

struct WeeAPIClient {
    var configuration: APIConfiguration

    private var baseURL: URL? {
        URL(string: configuration.baseURLString.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    private var session: URLSession {
        if configuration.allowInsecureTLS {
            URLSession(configuration: .default, delegate: InsecureSessionDelegate(), delegateQueue: nil)
        } else {
            URLSession.shared
        }
    }

    func health() async throws -> HealthResponse {
        try await request("GET", path: "/api/v1/health")
    }

    func appConfig() async throws -> AppConfigResponse {
        try await request("GET", path: "/api/v1/config")
    }

    func agents() async throws -> [AgentSummary] {
        let response: AgentsResponse = try await request("GET", path: "/api/v1/agents")
        return response.agents
    }

    func requestPairing(identity: String, channel: String) async throws -> PairingRequestResponse {
        let body = PairingRequest(identity: identity, channel: channel)
        return try await request("POST", path: "/api/v1/auth/request-pairing", body: body)
    }

    func verifyPairing(identity: String, code: String) async throws -> PairingVerificationResponse {
        let body = PairingVerificationRequest(code: code, identity: identity)
        return try await request("POST", path: "/api/v1/auth/verify-pairing", body: body)
    }

    func backgroundTasks() async throws -> [BackgroundTaskSummary] {
        let response: BackgroundTasksResponse = try await request("GET", path: "/api/v1/background-tasks")
        return response.tasks
    }

    func scheduledJobs() async throws -> [ScheduledJobSummary] {
        let response: ScheduledJobsResponse = try await request("GET", path: "/api/v1/scheduler/jobs")
        return response.result
    }

    func kanbanBoard() async throws -> KanbanBoardResponse {
        do {
            return try await request("GET", path: "/api/v1/kanban/board")
        } catch WeeAPIError.httpStatus(404, _) {
            let response: LegacyTodosResponse = try await request("GET", path: "/api/v1/todos")
            return KanbanBoardResponse.legacyTodos(response)
        }
    }

    func kanbanItem(id: String) async throws -> KanbanItemDetail {
        try await request("GET", path: "/api/v1/kanban/items/\(id)")
    }

    func updateKanbanItem(
        id: String,
        title: String?,
        details: String?,
        status: String?,
        agent: String?,
        due: String?,
        priority: String?,
        urgency: String?
    ) async throws -> KanbanItemDetail {
        let body = KanbanItemUpdateRequest(
            title: title,
            details: details,
            status: status,
            agent: agent,
            due: due,
            priority: priority,
            urgency: urgency
        )
        return try await request("PATCH", path: "/api/v1/kanban/items/\(id)", body: body)
    }

    func commentKanbanItem(id: String, body: String) async throws -> KanbanItemDetail {
        try await request("POST", path: "/api/v1/kanban/items/\(id)/comments", body: KanbanCommentRequest(body: body))
    }

    func completeKanbanItem(id: String) async throws -> KanbanItemDetail {
        try await request("POST", path: "/api/v1/kanban/items/\(id)/complete", body: Optional<String>.none)
    }

    func closeKanbanItem(id: String) async throws -> KanbanItemDetail {
        try await request("POST", path: "/api/v1/kanban/items/\(id)/close", body: Optional<String>.none)
    }

    func dispatchKanbanItem(id: String, agent: String, prompt: String?) async throws -> KanbanDispatchResponse {
        let body = KanbanDispatchRequest(agent: agent, prompt: prompt, timeout: 900)
        return try await request("POST", path: "/api/v1/kanban/items/\(id)/dispatch", body: body)
    }

    func backgroundTask(id: String) async throws -> BackgroundTaskDetail {
        try await request("GET", path: "/api/v1/background-tasks/\(id)")
    }

    func createBackgroundTask(prompt: String, agent: String?) async throws -> CreateBackgroundTaskResponse {
        let body = CreateBackgroundTaskRequest(
            prompt: prompt,
            agent: agent,
            runtime: nil,
            model: nil,
            timeout: 900,
            notify: true,
            permissionMode: nil
        )
        return try await request("POST", path: "/api/v1/background-tasks", body: body)
    }

    func createSession(agent: String?) async throws -> CreateSessionResponse {
        let body = CreateSessionRequest(sessionID: nil, agent: agent, model: nil, runtime: nil)
        return try await request("POST", path: "/api/v1/sessions/create", body: body)
    }

    func sessionStatus(sessionID: String) async throws -> SessionStatusResponse {
        try await request("GET", path: "/api/v1/sessions/\(sessionID)/status")
    }

    func execute(sessionID: String, query: String, agent: String?) async throws -> ExecuteResponse {
        let body = ExecuteRequest(query: query, timeout: 900, model: nil, runtime: nil, agent: agent)
        return try await request("POST", path: "/api/v1/sessions/\(sessionID)/execute", body: body)
    }

    func historySessions() async throws -> [HistorySessionSummary] {
        let response: HistorySessionsResponse = try await request("GET", path: "/api/v1/history/sessions")
        return response.sessions
    }

    func historyMessages(sessionID: String, limit: Int = 100) async throws -> HistoryMessagesResponse {
        try await request("GET", path: "/api/v1/history/sessions/\(sessionID)/messages?limit=\(limit)")
    }

    private func request<T: Decodable>(_ method: String, path: String) async throws -> T {
        try await request(method, path: path, body: Optional<String>.none)
    }

    private func request<T: Decodable, B: Encodable>(_ method: String, path: String, body: B?) async throws -> T {
        guard let baseURL else { throw WeeAPIError.invalidBaseURL }

        guard let url = makeURL(baseURL: baseURL, path: path) else {
            throw WeeAPIError.invalidBaseURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = 45
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let token = normalizedToken(configuration.token)
        if !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        if !configuration.identity.isEmpty {
            request.setValue(configuration.identity, forHTTPHeaderField: "X-User-Identity")
            request.setValue(configuration.channel, forHTTPHeaderField: "X-Auth-Channel")
        }

        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONEncoder().encode(body)
        }

        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw WeeAPIError.invalidResponse
        }

        guard (200..<300).contains(http.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? HTTPURLResponse.localizedString(forStatusCode: http.statusCode)
            throw WeeAPIError.httpStatus(http.statusCode, message)
        }

        return try JSONDecoder().decode(T.self, from: data)
    }

    private func makeURL(baseURL: URL, path: String) -> URL? {
        var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false)
        let basePath = components?.path.trimmingCharacters(in: CharacterSet(charactersIn: "/")) ?? ""
        let pathParts = path.split(separator: "?", maxSplits: 1).map(String.init)
        let requestPath = (pathParts.first ?? "").trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        components?.path = "/" + [basePath, requestPath].filter { !$0.isEmpty }.joined(separator: "/")
        if pathParts.count > 1 {
            components?.percentEncodedQuery = pathParts[1]
        }
        return components?.url
    }

    private func normalizedToken(_ token: String) -> String {
        let trimmed = token.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.lowercased().hasPrefix("bearer ") {
            return String(trimmed.dropFirst(7)).trimmingCharacters(in: .whitespacesAndNewlines)
        }
        return trimmed
    }
}
