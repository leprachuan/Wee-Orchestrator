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
