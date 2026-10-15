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


def test_extract_doc_id():
    """Verify document ID is extracted from raw ID or full browser URL."""
    from librus_collector.google_docs import extract_doc_id

    # Raw ID
    assert extract_doc_id("1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms") == "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms"
    # Full edit URL
    assert extract_doc_id("https://docs.google.com/document/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/edit") == "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms"
    # URL with fragment/params
    assert extract_doc_id("https://docs.google.com/document/d/my-doc_123/edit?usp=sharing#heading=h.123") == "my-doc_123"
    # Empty
    assert extract_doc_id("") is None
    assert extract_doc_id(None) is None


def test_google_docs_tabbed_document(tmp_path):
    """Verify document with tabs structure is parsed correctly."""
    sync = GoogleDocsSync(
        doc_id="tabbed_doc_123",
        local_fallback_file=str(tmp_path / "fallback.md"),
    )

    mock_service = MagicMock()
    mock_docs = MagicMock()
    mock_service.documents.return_value = mock_docs

    # Mock response with tabs instead of top-level body
    mock_get = MagicMock()
    mock_get.execute.return_value = {
        "tabs": [
            {
                "documentTab": {
                    "body": {
                        "content": [{"endIndex": 220}]
                    }
                }
            }
        ]
    }
    mock_docs.get.return_value = mock_get

    mock_batch = MagicMock()
    mock_batch.execute.return_value = {"replies": []}
    mock_docs.batchUpdate.return_value = mock_batch

    sync._service = mock_service
    sync.__class__.is_google_configured = property(lambda self: True)

    res = sync.append_text("Content for tabbed doc")
    assert res["status"] == "success"
    call_kwargs = mock_docs.batchUpdate.call_args[1]
    insert_req = call_kwargs["body"]["requests"][0]["insertText"]
    assert insert_req["location"]["index"] == 219


def test_google_docs_rewrite_mode(tmp_path):
    """When rewrite=True, deleteContentRange is called before insertText, and local mirror is rewritten."""
    local_file = tmp_path / "mirror.md"
    local_file.write_text("Old historical content", encoding="utf-8")

    sync = GoogleDocsSync(
        doc_id="doc_to_rewrite",
        local_fallback_file=str(local_file),
    )

    mock_service = MagicMock()
    mock_docs = MagicMock()
    mock_service.documents.return_value = mock_docs

    # Existing doc with 300 characters
    mock_docs.get.return_value.execute.return_value = {
        "body": {"content": [{"endIndex": 300}]}
    }
    mock_docs.batchUpdate.return_value.execute.return_value = {"replies": []}

    sync._service = mock_service
    sync.__class__.is_google_configured = property(lambda self: True)

    res = sync.append_text("Brand new replacement content", rewrite=True)
    assert res["status"] == "success"
    assert res["mode"] == "rewrite"

    # Verify requests sent to batchUpdate
    call_kwargs = mock_docs.batchUpdate.call_args[1]
    requests = call_kwargs["body"]["requests"]
    assert len(requests) == 2

    # Request 1: deleteContentRange from 1 to 299
    assert requests[0]["deleteContentRange"]["range"]["startIndex"] == 1
    assert requests[0]["deleteContentRange"]["range"]["endIndex"] == 299

    # Request 2: insertText at index 1
    assert requests[1]["insertText"]["location"]["index"] == 1
    assert requests[1]["insertText"]["text"] == "Brand new replacement content"

    # Verify local mirror was overwritten, NOT appended
    mirror_content = local_file.read_text(encoding="utf-8")
    assert mirror_content == "Brand new replacement content"
    assert "Old historical content" not in mirror_content


def test_google_docs_local_mirror_updated_on_successful_google_sync(tmp_path):
    """When Google Docs succeeds, local mirror file is also updated."""
    local_file = tmp_path / "mirror.md"
    sync = GoogleDocsSync(
        doc_id="doc_mirror_test",
        local_fallback_file=str(local_file),
    )

    mock_service = MagicMock()
    mock_docs = MagicMock()
    mock_service.documents.return_value = mock_docs
    mock_docs.get.return_value.execute.return_value = {
        "body": {"content": [{"endIndex": 50}]}
    }
    mock_docs.batchUpdate.return_value.execute.return_value = {}

    sync._service = mock_service
    sync.__class__.is_google_configured = property(lambda self: True)

    res = sync.append_text("Batch 1 content\n", rewrite=False)
    assert res["status"] == "success"
    assert res["target"] == "google_doc"
    assert local_file.exists()
    assert local_file.read_text(encoding="utf-8") == "Batch 1 content\n"

    # Append second batch
    res2 = sync.append_text("Batch 2 content\n", rewrite=False)
    assert res2["status"] == "success"
    assert local_file.read_text(encoding="utf-8") == "Batch 1 content\nBatch 2 content\n"


