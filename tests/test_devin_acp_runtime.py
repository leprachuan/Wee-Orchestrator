"""Unit tests for the Devin ACP runtime adapter."""

import unittest

from devin_acp_runtime import DevinACPAdapter


class TestDevinACPAdapterMapping(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.adapter = DevinACPAdapter(stream_push=lambda kind, data: self.events.append((kind, data)))

    def test_agent_message_chunk_maps_to_chunk(self):
        self.adapter._handle_session_update(
            {"update": {"sessionUpdate": "agent_message_chunk", "content": {"text": "hello"}}}
        )
        self.assertEqual(self.adapter.collected_text, ["hello"])
        self.assertEqual(self.events, [("chunk", {"text": "hello"})])

    def test_tool_call_maps_to_wee_tool_start(self):
        self.adapter._handle_session_update(
            {
                "update": {
                    "sessionUpdate": "tool_call",
                    "id": "tool-1",
                    "name": "bash",
                    "content": {"command": "pwd"},
                }
            }
        )
        kind, data = self.events[0]
        self.assertEqual(kind, "tool_call")
        self.assertEqual(data["event"], "start")
        self.assertEqual(data["id"], "tool-1")
        self.assertEqual(data["name"], "bash")
        self.assertEqual(data["runtime"], "devin-acp")

    def test_tool_call_update_maps_to_wee_tool_completed(self):
        self.adapter._handle_session_update(
            {
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "tool-1",
                    "status": "completed",
                    "output": "ok",
                }
            }
        )
        kind, data = self.events[0]
        self.assertEqual(kind, "tool_call")
        self.assertEqual(data["event"], "completed")
        self.assertEqual(data["id"], "tool-1")
        self.assertFalse(data["is_error"])

    def test_agent_stopped_sets_turn_done_event_when_present(self):
        class Done:
            def __init__(self):
                self.called = False

            def set(self):
                self.called = True

        done = Done()
        self.adapter._turn_done = done
        self.adapter._handle_session_update(
            {"update": {"sessionUpdate": "_cognition.ai/agent_stopped"}}
        )
        self.assertTrue(done.called)

    def test_mode_mapping_is_conservative(self):
        self.assertEqual(DevinACPAdapter._mode_to_acp("restricted"), "ask")
        self.assertEqual(DevinACPAdapter._mode_to_acp("sandboxed"), "plan")
        self.assertEqual(DevinACPAdapter._mode_to_acp("elevated"), "bypass")

    def test_resume_fields_are_recorded(self):
        adapter = DevinACPAdapter(existing_session_id="devin-session-1", resume=True)
        self.assertEqual(adapter.existing_session_id, "devin-session-1")
        self.assertTrue(adapter.resume)

    def test_session_load_replay_is_not_streamed_or_collected(self):
        self.adapter._loading_existing_session = True
        self.adapter._accepting_current_turn = False
        self.adapter._handle_session_update(
            {"update": {"sessionUpdate": "agent_message_chunk", "content": {"text": "old answer"}}}
        )
        self.assertEqual(self.adapter.collected_text, [])
        self.assertEqual(self.events, [])

    def test_current_turn_after_load_is_streamed_and_collected(self):
        self.adapter._loading_existing_session = False
        self.adapter._accepting_current_turn = True
        self.adapter._handle_session_update(
            {"update": {"sessionUpdate": "agent_message_chunk", "content": {"text": "new answer"}}}
        )
        self.assertEqual(self.adapter.collected_text, ["new answer"])
        self.assertEqual(self.events, [("chunk", {"text": "new answer"})])


if __name__ == "__main__":
    unittest.main()
