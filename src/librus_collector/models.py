"""Data models for Librus Collector."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
import hashlib
from pydantic import BaseModel, Field


class SchoolItemType(str, Enum):
    """Types of items retrieved from Librus."""
    ANNOUNCEMENT = "ANNOUNCEMENT"  # Ogłoszenie
    MESSAGE = "MESSAGE"            # Wiadomość
    HOMEWORK = "HOMEWORK"          # Zadanie domowe
    EVENT = "EVENT"                # Wydarzenie / Sprawdzian / Terminarz
    NOTE = "NOTE"                  # Uwaga / Informacja


class SchoolFeedItem(BaseModel):
    """Normalized school notification or communication item."""

    id: str = Field(description="Unique deterministic identifier for deduplication")
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

    @classmethod
    def make_id(cls, item_type: SchoolItemType, unique_part: str) -> str:
        """Create a reproducible unique ID."""
        clean_part = unique_part.strip()
        if len(clean_part) > 64:
            digest = hashlib.sha256(clean_part.encode("utf-8")).hexdigest()[:16]
            return f"{item_type.value.lower()}:{digest}"
        return f"{item_type.value.lower()}:{clean_part}"


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