def test_google_docs_clean_local_file(tmp_path):
    """clean_local_file removes duplicate batches from local file and reports saved characters."""
    local_file = tmp_path / "updates.md"
    batch = (
        "\n# ══════════════════════════════════════════════════════════════════════\n"
        "# LIBRUS UPDATE SYNC: 2026-10-15 09:00:00 UTC (1 new items)\n"
        "# ══════════════════════════════════════════════════════════════════════\n\n"
        "## [OGŁOSZENIE / ANNOUNCEMENT] Ogłoszenie testowe\n"
        "• Data / Date: 2026-10-10\n\n"
        "### Treść / Content:\n"
        "Treść ogłoszenia.\n\n---\n"
    )
    # Write duplicate batches
    local_file.write_text(batch + "\n\n" + batch, encoding="utf-8")

    sync = GoogleDocsSync(local_fallback_file=str(local_file))
    saved = sync.clean_local_file()
    assert saved > 0

    cleaned_content = local_file.read_text(encoding="utf-8")
    assert cleaned_content.count("LIBRUS UPDATE SYNC") == 1
    assert cleaned_content.count("Ogłoszenie testowe") == 1

    # Second call returns 0
    assert sync.clean_local_file() == 0


def test_google_docs_push_local_mirror(tmp_path):
    """Verify push_local_mirror_to_google uploads local file content to Google Doc."""
    local_file = tmp_path / "updates.md"
    local_file.write_text("# Local Content to Push\nSome local body text.", encoding="utf-8")

    sync = GoogleDocsSync(
        doc_id="remote_doc_to_push",
        local_fallback_file=str(local_file),
    )

    mock_service = MagicMock()
    mock_docs = MagicMock()
    mock_service.documents.return_value = mock_docs
    mock_docs.get.return_value.execute.return_value = {
        "body": {"content": [{"endIndex": 100}]}
    }
    mock_docs.batchUpdate.return_value.execute.return_value = {"replies": []}

    sync._service = mock_service
    sync.__class__.is_google_configured = property(lambda self: True)

    res = sync.push_local_mirror_to_google()
    assert res["status"] == "success"
    assert res["target"] == "google_doc"
    assert res["mode"] == "rewrite"

    call_kwargs = mock_docs.batchUpdate.call_args[1]
    requests = call_kwargs["body"]["requests"]
    assert requests[0]["deleteContentRange"]["range"]["startIndex"] == 1
    assert requests[1]["insertText"]["text"] == "# Local Content to Push\nSome local body text."


def test_google_docs_reconstructs_mirror_when_missing(tmp_path):
    """When local mirror does not exist on incremental sync, reconstruct full mirror from Google Doc."""
    local_file = tmp_path / "mirror.md"
    # Ensure local file does NOT exist
    assert not local_file.exists()

    sync = GoogleDocsSync(
        doc_id="remote_doc",
        local_fallback_file=str(local_file),
    )

    mock_service = MagicMock()
    mock_docs = MagicMock()
    mock_service.documents.return_value = mock_docs
    mock_docs.get.return_value.execute.return_value = {
        "body": {
            "content": [
                {
                    "paragraph": {
                        "elements": [
                            {"textRun": {"content": "# Historical Doc\n"}},
                        ]
                    },
                    "endIndex": 20,
                }
            ]
        }
    }
    mock_docs.batchUpdate.return_value.execute.return_value = {}

    sync._service = mock_service
    sync.__class__.is_google_configured = property(lambda self: True)

    res = sync.append_text("New batch\n", rewrite=False)
    assert res["status"] == "success"
    assert local_file.exists()
    content = local_file.read_text(encoding="utf-8")
    assert "Historical Doc" in content
    assert "New batch" in content


def test_google_docs_table_text_extraction(tmp_path):
    """Verify recursive extraction handles tables in document content."""
    sync = GoogleDocsSync(local_fallback_file=str(tmp_path / "updates.md"))

    doc = {
        "body": {
            "content": [
                {
                    "paragraph": {
                        "elements": [{"textRun": {"content": "Intro paragraph\n"}}]
                    }
                },
                {
                    "table": {
                        "tableRows": [
                            {
                                "tableCells": [
                                    {
                                        "content": [
                                            {
                                                "paragraph": {
                                                    "elements": [
                                                        {"textRun": {"content": "Cell 1 text\n"}}
                                                    ]
                                                }
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                },
            ]
        }
    }

    text = sync._extract_text_from_doc(doc)
    assert "Intro paragraph" in text
    assert "Cell 1 text" in text


def test_google_docs_tab_id_propagation(tmp_path):
    """Verify tabId is included in location and deleteContentRange when tabProperties has tabId."""
    local_file = tmp_path / "mirror.md"
    sync = GoogleDocsSync(
        doc_id="tabbed_doc",
        local_fallback_file=str(local_file),
    )

    mock_service = MagicMock()
    mock_docs = MagicMock()
    mock_service.documents.return_value = mock_docs
    mock_docs.get.return_value.execute.return_value = {
        "tabs": [
            {
                "tabProperties": {"tabId": "tab_algebra"},
                "documentTab": {
                    "body": {
                        "content": [{"endIndex": 150}]
                    }
                },
            }
        ]
    }
    mock_docs.batchUpdate.return_value.execute.return_value = {}

    sync._service = mock_service
    sync.__class__.is_google_configured = property(lambda self: True)

    res = sync.append_text("Tabbed content", rewrite=True)
    assert res["status"] == "success"

    call_kwargs = mock_docs.batchUpdate.call_args[1]
    requests = call_kwargs["body"]["requests"]
    assert requests[0]["deleteContentRange"]["range"]["tabId"] == "tab_algebra"
    assert requests[1]["insertText"]["location"]["tabId"] == "tab_algebra"


