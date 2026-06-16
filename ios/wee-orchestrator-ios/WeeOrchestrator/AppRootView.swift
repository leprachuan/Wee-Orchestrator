import SwiftUI

enum AppSection: String, CaseIterable, Identifiable {
    case chat
    case kanban
    case tasks
    case agents
    case settings

    var id: String { rawValue }

    var title: String {
        switch self {
        case .chat: "Chat"
        case .kanban: "Kanban"
        case .tasks: "Tasks"
        case .agents: "Agents"
        case .settings: "Settings"
        }
    }

    var symbol: String {
        switch self {
        case .chat: "bubble.left.and.bubble.right"
        case .kanban: "rectangle.3.group"
        case .tasks: "bolt.fill"
        case .agents: "person.3.sequence.fill"
        case .settings: "gearshape.fill"
        }
    }
}

struct AppRootView: View {
    @Bindable var model: WeeAppModel
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    @State private var selectedSection: AppSection = .chat

    private var isPadLayout: Bool {
        horizontalSizeClass == .regular
    }

    var body: some View {
        ZStack {
            WeeBackground()

            if isPadLayout {
                HStack(spacing: 14) {
                    SidebarView(selection: $selectedSection, model: model)
                        .frame(width: 286)

                    sectionView
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
                .padding(18)
            } else {
                TabView(selection: $selectedSection) {
                    ForEach(AppSection.allCases) { section in
                        NavigationStack {
                            section.makeView(model: model)
                                .navigationTitle(section.title)
                                .toolbarBackground(.hidden, for: .navigationBar)
                        }
                        .tag(section)
                        .tabItem {
                            Label(section.title, systemImage: section.symbol)
                        }
                    }
                }
                .tint(WeeTheme.accent)
            }
        }
        .preferredColorScheme(.dark)
        .task {
            await model.bootstrap()
        }
    }

    @ViewBuilder
    private var sectionView: some View {
        NavigationStack {
            selectedSection.makeView(model: model)
        }
    }
}

private extension AppSection {
    @ViewBuilder
    func makeView(model: WeeAppModel) -> some View {
        switch self {
        case .chat:
            ChatView(model: model)
        case .kanban:
            KanbanView(model: model)
        case .tasks:
            TasksView(model: model)
        case .agents:
            AgentsView(model: model)
        case .settings:
            SettingsView(model: model)
        }
    }
}
