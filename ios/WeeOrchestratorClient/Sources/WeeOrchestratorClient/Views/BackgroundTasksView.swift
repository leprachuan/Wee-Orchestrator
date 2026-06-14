import SwiftUI

struct BackgroundTasksView: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 12) {
                if appState.backgroundTasks.isEmpty && appState.scheduledJobs.isEmpty && appState.isLoading {
                    ProgressView()
                        .frame(maxWidth: .infinity)
                        .padding(.top, 40)
                } else {
                    if !appState.scheduledJobs.isEmpty {
                        sectionHeader("Scheduled Tasks")
                        ForEach(appState.scheduledJobs) { job in
                            scheduledJobRow(job)
                        }
                    }

                    if !appState.backgroundTasks.isEmpty {
                        sectionHeader("Background Tasks")
                        ForEach(appState.backgroundTasks) { task in
                            NavigationLink {
                                BackgroundTaskDetailView(summary: task)
                            } label: {
                                taskRow(task)
                            }
                            .buttonStyle(.plain)
                        }
                    }

                    if appState.backgroundTasks.isEmpty && appState.scheduledJobs.isEmpty {
                        Text("No tasks yet.")
                            .foregroundStyle(WeeTheme.textMuted)
                            .padding(.top, 40)
                    }
                }
            }
            .padding(16)
        }
        .navigationTitle("Tasks")
        .weeBackground()
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                NavigationLink {
                    NewTaskView()
                } label: {
                    Image(systemName: "plus")
                }
            }
        }
        .refreshable {
            await appState.loadAll()
        }
    }

    private func sectionHeader(_ title: String) -> some View {
        Text(title)
            .font(.headline)
            .foregroundStyle(WeeTheme.textPrimary)
            .padding(.top, 4)
    }

    private func taskRow(_ task: BackgroundTaskSummary) -> some View {
        GlassCard {
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Text(task.taskId)
                        .font(.subheadline.monospaced())
                        .foregroundStyle(WeeTheme.textPrimary)
                    Spacer()
                    statusBadge(task.status)
                }

                Text(task.prompt)
                    .font(.subheadline)
                    .foregroundStyle(WeeTheme.textSecondary)
                    .lineLimit(2)

                HStack(spacing: 12) {
                    Text(task.agent)
                        .font(.caption)
                        .foregroundStyle(WeeTheme.textMuted)
                    Text("\(task.runtime) / \(task.model)")
                        .font(.caption)
                        .foregroundStyle(WeeTheme.textMuted)
                }
            }
        }
    }

    private func scheduledJobRow(_ job: ScheduledJob) -> some View {
        GlassCard {
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Text(job.name)
                        .font(.subheadline.bold())
                        .foregroundStyle(WeeTheme.textPrimary)
                    Spacer()
                    enabledBadge(job.enabled)
                }

                if let task = job.task, !task.isEmpty {
                    Text(task)
                        .font(.subheadline)
                        .foregroundStyle(WeeTheme.textSecondary)
                        .lineLimit(2)
                }

                HStack(spacing: 12) {
                    if let agent = job.agent {
                        Text(agent)
                            .font(.caption)
                            .foregroundStyle(WeeTheme.textMuted)
                    }
                    if let schedule = job.schedule {
                        Text(schedule)
                            .font(.caption)
                            .foregroundStyle(WeeTheme.textMuted)
                    }
                }

                if let nextRun = formattedDate(job.nextRun) {
                    Text("Next run: \(nextRun)")
                        .font(.caption)
                        .foregroundStyle(WeeTheme.accent)
                } else if let lastRun = formattedDate(job.lastRun) {
                    Text("Last run: \(lastRun)")
                        .font(.caption)
                        .foregroundStyle(WeeTheme.textMuted)
                }
            }
        }
    }

    private func statusBadge(_ status: String) -> some View {
        let color: Color
        switch status {
        case "running", "queued":
            color = WeeTheme.accent
        case "failed":
            color = WeeTheme.danger
        default:
            color = WeeTheme.textSecondary
        }
        return Text(status.capitalized)
            .font(.caption.bold())
            .padding(.horizontal, 8)
            .padding(.vertical, 2)
            .foregroundStyle(color)
            .background(color.opacity(0.15))
            .clipShape(Capsule())
    }

    private func enabledBadge(_ enabled: Bool?) -> some View {
        let isEnabled = enabled ?? false
        let color = isEnabled ? WeeTheme.accent : WeeTheme.textSecondary
        return Text(isEnabled ? "Enabled" : "Paused")
            .font(.caption.bold())
            .padding(.horizontal, 8)
            .padding(.vertical, 2)
            .foregroundStyle(color)
            .background(color.opacity(0.15))
            .clipShape(Capsule())
    }

    private func formattedDate(_ isoString: String?) -> String? {
        guard let isoString else { return nil }
        if let date = ISO8601DateFormatter().date(from: isoString) {
            let formatter = DateFormatter()
            formatter.dateStyle = .medium
            formatter.timeStyle = .short
            return formatter.string(from: date)
        }
        return isoString
    }
}
