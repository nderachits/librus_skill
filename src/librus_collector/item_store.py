"""Structured storage for individual message Markdown files with YAML frontmatter (Option 3).

Each school item is stored as a standalone Markdown file named with a chronological timestamp prefix:
  `YYYY-MM-DD_HHMMSS_<item_type>_<unique_id>.md`
(e.g., `2026-10-15_061304_message_843897.md`).

This ensures files are naturally sorted chronologically when viewed in macOS Finder,
while YAML frontmatter preserves all structured metadata for lossless document assembly.
"""

from datetime import datetime, timezone
import hashlib
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Union
import yaml

from librus_collector.models import (
    extract_sortable_timestamp,
    SchoolFeedItem,
    SchoolItemType,
)

logger = logging.getLogger(__name__)

DEFAULT_ITEMS_DIR = "data/items"

TYPE_DISPLAY_LABELS = {
    SchoolItemType.ANNOUNCEMENT: "OGŁOSZENIE / ANNOUNCEMENT",
    SchoolItemType.MESSAGE: "WIADOMOŚĆ / MESSAGE",
    SchoolItemType.HOMEWORK: "ZADANIE DOMOWE / HOMEWORK",
    SchoolItemType.EVENT: "WYDARZENIE / EVENT",
    SchoolItemType.NOTE: "UWAGA / NOTE",
}


