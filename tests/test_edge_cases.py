"""Explicit tests for boundary values, error paths, and edge cases."""

from unittest.mock import MagicMock, patch
import pytest
import responses

from librus_collector.cli import cmd_run
from librus_collector.google_docs import GoogleDocsSync
from librus_collector.librus_client import (
    API_BASE_URL,
    API_OAUTH_AUTHORIZATION_URL,
    LibrusClient,
    SYNERGIA_BASE_URL,
    SYNERGIA_PORTAL_LOGIN_URL,
)
from librus_collector.models import SchoolFeedItem, SchoolItemType
from librus_collector.scheduler import DaemonScheduler
from librus_collector.security import LibrusAuthError


@responses.activate
def test_oauth_redirect_limit_exceeded():
    """Verify that an infinite redirect loop in OAuth terminates with LibrusAuthError."""
    client = LibrusClient("user", "pass")

    responses.add(
        responses.GET,
        SYNERGIA_PORTAL_LOGIN_URL,
        status=302,
        headers={"Location": f"{API_BASE_URL}/OAuth/Authorization?client_id=46"},
    )
    responses.add(
        responses.GET,
        f"{API_BASE_URL}/OAuth/Authorization?client_id=46",
        status=200,
    )
    responses.add(
        responses.POST,
        API_OAUTH_AUTHORIZATION_URL,
        json={"status": "ok", "goTo": "/OAuth/loop"},
        status=200,
    )

    # Endless redirect loop on /OAuth/loop
    responses.add(
        responses.GET,
        f"{API_BASE_URL}/OAuth/loop",
        status=302,
        headers={"Location": f"{API_BASE_URL}/OAuth/loop"},
    )

    with pytest.raises(LibrusAuthError, match="redirect limit exceeded"):
        client.login_sync()


def test_decode_message_content_edge_cases():
    """Verify non-base64 or malformed message content does not fail."""
    # Plain text that is not valid base64
    raw_plain = "Zwykły tekst, nie base64!"
    assert LibrusClient._decode_message_content(raw_plain) == raw_plain

    # Empty string
    assert LibrusClient._decode_message_content("") == ""

    # None-like or empty
    assert LibrusClient._decode_message_content(None) == ""


def test_google_docs_api_error_falls_back_to_local_file(tmp_path):
    """When Google Docs API returns an error during batchUpdate, fall back to local file."""
    fallback_file = tmp_path / "fallback_from_error.md"
    sync = GoogleDocsSync(
        doc_id="failing_doc_id",
        local_fallback_file=str(fallback_file),
    )

    mock_service = MagicMock()
    mock_docs = MagicMock()
    mock_service.documents.return_value = mock_docs
    mock_docs.get.return_value.execute.return_value = {"body": {"content": [{"endIndex": 10}]}}
    mock_docs.batchUpdate.return_value.execute.side_effect = RuntimeError("Google 503 Service Unavailable")

    sync._service = mock_service
    sync.__class__.is_google_configured = property(lambda self: True)

    res = sync.append_text("Emergency notice content")
    assert res["status"] == "success"
    assert res["target"] == "local_file"
    assert "Google 503 Service Unavailable" in res.get("google_error", "")
    assert fallback_file.exists()
    assert "Emergency notice content" in fallback_file.read_text(encoding="utf-8")


def test_daemon_scheduler_graceful_stop():
    """DaemonScheduler cleanly exits when signal is received."""
    mock_engine = MagicMock()
    mock_engine.run_sync.return_value = MagicMock(success=True, items_synced=0, target_type="local_file")

    scheduler = DaemonScheduler(engine=mock_engine, interval_seconds=1)
    
    # Simulate signal received immediately after start
    def stop_scheduler():
        scheduler.is_running = False

    # Stop after one iteration
    mock_engine.run_sync.side_effect = lambda: stop_scheduler()

    scheduler.start()
    assert scheduler.is_running is False
    assert mock_engine.run_sync.call_count == 1


def test_cli_run_missing_credentials_exits():
    """`librus-sync run` with missing credentials must exit with code 1."""
    with patch("librus_collector.cli.get_config") as mock_get_cfg:
        cfg = MagicMock()
        cfg.validate_librus_credentials.side_effect = ValueError("Librus credentials missing!")
        mock_get_cfg.return_value = cfg

        with pytest.raises(SystemExit) as exc:
            cmd_run(MagicMock())
        assert exc.value.code == 1
