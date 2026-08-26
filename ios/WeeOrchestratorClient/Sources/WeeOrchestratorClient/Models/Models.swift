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

// MARK: - Scheduler

/// Who created a scheduled job, as returned in `created_by`.
struct ScheduledJobCreator: Codable, Equatable {
    let identity: String?
    let channel: String?
    let username: String?
}

/// A scheduled task, as returned by `/api/v1/scheduler/jobs`.
struct ScheduledJob: Codable, Identifiable, Equatable {
    let id: String
    let name: String
    let agent: String?
    let runtime: String?
    let model: String?
    let mode: String?
    let task: String?
    let schedule: String?
    let cron: String?
    let recurring: Bool?
    let enabled: Bool?
    let notify: Bool?
    let createdAt: String?
    let nextRun: String?
    let lastRun: String?
    let createdBy: ScheduledJobCreator?

    enum CodingKeys: String, CodingKey {
        case id, name, agent, runtime, model, mode, task, schedule, cron, recurring, enabled, notify
        case createdAt = "created_at"
        case nextRun = "next_run"
        case lastRun = "last_run"
        case createdBy = "created_by"
    }
}

struct SchedulerJobsResponse: Codable {
    let success: Bool
    let result: [ScheduledJob]
    let message: String?
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

// MARK: - Telegram/WebEx Pairing Auth

/// Channels supported by `/api/v1/auth/request-pairing`, mirroring the
/// WebUI's auth channel picker.
enum AuthChannel: String, Codable, CaseIterable, Identifiable {
    case telegram
    case webex

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .telegram: return "Telegram"
        case .webex: return "WebEx"
        }
    }
}

/// Body for `POST /api/v1/auth/request-pairing`.
struct PairingRequestBody: Codable {
    let identity: String
    let channel: String
}

struct PairingRequestResponse: Codable {
    let message: String
    let expiresIn: Int
    let identityResolved: String?

    enum CodingKeys: String, CodingKey {
        case message
        case expiresIn = "expires_in"
        case identityResolved = "identity_resolved"
    }
}

/// Body for `POST /api/v1/auth/verify-pairing`.
struct PairingVerificationBody: Codable {
    let code: String
    let identity: String
    /// Shown back to the user in the device-token management UI (Settings,
    /// WebUI) so they can tell devices apart when revoking. Not security
    /// sensitive — purely a display label.
    let deviceName: String?
    let platform: String?

    enum CodingKeys: String, CodingKey {
        case code
        case identity
        case deviceName = "device_name"
        case platform
    }
}

struct PairingVerificationResponse: Codable {
    let token: String
    let tokenId: String?
    let expiresIn: Int
    let absoluteExpiresIn: Int
    let identity: String
    let channel: String
    let username: String?

    enum CodingKeys: String, CodingKey {
        case token
        case tokenId = "token_id"
        case expiresIn = "expires_in"
        case absoluteExpiresIn = "absolute_expires_in"
        case identity
        case channel
        case username
    }
}

/// One of the caller's own long-lived device tokens/sessions, as returned
/// by `GET /api/v1/auth/devices`. Metadata only — the API never returns
/// raw tokens after the initial pairing response.
struct DeviceToken: Codable, Identifiable {
    let tokenId: String
    let deviceName: String
    let platform: String
    let channel: String
    let createdAt: Double?
    let lastUsed: Double?
    let expiresAt: Double?
    let absoluteExpiresAt: Double?
    let current: Bool

    var id: String { tokenId }

    enum CodingKeys: String, CodingKey {
        case tokenId = "token_id"
        case deviceName = "device_name"
        case platform
        case channel
        case createdAt = "created_at"
        case lastUsed = "last_used"
        case expiresAt = "expires_at"
        case absoluteExpiresAt = "absolute_expires_at"
        case current
    }
}

struct DeviceTokensResponse: Codable {
    let devices: [DeviceToken]
}
