import SwiftUI

struct DashboardView: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if appState.settings.useMockData {
                    mockBanner
                }

                if let errorMessage = appState.errorMessage {
                    errorBanner(errorMessage)
                }

                healthCard
                serviceStatusCard

                NavigationLink {
                    NewTaskView()
                } label: {
                    HStack {
                        Image(systemName: "plus.circle.fill")
                        Text("New Background Task")
                            .fontWeight(.semibold)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 12)
                    .background(WeeTheme.accent)
                    .foregroundStyle(.black)
                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                }
            }
            .padding(16)
        }
        .navigationTitle("Dashboard")
        .weeBackground()
        .refreshable {
            await appState.loadAll()
        }
    }

    private var mockBanner: some View {
        Text("Showing mock data — configure a backend in Settings to connect live.")
            .font(.footnote)
            .foregroundStyle(WeeTheme.textSecondary)
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(WeeTheme.surfaceRaised)
            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
    }

    private func errorBanner(_ message: String) -> some View {
        Text(message)
            .font(.footnote)
            .foregroundStyle(WeeTheme.danger)
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(WeeTheme.danger.opacity(0.12))
            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
    }

    private var healthCard: some View {
        GlassCard {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Text("Health")
                        .font(.headline)
                        .foregroundStyle(WeeTheme.textPrimary)
                    Spacer()
                    if let health = appState.health {
                        StatusDot(isActive: health.status == "ok")
                        Text(health.status.uppercased())
                            .font(.caption.bold())
                            .foregroundStyle(health.status == "ok" ? WeeTheme.accent : WeeTheme.danger)
                    }
                }

                if let health = appState.health {
                    statRow("Environment", health.environment)
                    statRow("Version", health.version)
                    statRow("Agents loaded", "\(health.agentsLoaded)")
                    statRow("Active sessions", "\(health.activeSessions)")
                    statRow("Scheduler", health.schedulerEnabled ? "Enabled" : "Disabled")
                    statRow("Uptime", formatUptime(health.uptimeSeconds))
                } else if appState.isLoading {
                    ProgressView()
                } else {
                    Text("No data")
                        .foregroundStyle(WeeTheme.textMuted)
                }
            }
        }
    }

    private var serviceStatusCard: some View {
        GlassCard {
            VStack(alignment: .leading, spacing: 10) {
                Text("Services")
                    .font(.headline)
                    .foregroundStyle(WeeTheme.textPrimary)

                if let status = appState.serviceStatus {
                    ForEach(status.services.sorted(by: { $0.key < $1.key }), id: \.key) { name, info in
                        HStack {
                            StatusDot(isActive: info.active)
                            Text(name.capitalized)
                                .foregroundStyle(WeeTheme.textPrimary)
                            Spacer()
                            Text(info.status)
                                .font(.caption)
                                .foregroundStyle(WeeTheme.textSecondary)
                        }
                    }
                    Text("Node: \(status.node)")
                        .font(.caption)
                        .foregroundStyle(WeeTheme.textMuted)
                } else if appState.isLoading {
                    ProgressView()
                } else {
                    Text("No data")
                        .foregroundStyle(WeeTheme.textMuted)
                }
            }
        }
    }

    private func statRow(_ label: String, _ value: String) -> some View {
        HStack {
            Text(label)
                .foregroundStyle(WeeTheme.textSecondary)
            Spacer()
            Text(value)
                .foregroundStyle(WeeTheme.textPrimary)
        }
        .font(.subheadline)
    }

    private func formatUptime(_ seconds: Double) -> String {
        let hours = Int(seconds) / 3600
        let minutes = (Int(seconds) % 3600) / 60
        if hours > 0 {
            return "\(hours)h \(minutes)m"
        }
        return "\(minutes)m"
    }
}
