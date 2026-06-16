import SwiftUI
import UIKit

struct ChatView: View {
    @Bindable var model: WeeAppModel
    @State private var draft = ""
    @State private var isShowingHistory = false

    var body: some View {
        VStack(spacing: 12) {
            HeaderPanel(model: model, isShowingHistory: $isShowingHistory)

            if showsRecentChatsRail && model.historySessions.isEmpty == false {
                RecentChatsRail(model: model)
                    .frame(height: 82)
            }

            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 12) {
                        ForEach(model.chatMessages) { message in
                            ChatBubble(message: message)
                                .id(message.id)
                        }
                    }
                    .padding(14)
                }
                .scrollIndicators(.hidden)
                .glassPanel()
                .onChange(of: model.chatMessages.count) {
                    if let last = model.chatMessages.last {
                        withAnimation(.snappy) {
                            proxy.scrollTo(last.id, anchor: .bottom)
                        }
                    }
                }
            }

            inputBar
        }
        .padding(compactPadding)
        .sheet(isPresented: $isShowingHistory) {
            SessionHistorySheet(model: model, isPresented: $isShowingHistory)
        }
    }

    private var compactPadding: CGFloat {
        UIDevice.current.userInterfaceIdiom == .pad ? 0 : 12
    }

    private var showsRecentChatsRail: Bool {
        UIDevice.current.userInterfaceIdiom == .pad
    }

    private var inputBar: some View {
        HStack(alignment: .bottom, spacing: 10) {
            TextField("Message Wee", text: $draft, axis: .vertical)
                .lineLimit(1...5)
                .textFieldStyle(.plain)
                .foregroundStyle(WeeTheme.textPrimary)
                .padding(.horizontal, 12)
                .padding(.vertical, 11)
                .background(WeeTheme.sunken, in: RoundedRectangle(cornerRadius: 12, style: .continuous))

            Button {
                let prompt = draft
                draft = ""
                Task { await model.sendChat(prompt) }
            } label: {
                Image(systemName: "paperplane.fill")
                    .frame(width: 24, height: 24)
            }
            .buttonStyle(WeePrimaryButtonStyle())
            .disabled(draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || model.isLoading)
        }
        .padding(10)
        .glassPanel()
    }
}

private struct HeaderPanel: View {
    @Bindable var model: WeeAppModel
    @Binding var isShowingHistory: Bool

    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 5) {
                Text(model.currentSessionID ?? "New Session")
                    .font(.headline.weight(.semibold))
                    .foregroundStyle(WeeTheme.textPrimary)
                    .lineLimit(1)

                HStack(spacing: 8) {
                    agentMenu
                    if let runtime = model.health?.environment ?? model.appConfig?.appEnv {
                        StatusPill(text: runtime, color: WeeTheme.accent, symbol: "server.rack")
                    }
                }
            }

            Spacer()

            Button {
                Task { await model.startNewChat() }
            } label: {
                Image(systemName: "square.and.pencil")
                    .frame(width: 22, height: 22)
            }
            .buttonStyle(WeeIconButtonStyle())
            .accessibilityLabel("New Chat")

            Button {
                isShowingHistory = true
            } label: {
                Image(systemName: "clock.arrow.circlepath")
                    .frame(width: 22, height: 22)
            }
            .buttonStyle(WeeIconButtonStyle())
            .accessibilityLabel("Chat History")

            if model.isLoading {
                ProgressView()
                    .tint(WeeTheme.accent)
            }
        }
        .padding(14)
        .glassPanel()
    }

    private var agentMenu: some View {
        Menu {
            ForEach(model.agents) { agent in
                Button {
                    Task { await model.changeAgent(to: agent.name) }
                } label: {
                    Label(agent.name, systemImage: agent.name == model.selectedAgent ? "checkmark.circle.fill" : "person.crop.circle")
                }
            }
        } label: {
            HStack(spacing: 6) {
                Image(systemName: "person.crop.circle")
                Text(model.selectedAgent)
                    .lineLimit(1)
                Image(systemName: "chevron.down")
                    .font(.caption2.weight(.bold))
            }
            .font(.caption.weight(.semibold))
            .foregroundStyle(.black.opacity(0.82))
            .padding(.horizontal, 10)
            .padding(.vertical, 7)
            .background(WeeTheme.gold, in: Capsule())
        }
        .disabled(model.agents.isEmpty || model.isLoading)
        .accessibilityLabel("Change Agent")
    }
}

private struct RecentChatsRail: View {
    @Bindable var model: WeeAppModel

