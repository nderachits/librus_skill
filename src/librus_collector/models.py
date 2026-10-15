"""Data models for Librus Collector."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
import hashlib
import re
from pydantic import BaseModel, Field


class SchoolItemType(str, Enum):
    """Types of items retrieved from Librus."""
    ANNOUNCEMENT = "ANNOUNCEMENT"  # Ogłoszenie
    MESSAGE = "MESSAGE"            # Wiadomość
    HOMEWORK = "HOMEWORK"          # Zadanie domowe
    EVENT = "EVENT"                # Wydarzenie / Sprawdzian / Terminarz
    NOTE = "NOTE"                  # Uwaga / Informacja


def extract_sortable_timestamp(
    date_val: Optional[Any] = None,
    fallback_dt: Optional[datetime] = None,
) -> str:
    """
    Extract a sortable timestamp string (YYYY-MM-DD_HHMMSS) for macOS Finder ordering.
    Supports ISO datetimes, standard date strings, Polish date formats, and datetime objects.
    """
    if isinstance(date_val, datetime):
        return date_val.strftime("%Y-%m-%d_%H%M%S")

    if date_val and isinstance(date_val, str):
        val = date_val.strip()
        # 1. ISO format: 2026-10-15T06:13:04 or 2026-10-15 06:13:04
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})[T\s](\d{2}):(\d{2}):(\d{2})", val)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}_{m.group(4)}{m.group(5)}{m.group(6)}"

        # 2. Date with hour:minute: 2026-10-15 06:13
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})[T\s](\d{2}):(\d{2})", val)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}_{m.group(4)}{m.group(5)}00"

        # 3. Standard YYYY-MM-DD
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", val)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}_000000"

        # 4. Polish DD.MM.YYYY
        m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", val)
        if m:
            return f"{m.group(3)}-{m.group(2)}-{m.group(1)}_000000"

    dt = fallback_dt or datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%d_%H%M%S")


class SchoolFeedItem(BaseModel):
    """Normalized school notification or communication item."""

    id: str = Field(description="Sortable compound identifier for filesystem ordering")
    librus_id: str = Field(default="", description="Primary Librus identifier for deduplication")
    item_type: SchoolItemType = Field(description="Category of the notification")
    title: str = Field(description="Title, subject, or heading of the item")
    author: str = Field(default="", description="Sender, teacher, or authority name")
    date: str = Field(default="", description="Original date string from Librus")
    content: str = Field(default="", description="Body or details of the communication")
    attachments: List[str] = Field(default_factory=list, description="Attachment names or links")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional context")
    collected_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when the item was collected"
    )

    def model_post_init(self, __context: Any) -> None:
        """Ensure librus_id is populated from canonical_id if not explicitly provided."""
        if not self.librus_id:
            self.librus_id = self.canonical_id

    @property
    def canonical_id(self) -> str:
        """Return primary Librus deduplication ID (e.g. message:843897)."""
        if self.librus_id:
            return self.librus_id
        # Extract from compound sortable ID if prefixed with timestamp
        parts = self.id.split("_")
        if len(parts) >= 3 and re.match(r"^\d{4}-\d{2}-\d{2}$", parts[0]):
            return f"{parts[-2]}:{parts[-1]}"
        return self.id

    @classmethod
    def make_id(
        cls,
        item_type: SchoolItemType,
        unique_part: str,
        timestamp: Optional[Any] = None,
    ) -> str:
        """
        Create a reproducible unique ID.
        If timestamp is provided, prefixes with YYYY-MM-DD_HHMMSS so files sort chronologically in Finder.
        """
        clean_part = unique_part.strip().replace(":", "_")
        # Keep clean alphanumeric IDs readable (e.g. message:123456 or msg_999)
        if not re.match(r"^[a-zA-Z0-9_\-\.]{1,40}$", clean_part):
            # For multi-word, long, or special-char strings, hash with sha256 (16 hex = 64 bits)
            clean_part = hashlib.sha256(clean_part.encode("utf-8")).hexdigest()[:16]

        if timestamp is not None:
            ts = extract_sortable_timestamp(timestamp)
            return f"{ts}_{item_type.value.lower()}_{clean_part}"
        return f"{item_type.value.lower()}:{clean_part}"

    @property
    def sortable_id(self) -> str:
        """Return an ID guaranteed to start with a sortable timestamp for filesystem sorting."""
        if re.match(r"^\d{4}-\d{2}-\d{2}_\d{6}_", self.id):
            return self.id
        ts = extract_sortable_timestamp(self.date, self.collected_at)
        clean = self.id.replace(":", "_")
        return f"{ts}_{clean}"

    @property
    def filename(self) -> str:
        """Return filesystem-safe filename sorted by date in Finder (e.g. 2026-10-15_061304_message_843897.md)."""
        safe_id = self.sortable_id.replace(":", "_").replace("/", "_")
        return f"{safe_id}.md"

    @property
    def sort_datetime(self) -> datetime:
        """Return a timezone-aware datetime object for sorting."""
        if self.date:
            try:
                # Try full ISO
                dt = datetime.fromisoformat(self.date.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except Exception:
                pass
            m = re.search(r"(\d{4})-(\d{2})-(\d{2})", self.date)
            if m:
                return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
        if self.collected_at.tzinfo is None:
            return self.collected_at.replace(tzinfo=timezone.utc)
        return self.collected_at


class SyncResult(BaseModel):
    """Result summary of a sync run."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    success: bool
    items_found: int = 0
    items_new: int = 0
    items_synced: int = 0
    doc_id: Optional[str] = None
    target_type: str = "google_doc"  # or 'local_file'
    error_message: Optional[str] = None
    item_ids_synced: List[str] = Field(default_factory=list)
