"""
Regression test for issue #27: File handle leaks in connector file sends.

The bug: telegram_connector.send_document() opened a new file handle inline
in the retry path without closing it.

The fix: reuse the already-open file handle via f.seek(0).
"""

import builtins
import io
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, "/opt/n8n-copilot-shim-dev")

_REAL_OPEN = builtins.open


class _SeekSpy:
    """File-like wrapper that records seek() calls.

    Used to verify the retry path rewinds the stream via seek(0) rather than
    opening a second file handle.  Supports the context-manager protocol so
    it can be returned directly from a patched builtins.open.
    """

    def __init__(self, content: bytes):
        self._buf = io.BytesIO(content)
        self.seek_calls: list = []

    def read(self, *args):
        return self._buf.read(*args)

    def seek(self, *args):
        self.seek_calls.append(args)
        return self._buf.seek(*args)

    def tell(self):
        return self._buf.tell()

    def readable(self):
        return True

    def writable(self):
        return False

    def __iter__(self):
        return self._buf.__iter__()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class TestIssue27FileHandleLeak(unittest.TestCase):
    """Verify the send_document retry path rewinds the existing stream."""

    def setUp(self):
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

    def _make_open_spy(self, spy: _SeekSpy):
        """Return a patched open() that yields the spy for our test file
        and delegates to the real open() for everything else."""
        target = self.file_path
        open_count = [0]

        def patched_open(path, *args, **kwargs):
            if str(path) == target:
                open_count[0] += 1
                return spy
            return _REAL_OPEN(path, *args, **kwargs)

        return patched_open, open_count

    def test_retry_seeks_stream_to_zero_not_opens_new_handle(self):
        """
        On first-request failure the retry MUST call f.seek(0) to rewind the
        stream — it must NOT open a second file handle.

        Catches both failure modes:
          * seek(0) omitted  -> seek_calls is empty   -> assertion fails
          * new open() used  -> open_count[0] == 2    -> assertion fails
        """
        connector = self._make_connector()
        spy = _SeekSpy(b"hello world")
        patched_open, open_count = self._make_open_spy(spy)

        first_fail = MagicMock()
        first_fail.status_code = 400
        first_fail.text = "Bad Request: can't parse entities"

        second_ok = MagicMock()
        second_ok.status_code = 200
        second_ok.json.return_value = {"ok": True, "result": {"message_id": 42}}

        with patch("builtins.open", side_effect=patched_open):
            with patch("requests.post", side_effect=[first_fail, second_ok]):
                result = connector.send_document(
                    chat_id=12345,
                    file_path=self.file_path,
                    caption="<b>test</b>",
                )

        # 1. Exactly one open() call — retry must NOT open a new file handle
        self.assertEqual(
            open_count[0],
            1,
            f"Expected 1 open() for {self.file_path}, got {open_count[0]}. "
            "Retry path must reuse the existing handle, not open a new one.",
        )

        # 2. seek(0) was called — stream position was rewound before the retry
        self.assertTrue(
            any(args == (0,) for args in spy.seek_calls),
            f"Expected seek(0) before the retry request, "
            f"but seek was called with: {spy.seek_calls}. "
            "Without seek(0) the retry sends an empty/partial body.",
        )

        self.assertEqual(result, 42)

    def test_no_retry_when_first_request_succeeds(self):
        """Happy path: one open() call, seek() never called."""
        connector = self._make_connector()
        spy = _SeekSpy(b"hello world")
        patched_open, open_count = self._make_open_spy(spy)

        ok_response = MagicMock()
        ok_response.status_code = 200
        ok_response.json.return_value = {"ok": True, "result": {"message_id": 7}}

        with patch("builtins.open", side_effect=patched_open):
            with patch("requests.post", return_value=ok_response):
                result = connector.send_document(
                    chat_id=12345,
                    file_path=self.file_path,
                    caption="plain text",
                )

        self.assertEqual(open_count[0], 1, "Exactly one open() call on success.")
        self.assertEqual(
            spy.seek_calls,
            [],
            "seek() must not be called when no retry is needed.",
        )
        self.assertEqual(result, 7)


if __name__ == "__main__":
    unittest.main()