class ItemStore:
    """Manages individual Markdown message files with YAML frontmatter."""

    def __init__(self, items_dir: Optional[Union[str, Path]] = None):
        self.items_dir = Path(items_dir or os.getenv("LIBRUS_ITEMS_DIR", DEFAULT_ITEMS_DIR))
        self.items_dir.mkdir(parents=True, exist_ok=True)

    def _get_file_path(self, item: SchoolFeedItem) -> Path:
        """Get target file path for an item."""
        return self.items_dir / item.filename

    def save_item(self, item: SchoolFeedItem) -> Path:
        """
        Save a single SchoolFeedItem as a Markdown file with YAML frontmatter.
        Uses an atomic temporary file write and synchronizes filesystem timestamps (mtime)
        so macOS Finder sorting by date or name aligns perfectly.
        """
        self.items_dir.mkdir(parents=True, exist_ok=True)
        target_path = self._get_file_path(item)

        # Ensure legacy_id and source keys are stripped from metadata
        clean_metadata = {k: v for k, v in item.metadata.items() if k not in ("legacy_id", "source")}

        frontmatter_data: Dict[str, Any] = {
            "id": item.sortable_id,
            "librus_id": item.librus_id or item.canonical_id,
            "item_type": item.item_type.value,
            "title": item.title,
            "author": item.author,
            "date": item.date,
            "collected_at": item.collected_at.isoformat(),
            "attachments": item.attachments,
            "metadata": clean_metadata,
        }

        # Dump YAML frontmatter with unicode support and clean formatting
        yaml_text = yaml.safe_dump(
            frontmatter_data,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        ).strip()

        content_body = item.content.strip() if item.content else "(Brak treści / No body text)"
        file_content = f"---\n{yaml_text}\n---\n\n{content_body}\n"

        # Write atomically via temp file
        temp_path = self.items_dir / f".tmp_{item.sortable_id.replace(':', '_')}.md"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write(file_content)
            temp_path.replace(target_path)

            # Set mtime to match item date for macOS Finder Date Modified sorting
            try:
                ts_epoch = item.sort_datetime.timestamp()
                os.utime(target_path, (ts_epoch, ts_epoch))
            except Exception:
                pass

            logger.debug("Saved item %s to %s", item.id, target_path)
            return target_path
        except Exception as e:
            logger.error("Failed to save item %s to %s: %s", item.id, target_path, e)
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass
            raise

    def save_items(self, items: List[SchoolFeedItem]) -> List[Path]:
        """Save a batch of items, returning the paths of written files."""
        return [self.save_item(item) for item in items]

    def load_item(self, file_path_or_id: Union[str, Path]) -> Optional[SchoolFeedItem]:
        """Load and parse a SchoolFeedItem from a Markdown file with frontmatter or ID."""
        path = Path(file_path_or_id)
        if not path.is_file():
            # Try resolving ID directly in items_dir
            candidate = self.items_dir / f"{path.name}.md"
            if candidate.is_file():
                path = candidate
            else:
                # Search for any file ending with clean id
                clean_name = str(file_path_or_id).replace(":", "_")
                matches = list(self.items_dir.glob(f"*{clean_name}*.md"))
                if matches:
                    path = matches[0]
                else:
                    return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                raw_text = f.read()
        except Exception as e:
            logger.error("Failed to read item file %s: %s", path, e)
            return None

        return self.parse_markdown_item(raw_text, fallback_id=path.stem)

    @classmethod
    def parse_markdown_item(
        cls,
        text: str,
        fallback_id: str = "",
    ) -> Optional[SchoolFeedItem]:
        """Parse raw text containing YAML frontmatter and Markdown body into SchoolFeedItem."""
        # Match YAML frontmatter between starting '---' and closing '---'
        match = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)(.*)$", text, re.DOTALL)
        if not match:
            return None

        yaml_str = match.group(1)
        body_text = match.group(2).strip()

        try:
            data = yaml.safe_load(yaml_str) or {}
        except Exception as e:
            logger.warning("Failed to parse YAML frontmatter: %s", e)
            return None

        raw_type = data.get("item_type", "MESSAGE")
        try:
            item_type = SchoolItemType(raw_type)
        except ValueError:
            item_type = SchoolItemType.MESSAGE

        item_id = data.get("id") or fallback_id or "unknown"
        librus_id = data.get("librus_id") or data.get("legacy_id") or ""
        title = data.get("title", "Bez tytułu")
        author = data.get("author", "")
        date = data.get("date", "")
        attachments = data.get("attachments") or []
        metadata = data.get("metadata") or {}
        # Strip legacy/internal keys from metadata
        metadata.pop("legacy_id", None)
        metadata.pop("source", None)

        # Parse collected_at
        raw_collected = data.get("collected_at")
        if raw_collected:
            try:
                collected_at = datetime.fromisoformat(str(raw_collected).replace("Z", "+00:00"))
            except Exception:
                collected_at = datetime.now(timezone.utc)
        else:
            collected_at = datetime.now(timezone.utc)

        # Extract content: check frontmatter first, then parse from body below '### Treść / Content:'
        content = data.get("content")
        if not content:
            content_match = re.search(r"###\s+Treść\s*/\s*Content:\s*\n(.*)$", body_text, re.DOTALL)
            if content_match:
                content = content_match.group(1).strip()
            else:
                content = body_text

        # Clean any trailing separator from content
        content = re.sub(r"\n---\s*$", "", content).strip()

        return SchoolFeedItem(
            id=item_id,
            librus_id=librus_id,
            item_type=item_type,
            title=title,
            author=author,
            date=date,
            content=content,
            attachments=attachments,
            metadata=metadata,
            collected_at=collected_at,
        )

    def load_all_items(
        self,
        sort_by: str = "date",
        reverse: bool = False,
    ) -> List[SchoolFeedItem]:
        """
        Load all stored items.
        Because filenames start with the sortable timestamp (YYYY-MM-DD_HHMMSS),
        a simple sorted glob automatically provides chronological ordering.
        """
        if not self.items_dir.exists():
            return []

        files = sorted([f for f in self.items_dir.glob("*.md") if not f.name.startswith(".")])
        items: List[SchoolFeedItem] = []
        for f in files:
            item = self.load_item(f)
            if item is not None:
                items.append(item)

        if sort_by == "date":
            items.sort(key=lambda it: it.sort_datetime, reverse=reverse)
        elif sort_by == "type":
            items.sort(key=lambda it: (it.item_type.value, it.sort_datetime), reverse=reverse)

        return items

    def list_item_files(self) -> List[Path]:
        """List all stored item file paths sorted alphabetically (chronological by timestamp)."""
        if not self.items_dir.exists():
            return []
        return sorted([f for f in self.items_dir.glob("*.md") if not f.name.startswith(".")])

    def get_item(self, item_id: str) -> Optional[SchoolFeedItem]:
        """Retrieve an item by its ID."""
        return self.load_item(item_id)

    def delete_item(self, item_id: str) -> bool:
        """Delete an item file by its ID."""
        clean_id = item_id.replace(":", "_")
        matches = list(self.items_dir.glob(f"*{clean_id}*.md"))
        deleted = False
        for f in matches:
            try:
                f.unlink()
                deleted = True
                logger.info("Deleted item file: %s", f)
            except OSError as e:
                logger.error("Failed to delete %s: %s", f, e)
        return deleted

    def count(self) -> int:
        """Return the number of stored item files."""
        return len(self.list_item_files())

    def import_from_markdown(
        self,
        markdown_path: Union[str, Path],
        state_history: Optional[List[dict]] = None,
    ) -> List[SchoolFeedItem]:
        """
        Import existing items from a markdown export file (e.g. librus_updates.md).
        Cross-references with state history if provided to restore exact Librus message IDs.
        """
        path = Path(markdown_path)
        if not path.is_file():
            logger.warning("Markdown file not found for import: %s", path)
            return []

        text = path.read_text(encoding="utf-8")
        raw_blocks = [b.strip() for b in re.split(r"(?:^|\n)(?=##\s+\[)", text) if b.strip()]

        imported: List[SchoolFeedItem] = []
        # Reverse map history by title + date for exact ID restoration
        history_map = {}
        if state_history:
            for entry in state_history:
                raw_t = entry.get("title", "") or ""
                raw_d = entry.get("date", "") or ""
                key = (entry.get("type", ""), raw_t.strip(), raw_d.strip())
                history_map[key] = entry.get("id")

        for block in raw_blocks:
            lines = block.split("\n")
            header_line = lines[0].strip()
            header_match = re.match(r"^##\s+\[([^\]]+)\]\s*(.*)$", header_line)
            if not header_match:
                continue

            raw_type_str = header_match.group(1).split("/")[0].strip()
            title = header_match.group(2).strip()

            type_map = {
                "OGŁOSZENIE": SchoolItemType.ANNOUNCEMENT,
                "ANNOUNCEMENT": SchoolItemType.ANNOUNCEMENT,
                "WIADOMOŚĆ": SchoolItemType.MESSAGE,
                "MESSAGE": SchoolItemType.MESSAGE,
                "ZADANIE DOMOWE": SchoolItemType.HOMEWORK,
                "HOMEWORK": SchoolItemType.HOMEWORK,
                "WYDARZENIE": SchoolItemType.EVENT,
                "EVENT": SchoolItemType.EVENT,
                "UWAGA": SchoolItemType.NOTE,
                "NOTE": SchoolItemType.NOTE,
            }
            item_type = type_map.get(raw_type_str, SchoolItemType.MESSAGE)

            date = ""
            author = ""
            attachments = []

            content_lines = []
            is_content = False

            for line in lines[1:]:
                stripped = line.strip()
                if stripped.startswith("• Data / Date:"):
                    date = stripped.replace("• Data / Date:", "").strip()
                elif stripped.startswith("• Od / From:"):
                    author = stripped.replace("• Od / From:", "").strip()
                elif stripped.startswith("• Załączniki / Attachments:"):
                    att_str = stripped.replace("• Załączniki / Attachments:", "").strip()
                    attachments = [a.strip() for a in att_str.split(",") if a.strip()]
                elif stripped == "### Treść / Content:":
                    is_content = True
                elif is_content:
                    if stripped == "---":
                        break
                    content_lines.append(line)

            content = "\n".join(content_lines).strip()
            if content == "(Brak treści / No body text)":
                content = ""

            # Determine canonical Librus ID
            matched_id = history_map.get((item_type.value, title, date))
            if matched_id:
                clean_core = matched_id.split(":")[-1]
                canonical_librus_id = matched_id
            else:
                raw_unique = f"{title}_{date}_{author}_{content[:40]}"
                clean_core = hashlib.sha256(raw_unique.encode("utf-8")).hexdigest()[:16]
                canonical_librus_id = f"{item_type.value.lower()}:{clean_core}"

            # Generate sortable timestamp ID for filesystem ordering
            feed_id = SchoolFeedItem.make_id(
                item_type=item_type,
                unique_part=clean_core,
                timestamp=date or None,
            )

            metadata = {}

            feed_item = SchoolFeedItem(
                id=feed_id,
                librus_id=canonical_librus_id,
                item_type=item_type,
                title=title,
                author=author,
                date=date,
                content=content,
                attachments=attachments,
                metadata=metadata,
            )
            self.save_item(feed_item)
            imported.append(feed_item)

        logger.info("Imported %d items from %s into %s", len(imported), path, self.items_dir)
        return imported
