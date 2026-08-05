"""Tests for issue #487 — Webex inbound listener using Mercury real-time transport.

Scope: this covers everything testable without a live Webex bot account and
socket connection — feature-flag gating, backoff calculation, bounded dedup,
event parsing/filtering, bounded backpressure, and message enrichment via a
mocked Messages API call. It does NOT validate the actual live Mercury
WebSocket protocol handshake against Webex's cloud, which requires real
credentials and is explicitly a separate, human-in-the-loop operational step
(see the module docstring in webex_mercury_listener.py).
"""

import asyncio
import base64
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webex_mercury_listener as wml


class TestFeatureFlag:
    def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WEBEX_MERCURY_ENABLED", None)
            assert wml.is_enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "True", "yes", "YES"])
    def test_enabled_by_truthy_values(self, value):
        with patch.dict(os.environ, {"WEBEX_MERCURY_ENABLED": value}):
            assert wml.is_enabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "", "garbage"])
    def test_not_enabled_by_falsy_or_invalid_values(self, value):
        with patch.dict(os.environ, {"WEBEX_MERCURY_ENABLED": value}):
            assert wml.is_enabled() is False


class TestReconnectDelay:
    def test_delay_grows_with_attempt_and_stays_within_bounds(self):
        for attempt in range(10):
            delay = wml.reconnect_delay(attempt, base=1.0, cap=60.0)
            assert 0 <= delay <= 60.0

    def test_delay_is_capped(self):
        # attempt=10 -> 1 * 2**10 = 1024, must still be capped to 60
        for _ in range(20):
            assert wml.reconnect_delay(10, base=1.0, cap=60.0) <= 60.0

    def test_jitter_produces_varying_delays(self):
        delays = {wml.reconnect_delay(5, base=1.0, cap=60.0) for _ in range(20)}
        assert len(delays) > 1, "Jitter should not return the exact same delay every time"


class TestBoundedSeenSet:
    def test_new_key_returns_true_once(self):
        seen = wml.BoundedSeenSet(max_size=10)
        assert seen.add_if_new("a") is True
        assert seen.add_if_new("a") is False

    def test_eviction_bounds_memory(self):
        seen = wml.BoundedSeenSet(max_size=3)
        for key in ["a", "b", "c", "d"]:
            seen.add_if_new(key)
        assert len(seen) == 3
        # "a" was evicted, so it's new again
        assert seen.add_if_new("a") is True

    def test_recently_seen_key_is_not_evicted_early(self):
        seen = wml.BoundedSeenSet(max_size=2)
        seen.add_if_new("a")
        seen.add_if_new("b")
        seen.add_if_new("a")  # touches "a", making "b" the oldest
        seen.add_if_new("c")  # should evict "b", not "a"
        assert seen.add_if_new("a") is False
        assert seen.add_if_new("b") is True


@pytest.fixture
def listener():
    return wml.WebexMercuryListener(
        token="test-token",
        on_message=MagicMock(return_value=True),
        bot_person_id="bot-person-id",
    )


def _post_event(activity_id="m1", actor_id="someone-else", verb="post"):
    """Build a real-shaped Mercury frame (verified against a live test message,
    see the activity_id_to_message_id docstring): the message identity is the
    activity's own top-level `id`, NOT `object.id` -- `object` is the
    encrypted comment body and has no `id` field."""
    return json.dumps(
        {
            "data": {
                "activity": {
                    "id": activity_id,
                    "verb": verb,
                    "actor": {"id": actor_id},
                    "object": {"objectType": "comment", "displayName": "<encrypted>"},
                }
            }
        }
    )


class TestActivityIdToMessageId:
    def test_matches_the_real_messages_api_id_format(self):
        # Verified live: sent a real message, compared the Messages API's
        # returned `id` against the activity `id` Mercury delivered for it.
        activity_id = "48649760-9127-11f1-adfd-53ca1f681f98"
        expected = "Y2lzY29zcGFyazovL3VzL01FU1NBR0UvNDg2NDk3NjAtOTEyNy0xMWYxLWFkZmQtNTNjYTFmNjgxZjk4"
        assert wml.activity_id_to_message_id(activity_id) == expected


class TestDecodeWebexResourceId:
    def test_decodes_a_real_people_me_id_to_its_raw_uuid(self):
        # Verified live: GET /v1/people/me for the dev bot returned exactly
        # this id, decoding to ciscospark://us/PEOPLE/{uuid}. This is the bug
        # a first live test found -- the bot never recognized its own
        # replies and re-ingested them as new queries, in a loop.
        encoded = "Y2lzY29zcGFyazovL3VzL1BFT1BMRS9lYmQ1MDY3MC02YjhlLTQwMWYtOGM1Mi02ZWQ5NmYyZDY1YWY"
        assert wml.decode_webex_resource_id(encoded) == "ebd50670-6b8e-401f-8c52-6ed96f2d65af"

    def test_round_trips_with_activity_id_to_message_id_shape(self):
        # Same encoding scheme, different URN segment (PEOPLE vs MESSAGE) --
        # decoding a message id should recover the raw activity id.
        activity_id = "48649760-9127-11f1-adfd-53ca1f681f98"
        encoded = wml.activity_id_to_message_id(activity_id)
        assert wml.decode_webex_resource_id(encoded) == activity_id

    def test_falls_back_to_input_on_invalid_base64(self):
        assert wml.decode_webex_resource_id("not-valid-base64!!!") == "not-valid-base64!!!"

    def test_falls_back_to_input_when_decoded_has_no_slash(self):
        garbage = base64.b64encode(b"no-urn-shape-here").decode()
        assert wml.decode_webex_resource_id(garbage) == garbage


