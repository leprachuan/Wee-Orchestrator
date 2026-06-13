import SwiftUI

struct BackgroundTasksView: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 12) {
                if appState.backgroundTasks.isEmpty && appState.isLoading {
                    ProgressView()
                        .frame(maxWidth: .infinity)
                        .padding(.top, 40)
                } else if appState.backgroundTasks.isEmpty {
                    Text("No background tasks yet.")
                        .foregroundStyle(WeeTheme.textMuted)
                        .padding(.top, 40)
                } else {
                    ForEach(appState.backgroundTasks) { task in
                        NavigationLink {
                            BackgroundTaskDetailView(summary: task)
                        } label: {
                            taskRow(task)
                        }
                        .buttonStyle(.plain)
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
}
