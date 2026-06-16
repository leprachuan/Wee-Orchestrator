import Foundation

struct APIConfiguration: Equatable {
    var baseURLString: String
    var token: String
    var identity: String
    var channel: String
    var allowInsecureTLS: Bool

    static let defaults = APIConfiguration(
        baseURLString: "https://100.124.186.75:8000",
        token: "",
        identity: "",
        channel: "telegram",
        allowInsecureTLS: true
    )
}

struct PairingRequest: Encodable {
    let identity: String
    let channel: String
}

struct PairingRequestResponse: Decodable, Equatable {
    let message: String
    let expiresIn: Int?
    let identityResolved: String?

    enum CodingKeys: String, CodingKey {
        case message
        case expiresIn = "expires_in"
        case identityResolved = "identity_resolved"
    }
}

struct PairingVerificationRequest: Encodable {
    let code: String
    let identity: String
}

struct PairingVerificationResponse: Decodable, Equatable {
    let token: String
    let expiresIn: Int?
    let absoluteExpiresIn: Int?
    let identity: String
    let channel: String
    let username: String?

    enum CodingKeys: String, CodingKey {
        case token
        case expiresIn = "expires_in"
        case absoluteExpiresIn = "absolute_expires_in"
        case identity
        case channel
        case username
    }
}

struct HealthResponse: Decodable, Equatable {
    let status: String
    let uptimeSeconds: Double?
    let version: String?
    let environment: String?
    let agentsLoaded: Int?
    let schedulerEnabled: Bool?
    let activeSessions: Int?

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

struct AppConfigResponse: Decodable, Equatable {
    let schedulerEnabled: Bool?
    let backgroundTasksEnabled: Bool?
    let appEnv: String?

    enum CodingKeys: String, CodingKey {
        case schedulerEnabled = "scheduler_enabled"
        case backgroundTasksEnabled = "background_tasks_enabled"
        case appEnv = "app_env"
    }
}

struct AgentsResponse: Decodable {
    let agents: [AgentSummary]
}

struct AgentSummary: Decodable, Identifiable, Hashable {
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

struct BackgroundTasksResponse: Decodable {
    let tasks: [BackgroundTaskSummary]
}

struct ScheduledJobsResponse: Decodable {
    let success: Bool?
    let result: [ScheduledJobSummary]
    let message: String?
}

struct ScheduledJobSummary: Decodable, Identifiable, Hashable {
    var id: String { jobID }

    let jobID: String
    let name: String?
    let agent: String?
    let runtime: String?
    let model: String?
    let mode: String?
    let permissionMode: String?
    let task: String?
    let schedule: String?
    let cron: String?
    let workingDir: String?
    let notify: Bool?
    let fallbackRuntime: String?
    let fallbackModel: String?
    let recurring: Bool?
    let createdAt: String?
    let nextRun: String?
    let lastRun: String?
    let enabled: Bool?
    let retries: Int?
    let timeout: Int?

    enum CodingKeys: String, CodingKey {
        case id
        case name
        case agent
        case runtime
        case model
        case mode
        case permissionMode = "permission_mode"
        case task
        case schedule
        case cron
        case workingDir = "working_dir"
        case notify
        case fallbackRuntime = "fallback_runtime"
        case fallbackModel = "fallback_model"
        case recurring
        case createdAt = "created_at"
        case nextRun = "next_run"
        case lastRun = "last_run"
        case enabled
        case retries
        case timeout
    }

