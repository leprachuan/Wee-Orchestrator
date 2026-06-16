import Foundation

/// Sample data used when "Use Mock Data" is enabled in Settings, so the UI
/// is fully browsable without a configured backend.
enum MockData {
    static let health = HealthStatus(
        status: "ok",
        uptimeSeconds: 123456,
        version: "1.0.0",
        environment: "DEV",
        agentsLoaded: 4,
        schedulerEnabled: true,
        activeSessions: 2
    )

    static let agents: [Agent] = [
        Agent(name: "wee-dev", description: "Wee Orchestrator engineering agent — features, fixes, QA, docs.", path: "/opt/n8n-copilot-shim-dev", primaryRuntime: "claude", primaryModel: "claude-sonnet-4-6"),
        Agent(name: "orchestrator", description: "Routes requests and coordinates background tasks.", path: "/opt/n8n-copilot-shim", primaryRuntime: "claude", primaryModel: "claude-sonnet-4-6"),
        Agent(name: "research-dev", description: "Research and data-gathering agent.", path: "/opt/n8n-copilot-shim", primaryRuntime: "copilot", primaryModel: "claude-haiku-4.5"),
    ]

    static let serviceStatus = ServiceStatusResponse(
        services: [
            "telegram": ServiceInfo(service: "telegram-bot-listener-dev.service", status: "active", active: true, error: nil),
            "webex": ServiceInfo(service: "webex-connector-dev.service", status: "active", active: true, error: nil),
            "api": ServiceInfo(service: "agent-manager-api-dev.service", status: "active", active: true, error: nil),
            "scheduler": ServiceInfo(service: "task-scheduler-executor-dev.service", status: "inactive", active: false, error: nil),
        ],
        node: "wee-dev-host",
        checkedAt: Date().timeIntervalSince1970
    )

    static let backgroundTasks: [BackgroundTaskSummary] = [
        BackgroundTaskSummary(taskId: "bg_a1b2c3d4", agent: "wee-dev", runtime: "claude", model: "claude-sonnet-4-6", prompt: "Work on GitHub issue #362: Add iOS WebUI client starter.", status: "running", createdAt: ISO8601DateFormatter().string(from: Date()), completedAt: nil, error: nil),
        BackgroundTaskSummary(taskId: "bg_e5f6g7h8", agent: "research-dev", runtime: "copilot", model: "claude-haiku-4.5", prompt: "Get crude oil pricing stats", status: "completed", createdAt: ISO8601DateFormatter().string(from: Date().addingTimeInterval(-3600)), completedAt: ISO8601DateFormatter().string(from: Date().addingTimeInterval(-3500)), error: nil),
        BackgroundTaskSummary(taskId: "bg_i9j0k1l2", agent: "wee-dev", runtime: "claude", model: "claude-sonnet-4-6", prompt: "Investigate flaky test in test_issue_251_bg_tasks_rate_limit.py", status: "failed", createdAt: ISO8601DateFormatter().string(from: Date().addingTimeInterval(-7200)), completedAt: ISO8601DateFormatter().string(from: Date().addingTimeInterval(-7000)), error: "Timed out after 900s"),
    ]

    static let scheduledJobs: [ScheduledJob] = [
        ScheduledJob(
            id: "job_daily_report",
            name: "Daily Infrastructure Report",
            agent: "wee-dev",
            runtime: "claude",
            model: "claude-sonnet-4-6",
            mode: "ai",
            task: "Generate a daily summary of homelab health and open issues.",
            schedule: "every day at 9am",
            cron: "0 9 * * *",
            recurring: true,
            enabled: true,
            notify: true,
            createdAt: ISO8601DateFormatter().string(from: Date().addingTimeInterval(-86400 * 7)),
            nextRun: ISO8601DateFormatter().string(from: Date().addingTimeInterval(3600 * 5)),
            lastRun: ISO8601DateFormatter().string(from: Date().addingTimeInterval(-3600 * 19)),
            createdBy: ScheduledJobCreator(identity: "8193231291", channel: "telegram", username: "Foster")
        ),
        ScheduledJob(
            id: "job_ceph_check",
            name: "Ceph Health Check",
            agent: "wee-dev",
            runtime: "claude",
            model: nil,
            mode: "command",
            task: "ceph health",
            schedule: "every 6 hours",
            cron: "0 */6 * * *",
            recurring: true,
            enabled: false,
            notify: false,
            createdAt: ISO8601DateFormatter().string(from: Date().addingTimeInterval(-86400 * 2)),
            nextRun: nil,
            lastRun: ISO8601DateFormatter().string(from: Date().addingTimeInterval(-3600 * 2)),
            createdBy: nil
        ),
    ]

    static let chatSessions: [ChatSession] = [
        ChatSession(sessionId: "chat_a1b2c3d4", title: "Plan iOS chat controls", preview: "Add agent picker, new chat, and session list.", agent: "wee-dev", createdAt: Date().addingTimeInterval(-1800).timeIntervalSince1970, updatedAt: Date().addingTimeInterval(-60).timeIntervalSince1970),
        ChatSession(sessionId: "chat_e5f6g7h8", title: "Crude oil pricing stats", preview: "Here are the latest crude oil benchmarks…", agent: "research-dev", createdAt: Date().addingTimeInterval(-7200).timeIntervalSince1970, updatedAt: Date().addingTimeInterval(-7000).timeIntervalSince1970),
        ChatSession(sessionId: "chat_i9j0k1l2", title: nil, preview: "Sure, I can help with that.", agent: "orchestrator", createdAt: Date().addingTimeInterval(-86400).timeIntervalSince1970, updatedAt: Date().addingTimeInterval(-86000).timeIntervalSince1970),
    ]

    static func chatMessages(for sessionId: String) -> [ChatMessage] {
        guard let session = chatSessions.first(where: { $0.sessionId == sessionId }) else {
            return []
        }
        return [
            ChatMessage(role: "user", content: session.title ?? "Hello!", timestamp: session.createdAt ?? Date().timeIntervalSince1970),
            ChatMessage(role: "assistant", content: session.preview ?? "Mock response — connect a real backend in Settings to chat for real.", timestamp: session.updatedAt ?? Date().timeIntervalSince1970),
        ]
    }

    static func backgroundTaskDetail(for summary: BackgroundTaskSummary) -> BackgroundTaskDetail {
        BackgroundTaskDetail(
            taskId: summary.taskId,
            sessionId: UUID().uuidString,
            agent: summary.agent,
            runtime: summary.runtime,
            model: summary.model,
            prompt: summary.prompt,
            status: summary.status,
            createdAt: summary.createdAt,
            completedAt: summary.completedAt,
            recentOutput: [
                "[STATUS_UPDATE: Reading issue details...]",
                "[STATUS_UPDATE: Implementing changes...]",
                "Mock output — connect a real backend in Settings to see live data.",
            ],
            error: summary.error
        )
    }
}
