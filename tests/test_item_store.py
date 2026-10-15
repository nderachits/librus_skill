"""Tests for ItemStore and structured markdown message persistence (Option 3)."""

from datetime import datetime, timezone
import os
from pathlib import Path
import pytest

from librus_collector.formatter import assemble_full_document
from librus_collector.item_store import ItemStore
from librus_collector.models import (
    extract_sortable_timestamp,
    SchoolFeedItem,
    SchoolItemType,
)


def test_extract_sortable_timestamp():
    """Verify timestamp extraction across multiple date formats."""
    # Full ISO
    assert extract_sortable_timestamp("2026-10-15T06:13:04") == "2026-10-15_061304"
    assert extract_sortable_timestamp("2026-10-15 06:13:04") == "2026-10-15_061304"
    
    # Date with hours:minutes
    assert extract_sortable_timestamp("2026-10-15 08:30") == "2026-10-15_083000"

    # Date only
    assert extract_sortable_timestamp("2026-10-09") == "2026-10-09_000000"

    # Polish date
    assert extract_sortable_timestamp("15.10.2026") == "2026-10-15_000000"

    # Datetime object
    dt = datetime(2026, 9, 11, 15, 18, 54, tzinfo=timezone.utc)
    assert extract_sortable_timestamp(dt) == "2026-09-11_151854"

    # Empty / brak falls back to fallback_dt
    fallback = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert extract_sortable_timestamp("brak", fallback_dt=fallback) == "2026-01-01_120000"
    assert extract_sortable_timestamp("", fallback_dt=fallback) == "2026-01-01_120000"


def test_item_store_save_and_load(tmp_path):
    """Verify saving SchoolFeedItem to Markdown file with YAML frontmatter and loading back."""
    store = ItemStore(tmp_path)

    item = SchoolFeedItem(
        id="2026-10-15_061304_message_843897",
        item_type=SchoolItemType.MESSAGE,
        title="Wycieczka do filharmonii",
        author="Kruk-Zabawa Beata",
        date="2026-10-15T06:13:04",
        content="Drodzy Rodzice,\nZapraszamy na wycieczkę do filharmonii w przyszły wtorek.",
        attachments=["zgoda.pdf"],
        metadata={"source": "synergia_api"},
    )

    path = store.save_item(item)
    assert path.is_file()
    assert path.name == "2026-10-15_061304_message_843897.md"

    # Check file contents directly
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "id: 2026-10-15_061304_message_843897" in text
    assert "librus_id: message:843897" in text
    assert "item_type: MESSAGE" in text
    assert "title: Wycieczka do filharmonii" in text
    assert "author: Kruk-Zabawa Beata" in text
    assert "date: '2026-10-15T06:13:04'" in text or 'date: "2026-10-15T06:13:04"' in text or "date: 2026-10-15T06:13:04" in text
    assert "Zapraszamy na wycieczkę do filharmonii w przyszły wtorek." in text
    # Verify no duplicate headers in body
    assert "# [WIADOMOŚĆ / MESSAGE]" not in text
    assert "• **Data / Date:**" not in text
    assert "### Treść / Content:" not in text

    # Check that mtime was set to match item date for Finder Date Modified sorting
    stat = path.stat()
    assert abs(stat.st_mtime - item.sort_datetime.timestamp()) < 2.0

    # Load item back
    loaded = store.load_item(path)
    assert loaded is not None
    assert loaded.id == item.id
    assert loaded.item_type == SchoolItemType.MESSAGE
    assert loaded.title == item.title
    assert loaded.author == item.author
    assert loaded.date == item.date
    assert loaded.content == item.content
    assert loaded.attachments == ["zgoda.pdf"]
    assert loaded.metadata == {"source": "synergia_api"}


def test_item_store_finder_chronological_sorting(tmp_path):
    """
    Verify that filename sorting in macOS Finder naturally orders files chronologically.
    """
    store = ItemStore(tmp_path)

    # Create items with different timestamps in non-chronological insertion order
    item_sep = SchoolFeedItem(
        id="2026-09-15_100000_message_1",
        item_type=SchoolItemType.MESSAGE,
        title="September message",
        date="2026-09-15T10:00:00",
        content="Content 1",
    )
    item_oct_late = SchoolFeedItem(
        id="2026-10-15_061304_message_3",
        item_type=SchoolItemType.MESSAGE,
        title="October 15 message",
        date="2026-10-15T06:13:04",
        content="Content 3",
    )
    item_oct_early = SchoolFeedItem(
        id="2026-10-08_000000_announcement_2",
        item_type=SchoolItemType.ANNOUNCEMENT,
        title="October 8 announcement",
        date="2026-10-08",
        content="Content 2",
    )

    # Save in random order
    store.save_item(item_oct_late)
    store.save_item(item_sep)
    store.save_item(item_oct_early)

    # Directory file list sorted alphabetically (how macOS Finder sorts by Name)
    file_names = [f.name for f in store.list_item_files()]
    assert file_names == [
        "2026-09-15_100000_message_1.md",
        "2026-10-08_000000_announcement_2.md",
        "2026-10-15_061304_message_3.md",
    ]

    # Loading all items returns them in chronological order
    all_items = store.load_all_items(sort_by="date", reverse=False)
    assert len(all_items) == 3
    assert all_items[0].id == item_sep.id
    assert all_items[1].id == item_oct_early.id
    assert all_items[2].id == item_oct_late.id


