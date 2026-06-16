import SwiftUI

struct SidebarView: View {
    @Binding var selection: AppSection
    @Bindable var model: WeeAppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(spacing: 12) {
                Image("WeeIcon")
                    .resizable()
                    .frame(width: 38, height: 38)
                    .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))

                VStack(alignment: .leading, spacing: 2) {
                    Text("Wee")
                        .font(.headline.weight(.bold))
                        .foregroundStyle(WeeTheme.gold)
                    Text("Orchestrator")
                        .font(.caption)
                        .foregroundStyle(WeeTheme.textSecondary)
                }

                Spacer()
            }
            .padding(.bottom, 4)

            VStack(spacing: 8) {
                ForEach(AppSection.allCases) { section in
                    Button {
                        selection = section
                    } label: {
                        HStack(spacing: 10) {
                            Image(systemName: section.symbol)
                                .frame(width: 22)
                            Text(section.title)
                                .font(.subheadline.weight(.semibold))
                            Spacer()
                            if badgeCount(for: section) > 0 {
                                Text("\(badgeCount(for: section))")
                                    .font(.caption2.weight(.bold))
                                    .foregroundStyle(.black)
                                    .padding(.horizontal, 7)
                                    .padding(.vertical, 3)
                                    .background(badgeColor(for: section), in: Capsule())
                            }
                        }
                        .foregroundStyle(selection == section ? WeeTheme.textPrimary : WeeTheme.textSecondary)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 11)
                        .background(selection == section ? WeeTheme.accent.opacity(0.16) : .clear, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                        .overlay(
                            RoundedRectangle(cornerRadius: 10, style: .continuous)
                                .stroke(selection == section ? WeeTheme.accent.opacity(0.28) : .clear)
                        )
                    }
                    .buttonStyle(.plain)
                }
            }

            VStack(alignment: .leading, spacing: 10) {
                Text("Status")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(WeeTheme.textMuted)
                    .textCase(.uppercase)

                HStack {
                    StatusPill(
                        text: model.health?.status ?? "unknown",
                        color: model.health?.status == "ok" ? WeeTheme.accent : WeeTheme.gold,
                        symbol: model.health?.status == "ok" ? "checkmark.circle.fill" : "wifi.slash"
                    )
                    Spacer()
                }

                if let env = model.health?.environment ?? model.appConfig?.appEnv {
                    Text(env)
                        .font(.caption)
                        .foregroundStyle(WeeTheme.textSecondary)
                }
            }
            .padding(12)
            .background(WeeTheme.sunken, in: RoundedRectangle(cornerRadius: 12, style: .continuous))

            Spacer()

            if let error = model.errorMessage {
                Text(error)
                    .font(.caption)
                    .foregroundStyle(WeeTheme.danger)
                    .lineLimit(4)
                    .padding(12)
                    .background(WeeTheme.danger.opacity(0.10), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            }

            Button {
                Task { await model.refreshAll() }
            } label: {
                Label("Refresh", systemImage: "arrow.clockwise")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(WeePrimaryButtonStyle())
        }
        .padding(16)
        .glassPanel()
    }

    private var runningCount: Int {
        model.tasks.filter { $0.status == "running" }.count
    }

    private var dueCount: Int {
        model.kanbanBoard?.dueCards.count ?? 0
    }

    private func badgeCount(for section: AppSection) -> Int {
        switch section {
        case .kanban: dueCount
        case .tasks: runningCount
        default: 0
        }
    }

    private func badgeColor(for section: AppSection) -> Color {
        section == .kanban ? WeeTheme.gold : WeeTheme.accent
    }
}

struct WeePrimaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(.black.opacity(0.82))
            .padding(.horizontal, 14)
            .padding(.vertical, 11)
            .background(
                LinearGradient(
                    colors: [WeeTheme.accent, Color(red: 0.0, green: 0.71, blue: 0.31)],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                ),
                in: RoundedRectangle(cornerRadius: 10, style: .continuous)
            )
            .opacity(configuration.isPressed ? 0.78 : 1)
            .scaleEffect(configuration.isPressed ? 0.98 : 1)
    }
}

struct WeeGhostButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(WeeTheme.textSecondary)
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(Color.white.opacity(configuration.isPressed ? 0.14 : 0.08), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(WeeTheme.glassStroke))
    }
}