    init(
        id: String,
        name: String?,
        agent: String?,
        runtime: String?,
        model: String?,
        mode: String?,
        permissionMode: String?,
        task: String?,
        schedule: String?,
        cron: String?,
        workingDir: String?,
        notify: Bool?,
        fallbackRuntime: String?,
        fallbackModel: String?,
        recurring: Bool?,
        createdAt: String?,
        nextRun: String?,
        lastRun: String?,
        enabled: Bool?,
        retries: Int?,
        timeout: Int?
    ) {
        self.jobID = id
        self.name = name
        self.agent = agent
        self.runtime = runtime
        self.model = model
        self.mode = mode
        self.permissionMode = permissionMode
        self.task = task
        self.schedule = schedule
        self.cron = cron
        self.workingDir = workingDir
        self.notify = notify
        self.fallbackRuntime = fallbackRuntime
        self.fallbackModel = fallbackModel
        self.recurring = recurring
        self.createdAt = createdAt
        self.nextRun = nextRun
        self.lastRun = lastRun
        self.enabled = enabled
        self.retries = retries
        self.timeout = timeout
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let decodedID = try container.decodeIfPresent(String.self, forKey: .id)
        let decodedName = try container.decodeIfPresent(String.self, forKey: .name)
        jobID = decodedID ?? decodedName ?? UUID().uuidString
        name = decodedName
        agent = try container.decodeIfPresent(String.self, forKey: .agent)
        runtime = try container.decodeIfPresent(String.self, forKey: .runtime)
        model = try container.decodeIfPresent(String.self, forKey: .model)
        mode = try container.decodeIfPresent(String.self, forKey: .mode)
        permissionMode = try container.decodeIfPresent(String.self, forKey: .permissionMode)
        task = try container.decodeIfPresent(String.self, forKey: .task)
        schedule = try container.decodeIfPresent(String.self, forKey: .schedule)
        cron = try container.decodeIfPresent(String.self, forKey: .cron)
        workingDir = try container.decodeIfPresent(String.self, forKey: .workingDir)
        notify = try container.decodeIfPresent(Bool.self, forKey: .notify)
        fallbackRuntime = try container.decodeIfPresent(String.self, forKey: .fallbackRuntime)
        fallbackModel = try container.decodeIfPresent(String.self, forKey: .fallbackModel)
        recurring = try container.decodeIfPresent(Bool.self, forKey: .recurring)
        createdAt = try container.decodeIfPresent(String.self, forKey: .createdAt)
        nextRun = try container.decodeIfPresent(String.self, forKey: .nextRun)
        lastRun = try container.decodeIfPresent(String.self, forKey: .lastRun)
        enabled = try container.decodeIfPresent(Bool.self, forKey: .enabled)
        retries = try container.decodeIfPresent(Int.self, forKey: .retries)
        timeout = try container.decodeIfPresent(Int.self, forKey: .timeout)
    }