def test_item_store_legacy_id_normalization(tmp_path):
    """Items created with legacy colon IDs are automatically assigned a sortable filename."""
    store = ItemStore(tmp_path)

    legacy_item = SchoolFeedItem(
        id="message:843897",
        item_type=SchoolItemType.MESSAGE,
        title="Legacy ID message",
        date="2026-10-15T06:13:04",
        content="Legacy body",
    )

    saved_path = store.save_item(legacy_item)
    # The file on disk starts with timestamp and has safe underscores
    assert saved_path.name == "2026-10-15_061304_message_843897.md"

    # Loaded back cleanly
    loaded = store.load_item(saved_path)
    assert loaded is not None
    assert loaded.title == "Legacy ID message"


def test_assemble_full_document():
    """Verify document compendium generation with categorized sections."""
    item_event = SchoolFeedItem(
        id="2026-10-20_080000_event_1",
        item_type=SchoolItemType.EVENT,
        title="Sprawdzian z matematyki",
        date="2026-10-20",
        content="Zakres: ułamki zwykłe i dziesiętne",
    )
    item_announcement = SchoolFeedItem(
        id="2026-10-09_000000_announcement_2",
        item_type=SchoolItemType.ANNOUNCEMENT,
        title="Konkurs plastyczny",
        author="Sylwia Suchta",
        date="2026-10-09",
        content="Dynia podróżniczka",
    )
    item_msg = SchoolFeedItem(
        id="2026-10-15_061304_message_3",
        item_type=SchoolItemType.MESSAGE,
        title="Zgoda na wyjście",
        author="Beata Kruk-Zabawa",
        date="2026-10-15T06:13:04",
        content="Proszę o podpisanie zgód",
    )

    items = [item_event, item_announcement, item_msg]

    doc = assemble_full_document(items, group_by_type=True)
    assert "LIBRUS SCHOOL UPDATES COMPENDIUM" in doc
    assert "Total items: 3" in doc
    assert "📅 TERMINARZ I WYDARZENIA / EVENTS & EXAMS (1)" in doc
    assert "📢 OGŁOSZENIA / ANNOUNCEMENTS (1)" in doc
    assert "✉️ WIADOMOŚCI / MESSAGES (1)" in doc
    assert "Sprawdzian z matematyki" in doc
    assert "Dynia podróżniczka" in doc
    assert "Beata Kruk-Zabawa" in doc


def test_import_from_markdown(tmp_path):
    """Verify importing items from existing markdown export file."""
    md_file = tmp_path / "updates.md"
    md_content = """
# ══════════════════════════════════════════════════════════════════════
# LIBRUS UPDATE SYNC: 2026-10-15 11:19:03 UTC (2 new items)
# ══════════════════════════════════════════════════════════════════════

## [OGŁOSZENIE / ANNOUNCEMENT] Konkurs plastyczny
• Data / Date: 2026-10-09
• Od / From: Sylwia Suchta
• Źródło / Source: synergia_ogloszenia

### Treść / Content:
KONKURS DYNIA PODRÓŻNICZKA
Zapraszamy do udziału!

---

## [WIADOMOŚĆ / MESSAGE] Wyjście do teatru
• Data / Date: 2026-10-15T08:30:00
• Od / From: Jan Kowalski
• Załączniki / Attachments: bilet.pdf

### Treść / Content:
Szczegóły wyjazdu do teatru.

---
"""
    md_file.write_text(md_content, encoding="utf-8")

    items_dir = tmp_path / "items"
    store = ItemStore(items_dir)
    imported = store.import_from_markdown(md_file)

    assert len(imported) == 2
    assert store.count() == 2

    # Check files in items_dir
    files = store.list_item_files()
    assert len(files) == 2
    # Alphabetical order: 2026-10-09 before 2026-10-15
    assert files[0].name.startswith("2026-10-09_000000_announcement_")
    assert files[1].name.startswith("2026-10-15_083000_message_")

    # Verify loaded item content
    ann = store.load_item(files[0])
    assert ann.item_type == SchoolItemType.ANNOUNCEMENT
    assert ann.title == "Konkurs plastyczny"
    assert ann.author == "Sylwia Suchta"
    assert "DYNIA PODRÓŻNICZKA" in ann.content
