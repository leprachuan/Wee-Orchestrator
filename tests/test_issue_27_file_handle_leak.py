"""
Regression test for issue #27: File handle leaks in connector file sends.

The bug: telegram_connector.send_document() opened a new file handle inline
(`open(file_path, "rb")`) in the retry path without closing it.

The fix: reuse the already-open file handle `f` via `f.seek(0)`.
"""
import io
import tempfile
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, "/opt/n8n-copilot-shim-dev")


class TestIssue27FileHandleLeak(unittest.TestCase):
    """Verify no new file handles are opened in the send_document retry path."""

    def setUp(self):
        # Create a small temp file to send
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".txt")
        self.tmp.write(b"hello world")
        self.tmp.close()
        self.file_path = self.tmp.name

    def tearDown(self):
        os.unlink(self.file_path)

    def _make_connector(self):
        from telegram_connector import TelegramConnector

        connector = TelegramConnector.__new__(TelegramConnector)
        connector.token = "fake_token"
        connector.api_url = "https://api.telegram.org/botfake_token"
        connector.send_message = MagicMock()
        connector._is_safe_file_path = MagicMock(return_value=True)
        connector.sanitize_telegram_html = MagicMock(side_effect=lambda x: x)
        return connector

    def test_retry_reuses_file_handle_not_opens_new_one(self):
        """
        Simulate a first request failure (e.g. HTML parse_mode rejected).
        The retry must NOT call open() again — it must seek(0) on the existing handle.
        """
        connector = self._make_connector()

        # Track every builtin open() call for our temp file
        open_calls = []
        _real_open = open

        def tracking_open(path, *args, **kwargs):
            fh = _real_open(path, *args, **kwargs)
            if str(path) == self.file_path:
                open_calls.append(path)
            return fh

        first_fail = MagicMock()
        first_fail.status_code = 400
        first_fail.text = "Bad Request: can't parse entities"

        second_ok = MagicMock()
        second_ok.status_code = 200
        second_ok.json.return_value = {"ok": True, "result": {"message_id": 42}}

        with patch("builtins.open", side_effect=tracking_open):
            with patch("requests.post", side_effect=[first_fail, second_ok]):
                result = connector.send_document(
                    chat_id=12345,
                    file_path=self.file_path,
                    caption="<b>test</b>",
                )

        # The fix: only ONE open() call (for the initial `with open(...)`)
        # A second call would indicate the leak is back.
        self.assertEqual(
            len(open_calls),
            1,
            f"Expected exactly 1 open() call for {self.file_path}, got {len(open_calls)}. "
            "Retry path must reuse the existing file handle via seek(0), not open a new one.",
        )
        self.assertEqual(result, 42)

    def test_no_retry_when_first_request_succeeds(self):
        """When the first request succeeds, only one open() call should happen."""
        connector = self._make_connector()

        open_calls = []
        _real_open = open

        def tracking_open(path, *args, **kwargs):
            fh = _real_open(path, *args, **kwargs)
            if str(path) == self.file_path:
                open_calls.append(path)
            return fh

        ok_response = MagicMock()
        ok_response.status_code = 200
        ok_response.json.return_value = {"ok": True, "result": {"message_id": 7}}

        with patch("builtins.open", side_effect=tracking_open):
            with patch("requests.post", return_value=ok_response):
                result = connector.send_document(
                    chat_id=12345,
                    file_path=self.file_path,
                    caption="plain text",
                )

        self.assertEqual(len(open_calls), 1)
        self.assertEqual(result, 7)


if __name__ == "__main__":
    unittest.main()