    var body: some View {
        ScrollView(.horizontal) {
            HStack(spacing: 10) {
                ForEach(model.historySessions.prefix(12)) { session in
                    Button {
                        Task { await model.selectHistorySession(session) }
                    } label: {
                        VStack(alignment: .leading, spacing: 5) {
                            HStack(spacing: 6) {
                                Text(session.displayTitle)
                                    .font(.caption.weight(.semibold))
                                    .lineLimit(1)
                                if session.sessionID == model.currentSessionID {
                                    Image(systemName: "checkmark.circle.fill")
                                        .font(.caption)
                                        .foregroundStyle(WeeTheme.accent)
                                }
                            }
                            Text(session.agent?.isEmpty == false ? session.agent ?? "agent" : "agent")
                                .font(.caption2.weight(.medium))
                                .foregroundStyle(WeeTheme.gold)
                                .lineLimit(1)
                            Text(session.displayPreview)
                                .font(.caption2)
                                .foregroundStyle(WeeTheme.textSecondary)
                                .lineLimit(1)
                        }
                        .frame(width: 210, alignment: .leading)
                        .padding(10)
                        .background(session.sessionID == model.currentSessionID ? WeeTheme.accent.opacity(0.14) : Color.white.opacity(0.055), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).stroke(session.sessionID == model.currentSessionID ? WeeTheme.accent.opacity(0.34) : WeeTheme.glassStroke))
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
        }
        .scrollIndicators(.hidden)
        .glassPanel()
    }
}

private struct SessionHistorySheet: View {
    @Bindable var model: WeeAppModel
    @Binding var isPresented: Bool

    var body: some View {
        NavigationStack {
            ZStack {
                WeeTheme.background.ignoresSafeArea()
                List {
                    Section {
                        Button {
                            isPresented = false
                            Task { await model.startNewChat() }
                        } label: {
                            Label("New Chat", systemImage: "square.and.pencil")
                        }
                    }

                    Section("Previous Chats") {
                        ForEach(model.historySessions) { session in
                            Button {
                                isPresented = false
                                Task { await model.selectHistorySession(session) }
                            } label: {
                                HistorySessionRow(session: session, isSelected: session.sessionID == model.currentSessionID)
                            }
                        }
                    }
                }
                .scrollContentBackground(.hidden)
            }
            .navigationTitle("Chats")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") {
                        isPresented = false
                    }
                }
            }
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
        .task {
            await model.loadHistorySessions()
        }
    }
}

private struct HistorySessionRow: View {
    let session: HistorySessionSummary
    let isSelected: Bool

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: isSelected ? "checkmark.circle.fill" : "bubble.left.and.bubble.right")
                .foregroundStyle(isSelected ? WeeTheme.accent : WeeTheme.textSecondary)
                .frame(width: 24)

            VStack(alignment: .leading, spacing: 4) {
                Text(session.displayTitle)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(WeeTheme.textPrimary)
                    .lineLimit(1)

                Text(session.displayPreview)
                    .font(.caption)
                    .foregroundStyle(WeeTheme.textSecondary)
                    .lineLimit(2)

                if let agent = session.agent, agent.isEmpty == false {
                    Text(agent)
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(WeeTheme.gold)
                        .lineLimit(1)
                }
            }

            Spacer()
        }
        .padding(.vertical, 4)
    }
}

private struct ChatBubble: View {
    let message: ChatMessage

    var body: some View {
        HStack {
            if message.role == .user {
                Spacer(minLength: 34)
            }

            VStack(alignment: .leading, spacing: 6) {
                Text(message.role.rawValue.capitalized)
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(roleColor)
                    .textCase(.uppercase)
                Text(message.text)
                    .font(.body)
                    .foregroundStyle(WeeTheme.textPrimary)
                    .textSelection(.enabled)
            }
            .padding(13)
            .background(bubbleFill, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).stroke(WeeTheme.glassStroke))

            if message.role != .user {
                Spacer(minLength: 34)
            }
        }
    }

    private var roleColor: Color {
        switch message.role {
        case .user: WeeTheme.accent
        case .assistant: WeeTheme.gold
        case .system: WeeTheme.textMuted
        }
    }

    private var bubbleFill: Color {
        switch message.role {
        case .user: WeeTheme.accent.opacity(0.13)
        case .assistant: Color.white.opacity(0.07)
        case .system: WeeTheme.sunken
        }
    }
}

private struct WeeIconButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(WeeTheme.textPrimary)
            .padding(9)
            .background(Color.white.opacity(configuration.isPressed ? 0.14 : 0.08), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(WeeTheme.glassStroke))
            .opacity(configuration.isPressed ? 0.78 : 1)
    }
}
