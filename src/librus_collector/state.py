"""State management and deduplication for Librus sync runs."""

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from typing import List, Optional, Set

from librus_collector.models import SchoolFeedItem

logger = logging.getLogger(__name__)

DEFAULT_STATE_FILE = ".librus_state.json"


class SyncStateManager:
    """Tracks previously synced items to prevent duplicates in Google Docs."""

    def __init__(self, state_file_path: Optional[str] = None):
        self.path = Path(state_file_path or os.getenv("LIBRUS_STATE_FILE", DEFAULT_STATE_FILE))
        self.synced_ids: Set[str] = set()
        self.last_sync_timestamp: Optional[str] = None
        self.history: List[dict] = []
        self._load()

    def _load(self) -> None:
        """Load state from JSON file if it exists."""
        if not self.path.exists():
            logger.debug("State file %s does not exist yet. Starting fresh.", self.path)
            return

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.synced_ids = set(data.get("synced_ids", []))
                self.last_sync_timestamp = data.get("last_sync_timestamp")
                self.history = data.get("history", [])
                logger.debug("Loaded %d synced IDs from %s", len(self.synced_ids), self.path)
        except Exception as e:
            logger.warning("Failed to load state file %s: %s. Re-initializing empty state.", self.path, e)
            self.synced_ids = set()

    def filter_new(self, items: List[SchoolFeedItem]) -> List[SchoolFeedItem]:
        """Return only items whose ID has not been synced yet."""
        new_items = [item for item in items if item.id not in self.synced_ids]
        logger.info(
            "Checked %d total items; %d are new, %d already synced",
            len(items),
            len(new_items),
            len(items) - len(new_items),
        )
        return new_items

    def mark_synced(self, items: List[SchoolFeedItem], reset: bool = False) -> None:
        """Record items as synced and save atomically to disk."""
        if not items:
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        self.last_sync_timestamp = now_iso

        if reset:
            self.synced_ids = set()
            self.history = []

        for item in items:
            if item.id not in self.synced_ids:
                self.history.append({
                    "id": item.id,
                    "type": item.item_type.value,
                    "title": item.title,
                    "author": item.author,
                    "date": item.date,
                    "synced_at": now_iso,
                })
            self.synced_ids.add(item.id)

        # Keep history capped at 1000 latest entries to manage file size
        if len(self.history) > 1000:
            self.history = self.history[-1000:]

        self._save()

    def clean_history(self) -> int:
        """
        Deduplicate entries in history so that each item ID only occurs once.
        Keeps the latest entry for each item ID.
        Returns the number of duplicate entries removed.
        """
        seen = set()
        unique_history = []
        for entry in reversed(self.history):
            item_id = entry.get("id")
            if item_id and item_id not in seen:
                seen.add(item_id)
                unique_history.append(entry)
        unique_history.reverse()
        removed = len(self.history) - len(unique_history)
        if removed > 0:
            self.history = unique_history
            self._save()
            logger.info("Cleaned %d duplicate entries from history in %s", removed, self.path)
        return removed

    def _save(self) -> None:
        """Save state to file atomically using a temporary file."""
        data = {
            "last_sync_timestamp": self.last_sync_timestamp,
            "total_synced_count": len(self.synced_ids),
            "synced_ids": list(self.synced_ids),
            "history": self.history,
        }

        temp_path = self.path.with_suffix(".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            temp_path.replace(self.path)
            logger.debug("Saved state with %d IDs to %s", len(self.synced_ids), self.path)
        except Exception as e:
            logger.error("Failed to save state file %s: %s", self.path, e)
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass

    def get_summary(self) -> dict:
        """Return summary of current synchronization state."""
        return {
            "state_file": str(self.path),
            "total_synced_items": len(self.synced_ids),
            "last_sync_timestamp": self.last_sync_timestamp,
            "recent_entries_count": len(self.history),
        }