    var displayName: String {
        let trimmed = (name ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? jobID : trimmed
    }

    var displaySchedule: String {
        let trimmed = (schedule ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? "No schedule" : trimmed
    }
}

struct KanbanBoardResponse: Decodable, Equatable {
    let success: Bool
    let columns: [String: [KanbanCard]]
    let agents: [String]
    let sources: [String]
    let total: Int
    let repo: String?

    var dueCards: [KanbanCard] {
        KanbanColumnID.allCases
            .flatMap { columns[$0.rawValue] ?? [] }
            .filter { ["overdue", "today", "soon"].contains($0.dueBucket) }
    }
}

struct LegacyTodosResponse: Decodable, Equatable {
    let todos: [LegacyTodo]
    let count: Int?
    let agent: String?
    let file: String?
}

struct LegacyTodo: Decodable, Equatable {
    let description: String
    let due: String?
    let labels: [String]?
    let notes: [String]?
    let details: String?
}

struct KanbanCard: Decodable, Identifiable, Hashable, Equatable {
    let id: String
    let title: String
    let source: String
    let status: String
    let agent: String?
    let priority: String
    let urgency: String
    let due: String?
    let dueBucket: String
    let isOverdue: Bool
    let labels: [String]
    let details: String
    let url: String?
    let createdAt: String?
    let updatedAt: String?
    let githubIssueNumber: Int?

    enum CodingKeys: String, CodingKey {
        case id
        case title
        case source
        case status
        case agent
        case priority
        case urgency
        case due
        case dueBucket = "due_bucket"
        case isOverdue = "is_overdue"
        case labels
        case details
        case url
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case githubIssueNumber = "github_issue_number"
    }
}

struct KanbanComment: Decodable, Identifiable, Hashable, Equatable {
    let id: String
    let body: String
    let createdAt: String?
    let author: KanbanCommentAuthor?
    let url: String?

    enum CodingKeys: String, CodingKey {
        case id
        case body
        case createdAt = "createdAt"
        case author
        case url
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        body = try container.decodeIfPresent(String.self, forKey: .body) ?? ""
        createdAt = try container.decodeIfPresent(String.self, forKey: .createdAt)
        author = try container.decodeIfPresent(KanbanCommentAuthor.self, forKey: .author)
        url = try container.decodeIfPresent(String.self, forKey: .url)
        id = try container.decodeIfPresent(String.self, forKey: .id) ?? "\(createdAt ?? ""):\(body.hashValue)"
    }
}

struct KanbanCommentAuthor: Decodable, Hashable, Equatable {
    let login: String?
}

struct KanbanItemDetail: Decodable, Identifiable, Hashable, Equatable {
    let id: String
    let title: String
    let source: String
    let status: String
    let agent: String?
    let priority: String
    let urgency: String
    let due: String?
    let dueBucket: String
    let isOverdue: Bool
    let labels: [String]
    let details: String
    let url: String?
    let createdAt: String?
    let updatedAt: String?
    let githubIssueNumber: Int?
    let comments: [KanbanComment]
    let repo: String?

    enum CodingKeys: String, CodingKey {
        case id
        case title
        case source
        case status
        case agent
        case priority
        case urgency
        case due
        case dueBucket = "due_bucket"
        case isOverdue = "is_overdue"
        case labels
        case details
        case url
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case githubIssueNumber = "github_issue_number"
        case comments
        case repo
    }
}

struct KanbanItemUpdateRequest: Encodable {
    let title: String?
    let details: String?
    let status: String?
    let agent: String?
    let due: String?
    let priority: String?
    let urgency: String?
}

struct KanbanCommentRequest: Encodable {
    let body: String
}

struct KanbanDispatchRequest: Encodable {
    let agent: String
    let prompt: String?
    let timeout: Int?
}

struct KanbanDispatchResponse: Decodable {
    let success: Bool
    let task: CreateBackgroundTaskResponse
    let item: KanbanItemDetail
}

extension KanbanBoardResponse {
    static func legacyTodos(_ response: LegacyTodosResponse) -> KanbanBoardResponse {
        let cards = response.todos.enumerated().map { index, todo in
            let labels = todo.labels ?? []
            let status = labels.labelValue(prefix: "status:") ?? "todo"
            let normalizedStatus = KanbanColumnID(rawValue: status)?.rawValue ?? "todo"
            let dueBucket = Self.dueBucket(for: todo.due)
            return KanbanCard(
                id: "legacy-todo:\(index):\(todo.description)",
                title: todo.description,
                source: "todo",
                status: normalizedStatus,
                agent: labels.labelValue(prefix: "agent:") ?? response.agent,
                priority: labels.labelValue(prefix: "priority:") ?? "normal",
                urgency: labels.contains(where: { ["urgent", "urgency:urgent", "urgency:high"].contains($0.lowercased()) }) ? "urgent" : "normal",
                due: todo.due,
                dueBucket: dueBucket,
                isOverdue: dueBucket == "overdue",
                labels: labels,
                details: todo.details ?? "",
                url: nil,
                createdAt: nil,
                updatedAt: nil,
                githubIssueNumber: nil
            )
        }

        let columns = Dictionary(grouping: cards, by: \.status)
        return KanbanBoardResponse(
            success: true,
            columns: KanbanColumnID.allCases.reduce(into: [:]) { result, column in
                result[column.rawValue] = columns[column.rawValue] ?? []
            },
            agents: Array(Set(cards.compactMap(\.agent))).sorted(),
            sources: cards.isEmpty ? [] : ["todo"],
            total: cards.count,
            repo: nil
        )
    }

    private static func dueBucket(for due: String?) -> String {
        guard let date = due.flatMap(parseLegacyDate) else { return "none" }
        let calendar = Calendar.current
        let now = Date()

        if date < now { return "overdue" }
        if calendar.isDate(date, inSameDayAs: now) { return "today" }
        if let soon = calendar.date(byAdding: .day, value: 3, to: now), date <= soon {
            return "soon"
        }
        return "future"
    }

    private static func parseLegacyDate(_ value: String) -> Date? {
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = iso.date(from: value) { return date }

        iso.formatOptions = [.withInternetDateTime]
        if let date = iso.date(from: value) { return date }

        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone.current
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter.date(from: String(value.prefix(10)))
    }
}

private extension Array where Element == String {
    func labelValue(prefix: String) -> String? {
        first { $0.lowercased().hasPrefix(prefix) }?
            .split(separator: ":", maxSplits: 1)
            .last
            .map { String($0).trimmingCharacters(in: .whitespacesAndNewlines) }
    }
}

enum KanbanColumnID: String, CaseIterable, Identifiable {
    case todo
    case inProgress = "in-progress"
    case aiActive = "ai-active"
    case pendingReview = "pending-review"
    case done

    var id: String { rawValue }

    var title: String {
        switch self {
        case .todo: "To Do"
        case .inProgress: "In Progress"
        case .aiActive: "AI Active"
        case .pendingReview: "Review"
        case .done: "Done"
        }
    }

    var symbol: String {
        switch self {
        case .todo: "tray"
        case .inProgress: "arrow.triangle.2.circlepath"
        case .aiActive: "sparkles"
        case .pendingReview: "checklist"
        case .done: "checkmark.circle"
        }
    }
}

struct BackgroundTaskSummary: Decodable, Identifiable, Hashable {
    var id: String { taskID }
    let taskID: String
    let agent: String
    let runtime: String?
    let model: String?
    let prompt: String
    let status: String
    let createdAt: String?
    let completedAt: String?
    let error: String?
    let usedFallback: Bool?
    let actualRuntime: String?
    let actualModel: String?

    enum CodingKeys: String, CodingKey {
        case taskID = "task_id"
        case agent
        case runtime
        case model
        case prompt
        case status
        case createdAt = "created_at"
        case completedAt = "completed_at"
        case error
        case usedFallback = "used_fallback"
        case actualRuntime = "actual_runtime"
        case actualModel = "actual_model"
    }
}

struct BackgroundTaskDetail: Decodable, Identifiable {
    var id: String { taskID }
    let taskID: String
    let sessionID: String?
    let agent: String
    let runtime: String?
    let model: String?
    let prompt: String
    let status: String
    let createdAt: String?
    let completedAt: String?
    let recentOutput: [String]?
    let error: String?
    let toolCallCount: Int?

    enum CodingKeys: String, CodingKey {
        case taskID = "task_id"
        case sessionID = "session_id"
        case agent
        case runtime
        case model
        case prompt
        case status
        case createdAt = "created_at"
        case completedAt = "completed_at"
        case recentOutput = "recent_output"
        case error
        case toolCallCount = "tool_call_count"
    }
}

struct CreateBackgroundTaskRequest: Encodable {
    let prompt: String
    let agent: String?
    let runtime: String?
    let model: String?
    let timeout: Int?
    let notify: Bool?
    let permissionMode: String?

    enum CodingKeys: String, CodingKey {
        case prompt
        case agent
        case runtime
        case model
        case timeout
        case notify
        case permissionMode = "permission_mode"
    }
}

struct CreateBackgroundTaskResponse: Decodable {
    let taskID: String
    let sessionID: String?
    let agent: String
    let runtime: String?
    let model: String?
    let permissionMode: String?
    let status: String
    let queuePosition: Int?
    let timeout: Int?

    enum CodingKeys: String, CodingKey {
        case taskID = "task_id"
        case sessionID = "session_id"
        case agent
        case runtime
        case model
        case permissionMode = "permission_mode"
        case status
        case queuePosition = "queue_position"
        case timeout
    }
}

struct CreateSessionRequest: Encodable {
    let sessionID: String?
    let agent: String?
    let model: String?
    let runtime: String?

    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id"
        case agent
        case model
        case runtime
    }
}

struct CreateSessionResponse: Decodable {
    let sessionID: String
    let agent: String?
    let model: String?
    let runtime: String?

    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id"
        case agent
        case model
        case runtime
    }
}

struct ExecuteRequest: Encodable {
    let query: String
    let timeout: Int?
    let model: String?
    let runtime: String?
    let agent: String?
}

struct ExecuteResponse: Decodable {
    let sessionID: String
    let response: String
    let runtime: String?
    let model: String?

    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id"
        case response
        case runtime
        case model
    }
}

struct SessionStatusResponse: Decodable {
    let sessionID: String
    let agent: String?
    let runtime: String?
    let model: String?

    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id"
        case agent
        case runtime
        case model
    }
}

