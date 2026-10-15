"""Tests for CLI subcommands."""

from unittest.mock import patch
import pytest

from librus_collector.cli import main


def test_cli_help(capsys):
    """Test CLI help output."""
    with pytest.raises(SystemExit) as exc_info:
        with patch("sys.argv", ["librus-sync", "--help"]):
            main()
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "Hourly collector for Librus" in captured.out


def test_cli_status(capsys, monkeypatch, tmp_path):
    """Test CLI status output."""
    monkeypatch.setenv("LIBRUS_LOGIN", "sample_login")
    monkeypatch.setenv("LIBRUS_PASSWORD", "sample_pass")
    monkeypatch.setenv("LIBRUS_STATE_FILE", str(tmp_path / "st.json"))

    with patch("sys.argv", ["librus-sync", "status"]):
        main()

    captured = capsys.readouterr()
    assert "LIBRUS COLLECTOR STATUS" in captured.out
    assert "sample_login" in captured.out
    assert "s***s" in captured.out  # Masked password


def test_cli_install_cron(capsys):
    """Test CLI install-cron command."""
    with patch("sys.argv", ["librus-sync", "install-cron"]):
        main()

    captured = capsys.readouterr()
    assert "crontab -e" in captured.out
    assert "uv run librus-sync run" in captured.out


def test_cli_install_launchd(capsys):
    """Test CLI install-launchd command."""
    with patch("sys.argv", ["librus-sync", "install-launchd"]):
        main()

    captured = capsys.readouterr()
    assert "<!DOCTYPE plist" in captured.out
    assert "<integer>3600</integer>" in captured.out


def test_cli_run_force():
    """Test CLI run accepts --force flag and passes it to engine."""
    with patch("librus_collector.cli.get_config") as mock_cfg, \
         patch("librus_collector.cli.LibrusSyncEngine") as mock_engine_cls:
        cfg = mock_cfg.return_value
        cfg.validate_librus_credentials.return_value = None
        mock_engine = mock_engine_cls.return_value
        mock_engine.run_sync.return_value.success = True
        mock_engine.run_sync.return_value.items_found = 10
        mock_engine.run_sync.return_value.items_synced = 10
        mock_engine.run_sync.return_value.target_type = "local_file"
        mock_engine.run_sync.return_value.doc_id = None

        with patch("sys.argv", ["librus-sync", "run", "--force"]):
            main()

        mock_engine.run_sync.assert_called_once_with(force=True)


def test_cli_clean(capsys, tmp_path):
    """Test CLI clean command."""
    local_file = tmp_path / "updates.md"
    local_file.write_text("Sample content", encoding="utf-8")
    state_file = tmp_path / ".state.json"

    with patch("librus_collector.cli.get_config") as mock_cfg, \
         patch("librus_collector.cli.GoogleDocsSync") as mock_docs_cls, \
         patch("librus_collector.cli.SyncStateManager") as mock_state_cls:
        mock_cfg.return_value.local_export_path = str(local_file)
        mock_cfg.return_value.state_file_path = str(state_file)
        mock_docs = mock_docs_cls.return_value
        mock_docs.clean_local_file.return_value = 500
        mock_state = mock_state_cls.return_value
        mock_state.clean_history.return_value = 10

        with patch("sys.argv", ["librus-sync", "clean"]):
            main()

    captured = capsys.readouterr()
    assert "Cleaned duplicate batches from" in captured.out
    assert "500 redundant characters" in captured.out
    assert "Cleaned 10 duplicate history entries" in captured.out


def test_cli_pull(capsys, tmp_path):
    """Test CLI pull command when Google Docs is configured."""
    local_file = tmp_path / "updates.md"

    with patch("librus_collector.cli.get_config") as mock_cfg, \
         patch("librus_collector.cli.GoogleDocsSync") as mock_docs_cls:
        mock_cfg.return_value.local_export_path = str(local_file)
        mock_cfg.return_value.google_doc_id = "doc_xyz"
        mock_docs = mock_docs_cls.return_value
        mock_docs.is_google_configured = True
        mock_docs.doc_id = "doc_xyz"
        mock_docs.sync_local_mirror_from_google.return_value = "Content from Google Doc"

        with patch("sys.argv", ["librus-sync", "pull"]):
            main()

    captured = capsys.readouterr()
    assert "Successfully synchronized local mirror from Google Doc" in captured.out
    assert "doc_xyz" in captured.out


def test_cli_push(capsys, tmp_path):
    """Test CLI push command when Google Docs is configured."""
    local_file = tmp_path / "updates.md"

    with patch("librus_collector.cli.get_config") as mock_cfg, \
         patch("librus_collector.cli.GoogleDocsSync") as mock_docs_cls:
        mock_cfg.return_value.local_export_path = str(local_file)
        mock_cfg.return_value.google_doc_id = "doc_xyz"
        mock_docs = mock_docs_cls.return_value
        mock_docs.is_google_configured = True
        mock_docs.doc_id = "doc_xyz"
        mock_docs.push_local_mirror_to_google.return_value = {
            "status": "success",
            "doc_url": "https://docs.google.com/document/d/doc_xyz/edit",
        }

        with patch("sys.argv", ["librus-sync", "push"]):
            main()

    captured = capsys.readouterr()
    assert "Successfully pushed local mirror to Google Doc" in captured.out
    assert "doc_xyz" in captured.out


def test_cli_assemble(capsys, tmp_path):
    """Test CLI assemble command."""
    with patch("librus_collector.cli.get_config") as mock_cfg, \
         patch("librus_collector.cli.LibrusSyncEngine") as mock_engine_cls:
        mock_cfg.return_value.items_dir = str(tmp_path / "items")
        mock_engine = mock_engine_cls.return_value
        mock_engine.assemble.return_value = {
            "status": "success",
            "target": "local_file",
            "total_items_assembled": 15,
            "local_mirror": "librus_updates.md",
        }

        with patch("sys.argv", ["librus-sync", "assemble"]):
            main()

    mock_engine.assemble.assert_called_once_with(group_by_type=True)
    captured = capsys.readouterr()
    assert "Compendium assembled successfully!" in captured.out
    assert "15" in captured.out


def test_cli_import(capsys, tmp_path):
    """Test CLI import command."""
    sample_md = tmp_path / "updates.md"
    sample_md.write_text("sample", encoding="utf-8")

    with patch("librus_collector.cli.get_config") as mock_cfg, \
         patch("librus_collector.cli.ItemStore") as mock_store_cls, \
         patch("librus_collector.cli.SyncStateManager") as mock_state_cls:
        mock_cfg.return_value.local_export_path = str(sample_md)
        mock_cfg.return_value.items_dir = str(tmp_path / "items")
        mock_cfg.return_value.state_file_path = str(tmp_path / "state.json")

        mock_store = mock_store_cls.return_value
        mock_store.import_from_markdown.return_value = ["item1", "item2"]

        with patch("sys.argv", ["librus-sync", "import"]):
            main()

    captured = capsys.readouterr()
    assert "Import completed!" in captured.out
    assert "2" in captured.out


