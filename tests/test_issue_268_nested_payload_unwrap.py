import sys

import pytest

sys.path.insert(0, "/opt/n8n-copilot-shim-dev")

from webex_connector import WebEXConnector


class TestNestedPayloadUnwrap:
    """Regression tests for issue #268: nested payload unwrap support."""

    def test_issue_268_unwrap_single_level_payload(self):
        """Test unwrapping a single-level envelope."""
        payload = {
            "event": "messages",
            "data": {"personId": "ABC", "roomId": "ROOM123", "text": "hello"},
        }
        result = WebEXConnector._unwrap_payload(payload, "data")
        assert result["personId"] == "ABC"
        assert result["roomId"] == "ROOM123"
        assert result["text"] == "hello"

    def test_issue_268_unwrap_double_wrapped_with_message_data(self):
        """Test unwrapping double-wrapped payload with message_data."""
        payload = {
            "event": "messages",
            "resource": "messages",
            "data": {
                "personId": "ABC",
                "roomId": "ROOM123",
                "message_data": {
                    "text": "hello",
                },
            },
        }
        result = WebEXConnector._unwrap_payload(payload, "data")
        assert result["text"] == "hello"
        assert "personId" not in result

    def test_issue_268_unwrap_dotted_path(self):
        """Test unwrapping with dotted path notation."""
        payload = {
            "event": "messages",
            "wrapper": {"data": {"personId": "ABC", "message_data": {"text": "world"}}},
        }
        result = WebEXConnector._unwrap_payload(payload, "wrapper.data.message_data")
        assert result["text"] == "world"

    def test_issue_268_no_unwrap_when_no_key_provided(self):
        """Test that payload is returned as-is when no key is provided."""
        payload = {"personId": "ABC", "roomId": "ROOM123", "text": "direct"}
        result = WebEXConnector._unwrap_payload(payload)
        assert result == payload

    def test_issue_268_no_unwrap_message_data_without_key(self):
        """Test MAJOR regression: payload_key=None must not auto-unwrap message_data.

        A payload that happens to contain a top-level 'message_data' dict must
        be returned unchanged when no payload_key is configured.
        """
        payload = {
            "personId": "ABC",
            "text": "direct message",
            "message_data": {"meta": "extra info"},
        }
        result = WebEXConnector._unwrap_payload(payload)
        assert result == payload
        assert result["personId"] == "ABC"
        assert result["text"] == "direct message"

    def test_issue_268_auto_unwrap_message_data_after_primary(self):
        """Test auto-unwrapping of message_data after primary unwrap."""
        payload = {"data": {"nested": {"message_data": {"text": "nested"}}}}
        result = WebEXConnector._unwrap_payload(payload, "data.nested")
        assert result["text"] == "nested"

    def test_issue_268_stop_at_max_depth(self):
        """Test that unwrapping stops at max_depth to prevent infinite loops."""
        payload = {
            "data": {
                "message_data": {
                    "message_data": {
                        "message_data": {
                            "message_data": {"message_data": {"text": "too deep"}}
                        }
                    }
                }
            }
        }
        result = WebEXConnector._unwrap_payload(payload, "data", max_depth=2)
        # Should stop at depth 2, not reach the "text" field
        assert "text" not in result
        assert "message_data" in result

    def test_issue_268_partial_path_fallback(self):
        """Test fallback when dotted path doesn't fully exist."""
        payload = {"data": {"personId": "ABC", "roomId": "ROOM123", "text": "fallback"}}
        # Path is "data.nonexistent" but only "data" exists
        result = WebEXConnector._unwrap_payload(payload, "data.nonexistent")
        # Should stop at "data" and return what's there
        assert result["personId"] == "ABC"
        assert result["text"] == "fallback"

    def test_issue_268_non_dict_payload_returns_as_is(self):
        """Test that non-dict payloads are returned unchanged."""
        payload = "not a dict"
        result = WebEXConnector._unwrap_payload(payload, "data")
        assert result == payload

    def test_issue_268_key_not_in_payload(self):
        """Test payload when specified key doesn't exist."""
        payload = {"event": "messages", "personId": "ABC", "text": "hello"}
        result = WebEXConnector._unwrap_payload(payload, "data")
        # Should return payload as-is since key not found
        assert result == payload

    def test_issue_268_no_auto_unwrap_when_primary_key_absent(self):
        """Test MAJOR bug fix: no auto-unwrap when payload_key configured but absent.

        When payload_key="data" configured but key not found, and payload has
        top-level message_data, auto-unwrap (Step 2) must NOT fire.
        """
        payload = {
            "personId": "ABC",
            "text": "real message",
            "message_data": {"meta": "x"},
        }
        result = WebEXConnector._unwrap_payload(payload, payload_key="data")
        assert result == payload
        assert result["personId"] == "ABC"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
