import Foundation
import Combine

/// Network operations needed by `ChatStore`, abstracted so tests can supply
/// a fake implementation without hitting the network.
protocol ChatAPI {
    func fetchChatSessions() async throws -> [ChatSession]
    func fetchChatSessionMessages(sessionId: String) async throws -> [ChatMessage]
    func createChatSession(agent: String?, model: String?, runtime: String?) async throws -> CreateChatSessionResponse
    func executeChatSession(sessionId: String, query: String) async throws -> ExecuteSessionResponse
}

extension APIClient: ChatAPI {}

/// Observable store backing the Chat tab: tracks the active session, its
/// transcript, the active agent, and the list of previous sessions.
@MainActor
final class ChatStore: ObservableObject {
    @Published var messages: [ChatMessage] = []
    @Published var sessions: [ChatSession] = []
    @Published var currentSessionId: String?
    @Published var currentAgent: String
    @Published var isSending = false
    @Published var isLoadingSessions = false
    @Published var errorMessage: String?

    let settings: SettingsStore
    private var client: ChatAPI

    init(settings: SettingsStore, client: ChatAPI? = nil, defaultAgent: String = "orchestrator") {
        self.settings = settings
        self.client = client ?? APIClient(settings: settings)
        self.currentAgent = defaultAgent
    }

    /// Rebuilds the API client, e.g. after Settings change.
    func refreshClient() {
        client = APIClient(settings: settings)
    }

    /// Starts a brand-new chat: clears the transcript and forgets the
    /// current session id so the next sent message creates a fresh
    /// backend session. The currently selected agent carries over.
    func newChat() {
        messages = []
        currentSessionId = nil
        errorMessage = nil
    }

    /// Changes the active agent.
    ///
    /// For a brand-new (not-yet-created) session this just updates the
    /// agent that will be used when the session is created on next send.
    /// For an existing session, the agent is switched in place via the
    /// `/agent set` command — the backend intentionally does not allow
    /// silently changing a session's agent any other way (see F015).
    func selectAgent(_ agentName: String) async {
        guard agentName != currentAgent else { return }
        currentAgent = agentName

        guard let sessionId = currentSessionId, !settings.useMockData else { return }

        do {
            let response = try await client.executeChatSession(
                sessionId: sessionId,
                query: "/agent set \"\(agentName)\""
            )
            messages.append(
                ChatMessage(role: "assistant", content: response.response, timestamp: Date().timeIntervalSince1970)
            )
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func loadSessions() async {
        if settings.useMockData {
            sessions = MockData.chatSessions
            return
        }

        isLoadingSessions = true
        defer { isLoadingSessions = false }
        do {
            sessions = try await client.fetchChatSessions()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    /// Loads an existing session's transcript and makes it active.
    func selectSession(_ session: ChatSession) async {
        currentSessionId = session.sessionId
        if let agent = session.agent, !agent.isEmpty {
            currentAgent = agent
        }
        errorMessage = nil

        if settings.useMockData {
            messages = MockData.chatMessages(for: session.sessionId)
            return
        }

        do {
            messages = try await client.fetchChatSessionMessages(sessionId: session.sessionId)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    /// Sends a message, creating a new backend session first if needed.
    func send(_ text: String) async {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }

        errorMessage = nil
        messages.append(ChatMessage(role: "user", content: trimmed, timestamp: Date().timeIntervalSince1970))

        if settings.useMockData {
            messages.append(
                ChatMessage(
                    role: "assistant",
                    content: "Mock response from \(currentAgent). Connect a real backend in Settings to chat for real.",
                    timestamp: Date().timeIntervalSince1970
                )
            )
            return
        }

        isSending = true
        defer { isSending = false }

        do {
            let sessionId: String
            if let existing = currentSessionId {
                sessionId = existing
            } else {
                let created = try await client.createChatSession(agent: currentAgent, model: nil, runtime: nil)
                sessionId = created.sessionId
                currentSessionId = sessionId
            }

            let response = try await client.executeChatSession(sessionId: sessionId, query: trimmed)
            messages.append(
                ChatMessage(role: "assistant", content: response.response, timestamp: Date().timeIntervalSince1970)
            )
            await loadSessions()
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
