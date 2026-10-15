"""Tests for state management and deduplication."""

import json
from librus_collector.models import SchoolFeedItem, SchoolItemType
from librus_collector.state import SyncStateManager


def test_state_manager_empty_on_missing_file(tmp_path):
    """Missing state file should initialize empty without error."""
    state_file = tmp_path / ".test_state.json"
    manager = SyncStateManager(str(state_file))
    assert len(manager.synced_ids) == 0
    assert manager.last_sync_timestamp is None


def test_state_manager_corrupted_file_recovery(tmp_path):
    """Corrupted JSON in state file should not crash the collector."""
    state_file = tmp_path / ".test_state.json"
    state_file.write_text("CORRUPTED NOT JSON {{{", encoding="utf-8")

    manager = SyncStateManager(str(state_file))
    assert len(manager.synced_ids) == 0


def test_filter_new_and_mark_synced(tmp_path):
    """Verify deduplication filtering and persistence."""
    state_file = tmp_path / ".test_state.json"
    manager = SyncStateManager(str(state_file))

    item1 = SchoolFeedItem(
        id="announcement:1",
        item_type=SchoolItemType.ANNOUNCEMENT,
        title="Test 1",
    )
    item2 = SchoolFeedItem(
        id="message:2",
        item_type=SchoolItemType.MESSAGE,
        title="Test 2",
    )

    # First run: both are new
    new_items = manager.filter_new([item1, item2])
    assert len(new_items) == 2

    # Mark item1 as synced
    manager.mark_synced([item1])

    # Second check: only item2 is new
    new_items_2 = manager.filter_new([item1, item2])
    assert len(new_items_2) == 1
    assert new_items_2[0].id == "message:2"

    # Reload state from disk in a fresh manager instance
    fresh_manager = SyncStateManager(str(state_file))
    assert "announcement:1" in fresh_manager.synced_ids
    assert fresh_manager.last_sync_timestamp is not None

    # Filter with fresh manager
    assert len(fresh_manager.filter_new([item1])) == 0
    assert len(fresh_manager.filter_new([item2])) == 1


def test_history_capping(tmp_path):
    """History list is capped to prevent unbound file growth."""
    state_file = tmp_path / ".test_state.json"
    manager = SyncStateManager(str(state_file))

    # Add 1050 items
    items = [
        SchoolFeedItem(id=f"msg:{i}", item_type=SchoolItemType.MESSAGE, title=f"Msg {i}")
        for i in range(1050)
    ]
    manager.mark_synced(items)

    assert len(manager.history) == 1000
    assert len(manager.synced_ids) == 1050
