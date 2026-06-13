import SwiftUI

struct AgentsView: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 12) {
                if appState.agents.isEmpty && appState.isLoading {
                    ProgressView()
                        .frame(maxWidth: .infinity)
                        .padding(.top, 40)
                } else if appState.agents.isEmpty {
                    Text("No agents found.")
                        .foregroundStyle(WeeTheme.textMuted)
                        .padding(.top, 40)
                } else {
                    ForEach(appState.agents) { agent in
                        GlassCard {
                            VStack(alignment: .leading, spacing: 6) {
                                Text(agent.name)
                                    .font(.headline)
                                    .foregroundStyle(WeeTheme.textPrimary)

                                if !agent.description.isEmpty {
                                    Text(agent.description)
                                        .font(.subheadline)
                                        .foregroundStyle(WeeTheme.textSecondary)
                                }

                                HStack(spacing: 12) {
                                    if let runtime = agent.primaryRuntime {
                                        tag(runtime)
                                    }
                                    if let model = agent.primaryModel {
                                        tag(model)
                                    }
                                }
                            }
                        }
                    }
                }
            }
            .padding(16)
        }
        .navigationTitle("Agents")
        .weeBackground()
        .refreshable {
            await appState.loadAll()
        }
    }

    private func tag(_ text: String) -> some View {
        Text(text)
            .font(.caption)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(WeeTheme.surfaceRaised)
            .foregroundStyle(WeeTheme.accent)
            .clipShape(Capsule())
    }
}
