import SwiftUI

/// Chat tab: a transcript view with controls to switch agents, start a new
/// chat, and browse/select previous sessions. Works in both the iPad
/// sidebar (`NavigationSplitView`) and iPhone tab bar layouts.
struct ChatView: View {
    @EnvironmentObject private var appState: AppState
    @EnvironmentObject private var chatStore: ChatStore

    @State private var draft: String = ""
    @State private var showAgentPicker = false
    @State private var showSessions = false
    @State private var draftTextHeight: CGFloat = ChatView.inputMinHeight

    private static let inputMinHeight: CGFloat = 36
    private static let inputMaxHeight: CGFloat = 200

    /// Clamps the composer's measured text height between a one-line floor
    /// (so a single short line never renders smaller than a normal text
    /// field) and issue #509's 200pt cap (past which the ScrollView it's
    /// wrapped in takes over rather than clipping).
    static func inputBarHeight(forMeasuredTextHeight measuredHeight: CGFloat) -> CGFloat {
        min(max(measuredHeight, inputMinHeight), inputMaxHeight)
    }

    var body: some View {
        VStack(spacing: 0) {
            if let errorMessage = chatStore.errorMessage {
                Text(errorMessage)
                    .font(.footnote)
                    .foregroundStyle(WeeTheme.danger)
                    .padding(.horizontal, 16)
                    .padding(.top, 8)
            }

            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 12) {
                        if chatStore.messages.isEmpty {
                            Text("Start a conversation with \(chatStore.currentAgent).")
                                .foregroundStyle(WeeTheme.textMuted)
                                .padding(.top, 40)
                                .frame(maxWidth: .infinity, alignment: .center)
                        } else {
                            ForEach(chatStore.messages) { message in
                                ChatBubble(message: message)
                                    .id(message.id)
                            }
                        }

                        if chatStore.isSending {
                            ProgressView()
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.leading, 16)
                        }
                    }
                    .padding(16)
                }
                .onChange(of: chatStore.messages.count) { _, _ in
                    if let last = chatStore.messages.last {
                        withAnimation {
                            proxy.scrollTo(last.id, anchor: .bottom)
                        }
                    }
                }
            }

            inputBar
        }
        .navigationTitle("Chat")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .principal) {
                Button {
                    showAgentPicker = true
                } label: {
                    HStack(spacing: 4) {
                        Text(chatStore.currentAgent)
                            .font(.headline)
                            .foregroundStyle(WeeTheme.textPrimary)
                        Image(systemName: "chevron.down")
                            .font(.caption2)
                            .foregroundStyle(WeeTheme.textSecondary)
                    }
                }
            }
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    showSessions = true
                    Task { await chatStore.loadSessions() }
                } label: {
                    Image(systemName: "clock.arrow.circlepath")
                }
                .accessibilityLabel("Previous Chats")
            }
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    chatStore.newChat()
                } label: {
                    Image(systemName: "square.and.pencil")
                }
                .accessibilityLabel("New Chat")
            }
        }
        .weeBackground()
        .sheet(isPresented: $showAgentPicker) {
            AgentPickerSheet(selectedAgent: chatStore.currentAgent) { agent in
                Task { await chatStore.selectAgent(agent) }
            }
        }
        .sheet(isPresented: $showSessions) {
            ChatSessionListSheet(
                sessions: chatStore.sessions,
                isLoading: chatStore.isLoadingSessions,
                currentSessionId: chatStore.currentSessionId
            ) { session in
                Task { await chatStore.selectSession(session) }
            }
        }
        .task {
            await chatStore.loadSessions()
        }
    }

    private var inputBar: some View {
        HStack(spacing: 8) {
            // Issue #509: a bare TextField(axis: .vertical) clips anything
            // past its lineLimit instead of scrolling to it. Wrapping it in
            // a ScrollView fixes the clipping, but a ScrollView with no size
            // of its own asks to fill whatever height its parent offers --
            // capping that with frame(maxHeight:) alone left the input bar
            // sitting near 200pt tall even for a single short line, and
            // fixedSize(vertical:) does not read a wrapped vertical-axis
            // TextField's true single-line height reliably. Measuring the
            // TextField's own rendered height via GeometryReader and driving
            // the ScrollView's frame from that (clamped between a one-line
            // floor and the 200pt cap) is what actually makes it hug short
            // text and only grow -- then scroll -- as content demands more.
            ScrollView {
                TextField("Message \(chatStore.currentAgent)…", text: $draft, axis: .vertical)
                    .textFieldStyle(.plain)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 8)
                    .lineLimit(1...10)
                    .background(
                        GeometryReader { geometry in
                            Color.clear.preference(key: InputTextHeightKey.self, value: geometry.size.height)
                        }
                    )
            }
            .onPreferenceChange(InputTextHeightKey.self) { draftTextHeight = $0 }
            .frame(height: Self.inputBarHeight(forMeasuredTextHeight: draftTextHeight))
            .background(WeeTheme.surfaceRaised)
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))

            Button(action: sendDraft) {
                Image(systemName: "arrow.up.circle.fill")
                    .font(.system(size: 28))
                    .foregroundStyle(
                        draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                            ? WeeTheme.textMuted
                            : WeeTheme.accent
                    )
            }
            .disabled(draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || chatStore.isSending)
        }
        .padding(12)
        .background(.ultraThinMaterial)
    }

    private func sendDraft() {
        let text = draft
        draft = ""
        Task { await chatStore.send(text) }
    }
}