struct HistorySessionsResponse: Decodable {
    let sessions: [HistorySessionSummary]
}

struct HistorySessionSummary: Decodable, Identifiable, Hashable {
    var id: String { sessionID }

    let sessionID: String
    let title: String?
    let preview: String?
    let agent: String?
    let createdAt: Double?
    let updatedAt: Double?

    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id"
        case title
        case preview
        case agent
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }

    var displayTitle: String {
        let trimmed = (title ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? "Session \(sessionID)" : trimmed
    }

    var displayPreview: String {
        let trimmed = (preview ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? "No messages yet" : trimmed
    }
}

struct HistoryMessagesResponse: Decodable {
    let sessionID: String
    let messages: [HistoryMessage]
    let total: Int?
    let offset: Int?
    let limit: Int?

    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id"
        case messages
        case total
        case offset
        case limit
    }
}

struct HistoryMessage: Decodable, Hashable {
    let role: String
    let content: String
    let timestamp: Double?
}

struct ChatMessage: Identifiable, Hashable {
    enum Role: String {
        case user
        case assistant
        case system
    }

    let id = UUID()
    let role: Role
    let text: String
    let createdAt = Date()

    init(role: Role, text: String) {
        self.role = role
        self.text = text
    }

    init(historyMessage: HistoryMessage) {
        role = Role(rawValue: historyMessage.role) ?? .system
        text = historyMessage.content
    }
}
