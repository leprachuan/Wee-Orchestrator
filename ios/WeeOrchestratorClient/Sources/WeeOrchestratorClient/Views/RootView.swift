import SwiftUI

/// Top-level navigation. Uses a sidebar on iPad (regular width) and a
/// tab bar on iPhone (compact width), mirroring the WebUI's sidebar nav
/// (Chat / Tasks / Scheduler) adapted for this starter's scope
/// (Chat / Dashboard / Agents / Tasks / Settings).
struct RootView: View {
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    @EnvironmentObject private var appState: AppState

    enum Section: String, CaseIterable, Identifiable {
        case chat = "Chat"
        case dashboard = "Dashboard"
        case agents = "Agents"
        case tasks = "Tasks"
        case settings = "Settings"

        var id: String { rawValue }

        var icon: String {
            switch self {
            case .chat: return "bubble.left.and.bubble.right.fill"
            case .dashboard: return "gauge.with.dots.needle.50percent"
            case .agents: return "person.2.fill"
            case .tasks: return "bolt.fill"
            case .settings: return "gearshape.fill"
            }
        }
    }

    @State private var selection: Section? = .chat

    var body: some View {
        Group {
            if horizontalSizeClass == .regular {
                NavigationSplitView {
                    sidebar
                } detail: {
                    detail(for: selection ?? .chat)
                }
            } else {
                TabView(selection: Binding(get: { selection ?? .chat }, set: { selection = $0 })) {
                    ForEach(Section.allCases) { section in
                        NavigationStack {
                            detail(for: section)
                        }
                        .tabItem {
                            Label(section.rawValue, systemImage: section.icon)
                        }
                        .tag(section)
                    }
                }
                .tint(WeeTheme.accent)
            }
        }
        .task {
            await appState.loadAll()
        }
    }

    private var sidebar: some View {
        List(Section.allCases, selection: $selection) { section in
            Label(section.rawValue, systemImage: section.icon)
                .tag(section)
        }
        .navigationTitle("Wee Orchestrator")
        .listStyle(.sidebar)
    }

    @ViewBuilder
    private func detail(for section: Section) -> some View {
        switch section {
        case .chat:
            ChatView()
        case .dashboard:
            DashboardView()
        case .agents:
            AgentsView()
        case .tasks:
            BackgroundTasksView()
        case .settings:
            SettingsView()
        }
    }
}
