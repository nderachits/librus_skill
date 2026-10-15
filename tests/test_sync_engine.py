"""Tests for end-to-end sync engine coordination."""

from unittest.mock import MagicMock
from librus_collector.config import AppConfig
from librus_collector.google_docs import GoogleDocsSync
from librus_collector.models import SchoolFeedItem, SchoolItemType
from librus_collector.state import SyncStateManager
from librus_collector.sync_engine import LibrusSyncEngine


def test_sync_engine_end_to_end(tmp_path):
    """Verify complete cycle: fetch -> filter -> format -> append -> save state."""
    state_file = tmp_path / "state.json"
    fallback_file = tmp_path / "updates.md"

    config = AppConfig(
        librus_login="test_user",
        librus_password="test_password",
        local_export_path=str(fallback_file),
        state_file_path=str(state_file),
        items_dir=str(tmp_path / "items"),
    )

    state_mgr = SyncStateManager(str(state_file))
    docs_sync = GoogleDocsSync(local_fallback_file=str(fallback_file))

    # Mock LibrusClient
    mock_client = MagicMock()
    items = [
        SchoolFeedItem(
            id="ann:001",
            item_type=SchoolItemType.ANNOUNCEMENT,
            title="Dzień Nauczyciela",
            author="Dyrekcja",
            date="2026-10-14",
            content="Akademia odbędzie się w piątek.",
        ),
        SchoolFeedItem(
            id="msg:002",
            item_type=SchoolItemType.MESSAGE,
            title="Sprawdzian z geografii",
            author="Nauczyciel Geografii",
            date="2026-10-15",
            content="Sprawdzian obejmuje dział 3.",
        ),
    ]
    mock_client.collect_all.return_value = items

    engine = LibrusSyncEngine(
        config=config,
        librus_client=mock_client,
        state_manager=state_mgr,
        docs_sync=docs_sync,
    )

    # First run: both items synced
    result = engine.run_sync()
    assert result.success is True
    assert result.items_found == 2
    assert result.items_new == 2
    assert result.items_synced == 2
    assert result.target_type == "local_file"
    assert mock_client.populate_message_content.call_count == 1

    # Verify content written to local file
    content = fallback_file.read_text(encoding="utf-8")
    assert "Dzień Nauczyciela" in content
    assert "Sprawdzian z geografii" in content

    # Second run with same items: 0 new items, populate_message_content not called again
    second_result = engine.run_sync()
    assert second_result.success is True
    assert second_result.items_found == 2
    assert second_result.items_new == 0
    assert second_result.items_synced == 0
    assert mock_client.populate_message_content.call_count == 1


def test_sync_engine_handles_exception(tmp_path):
    """Engine catches unexpected errors and reports failure without crashing."""
    config = AppConfig(
        librus_login="user",
        librus_password="pass",
        state_file_path=str(tmp_path / "state.json"),
        items_dir=str(tmp_path / "items"),
    )
    mock_client = MagicMock()
    mock_client.collect_all.side_effect = RuntimeError("Connection dropped")

    engine = LibrusSyncEngine(config=config, librus_client=mock_client)
    res = engine.run_sync()

    assert res.success is False
    assert "Connection dropped" in res.error_message


def test_sync_engine_force_resync(tmp_path):
    """Verify force=True bypasses deduplication and resyncs all items."""
    state_file = tmp_path / "state.json"
    fallback_file = tmp_path / "updates.md"

    config = AppConfig(
        librus_login="test_user",
        librus_password="test_password",
        local_export_path=str(fallback_file),
        state_file_path=str(state_file),
        items_dir=str(tmp_path / "items"),
    )

    state_mgr = SyncStateManager(str(state_file))
    docs_sync = GoogleDocsSync(local_fallback_file=str(fallback_file))

    mock_client = MagicMock()
    item = SchoolFeedItem(
        id="msg:100",
        item_type=SchoolItemType.MESSAGE,
        title="Ważne ogłoszenie",
        content="Podgląd",
    )
    mock_client.collect_all.return_value = [item]

    engine = LibrusSyncEngine(
        config=config,
        librus_client=mock_client,
        state_manager=state_mgr,
        docs_sync=docs_sync,
    )

    # First sync
    res1 = engine.run_sync()
    assert res1.items_synced == 1

    # Normal second sync has 0 items
    res2 = engine.run_sync(force=False)
    assert res2.items_synced == 0

    # Forced sync resyncs the item and rewrites local file without duplication
    res3 = engine.run_sync(force=True)
    assert res3.items_synced == 1
    assert mock_client.populate_message_content.call_count == 2

    # Verify fallback file was rewritten without duplicates
    content = fallback_file.read_text(encoding="utf-8")
    assert content.count("Ważne ogłoszenie") == 1
    assert content.count("LIBRUS UPDATE SYNC") == 1

