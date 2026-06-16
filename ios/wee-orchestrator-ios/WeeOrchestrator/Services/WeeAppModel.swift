import Foundation
import Observation
import UserNotifications

@MainActor
@Observable
final class WeeAppModel {
    var configuration: APIConfiguration
    var health: HealthResponse?
    var appConfig: AppConfigResponse?
    var agents: [AgentSummary] = []
    var tasks: [BackgroundTaskSummary] = []
    var scheduledJobs: [ScheduledJobSummary] = []
    var schedulerStatusMessage: String?
    var kanbanBoard: KanbanBoardResponse?
    var kanbanStatusMessage: String?
    var selectedTask: BackgroundTaskDetail?
    var historySessions: [HistorySessionSummary] = []
    var chatMessages: [ChatMessage] = [
        ChatMessage(role: .system, text: "Wee iOS client ready.")
    ]
    var selectedAgent: String = "orchestrator"
    var currentSessionID: String?
    var isLoading = false
    var errorMessage: String?
    var lastRefresh: Date?
    var authPairingIdentity: String?
    var authStatusMessage: String?

    private let defaults = UserDefaults.standard

    init() {
        let storedToken = KeychainStore.loadToken()
        configuration = APIConfiguration(
            baseURLString: defaults.string(forKey: "wee.baseURL") ?? APIConfiguration.defaults.baseURLString,
            token: storedToken.isEmpty ? Self.launchToken : storedToken,
            identity: defaults.string(forKey: "wee.identity") ?? APIConfiguration.defaults.identity,
            channel: defaults.string(forKey: "wee.channel") ?? APIConfiguration.defaults.channel,
            allowInsecureTLS: defaults.object(forKey: "wee.allowInsecureTLS") as? Bool ?? APIConfiguration.defaults.allowInsecureTLS
        )
        selectedAgent = defaults.string(forKey: "wee.selectedAgent") ?? "orchestrator"
    }

    var client: WeeAPIClient {
        WeeAPIClient(configuration: configuration)
    }

    var isAuthenticated: Bool {
        hasAuthToken
    }

    func bootstrap() async {
        await refreshAll(useMockOnFailure: true)
    }

    func saveConfiguration() {
        defaults.set(configuration.baseURLString, forKey: "wee.baseURL")
        defaults.set(configuration.identity, forKey: "wee.identity")
        defaults.set(configuration.channel, forKey: "wee.channel")
        defaults.set(configuration.allowInsecureTLS, forKey: "wee.allowInsecureTLS")
        defaults.set(selectedAgent, forKey: "wee.selectedAgent")
        KeychainStore.saveToken(configuration.token)
    }

    func requestTelegramPairing(identity: String) async {
        let trimmed = normalizeIdentity(identity)
        guard !trimmed.isEmpty else {
            authStatusMessage = "Enter your Telegram username first."
            return
        }

        isLoading = true
        errorMessage = nil
        authStatusMessage = nil
        defer { isLoading = false }

        do {
            let response = try await client.requestPairing(identity: trimmed, channel: "telegram")
            let resolved = response.identityResolved ?? trimmed.replacingOccurrences(of: "@", with: "")
            authPairingIdentity = resolved
            configuration.identity = resolved
            configuration.channel = "telegram"
            defaults.set(resolved, forKey: "wee.identity")
            defaults.set("telegram", forKey: "wee.channel")
            if let expires = response.expiresIn {
                authStatusMessage = "Code sent in Telegram. It expires in \(expires / 60) minutes."
            } else {
                authStatusMessage = response.message
            }
        } catch {
            errorMessage = error.localizedDescription
            authStatusMessage = error.localizedDescription
        }
    }

    func verifyTelegramPairing(code: String) async {
        let trimmed = code.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.isEmpty == false else {
            authStatusMessage = "Enter the 6-digit pairing code."
            return
        }
        guard let identity = authPairingIdentity ?? optionalIdentity else {
            authStatusMessage = "Send a pairing code first."
            return
        }

        isLoading = true
        errorMessage = nil
        authStatusMessage = nil
        defer { isLoading = false }

        do {
            let response = try await client.verifyPairing(identity: identity, code: trimmed)
            configuration.token = response.token
            configuration.identity = response.identity
            configuration.channel = response.channel
            authPairingIdentity = nil
            saveConfiguration()
            currentSessionID = nil
            authStatusMessage = "Signed in with Telegram."
            await refreshAll()
        } catch {
            errorMessage = error.localizedDescription
            authStatusMessage = error.localizedDescription
        }
    }

