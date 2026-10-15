"""Librus Collector - Hourly collector for Librus messages and announcements synced to Google Docs for NotebookLM."""

from librus_collector.librus_client import LibrusClient
from librus_collector.models import SchoolFeedItem, SchoolItemType, SyncResult
from librus_collector.state import SyncStateManager
from librus_collector.google_docs import GoogleDocsSync
from librus_collector.sync_engine import LibrusSyncEngine

__all__ = [
    "LibrusClient",
    "SchoolFeedItem",
    "SchoolItemType",
    "SyncResult",
    "SyncStateManager",
    "GoogleDocsSync",
    "LibrusSyncEngine",
]

__version__ = "0.1.0"
