"""Unit tests for TelegramNotifier and Telegram sync integration."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import pytest
import requests

from librus_collector.models import SchoolFeedItem, SchoolItemType, SyncResult
from librus_collector.telegram_notifier import TelegramNotifier
from librus_collector.sync_engine import LibrusSyncEngine


def test_telegram_notifier_not_configured():
    """Verify notifier gracefully handles unconfigured state."""
    notifier_no_token = TelegramNotifier(bot_token="", chat_id="12345")
    assert not notifier_no_token.is_configured
    assert not notifier_no_token.send_message("Test")

    notifier_no_chat = TelegramNotifier(bot_token="token", chat_id="")
    assert not notifier_no_chat.is_configured
    assert not notifier_no_chat.send_message("Test")

    item = SchoolFeedItem(
        id="test_msg",
        item_type=SchoolItemType.MESSAGE,
        title="Test",
        content="Content",
    )
    assert not notifier_no_chat.send_item(item)
    assert notifier_no_chat.send_items([item]) == 0


def test_telegram_notifier_formatting():
    """Verify HTML escaping and label formatting."""
    notifier = TelegramNotifier(bot_token="token123", chat_id="chat456")
    item = SchoolFeedItem(
        id="test_1",
        item_type=SchoolItemType.MESSAGE,
        title="Ważna wiadomość <Klasa 1 & 2>",
        author="Kowalska Anna",
        date="2026-10-15 10:00",
        attachments=["dokument.pdf", "plan.xlsx"],
        content="Cześć! Proszę sprawdzić <b>link</b> oraz formularz.",
    )
    chunks = notifier.format_item(item)
    assert len(chunks) == 1
    msg = chunks[0]

    assert "✉️" in msg
    assert "Wiadomość / Message" in msg
    assert "Ważna wiadomość &lt;Klasa 1 &amp; 2&gt;" in msg
    assert "Kowalska Anna" in msg
    assert "dokument.pdf, plan.xlsx" in msg
    assert "&lt;b&gt;link&lt;/b&gt;" in msg  # Ensure raw HTML in user text is escaped


def test_telegram_notifier_chunking_large_message():
    """Verify messages exceeding 4000 chars are cleanly chunked."""
    notifier = TelegramNotifier(bot_token="token123", chat_id="chat456")
    large_content = "To jest ważny tekst akapitu. " * 300  # ~8700 characters
    item = SchoolFeedItem(
        id="large_1",
        item_type=SchoolItemType.ANNOUNCEMENT,
        title="Długie ogłoszenie",
        content=large_content,
    )
    chunks = notifier.format_item(item)
    assert len(chunks) > 1
    assert "Part 1/" in chunks[0]
    assert "Part 2/" in chunks[1]
    for ch in chunks:
        assert len(ch) <= 4000


def test_telegram_notifier_send_message_success():
    """Verify HTTP POST structure on successful delivery."""
    mock_session = MagicMock(spec=requests.Session)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_session.post.return_value = mock_resp

    notifier = TelegramNotifier(bot_token="1234:ABC", chat_id="98765", session=mock_session)
    ok = notifier.send_message("<b>Hello</b>", parse_mode="HTML")

    assert ok is True
    mock_session.post.assert_called_once()
    args, kwargs = mock_session.post.call_args
    assert "https://api.telegram.org/bot1234:ABC/sendMessage" in args[0]
    assert kwargs["json"]["chat_id"] == "98765"
    assert kwargs["json"]["text"] == "<b>Hello</b>"
    assert kwargs["json"]["parse_mode"] == "HTML"
    assert kwargs["json"]["disable_web_page_preview"] is True


def test_telegram_notifier_html_retry_fallback():
    """Verify that if Telegram rejects HTML entities (HTTP 400), it automatically falls back to plain text."""
    mock_session = MagicMock(spec=requests.Session)
    fail_resp = MagicMock()
    fail_resp.status_code = 400
    fail_resp.text = '{"ok":false,"description":"Bad Request: can\'t parse entities"}'

    ok_resp = MagicMock()
    ok_resp.status_code = 200

    mock_session.post.side_effect = [fail_resp, ok_resp]

    notifier = TelegramNotifier(bot_token="token123", chat_id="chat456", session=mock_session)
    ok = notifier.send_message("<b>Bad <entity</b>", parse_mode="HTML")

    assert ok is True
    assert mock_session.post.call_count == 2
    # Verify second call dropped parse_mode
    second_call_kwargs = mock_session.post.call_args_list[1][1]
    assert "parse_mode" not in second_call_kwargs["json"]


def test_telegram_notifier_send_items_chronological():
    """Verify items are dispatched in chronological order (oldest first)."""
    notifier = TelegramNotifier(bot_token="tok", chat_id="123")
    with patch.object(notifier, "send_item", return_value=True) as mock_send_item:
        item_newer = SchoolFeedItem(
            id="msg_new",
            item_type=SchoolItemType.MESSAGE,
            title="Newer",
            date="2026-10-15 14:00",
        )
        item_older = SchoolFeedItem(
            id="msg_old",
            item_type=SchoolItemType.MESSAGE,
            title="Older",
            date="2026-10-15 09:00",
        )
        # Pass in reverse order
        sent = notifier.send_items([item_newer, item_older])
        assert sent == 2
        assert mock_send_item.call_count == 2
        # First call should be older item
        assert mock_send_item.call_args_list[0][0][0].id == "msg_old"
        assert mock_send_item.call_args_list[1][0][0].id == "msg_new"


def test_sync_engine_telegram_integration(tmp_path):
    """Test sync engine dispatches new items to TelegramNotifier."""
    mock_client = MagicMock()
    mock_state = MagicMock()
    mock_docs = MagicMock()
    mock_store = MagicMock()
    mock_telegram = MagicMock()

    mock_telegram.is_configured = True
    mock_telegram.send_items.return_value = 2

    item1 = SchoolFeedItem(id="m1", item_type=SchoolItemType.MESSAGE, title="Msg 1", content="Hi")
    item2 = SchoolFeedItem(id="m2", item_type=SchoolItemType.ANNOUNCEMENT, title="Ann 1", content="Alert")

    mock_client.collect_all.return_value = [item1, item2]
    mock_state.filter_new.return_value = [item1, item2]
    mock_docs.append_text.return_value = {"target": "local_file"}
    mock_docs.is_google_configured = False
    mock_docs.doc_id = None

    engine = LibrusSyncEngine(
        librus_client=mock_client,
        state_manager=mock_state,
        docs_sync=mock_docs,
        item_store=mock_store,
        telegram_notifier=mock_telegram,
    )

    result = engine.run_sync()

    assert result.success is True
    assert result.items_synced == 2
    assert result.telegram_sent == 2
    mock_telegram.send_items.assert_called_once_with([item1, item2])
    mock_state.mark_synced.assert_called_once_with([item1, item2], reset=False)


def test_sync_engine_telegram_no_new_items():
    """Test sync engine does not send to Telegram if there are no new items."""
    mock_client = MagicMock()
    mock_state = MagicMock()
    mock_docs = MagicMock()
    mock_telegram = MagicMock()

    mock_telegram.is_configured = True
    mock_client.collect_all.return_value = []
    mock_state.filter_new.return_value = []
    mock_docs.doc_id = None
    mock_docs.is_google_configured = False

    engine = LibrusSyncEngine(
        librus_client=mock_client,
        state_manager=mock_state,
        docs_sync=mock_docs,
        telegram_notifier=mock_telegram,
    )

    result = engine.run_sync()
    assert result.success is True
    assert result.items_new == 0
    assert result.telegram_sent == 0
    mock_telegram.send_items.assert_not_called()
