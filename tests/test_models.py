"""Tests for data models and unique ID generation."""

from librus_collector.models import SchoolFeedItem, SchoolItemType, SyncResult


def test_school_feed_item_id_generation():
    """Verify deterministic unique IDs."""
    id1 = SchoolFeedItem.make_id(SchoolItemType.ANNOUNCEMENT, "short_id")
    assert id1 == "announcement:short_id"

    # Very long identifier gets hashed into 16-char sha256 hex
    long_key = "a" * 150
    id2 = SchoolFeedItem.make_id(SchoolItemType.MESSAGE, long_key)
    assert id2.startswith("message:")
    assert len(id2.split(":")[1]) == 16

    # Same input produces identical ID
    id3 = SchoolFeedItem.make_id(SchoolItemType.MESSAGE, long_key)
    assert id2 == id3


def test_school_feed_item_serialization():
    """Verify model instantiation and fields."""
    item = SchoolFeedItem(
        id="announcement:123",
        item_type=SchoolItemType.ANNOUNCEMENT,
        title="Wycieczka szkolna",
        author="Jan Kowalski",
        date="2026-10-15",
        content="Szczegóły wycieczki do muzeum.",
        attachments=["zgoda.pdf"],
        metadata={"category": "school_trips"},
    )
    assert item.id == "announcement:123"
    assert item.item_type == SchoolItemType.ANNOUNCEMENT
    assert item.title == "Wycieczka szkolna"
    assert item.attachments == ["zgoda.pdf"]


def test_sync_result_defaults():
    """Verify SyncResult summary model."""
    res = SyncResult(
        success=True,
        items_found=5,
        items_new=2,
        items_synced=2,
        doc_id="doc123",
    )
    assert res.success is True
    assert res.items_found == 5
    assert res.items_synced == 2
    assert res.doc_id == "doc123"
    assert res.error_message is None
