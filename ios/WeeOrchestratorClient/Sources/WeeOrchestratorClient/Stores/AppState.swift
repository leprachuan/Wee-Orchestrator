import Foundation
import Combine

/// Central observable store for data fetched from the Wee Orchestrator API.
///
/// When `settings.useMockData` is true, every load* method populates state
/// from `MockData` instead of making a network call — this lets the app run
/// fully offline during early development.
@MainActor
final class AppState: ObservableObject {
    @Published var health: HealthStatus?
    @Published var agents: [Agent] = []
    @Published var serviceStatus: ServiceStatusResponse?
    @Published var backgroundTasks: [BackgroundTaskSummary] = []
    @Published var scheduledJobs: [ScheduledJob] = []
    @Published var scheduledJobsErrorMessage: String?

    @Published var isLoading = false
    @Published var errorMessage: String?

    let settings: SettingsStore
    private var client: AppStateAPI

    init(settings: SettingsStore, client: AppStateAPI? = nil) {
        self.settings = settings
        self.client = client ?? APIClient(settings: settings)
    }

    /// Rebuilds the API client, e.g. after Settings change.
    func refreshClient() {
        client = APIClient(settings: settings)
    }

    func loadAll() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        if settings.useMockData {
            health = MockData.health
            agents = MockData.agents
            serviceStatus = MockData.serviceStatus
            backgroundTasks = MockData.backgroundTasks
            scheduledJobs = MockData.scheduledJobs
            return
        }

        do {
            health = try await client.fetchHealth()
            agents = try await client.fetchAgents()
            serviceStatus = try await client.fetchServiceStatus()
            backgroundTasks = try await client.fetchBackgroundTasks()
        } catch {
            errorMessage = error.localizedDescription
        }

        // Scheduled jobs are fetched separately: users without scheduler
        // access get a 403 here, which is expected and shouldn't block the
        // rest of the page. Any other failure (network error, decoding
        // error, 401, 500, ...) is a real problem and is surfaced via
        // `scheduledJobsErrorMessage` instead of silently looking like
        // "no scheduled tasks".
        do {
            scheduledJobs = try await client.fetchScheduledJobs()
            scheduledJobsErrorMessage = nil
        } catch APIError.http(403, _) {
            scheduledJobs = []
            scheduledJobsErrorMessage = nil
        } catch {
            scheduledJobs = []
            scheduledJobsErrorMessage = error.localizedDescription
        }
    }

    func fetchTaskDetail(taskId: String) async throws -> BackgroundTaskDetail {
        if settings.useMockData {
            guard let summary = backgroundTasks.first(where: { $0.taskId == taskId }) else {
                throw APIError.invalidResponse
            }
            return MockData.backgroundTaskDetail(for: summary)
        }
        return try await client.fetchBackgroundTask(taskId: taskId)
    }

    func createBackgroundTask(prompt: String, agent: String?, runtime: String?, model: String?, timeout: Int?) async throws -> CreateBackgroundTaskResponse {
        let body = CreateBackgroundTaskRequest(prompt: prompt, agent: agent, runtime: runtime, model: model, timeout: timeout)

        if settings.useMockData {
            let response = CreateBackgroundTaskResponse(
                taskId: "bg_\(UUID().uuidString.prefix(8))",
                sessionId: UUID().uuidString,
                agent: agent ?? "orchestrator",
                runtime: runtime ?? "claude",
                model: model ?? "claude-sonnet-4-6",
                status: "running"
            )
            let summary = BackgroundTaskSummary(
                taskId: response.taskId,
                agent: response.agent,
                runtime: response.runtime,
                model: response.model,
                prompt: prompt,
                status: "running",
                createdAt: ISO8601DateFormatter().string(from: Date()),
                completedAt: nil,
                error: nil
            )
            backgroundTasks.insert(summary, at: 0)
            return response
        }

        let response = try await client.createBackgroundTask(body)
        await loadAll()
        return response
    }
}
