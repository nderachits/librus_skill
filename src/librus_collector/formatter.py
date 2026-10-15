"""Formatting utilities for NotebookLM-optimized Google Doc content."""

from datetime import datetime, timezone
from typing import List, Optional

from librus_collector.models import SchoolFeedItem, SchoolItemType

TYPE_LABELS = {
    SchoolItemType.ANNOUNCEMENT: "OGŁOSZENIE / ANNOUNCEMENT",
    SchoolItemType.MESSAGE: "WIADOMOŚĆ / MESSAGE",
    SchoolItemType.HOMEWORK: "ZADANIE DOMOWE / HOMEWORK",
    SchoolItemType.EVENT: "WYDARZENIE / EVENT",
    SchoolItemType.NOTE: "UWAGA / NOTE",
}


def format_single_item(item: SchoolFeedItem) -> str:
    """Format a single school feed item into a structured Markdown block optimized for NotebookLM."""
    label = TYPE_LABELS.get(item.item_type, item.item_type.value)
    
    lines = [
        f"## [{label}] {item.title}",
    ]

    if item.date:
        lines.append(f"• Data / Date: {item.date}")
    if item.author:
        lines.append(f"• Od / From: {item.author}")
    if item.attachments:
        lines.append(f"• Załączniki / Attachments: {', '.join(item.attachments)}")
    
    # Metadata details if present
    source = item.metadata.get("source")
    if source:
        lines.append(f"• Źródło / Source: {source}")

    lines.append("")
    lines.append("### Treść / Content:")
    lines.append(item.content.strip() if item.content else "(Brak treści / No body text)")
    lines.append("")
    lines.append("---")
    lines.append("")

    return "\n".join(lines)


def format_batch_for_doc(items: List[SchoolFeedItem], batch_time: Optional[datetime] = None) -> str:
    """Format a batch of new items to append to the Google Doc."""
    if not items:
        return ""

    dt = batch_time or datetime.now(timezone.utc)
    ts_str = dt.strftime("%Y-%m-%d %H:%M:%S UTC")

    header = [
        f"\n\n",
        f"# ══════════════════════════════════════════════════════════════════════",
        f"# LIBRUS UPDATE SYNC: {ts_str} ({len(items)} new items)",
        f"# ══════════════════════════════════════════════════════════════════════\n\n",
    ]

    item_blocks = [format_single_item(it) for it in items]
    return "".join(header) + "\n".join(item_blocks) + "\n"
