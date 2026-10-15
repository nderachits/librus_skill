"""Daemon scheduler and cron / launchd configuration generator for hourly execution."""

from datetime import datetime, timezone, timedelta
import logging
from pathlib import Path
import signal
import sys
import time
from typing import Optional

from librus_collector.sync_engine import LibrusSyncEngine

logger = logging.getLogger(__name__)


class DaemonScheduler:
    """Manages continuous hourly sync cycles with graceful termination."""

    def __init__(self, engine: Optional[LibrusSyncEngine] = None, interval_seconds: int = 3600):
        self.engine = engine or LibrusSyncEngine()
        self.interval_seconds = interval_seconds
        self.is_running = False
        self._setup_signals()

    def _setup_signals(self) -> None:
        """Register signal handlers for clean exit."""
        signal.signal(signal.SIGINT, self._handle_exit)
        signal.signal(signal.SIGTERM, self._handle_exit)

    def _handle_exit(self, signum, frame) -> None:
        logger.info("Received termination signal (%d). Shutting down scheduler gracefully...", signum)
        self.is_running = False

    def start(self) -> None:
        """Start daemon loop running sync every interval_seconds."""
        self.is_running = True
        logger.info(
            "Librus Collector daemon started. Syncing every %d seconds (%0.1f hours). Press Ctrl+C to stop.",
            self.interval_seconds,
            self.interval_seconds / 3600,
        )

        while self.is_running:
            try:
                res = self.engine.run_sync()
                if res.success:
                    logger.info(
                        "Sync completed: %d new items synced to %s.",
                        res.items_synced,
                        res.target_type,
                    )
                else:
                    logger.warning("Sync run reported failure: %s", res.error_message)

            except Exception as e:
                logger.error("Unexpected error in sync daemon cycle: %s", e)

            # Sleep in small increments to respond promptly to termination signals
            next_run = datetime.now(timezone.utc) + timedelta(seconds=self.interval_seconds)
            logger.info("Next sync scheduled for: %s", next_run.strftime("%Y-%m-%d %H:%M:%S UTC"))

            elapsed = 0
            while self.is_running and elapsed < self.interval_seconds:
                sleep_chunk = min(1.0, self.interval_seconds - elapsed)
                time.sleep(sleep_chunk)
                elapsed += sleep_chunk

        logger.info("Librus Collector daemon stopped.")


def generate_crontab_line(project_dir: Path) -> str:
    """Generate crontab line for hourly execution."""
    abs_dir = project_dir.resolve()
    return f"0 * * * * cd {abs_dir} && uv run librus-sync run >> {abs_dir}/sync.log 2>&1"


def generate_launchd_plist(project_dir: Path, label: str = "com.librus.collector") -> str:
    """Generate macOS launchd plist for hourly execution."""
    abs_dir = project_dir.resolve()
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>WorkingDirectory</key>
    <string>{abs_dir}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/uv</string>
        <string>run</string>
        <string>librus-sync</string>
        <string>run</string>
    </array>
    <key>StartInterval</key>
    <integer>3600</integer>
    <key>StandardOutPath</key>
    <string>{abs_dir}/sync.log</string>
    <key>StandardErrorPath</key>
    <string>{abs_dir}/sync.log</string>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
"""
