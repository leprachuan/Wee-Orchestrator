import SwiftUI
import UIKit

struct AgentsView: View {
    @Bindable var model: WeeAppModel

    var body: some View {
        VStack(spacing: 12) {
            HStack {
                VStack(alignment: .leading, spacing: 5) {
                    Text("Agents")
                        .font(.title3.weight(.bold))
                        .foregroundStyle(WeeTheme.textPrimary)
                    Text("\(model.agents.count) configured")
                        .font(.caption)
                        .foregroundStyle(WeeTheme.textSecondary)
                }
                Spacer()
                Picker("Active", selection: $model.selectedAgent) {
                    ForEach(model.agents) { agent in
                        Text(agent.name).tag(agent.name)
                    }
                }
                .pickerStyle(.menu)
                .tint(WeeTheme.accent)
            }
            .padding(14)
            .glassPanel()

            ScrollView {
                LazyVStack(spacing: 12) {
                    ForEach(model.agents) { agent in
                        AgentCard(agent: agent, isSelected: agent.name == model.selectedAgent)
                            .onTapGesture {
                                model.selectedAgent = agent.name
                                model.saveConfiguration()
                            }
                    }
                }
                .padding(14)
            }
            .scrollIndicators(.hidden)
            .glassPanel()
        }
        .padding(UIDevice.current.userInterfaceIdiom == .pad ? 0 : 12)
    }
}

private struct AgentCard: View {
    let agent: AgentSummary
    let isSelected: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(agent.name)
                    .font(.headline.weight(.semibold))
                    .foregroundStyle(isSelected ? WeeTheme.gold : WeeTheme.textPrimary)
                Spacer()
                if isSelected {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundStyle(WeeTheme.accent)
                }
            }

            Text(agent.description)
                .font(.subheadline)
                .foregroundStyle(WeeTheme.textSecondary)
                .lineLimit(3)

            HStack {
                if let runtime = agent.primaryRuntime {
                    StatusPill(text: runtime, color: WeeTheme.accent, symbol: "terminal")
                }
                if let model = agent.primaryModel {
                    StatusPill(text: model, color: WeeTheme.gold, symbol: "cpu")
                }
            }
        }
        .padding(14)
        .background(isSelected ? WeeTheme.accent.opacity(0.12) : Color.white.opacity(0.055), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).stroke(isSelected ? WeeTheme.accent.opacity(0.32) : WeeTheme.glassStroke))
    }
}