    func signOut() {
        configuration.token = ""
        currentSessionID = nil
        authPairingIdentity = nil
        saveConfiguration()
        tasks = []
        scheduledJobs = []
        schedulerStatusMessage = nil
        kanbanBoard = nil
        kanbanStatusMessage = nil
        historySessions = []
        chatMessages = [ChatMessage(role: .system, text: "Signed out.")]
        authStatusMessage = "Signed out."
    }

    func refreshAll(useMockOnFailure: Bool = false) async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            async let healthResponse = client.health()
            async let configResponse = client.appConfig()
            async let agentResponse = client.agents()

            health = try await healthResponse
            appConfig = try await configResponse
            agents = try await agentResponse
            if agents.contains(where: { $0.name == selectedAgent }) == false {
                selectedAgent = agents.first?.name ?? "orchestrator"
            }

            if !configuration.token.isEmpty {
                do {
                    async let taskResponse = client.backgroundTasks()
                    async let sessionResponse = client.historySessions()
                    tasks = try await taskResponse
                    historySessions = try await sessionResponse
                } catch {
                    tasks = []
                    historySessions = []
                }

                await loadScheduledJobs()
                await loadKanbanBoard()
            }

            lastRefresh = Date()
        } catch {
            errorMessage = error.localizedDescription
            if useMockOnFailure {
                installMockData()
            }
        }
    }

    func sendChat(_ prompt: String) async {
        let trimmed = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }

        guard hasAuthToken else {
            let message = "Authentication required. Add a bearer token in Settings, then tap Save."
            errorMessage = message
            chatMessages.append(ChatMessage(role: .system, text: message))
            return
        }

        chatMessages.append(ChatMessage(role: .user, text: trimmed))
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            let sessionID: String
            if let currentSessionID {
                sessionID = currentSessionID
            } else {
                let session = try await client.createSession(agent: selectedAgent)
                currentSessionID = session.sessionID
                sessionID = session.sessionID
            }
            let response = try await client.execute(sessionID: sessionID, query: trimmed, agent: selectedAgent)
            chatMessages.append(ChatMessage(role: .assistant, text: response.response))
            await loadHistorySessions()
        } catch {
            let message = handleAuthErrorIfNeeded(error) ?? error.localizedDescription
            errorMessage = message
            chatMessages.append(ChatMessage(role: .system, text: message))
        }
    }

    func startNewChat() async {
        guard hasAuthToken else {
            let message = "Authentication required. Sign in with Telegram in Settings."
            errorMessage = message
            chatMessages = [ChatMessage(role: .system, text: message)]
            return
        }

        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            let session = try await client.createSession(agent: selectedAgent)
            currentSessionID = session.sessionID
            if let agent = session.agent, agent.isEmpty == false {
                selectedAgent = agent
            }
            saveConfiguration()
            chatMessages = [ChatMessage(role: .system, text: "New chat ready.")]
            await loadHistorySessions()
        } catch {
            let message = handleAuthErrorIfNeeded(error) ?? error.localizedDescription
            errorMessage = message
            chatMessages.append(ChatMessage(role: .system, text: message))
        }
    }

    func loadHistorySessions() async {
        guard hasAuthToken else {
            historySessions = []
            return
        }

        do {
            historySessions = try await client.historySessions()
        } catch {
            historySessions = []
        }
    }

    func selectHistorySession(_ session: HistorySessionSummary) async {
        guard hasAuthToken else { return }

        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            let response = try await client.historyMessages(sessionID: session.sessionID)
            currentSessionID = session.sessionID
            if let agent = session.agent, agent.isEmpty == false {
                selectedAgent = agent
                saveConfiguration()
            }
            chatMessages = response.messages.map(ChatMessage.init(historyMessage:))
            if chatMessages.isEmpty {
                chatMessages = [ChatMessage(role: .system, text: "This chat has no messages yet.")]
            }
            await refreshSessionStatus(sessionID: session.sessionID)
        } catch {
            let message = handleAuthErrorIfNeeded(error) ?? error.localizedDescription
            errorMessage = message
            chatMessages.append(ChatMessage(role: .system, text: message))
        }
    }

    func changeAgent(to agent: String) async {
        guard agent != selectedAgent else { return }
        guard agents.contains(where: { $0.name == agent }) else { return }

        if currentSessionID == nil {
            selectedAgent = agent
            saveConfiguration()
            chatMessages.append(ChatMessage(role: .system, text: "Next chat will use \(agent)."))
            return
        }

        guard hasAuthToken else {
            selectedAgent = agent
            saveConfiguration()
            return
        }

        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            let sessionID = currentSessionID ?? ""
            let response = try await client.execute(sessionID: sessionID, query: "/agent set \(agent)", agent: nil)
            selectedAgent = agent
            saveConfiguration()
            chatMessages.append(ChatMessage(role: .system, text: response.response))
            await refreshSessionStatus(sessionID: sessionID)
            await loadHistorySessions()
        } catch {
            let message = handleAuthErrorIfNeeded(error) ?? error.localizedDescription
            errorMessage = message
            chatMessages.append(ChatMessage(role: .system, text: message))
        }
    }

    func createBackgroundTask(prompt: String) async {
        let trimmed = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }

        guard hasAuthToken else {
            errorMessage = "Authentication required. Add a bearer token in Settings, then tap Save."
            return
        }

        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            _ = try await client.createBackgroundTask(prompt: trimmed, agent: selectedAgent)
            tasks = try await client.backgroundTasks()
            await loadScheduledJobs()
            lastRefresh = Date()
        } catch {
            errorMessage = handleAuthErrorIfNeeded(error) ?? error.localizedDescription
        }
    }

    func loadScheduledJobs() async {
        guard hasAuthToken else {
            scheduledJobs = []
            schedulerStatusMessage = "Sign in to view scheduled jobs."
            return
        }

        do {
            scheduledJobs = try await client.scheduledJobs()
            schedulerStatusMessage = scheduledJobs.isEmpty ? "No scheduled jobs returned by the backend." : nil
        } catch {
            scheduledJobs = []
            schedulerStatusMessage = handleAuthErrorIfNeeded(error) ?? error.localizedDescription
        }
    }

    func loadKanbanBoard() async {
        guard hasAuthToken else {
            kanbanBoard = nil
            kanbanStatusMessage = "Sign in to view the Kanban board."
            return
        }

        do {
            let board = try await client.kanbanBoard()
            kanbanBoard = board
            kanbanStatusMessage = board.total == 0 ? "No Kanban cards returned by the backend." : nil
            await scheduleDueNotifications(for: board.dueCards)
        } catch {
            kanbanBoard = nil
            kanbanStatusMessage = handleAuthErrorIfNeeded(error) ?? error.localizedDescription
        }
    }

    func loadKanbanItem(id: String) async -> KanbanItemDetail? {
        do {
            return try await client.kanbanItem(id: id)
        } catch {
            errorMessage = handleAuthErrorIfNeeded(error) ?? error.localizedDescription
            return nil
        }
    }

    func updateKanbanItem(
        id: String,
        title: String,
        details: String,
        status: String,
        agent: String,
        due: String,
        priority: String,
        urgency: String
    ) async -> KanbanItemDetail? {
        do {
            let item = try await client.updateKanbanItem(
                id: id,
                title: nilIfEmpty(title),
                details: details,
                status: nilIfEmpty(status),
                agent: agent.trimmingCharacters(in: .whitespacesAndNewlines),
                due: due.trimmingCharacters(in: .whitespacesAndNewlines),
                priority: nilIfEmpty(priority),
                urgency: nilIfEmpty(urgency)
            )
            await loadKanbanBoard()
            return item
        } catch {
            errorMessage = handleAuthErrorIfNeeded(error) ?? error.localizedDescription
            return nil
        }
    }

    func commentKanbanItem(id: String, body: String) async -> KanbanItemDetail? {
        do {
            let item = try await client.commentKanbanItem(id: id, body: body)
            await loadKanbanBoard()
            return item
        } catch {
            errorMessage = handleAuthErrorIfNeeded(error) ?? error.localizedDescription
            return nil
        }
    }

    func completeKanbanItem(id: String) async -> KanbanItemDetail? {
        do {
            let item = try await client.completeKanbanItem(id: id)
            await loadKanbanBoard()
            return item
        } catch {
            errorMessage = handleAuthErrorIfNeeded(error) ?? error.localizedDescription
            return nil
        }
    }

    func closeKanbanItem(id: String) async -> KanbanItemDetail? {
        do {
            let item = try await client.closeKanbanItem(id: id)
            await loadKanbanBoard()
            return item
        } catch {
            errorMessage = handleAuthErrorIfNeeded(error) ?? error.localizedDescription
            return nil
        }
    }

    func dispatchKanbanItem(id: String, agent: String, prompt: String) async -> KanbanDispatchResponse? {
        do {
            let response = try await client.dispatchKanbanItem(
                id: id,
                agent: agent,
                prompt: nilIfEmpty(prompt)
            )
            await loadKanbanBoard()
            return response
        } catch {
            errorMessage = handleAuthErrorIfNeeded(error) ?? error.localizedDescription
            return nil
        }
    }

    func loadTaskDetail(_ task: BackgroundTaskSummary) async {
        isLoading = true
        defer { isLoading = false }

        do {
            selectedTask = try await client.backgroundTask(id: task.taskID)
        } catch {
            errorMessage = handleAuthErrorIfNeeded(error) ?? error.localizedDescription
        }
    }

    func installMockData() {
        health = HealthResponse(
            status: "offline",
            uptimeSeconds: nil,
            version: "mock",
            environment: "LOCAL",
            agentsLoaded: 6,
            schedulerEnabled: true,
            activeSessions: 0
        )
        appConfig = AppConfigResponse(schedulerEnabled: true, backgroundTasksEnabled: true, appEnv: "LOCAL")
        agents = [
            AgentSummary(name: "orchestrator", description: "Main routing and coordination agent.", path: nil, primaryRuntime: "claude", primaryModel: "haiku"),
            AgentSummary(name: "wee-dev", description: "Wee Orchestrator feature development and validation.", path: nil, primaryRuntime: "claude", primaryModel: "sonnet"),
            AgentSummary(name: "wee-qa", description: "Independent QA validation for orchestrator changes.", path: nil, primaryRuntime: "codex", primaryModel: "gpt-5.5")
        ]
        tasks = [
            BackgroundTaskSummary(taskID: "bg_mock1", agent: "wee-dev", runtime: "claude", model: "sonnet", prompt: "Prepare iOS WebUI starter", status: "running", createdAt: "local preview", completedAt: nil, error: nil, usedFallback: false, actualRuntime: nil, actualModel: nil),
            BackgroundTaskSummary(taskID: "bg_mock2", agent: "wee-qa", runtime: "codex", model: "gpt-5.5", prompt: "Review mobile layout", status: "queued", createdAt: "local preview", completedAt: nil, error: nil, usedFallback: false, actualRuntime: nil, actualModel: nil)
        ]
        scheduledJobs = [
            ScheduledJobSummary(id: "mock-heartbeat", name: "Heartbeat", agent: "orchestrator", runtime: "wee", model: nil, mode: "ai", permissionMode: "restricted", task: "Check runtime health", schedule: "every hour", cron: "0 * * * *", workingDir: nil, notify: true, fallbackRuntime: nil, fallbackModel: nil, recurring: true, createdAt: "local preview", nextRun: "local preview", lastRun: nil, enabled: true, retries: 0, timeout: 300)
        ]
        schedulerStatusMessage = nil
        kanbanBoard = KanbanBoardResponse(
            success: true,
            columns: [
                "todo": [
                    KanbanCard(id: "mock:1", title: "Plan family dashboard", source: "flatfile", status: "todo", agent: "family_knowledge", priority: "normal", urgency: "normal", due: nil, dueBucket: "none", isOverdue: false, labels: ["agent:family_knowledge"], details: "Mock card for local preview.", url: nil, createdAt: nil, updatedAt: nil, githubIssueNumber: nil)
                ],
                "ai-active": [
                    KanbanCard(id: "mock:2", title: "Build iOS Kanban view", source: "github", status: "ai-active", agent: "wee-dev", priority: "high", urgency: "urgent", due: "2026-06-14", dueBucket: "today", isOverdue: false, labels: ["agent:wee-dev", "urgent"], details: "Mock active card.", url: nil, createdAt: nil, updatedAt: nil, githubIssueNumber: 367)
                ],
                "done": [],
                "in-progress": [],
                "pending-review": []
            ],
            agents: ["family_knowledge", "wee-dev"],
            sources: ["flatfile", "github"],
            total: 2,
            repo: "leprachuan/Wee-Orchestrator"
        )
        kanbanStatusMessage = nil
        historySessions = [
            HistorySessionSummary(sessionID: "mock-chat", title: "Mock orchestration chat", preview: "Local preview session", agent: "orchestrator", createdAt: nil, updatedAt: nil)
        ]
        lastRefresh = Date()
    }

    private func nilIfEmpty(_ value: String) -> String? {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    private func refreshSessionStatus(sessionID: String) async {
        do {
            let status = try await client.sessionStatus(sessionID: sessionID)
            if let agent = status.agent, agent.isEmpty == false {
                selectedAgent = agent
                saveConfiguration()
            }
        } catch {
            // History selection should still succeed if status metadata is temporarily unavailable.
        }
    }

    private var hasAuthToken: Bool {
        configuration.token.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false
    }

    private var optionalIdentity: String? {
        let identity = configuration.identity.trimmingCharacters(in: .whitespacesAndNewlines)
        return identity.isEmpty ? nil : identity
    }

    private func normalizeIdentity(_ identity: String) -> String {
        identity.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func handleAuthErrorIfNeeded(_ error: Error) -> String? {
        guard case WeeAPIError.httpStatus(401, _) = error else {
            return nil
        }

        let message = "Session expired. Sign in with Telegram in Settings."
        configuration.token = ""
        currentSessionID = nil
        authPairingIdentity = nil
        authStatusMessage = message
        KeychainStore.saveToken("")
        return message
    }

    private func scheduleDueNotifications(for cards: [KanbanCard]) async {
        guard cards.isEmpty == false else {
            UNUserNotificationCenter.current().removePendingNotificationRequests(withIdentifiers: pendingKanbanNotificationIDs())
            defaults.set([], forKey: "wee.kanbanNotificationIDs")
            return
        }

        let center = UNUserNotificationCenter.current()
        do {
            let granted = try await center.requestAuthorization(options: [.alert, .badge, .sound])
            guard granted else { return }
        } catch {
            return
        }

        center.removePendingNotificationRequests(withIdentifiers: pendingKanbanNotificationIDs())
        var identifiers: [String] = []

        for card in cards {
            guard let triggerDate = notificationDate(for: card) else { continue }
            let identifier = "wee.kanban.\(card.id)"
            let content = UNMutableNotificationContent()
            content.title = notificationTitle(for: card)
            content.body = card.title
            content.sound = .default
            let trigger = UNCalendarNotificationTrigger(
                dateMatching: Calendar.current.dateComponents([.year, .month, .day, .hour, .minute], from: triggerDate),
                repeats: false
            )
            do {
                try await center.add(UNNotificationRequest(identifier: identifier, content: content, trigger: trigger))
                identifiers.append(identifier)
            } catch {
                continue
            }
        }

        defaults.set(identifiers, forKey: "wee.kanbanNotificationIDs")
    }

    private func pendingKanbanNotificationIDs() -> [String] {
        defaults.stringArray(forKey: "wee.kanbanNotificationIDs") ?? []
    }

    private func notificationDate(for card: KanbanCard) -> Date? {
        guard let due = card.due.flatMap(Self.parseBackendDate) else {
            return nil
        }

        let now = Date()
        if due <= now {
            return now.addingTimeInterval(60)
        }
        return due
    }

    private func notificationTitle(for card: KanbanCard) -> String {
        switch card.dueBucket {
        case "overdue": "Overdue TODO"
        case "today": "TODO due today"
        case "soon": "TODO due soon"
        default: "TODO due"
        }
    }

    private static func parseBackendDate(_ value: String) -> Date? {
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = iso.date(from: value) {
            return date
        }

        iso.formatOptions = [.withInternetDateTime]
        if let date = iso.date(from: value) {
            return date
        }

        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone.current
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter.date(from: String(value.prefix(10)))
    }

    private static var launchToken: String {
        let environment = ProcessInfo.processInfo.environment
        let token = (environment["WEE_API_TOKEN"] ?? environment["WEE_AUTH_TOKEN"] ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if token.lowercased().hasPrefix("bearer ") {
            return String(token.dropFirst(7)).trimmingCharacters(in: .whitespacesAndNewlines)
        }
        return token
    }
}