class TestHandleRawEvent:
    @pytest.mark.asyncio
    async def test_non_post_verb_is_ignored(self, listener):
        await listener._handle_raw_event(_post_event(verb="typing"))
        assert listener._queue.qsize() == 0

    @pytest.mark.asyncio
    async def test_bot_authored_message_is_ignored(self, listener):
        await listener._handle_raw_event(_post_event(actor_id="bot-person-id"))
        assert listener._queue.qsize() == 0

    @pytest.mark.asyncio
    async def test_valid_post_is_queued(self, listener):
        await listener._handle_raw_event(_post_event(activity_id="m1"))
        assert listener._queue.qsize() == 1
        item = await listener._queue.get()
        assert item["message_id"] == wml.activity_id_to_message_id("m1")

    @pytest.mark.asyncio
    async def test_duplicate_message_id_is_deduplicated(self, listener):
        event = _post_event(activity_id="dup-1")
        await listener._handle_raw_event(event)
        await listener._handle_raw_event(event)
        assert listener._queue.qsize() == 1

    @pytest.mark.asyncio
    async def test_malformed_json_is_discarded_without_raising(self, listener):
        await listener._handle_raw_event("not json")
        assert listener._queue.qsize() == 0

    @pytest.mark.asyncio
    async def test_missing_activity_id_is_ignored(self, listener):
        await listener._handle_raw_event(
            '{"data": {"activity": {"verb": "post", "actor": {"id": "x"}, "object": {}}}}'
        )
        assert listener._queue.qsize() == 0

    @pytest.mark.asyncio
    async def test_backpressure_drops_oldest_when_queue_is_full(self):
        small_listener = wml.WebexMercuryListener(
            token="t", on_message=MagicMock(), max_queue_size=2
        )
        for i in range(4):
            await small_listener._handle_raw_event(_post_event(activity_id=f"m{i}"))
        assert small_listener._queue.qsize() == 2
        remaining_ids = []
        while not small_listener._queue.empty():
            remaining_ids.append((await small_listener._queue.get())["message_id"])
        # The two oldest (m0, m1) should have been dropped in favor of m2, m3.
        assert remaining_ids == [
            wml.activity_id_to_message_id("m2"),
            wml.activity_id_to_message_id("m3"),
        ]


class TestFetchMessage:
    def test_fetch_message_returns_json_body(self, listener):
        fake_response = MagicMock(status_code=200)
        fake_response.json.return_value = {"id": "m1", "text": "hello", "roomId": "r1"}
        fake_response.raise_for_status.return_value = None
        with patch.object(listener._session, "get", return_value=fake_response) as mock_get:
            result = listener._fetch_message("m1")
        assert result == {"id": "m1", "text": "hello", "roomId": "r1"}
        mock_get.assert_called_once()
        assert "m1" in mock_get.call_args[0][0]

    def test_fetch_message_returns_none_on_404(self, listener):
        fake_response = MagicMock(status_code=404)
        with patch.object(listener._session, "get", return_value=fake_response):
            result = listener._fetch_message("deleted-message")
        assert result is None


class TestHealth:
    def test_health_reflects_initial_state(self, listener):
        health = listener.health()
        assert health["connected"] is False
        assert health["reconnect_count"] == 0
        assert health["last_event_at"] is None
        assert health["queue_depth"] == 0

    @pytest.mark.asyncio
    async def test_health_reflects_queue_depth_after_event(self, listener):
        await listener._handle_raw_event(_post_event(activity_id="m1"))
        assert listener.health()["queue_depth"] == 1
        assert listener.health()["last_event_at"] is not None


class TestProcessQueueForever:
    @pytest.mark.asyncio
    async def test_dispatches_enriched_message_to_on_message_callback(self):
        on_message = MagicMock(return_value=True)
        listener = wml.WebexMercuryListener(token="t", on_message=on_message)
        await listener._queue.put({"message_id": "m1"})
        with patch.object(
            listener, "_fetch_message", return_value={"id": "m1", "text": "hi", "roomId": "r1"}
        ):
            task = asyncio.create_task(listener.process_queue_forever())
            await asyncio.sleep(0.05)
            await listener.stop()
            await task
        on_message.assert_called_once_with({"id": "m1", "text": "hi", "roomId": "r1"})

    @pytest.mark.asyncio
    async def test_skips_dispatch_when_message_fetch_returns_none(self):
        on_message = MagicMock(return_value=True)
        listener = wml.WebexMercuryListener(token="t", on_message=on_message)
        await listener._queue.put({"message_id": "gone"})
        with patch.object(listener, "_fetch_message", return_value=None):
            task = asyncio.create_task(listener.process_queue_forever())
            await asyncio.sleep(0.05)
            await listener.stop()
            await task
        on_message.assert_not_called()
