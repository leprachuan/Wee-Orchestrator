import XCTest
@testable import WeeOrchestratorClient

/// In-memory fake of `AppStateAPI` so `AppState` can be tested without a
/// network connection or backend.
private final class FakeAppStateAPI: AppStateAPI {
    var health = HealthStatus(
        status: "ok",
        uptimeSeconds: 1,
        version: "test",
        environment: "test",
        agentsLoaded: 1,
        schedulerEnabled: true,
        activeSessions: 0
    )
    var agents: [Agent] = []
    var serviceStatus = ServiceStatusResponse(services: [:], node: "test-node", checkedAt: 0)
    var backgroundTasks: [BackgroundTaskSummary] = []
    var scheduledJobsResult: Result<[ScheduledJob], Error> = .success([])

    func fetchHealth() async throws -> HealthStatus { health }
    func fetchAgents() async throws -> [Agent] { agents }
    func fetchServiceStatus() async throws -> ServiceStatusResponse { serviceStatus }
    func fetchBackgroundTasks() async throws -> [BackgroundTaskSummary] { backgroundTasks }
    func fetchScheduledJobs() async throws -> [ScheduledJob] { try scheduledJobsResult.get() }

    func fetchBackgroundTask(taskId: String) async throws -> BackgroundTaskDetail {
        throw APIError.invalidResponse
    }

    func createBackgroundTask(_ body: CreateBackgroundTaskRequest) async throws -> CreateBackgroundTaskResponse {
        throw APIError.invalidResponse
    }
}

private struct FakeError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

@MainActor
final class AppStateTests: XCTestCase {
    /// Regression test for #366: a `403` from `/api/v1/scheduler/jobs` means
    /// the signed-in user isn't on the scheduler allowlist. That's expected
    /// (e.g. the simulator's session isn't allowlisted) and shouldn't be
    /// reported as an error — the Scheduled Tasks section is just empty.
    func testScheduledJobs403IsTreatedAsNoAccessNotAnError() async {
        let settings = SettingsStore()
        settings.useMockData = false
        let api = FakeAppStateAPI()
        api.scheduledJobsResult = .failure(APIError.http(status: 403, message: "Forbidden"))
        let state = AppState(settings: settings, client: api)

        await state.loadAll()

        XCTAssertTrue(state.scheduledJobs.isEmpty)
        XCTAssertNil(state.scheduledJobsErrorMessage)
    }

    /// Regression test for #366: the iOS client previously swallowed *every*
    /// failure from `/api/v1/scheduler/jobs` (network errors, decoding
    /// errors, 401s, 500s, ...) and silently showed an empty Scheduled Tasks
    /// list, indistinguishable from "no scheduled tasks exist". Non-403
    /// failures must surface via `scheduledJobsErrorMessage` so the UI can
    /// tell the user something actually went wrong.
    func testScheduledJobsOtherFailureSurfacesError() async {
        let settings = SettingsStore()
        settings.useMockData = false
        let api = FakeAppStateAPI()
        api.scheduledJobsResult = .failure(FakeError(message: "The network connection was lost."))
        let state = AppState(settings: settings, client: api)

        await state.loadAll()

        XCTAssertTrue(state.scheduledJobs.isEmpty)
        XCTAssertEqual(state.scheduledJobsErrorMessage, "The network connection was lost.")
    }

    /// A successful fetch populates `scheduledJobs` and clears any
    /// previously surfaced error.
    func testScheduledJobsSuccessPopulatesListAndClearsError() async {
        let settings = SettingsStore()
        settings.useMockData = false
        let api = FakeAppStateAPI()
        let job = ScheduledJob(
            id: "job_1",
            name: "Daily Report",
            agent: "wee-dev",
            runtime: "claude",
            model: "claude-sonnet-4-6",
            mode: "ai",
            task: "Generate a daily summary.",
            schedule: "every day at 9am",
            cron: "0 9 * * *",
            recurring: true,
            enabled: true,
            notify: true,
            createdAt: "2026-06-01T00:00:00Z",
            nextRun: "2026-06-15T09:00:00Z",
            lastRun: "2026-06-14T09:00:00Z",
            createdBy: nil
        )
        api.scheduledJobsResult = .success([job])
        let state = AppState(settings: settings, client: api)

        await state.loadAll()

        XCTAssertEqual(state.scheduledJobs, [job])
        XCTAssertNil(state.scheduledJobsErrorMessage)
    }
}
