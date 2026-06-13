import SwiftUI

struct BackgroundTaskDetailView: View {
    let summary: BackgroundTaskSummary

    @EnvironmentObject private var appState: AppState
    @State private var detail: BackgroundTaskDetail?
    @State private var errorMessage: String?
    @State private var isLoading = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                GlassCard {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(summary.taskId)
                            .font(.headline.monospaced())
                            .foregroundStyle(WeeTheme.textPrimary)
                        Text(summary.prompt)
                            .font(.subheadline)
                            .foregroundStyle(WeeTheme.textSecondary)

                        Divider().overlay(WeeTheme.glassBorder)

                        infoRow("Agent", summary.agent)
                        infoRow("Runtime", summary.runtime)
                        infoRow("Model", summary.model)
                        infoRow("Status", (detail?.status ?? summary.status).capitalized)
                        infoRow("Created", summary.createdAt)
                        if let completed = (detail?.completedAt ?? summary.completedAt) {
                            infoRow("Completed", completed)
                        }
                        if let error = (detail?.error ?? summary.error) {
                            infoRow("Error", error)
                        }
                    }
                }

                GlassCard {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Recent Output")
                            .font(.headline)
                            .foregroundStyle(WeeTheme.textPrimary)

                        if isLoading {
                            ProgressView()
                        } else if let errorMessage {
                            Text(errorMessage)
                                .font(.footnote)
                                .foregroundStyle(WeeTheme.danger)
                        } else if let lines = detail?.recentOutput, !lines.isEmpty {
                            ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                                Text(line)
                                    .font(.caption.monospaced())
                                    .foregroundStyle(WeeTheme.textSecondary)
                            }
                        } else {
                            Text("No output yet.")
                                .foregroundStyle(WeeTheme.textMuted)
                        }
                    }
                }
            }
            .padding(16)
        }
        .navigationTitle(summary.taskId)
        .navigationBarTitleDisplayMode(.inline)
        .weeBackground()
        .task {
            await loadDetail()
        }
        .refreshable {
            await loadDetail()
        }
    }

    private func infoRow(_ label: String, _ value: String) -> some View {
        HStack(alignment: .top) {
            Text(label)
                .foregroundStyle(WeeTheme.textSecondary)
            Spacer()
            Text(value)
                .foregroundStyle(WeeTheme.textPrimary)
                .multilineTextAlignment(.trailing)
        }
        .font(.subheadline)
    }

    private func loadDetail() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            detail = try await appState.fetchTaskDetail(taskId: summary.taskId)
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
