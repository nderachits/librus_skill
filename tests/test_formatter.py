"""Tests for document formatter output."""

from datetime import datetime, timezone
from librus_collector.formatter import format_single_item, format_batch_for_doc
from librus_collector.models import SchoolFeedItem, SchoolItemType


def test_format_single_item():
    """Verify single item formatted block."""
    item = SchoolFeedItem(
        id="ann:10",
        item_type=SchoolItemType.ANNOUNCEMENT,
        title="Zebranie z rodzicami",
        author="Wychowawca Klasy",
        date="2026-10-20 17:00",
        content="Zapraszamy na spotkanie w sali 102.",
        attachments=["porzadek_zebrania.pdf"],
        metadata={"source": "synergia_ogloszenia"},
    )
    formatted = format_single_item(item)

    assert "[OGŁOSZENIE / ANNOUNCEMENT] Zebranie z rodzicami" in formatted
    assert "• Data / Date: 2026-10-20 17:00" in formatted
    assert "• Od / From: Wychowawca Klasy" in formatted
    assert "• Załączniki / Attachments: porzadek_zebrania.pdf" in formatted
    assert "• Źródło / Source: synergia_ogloszenia" in formatted
    assert "Zapraszamy na spotkanie w sali 102." in formatted


def test_format_single_item_empty_content():
    """Verify item with no content handles fallback gracefully."""
    item = SchoolFeedItem(
        id="msg:20",
        item_type=SchoolItemType.MESSAGE,
        title="Krótka notatka",
        content="",
    )
    formatted = format_single_item(item)
    assert "(Brak treści / No body text)" in formatted


def test_format_batch_for_doc():
    """Verify batch header and formatting."""
    items = [
        SchoolFeedItem(id="m1", item_type=SchoolItemType.MESSAGE, title="Msg 1", content="Hello 1"),
        SchoolFeedItem(id="m2", item_type=SchoolItemType.MESSAGE, title="Msg 2", content="Hello 2"),
    ]
    batch_time = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
    batch_text = format_batch_for_doc(items, batch_time=batch_time)

    assert "LIBRUS UPDATE SYNC: 2026-10-15 12:00:00 UTC (2 new items)" in batch_text
    assert "Msg 1" in batch_text
    assert "Msg 2" in batch_text


def test_format_batch_for_doc_empty():
    """Empty list returns empty string."""
    assert format_batch_for_doc([]) == ""
