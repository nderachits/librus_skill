"""Synchronization engine coordinating Librus data collection, deduplication, and Google Docs delivery."""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional

from librus_collector.config import AppConfig, get_config
from librus_collector.formatter import assemble_full_document, format_batch_for_doc
from librus_collector.google_docs import GoogleDocsSync
from librus_collector.item_store import ItemStore
from librus_collector.librus_client import LibrusClient
from librus_collector.models import SchoolItemType, SyncResult
from librus_collector.state import SyncStateManager

logger = logging.getLogger(__name__)


class LibrusSyncEngine:
    """Orchestrates an end-to-end sync operation."""

    def __init__(
        self,
        config: Optional[AppConfig] = None,
        librus_client: Optional[LibrusClient] = None,
        state_manager: Optional[SyncStateManager] = None,
        docs_sync: Optional[GoogleDocsSync] = None,
        item_store: Optional[ItemStore] = None,
    ):
        self.config = config or get_config()
        self.state_manager = state_manager or SyncStateManager(self.config.state_file_path)
        self.item_store = item_store or ItemStore(self.config.items_dir)
        self.docs_sync = docs_sync or GoogleDocsSync(
            doc_id=self.config.google_doc_id,
            service_account_file=self.config.google_service_account_file,
            client_secrets_file=self.config.google_client_secrets_file,
            token_file=self.config.google_token_file,
            local_fallback_file=self.config.local_export_path,
        )
        self.librus_client = librus_client

    def _get_client(self) -> LibrusClient:
        if self.librus_client is not None:
            return self.librus_client
        self.config.validate_librus_credentials()
        self.librus_client = LibrusClient(
            login=self.config.librus_login,
            password=self.config.librus_password,
        )
        return self.librus_client

    def run_sync(self, force: bool = False, rebuild: bool = False) -> SyncResult:
        """
        Execute one sync cycle:
        1. Query Librus for announcements, messages, and events.
        2. Identify unseen items using state manager (or all items if force=True).
        3. Fetch full content bodies and save items to structured ItemStore.
        4. Format and deliver items to Google Doc and local mirror.
        5. Persist updated sync state.
        """
        start_time = datetime.now(timezone.utc)
        logger.info("Starting Librus synchronization cycle at %s", start_time.isoformat())

        try:
            client = self._get_client()

            # Fetch all items from Librus
            all_items = client.collect_all(limit_messages=self.config.fetch_messages_limit)
            logger.info("Collected %d total items from Librus", len(all_items))

            # Filter for new items (or all items if forced)
            new_items = all_items if force else self.state_manager.filter_new(all_items)

            if not new_items and not rebuild:
                logger.info("No new items to sync. State is up to date.")
                return SyncResult(
                    timestamp=start_time,
                    success=True,
                    items_found=len(all_items),
                    items_new=0,
                    items_synced=0,
                    doc_id=self.docs_sync.doc_id,
                    target_type="google_doc" if self.docs_sync.is_google_configured else "local_file",
                )

            # Populate full body content for any newly identified messages
            for item in new_items:
                if item.item_type == SchoolItemType.MESSAGE:
                    try:
                        client.populate_message_content(item)
                    except Exception as e:
                        logger.warning("Could not fetch full body for message %s: %s", item.id, e)

            # Save items into structured ItemStore (Option 3: individual .md files with YAML frontmatter)
            if new_items:
                self.item_store.save_items(new_items)
                logger.info("Persisted %d items into structured storage at %s", len(new_items), self.item_store.items_dir)

            if rebuild:
                # Assemble entire document from all stored items
                all_stored = self.item_store.load_all_items()
                formatted_text = assemble_full_document(all_stored, group_by_type=True, assemble_time=start_time)
                append_res = self.docs_sync.append_text(formatted_text, rewrite=True)
            else:
                # Format incremental batch
                formatted_text = format_batch_for_doc(new_items, batch_time=start_time)
                append_res = self.docs_sync.append_text(formatted_text, rewrite=force)

            target_type = append_res.get("target", "unknown")

            # Persist state (reset history if forced)
            self.state_manager.mark_synced(new_items, reset=force)
            logger.info("Successfully synced %d new items to %s", len(new_items), target_type)

            return SyncResult(
                timestamp=start_time,
                success=True,
                items_found=len(all_items),
                items_new=len(new_items),
                items_synced=len(new_items),
                doc_id=self.docs_sync.doc_id,
                target_type=target_type,
                item_ids_synced=[item.id for item in new_items],
            )

        except Exception as e:
            logger.exception("Synchronization failed: %s", e)
            return SyncResult(
                timestamp=start_time,
                success=False,
                error_message=str(e),
                doc_id=self.docs_sync.doc_id,
            )

    def assemble(self, group_by_type: bool = True) -> Dict[str, Any]:
        """
        Assemble all items stored in ItemStore into a single cohesive document
        and update Google Doc and local mirror.
        """
        items = self.item_store.load_all_items(sort_by="date")
        formatted_text = assemble_full_document(items, group_by_type=group_by_type)
        res = self.docs_sync.append_text(formatted_text, rewrite=True)
        res["total_items_assembled"] = len(items)
        return res
