"""Tests for Google Docs sync and local fallback handling."""

from unittest.mock import MagicMock
from librus_collector.google_docs import GoogleDocsSync


def test_google_docs_local_fallback_when_unconfigured(tmp_path):
    """When no Google credentials exist, fall back to local file safely."""
    local_file = tmp_path / "test_updates.md"
    sync = GoogleDocsSync(
        doc_id=None,
        local_fallback_file=str(local_file),
    )

    res = sync.append_text("# Test Sync Header\nSome test content\n")
    assert res["status"] == "success"
    assert res["target"] == "local_file"
    assert local_file.exists()
    assert "Test Sync Header" in local_file.read_text(encoding="utf-8")


def test_google_docs_noop_on_empty_text(tmp_path):
    """Appending empty string should return noop without error."""
    sync = GoogleDocsSync(local_fallback_file=str(tmp_path / "out.md"))
    res = sync.append_text("")
    assert res["status"] == "noop"


def test_google_docs_mocked_api_append(tmp_path):
    """Verify batchUpdate payload generation and index calculation."""
    sync = GoogleDocsSync(
        doc_id="mock_doc_id_999",
        local_fallback_file=str(tmp_path / "fallback.md"),
    )

    # Mock the Google Docs API service
    mock_service = MagicMock()
    mock_docs = MagicMock()
    mock_service.documents.return_value = mock_docs

    # Mock get() call returning content with endIndex = 150
    mock_get = MagicMock()
    mock_get.execute.return_value = {
        "body": {
            "content": [
                {"endIndex": 150}
            ]
        }
    }
    mock_docs.get.return_value = mock_get

    # Mock batchUpdate() call
    mock_batch = MagicMock()
    mock_batch.execute.return_value = {"replies": []}
    mock_docs.batchUpdate.return_value = mock_batch

    sync._service = mock_service

    # Monkey patch is_google_configured to True for this test
    sync.__class__.is_google_configured = property(lambda self: True)

    res = sync.append_text("New content to append")
    assert res["status"] == "success"
    assert res["target"] == "google_doc"
    assert res["doc_id"] == "mock_doc_id_999"

    # Verify insertText location was index 149 (endIndex 150 - 1)
    call_kwargs = mock_docs.batchUpdate.call_args[1]
    requests_sent = call_kwargs["body"]["requests"]
    assert len(requests_sent) == 1
    insert_req = requests_sent[0]["insertText"]
    assert insert_req["location"]["index"] == 149
    assert insert_req["text"] == "New content to append"
