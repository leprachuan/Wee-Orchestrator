import Foundation

struct HealthStatus: Codable, Equatable {
    let status: String
    let uptimeSeconds: Double
    let version: String
    let environment: String
    let agentsLoaded: Int
    let schedulerEnabled: Bool
    let activeSessions: Int

    enum CodingKeys: String, CodingKey {
        case status
        case uptimeSeconds = "uptime_seconds"
        case version
        case environment
        case agentsLoaded = "agents_loaded"
        case schedulerEnabled = "scheduler_enabled"
        case activeSessions = "active_sessions"
    }
}

struct Agent: Codable, Identifiable, Equatable {
    var id: String { name }
    let name: String
    let description: String
    let path: String?
    let primaryRuntime: String?
    let primaryModel: String?

    enum CodingKeys: String, CodingKey {
        case name
        case description
        case path
        case primaryRuntime = "primary_runtime"
        case primaryModel = "primary_model"
    }
}

struct AgentsResponse: Codable {
    let agents: [Agent]
}

struct ServiceInfo: Codable, Equatable {
    let service: String
    let status: String
    let active: Bool
    let error: String?
}

struct ServiceStatusResponse: Codable {
    let services: [String: ServiceInfo]
    let node: String
    let checkedAt: Double

    enum CodingKeys: String, CodingKey {
        case services
        case node
        case checkedAt = "checked_at"
    }
}

struct BackgroundTaskSummary: Codable, Identifiable, Equatable {
    var id: String { taskId }
    let taskId: String
    let agent: String
    let runtime: String
    let model: String
    let prompt: String
    let status: String
    let createdAt: String
    let completedAt: String?
    let error: String?

    enum CodingKeys: String, CodingKey {
        case taskId = "task_id"
        case agent
        case runtime
        case model
        case prompt
        case status
        case createdAt = "created_at"
        case completedAt = "completed_at"
        case error
    }
}

struct BackgroundTasksResponse: Codable {
    let tasks: [BackgroundTaskSummary]
}

struct BackgroundTaskDetail: Codable, Identifiable, Equatable {
    var id: String { taskId }
    let taskId: String
    let sessionId: String
    let agent: String
    let runtime: String
    let model: String
    let prompt: String
    let status: String
    let createdAt: String
    let completedAt: String?
    let recentOutput: [String]
    let error: String?

    enum CodingKeys: String, CodingKey {
        case taskId = "task_id"
        case sessionId = "session_id"
        case agent
        case runtime
        case model
        case prompt
        case status
        case createdAt = "created_at"
        case completedAt = "completed_at"
        case recentOutput = "recent_output"
        case error
    }
}

struct CreateBackgroundTaskRequest: Codable {
    let prompt: String
    let agent: String?
    let runtime: String?
    let model: String?
    let timeout: Int?
}

struct CreateBackgroundTaskResponse: Codable {
    let taskId: String
    let sessionId: String
    let agent: String
    let runtime: String
    let model: String
    let status: String

    enum CodingKeys: String, CodingKey {
        case taskId = "task_id"
        case sessionId = "session_id"
        case agent
        case runtime
        case model
        case status
    }
}

// MARK: - Chat

/// A single message in a chat session, as returned by
/// `/api/v1/history/sessions/{session_id}/messages`.
struct ChatMessage: Codable, Identifiable, Equatable {
    var id: String { "\(role)-\(timestamp)" }
    let role: String
    let content: String
    let timestamp: Double

    var isUser: Bool { role == "user" }
}

/// A chat session summary (no messages), as returned by
/// `/api/v1/history/sessions`.
struct ChatSession: Codable, Identifiable, Equatable {
    var id: String { sessionId }
    let sessionId: String
    let title: String?
    let preview: String?
    let agent: String?
    let createdAt: Double?
    let updatedAt: Double?

    enum CodingKeys: String, CodingKey {
        case sessionId = "session_id"
        case title
        case preview
        case agent
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }

    /// A short label for display in session lists, falling back to the
    /// preview or session id when no title has been generated yet.
    var displayTitle: String {
        if let title, !title.isEmpty { return title }
        if let preview, !preview.isEmpty { return preview }
        return sessionId
    }
}

struct ChatSessionsResponse: Codable {
    let sessions: [ChatSession]
}

struct ChatSessionMessagesResponse: Codable {
    let sessionId: String
    let messages: [ChatMessage]

    enum CodingKeys: String, CodingKey {
        case sessionId = "session_id"
        case messages
    }
}

struct CreateChatSessionRequest: Codable {
    let sessionId: String?
    let agent: String?
    let model: String?
    let runtime: String?

    enum CodingKeys: String, CodingKey {
        case sessionId = "session_id"
        case agent
        case model
        case runtime
    }
}

struct CreateChatSessionResponse: Codable {
    let sessionId: String
    let agent: String?
    let model: String?
    let runtime: String?

    enum CodingKeys: String, CodingKey {
        case sessionId = "session_id"
        case agent
        case model
        case runtime
    }
}

/// Body for `POST /api/v1/sessions/{session_id}/execute`.
struct ExecuteSessionRequest: Codable {
    let query: String
}

struct ExecuteSessionResponse: Codable {
    let sessionId: String
    let response: String
    let runtime: String?
    let model: String?

    enum CodingKeys: String, CodingKey {
        case sessionId = "session_id"
        case response
        case runtime
        case model
    }
}
