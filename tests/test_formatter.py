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


def test_clean_markdown_batches_empty():
    """Empty or whitespace text returns empty string."""
    from librus_collector.formatter import clean_markdown_batches
    assert clean_markdown_batches("") == ""
    assert clean_markdown_batches("   \n\n  ") == ""


def test_clean_markdown_batches_deduplicates_repeated_batch():
    """Verify that an exact duplicate batch is stripped out while unique batches are preserved."""
    from librus_collector.formatter import clean_markdown_batches

    batch1 = (
        "\n# ══════════════════════════════════════════════════════════════════════\n"
        "# LIBRUS UPDATE SYNC: 2026-10-15 09:00:00 UTC (1 new items)\n"
        "# ══════════════════════════════════════════════════════════════════════\n\n"
        "## [OGŁOSZENIE / ANNOUNCEMENT] Konkurs\n"
        "• Data / Date: 2026-10-10\n\n"
        "### Treść / Content:\n"
        "Szczegóły konkursu.\n\n---\n"
    )

    batch_dup = (
        "\n# ══════════════════════════════════════════════════════════════════════\n"
        "# LIBRUS UPDATE SYNC: 2026-10-15 09:10:00 UTC (1 new items)\n"
        "# ══════════════════════════════════════════════════════════════════════\n\n"
        "## [OGŁOSZENIE / ANNOUNCEMENT] Konkurs\n"
        "• Data / Date: 2026-10-10\n\n"
        "### Treść / Content:\n"
        "Szczegóły konkursu.\n\n---\n"
    )

    batch_new = (
        "\n# ══════════════════════════════════════════════════════════════════════\n"
        "# LIBRUS UPDATE SYNC: 2026-10-15 10:00:00 UTC (1 new items)\n"
        "# ══════════════════════════════════════════════════════════════════════\n\n"
        "## [WIADOMOŚĆ / MESSAGE] Wycieczka\n"
        "• Data / Date: 2026-10-11\n\n"
        "### Treść / Content:\n"
        "Szczegóły wycieczki.\n\n---\n"
    )

    combined = batch1 + "\n\n" + batch_dup + "\n\n" + batch_new
    cleaned = clean_markdown_batches(combined)

    # Dup batch should be removed
    assert cleaned.count("LIBRUS UPDATE SYNC") == 2
    assert "2026-10-15 09:00:00 UTC" in cleaned
    assert "2026-10-15 09:10:00 UTC" not in cleaned
    assert "2026-10-15 10:00:00 UTC" in cleaned
    assert cleaned.count("Konkurs") == 1
    assert cleaned.count("Wycieczka") == 1


def test_clean_markdown_batches_normalizes_smashed_header():
    """Verify that header lines accidentally concatenated without newlines are separated."""
    from librus_collector.formatter import clean_markdown_batches

    smashed = (
        "# ══════════════════════════════════════════════════════════════════════"
        "# LIBRUS UPDATE SYNC: 2026-10-15 09:42:17 UTC (1 new items)"
        "# ══════════════════════════════════════════════════════════════════════\n\n"
        "## [OGŁOSZENIE / ANNOUNCEMENT] Spotkanie\n"
        "### Treść / Content:\n"
        "Treść spotkania.\n"
    )

    cleaned = clean_markdown_batches(smashed)
    assert "# ══════════════════════════════════════════════════════════════════════\n# LIBRUS UPDATE SYNC" in cleaned
    assert "Spotkanie" in cleaned


def test_clean_markdown_batches_preserves_same_title_different_content():
    """Verify that items sharing generic titles (like calendar events) are preserved if content differs."""
    from librus_collector.formatter import clean_markdown_batches

    batch1 = (
        "# ══════════════════════════════════════════════════════════════════════\n"
        "# LIBRUS UPDATE SYNC: 2026-10-15 08:00:00 UTC (1 new items)\n"
        "# ══════════════════════════════════════════════════════════════════════\n\n"
        "## [WYDARZENIE / EVENT] Terminarz: Wydarzenie / Sprawdzian\n"
        "• Data / Date: 2026-10-16\n\n"
        "### Treść / Content:\n"
        "School trip on Monday.\n\n---\n"
    )

    batch2 = (
        "# ══════════════════════════════════════════════════════════════════════\n"
        "# LIBRUS UPDATE SYNC: 2026-10-15 09:00:00 UTC (1 new items)\n"
        "# ══════════════════════════════════════════════════════════════════════\n\n"
        "## [WYDARZENIE / EVENT] Terminarz: Wydarzenie / Sprawdzian\n"
        "• Data / Date: 2026-10-20\n\n"
        "### Treść / Content:\n"
        "Parent meeting on Friday.\n\n---\n"
    )

    combined = batch1 + "\n\n" + batch2
    cleaned = clean_markdown_batches(combined)

    assert "School trip on Monday." in cleaned
    assert "Parent meeting on Friday." in cleaned
    assert cleaned.count("Terminarz: Wydarzenie / Sprawdzian") == 2
    assert cleaned.count("LIBRUS UPDATE SYNC") == 2


def test_clean_markdown_batches_partial_batch_deduplication():
    """Verify partial batch deduplication removes repeated items and updates header count."""
    from librus_collector.formatter import clean_markdown_batches

    batch1 = (
        "# ══════════════════════════════════════════════════════════════════════\n"
        "# LIBRUS UPDATE SYNC: 2026-10-15 08:00:00 UTC (1 new items)\n"
        "# ══════════════════════════════════════════════════════════════════════\n\n"
        "## [OGŁOSZENIE / ANNOUNCEMENT] Konkurs 1\n"
        "### Treść / Content:\n"
        "Treść 1.\n\n---\n"
    )

    # Batch 2 has Konkurs 1 (already seen) and Konkurs 2 (brand new)
    batch2 = (
        "# ══════════════════════════════════════════════════════════════════════\n"
        "# LIBRUS UPDATE SYNC: 2026-10-15 09:00:00 UTC (2 new items)\n"
        "# ══════════════════════════════════════════════════════════════════════\n\n"
        "## [OGŁOSZENIE / ANNOUNCEMENT] Konkurs 1\n"
        "### Treść / Content:\n"
        "Treść 1.\n\n---\n\n"
        "## [OGŁOSZENIE / ANNOUNCEMENT] Konkurs 2\n"
        "### Treść / Content:\n"
        "Treść 2.\n\n---\n"
    )

    combined = batch1 + "\n\n" + batch2
    cleaned = clean_markdown_batches(combined)

    assert cleaned.count("Konkurs 1") == 1
    assert cleaned.count("Konkurs 2") == 1
    # Header for batch 2 should be updated from 2 new items to 1 new items
    assert "(1 new items)" in cleaned

