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

