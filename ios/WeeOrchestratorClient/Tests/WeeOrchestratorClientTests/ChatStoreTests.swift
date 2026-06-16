import XCTest
@testable import WeeOrchestratorClient

/// In-memory fake of `ChatAPI` so `ChatStore` can be tested without a
/// network connection or backend.
private final class FakeChatAPI: ChatAPI {
    var sessions: [ChatSession] = []
    var messagesBySession: [String: [ChatMessage]] = [:]
    var nextCreatedSessionId = "new-session"
    var executeQueries: [(sessionId: String, query: String)] = []
    var executeResult: (String) -> String = { query in "echo: \(query)" }

    func fetchChatSessions() async throws -> [ChatSession] {
        sessions
    }

    func fetchChatSessionMessages(sessionId: String) async throws -> [ChatMessage] {
        messagesBySession[sessionId] ?? []
    }

    func createChatSession(agent: String?, model: String?, runtime: String?) async throws -> CreateChatSessionResponse {
        CreateChatSessionResponse(sessionId: nextCreatedSessionId, agent: agent, model: model, runtime: runtime)
    }

    func executeChatSession(sessionId: String, query: String) async throws -> ExecuteSessionResponse {
        executeQueries.append((sessionId: sessionId, query: query))
        return ExecuteSessionResponse(sessionId: sessionId, response: executeResult(query), runtime: nil, model: nil)
    }
}

@MainActor
final class ChatStoreTests: XCTestCase {
    /// Regression test for #364: tapping the agent name should be able to
    /// change the active agent, "New Chat" should clear the transcript and
    /// start a new session on next send, and previous sessions should be
    /// listable and selectable.
    func testNewChatClearsTranscriptAndStartsFreshSessionOnNextSend() async {
        let settings = SettingsStore()
        settings.useMockData = false
        let api = FakeChatAPI()
        api.nextCreatedSessionId = "session-1"
        let store = ChatStore(settings: settings, client: api, defaultAgent: "orchestrator")

        await store.send("hello")
        XCTAssertEqual(store.currentSessionId, "session-1")
        XCTAssertEqual(store.messages.count, 2)

        store.newChat()
        XCTAssertNil(store.currentSessionId)
        XCTAssertTrue(store.messages.isEmpty)

        api.nextCreatedSessionId = "session-2"
        await store.send("hello again")
        XCTAssertEqual(store.currentSessionId, "session-2")
        XCTAssertEqual(store.messages.count, 2)
    }

    func testSelectingAgentForNewSessionDoesNotCallBackend() async {
        let settings = SettingsStore()
        settings.useMockData = false
        let api = FakeChatAPI()
        let store = ChatStore(settings: settings, client: api, defaultAgent: "orchestrator")

        await store.selectAgent("research-dev")

        XCTAssertEqual(store.currentAgent, "research-dev")
        XCTAssertTrue(api.executeQueries.isEmpty)
    }

    func testSelectingAgentForExistingSessionSendsAgentSetCommand() async {
        let settings = SettingsStore()
        settings.useMockData = false
        let api = FakeChatAPI()
        api.nextCreatedSessionId = "session-1"
        let store = ChatStore(settings: settings, client: api, defaultAgent: "orchestrator")

        await store.send("hello")
        await store.selectAgent("research-dev")

        XCTAssertEqual(store.currentAgent, "research-dev")
        XCTAssertEqual(api.executeQueries.last?.sessionId, "session-1")
        XCTAssertEqual(api.executeQueries.last?.query, "/agent set \"research-dev\"")
    }

    func testLoadingAndSelectingPreviousSession() async {
        let settings = SettingsStore()
        settings.useMockData = false
        let api = FakeChatAPI()
        let session = ChatSession(
            sessionId: "old-session",
            title: "Past chat",
            preview: "Hi there",
            agent: "research-dev",
            createdAt: 1,
            updatedAt: 2
        )
        api.sessions = [session]
        api.messagesBySession["old-session"] = [
            ChatMessage(role: "user", content: "Hi there", timestamp: 1),
            ChatMessage(role: "assistant", content: "Hello!", timestamp: 2),
        ]
        let store = ChatStore(settings: settings, client: api, defaultAgent: "orchestrator")

        await store.loadSessions()
        XCTAssertEqual(store.sessions.map(\.sessionId), ["old-session"])

        await store.selectSession(session)
        XCTAssertEqual(store.currentSessionId, "old-session")
        XCTAssertEqual(store.currentAgent, "research-dev")
        XCTAssertEqual(store.messages.count, 2)
    }
}
