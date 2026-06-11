"""Tests for issue #353: Session selector — click/keyboard to switch active session."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from textual.widgets import DataTable


def test_session_list_panel_is_datatable():
    """SessionListPanel must subclass DataTable for row selection support."""
    from tui.components.session_list import SessionListPanel

    assert issubclass(SessionListPanel, DataTable)


def test_chat_panel_has_load_transcript():
    """ChatPanel must have a load_transcript method for switching sessions."""
    from tui.components.chat_panel import ChatPanel

    assert callable(getattr(ChatPanel, "load_transcript", None))


def test_api_client_has_get_session_messages():
    """WeeAPIClient must expose get_session_messages for transcript fetching."""
    from tui.api.client import WeeAPIClient

    assert callable(getattr(WeeAPIClient, "get_session_messages", None))


def test_wee_tui_has_load_session():
    """WeeTUI must have _load_session worker for switching sessions."""
    from tui.app import WeeTUI

    assert callable(getattr(WeeTUI, "_load_session", None))


def test_wee_tui_has_row_selected_handler():
    """WeeTUI must handle DataTable.RowSelected to detect session clicks."""
    from tui.app import WeeTUI

    assert callable(getattr(WeeTUI, "on_data_table_row_selected", None))


@pytest.mark.asyncio
async def test_load_transcript_clears_then_fills():
    """load_transcript should clear existing messages and write new ones."""
    from tui.components.chat_panel import ChatPanel

    panel = ChatPanel()
    panel.clear = MagicMock()
    panel.write = MagicMock()
    panel.scroll_end = MagicMock()

    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]
    await panel.load_transcript(messages)

    panel.clear.assert_called_once()
    assert panel.write.call_count == 2


@pytest.mark.asyncio
async def test_load_transcript_empty_shows_placeholder():
    """load_transcript with no messages should display a placeholder."""
    from tui.components.chat_panel import ChatPanel

    panel = ChatPanel()
    panel.clear = MagicMock()
    panel.write = MagicMock()

    await panel.load_transcript([])

    panel.clear.assert_called_once()
    panel.write.assert_called_once()


@pytest.mark.asyncio
async def test_load_transcript_handles_content_blocks():
    """load_transcript should handle content as a list of text blocks."""
    from tui.components.chat_panel import ChatPanel

    panel = ChatPanel()
    panel.clear = MagicMock()
    panel.write = MagicMock()
    panel.scroll_end = MagicMock()

    messages = [
        {"role": "assistant", "content": [{"type": "text", "text": "Hello world"}]},
    ]
    await panel.load_transcript(messages)

    panel.clear.assert_called_once()
    panel.write.assert_called_once()
    written_arg = panel.write.call_args[0][0]
    assert "Hello world" in str(written_arg)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