/// A single chat message bubble, right-aligned for the user and
/// left-aligned for the agent.
private struct ChatBubble: View {
    let message: ChatMessage

    var body: some View {
        HStack {
            if message.isUser { Spacer(minLength: 40) }

            Text(message.content)
                .font(.body)
                .foregroundStyle(WeeTheme.textPrimary)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(message.isUser ? WeeTheme.accent.opacity(0.22) : WeeTheme.surfaceRaised)
                .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 16, style: .continuous)
                        .strokeBorder(WeeTheme.glassBorder, lineWidth: 1)
                )

            if !message.isUser { Spacer(minLength: 40) }
        }
    }
}

/// Sheet allowing the user to pick the active agent for the chat header.
struct AgentPickerSheet: View {
    @EnvironmentObject private var appState: AppState
    @Environment(\.dismiss) private var dismiss

    let selectedAgent: String
    let onSelect: (String) -> Void

    var body: some View {
        NavigationStack {
            List(appState.agents) { agent in
                Button {
                    onSelect(agent.name)
                    dismiss()
                } label: {
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(agent.name)
                                .foregroundStyle(WeeTheme.textPrimary)
                            if !agent.description.isEmpty {
                                Text(agent.description)
                                    .font(.caption)
                                    .foregroundStyle(WeeTheme.textSecondary)
                            }
                        }
                        Spacer()
                        if agent.name == selectedAgent {
                            Image(systemName: "checkmark")
                                .foregroundStyle(WeeTheme.accent)
                        }
                    }
                }
            }
            .navigationTitle("Choose Agent")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
            .scrollContentBackground(.hidden)
            .weeBackground()
        }
    }
}

/// Sheet listing previous chat sessions, for switching between them.
struct ChatSessionListSheet: View {
    @Environment(\.dismiss) private var dismiss

    let sessions: [ChatSession]
    let isLoading: Bool
    let currentSessionId: String?
    let onSelect: (ChatSession) -> Void

    var body: some View {
        NavigationStack {
            Group {
                if sessions.isEmpty && isLoading {
                    ProgressView()
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if sessions.isEmpty {
                    Text("No previous chats yet.")
                        .foregroundStyle(WeeTheme.textMuted)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    List(sessions) { session in
                        Button {
                            onSelect(session)
                            dismiss()
                        } label: {
                            HStack {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(session.displayTitle)
                                        .foregroundStyle(WeeTheme.textPrimary)
                                        .lineLimit(1)
                                    if let agent = session.agent, !agent.isEmpty {
                                        Text(agent)
                                            .font(.caption)
                                            .foregroundStyle(WeeTheme.textSecondary)
                                    }
                                }
                                Spacer()
                                if session.sessionId == currentSessionId {
                                    Image(systemName: "checkmark")
                                        .foregroundStyle(WeeTheme.accent)
                                }
                            }
                        }
                    }
                    .scrollContentBackground(.hidden)
                }
            }
            .navigationTitle("Previous Chats")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") { dismiss() }
                }
            }
            .weeBackground()
        }
    }
}

/// Reports the composer TextField's own rendered height so `inputBar` can
/// size the ScrollView wrapping it to actual content instead of either
/// clipping (no ScrollView) or always filling its cap (ScrollView sized by
/// its parent instead of its content).
private struct InputTextHeightKey: PreferenceKey {
    static var defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
    }
}
