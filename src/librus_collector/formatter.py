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

    lines.append("")
    lines.append("### Treść / Content:")
    lines.append(item.content.strip() if item.content else "(Brak treści / No body text)")
    lines.append("")
    lines.append("---")
    lines.append("")

    return "\n".join(lines)


def format_batch_for_doc(
    items: List[SchoolFeedItem],
    batch_time: Optional[datetime] = None,
    sort_newest_first: bool = True,
) -> str:
    """Format a batch of new items to append or prepend to the Google Doc without banners."""
    if not items:
        return ""

    # Ensure items within batch are sorted newest first regardless of type
    sorted_items = (
        sorted(items, key=lambda it: it.sort_datetime, reverse=True)
        if sort_newest_first
        else items
    )

    item_blocks = [format_single_item(it) for it in sorted_items]
    return "\n".join(item_blocks).strip() + "\n\n"


def assemble_full_document(
    items: List[SchoolFeedItem],
    group_by_type: bool = False,
    title: Optional[str] = None,
    assemble_time: Optional[datetime] = None,
) -> str:
    """
    Assemble all stored items into a single, cohesive, up-to-date document.
    Outputs a pure stream of item blocks sorted newest-first without headers or banners.
    """
    if not items:
        return "(Brak powiadomień / No updates collected yet)\n"

    if not group_by_type:
        # Flat chronological order (newest first regardless of type)
        sorted_items = sorted(items, key=lambda it: it.sort_datetime, reverse=True)
        blocks = [format_single_item(it) for it in sorted_items]
        return "\n".join(blocks).strip() + "\n"

    # Grouped sections in natural school priority order
    sections_order = [
        (SchoolItemType.EVENT, "📅 TERMINARZ I WYDARZENIA / EVENTS & EXAMS"),
        (SchoolItemType.ANNOUNCEMENT, "📢 OGŁOSZENIA / ANNOUNCEMENTS"),
        (SchoolItemType.HOMEWORK, "📝 ZADANIA DOMOWE / HOMEWORK"),
        (SchoolItemType.MESSAGE, "✉️ WIADOMOŚCI / MESSAGES"),
        (SchoolItemType.NOTE, "📋 UWAGI I INFORMACJE / NOTES & CONDUCT"),
    ]

    section_outputs = []
    for it_type, section_title in sections_order:
        matching = [it for it in items if it.item_type == it_type]
        if not matching:
            continue

        # Sort within section by date
        matching.sort(key=lambda it: it.sort_datetime, reverse=True)

        sec_header = [
            f"\n# ──────────────────────────────────────────────────────────────────────",
            f"# {section_title} ({len(matching)})",
            f"# ──────────────────────────────────────────────────────────────────────\n",
        ]
        sec_blocks = [format_single_item(it) for it in matching]
        section_outputs.append("\n".join(sec_header) + "\n" + "\n".join(sec_blocks))

    return "\n\n".join(section_outputs).strip() + "\n"


def clean_markdown_batches(text: str) -> str:
    """
    Remove duplicate sync batches and duplicate items from markdown text.
    Ensures headers are properly formatted with newlines and drops any batch
    or items that are already present in earlier batches.
    Identifies items by their full normalized block rather than title alone to
    prevent accidental deletion of distinct events sharing generic titles.
    """
    if not text or not text.strip():
        return ""

    import re

    # Normalize smashed batch headers if present (e.g. # ═+# LIBRUS UPDATE SYNC: ...# ═+)
    text = re.sub(
        r"#\s*([═=]{20,})\s*#\s*(LIBRUS UPDATE SYNC:[^#\n]+)#\s*([═=]{20,})",
        r"# \1\n# \2\n# \3",
        text,
    )

    # Pattern to match the start of a sync batch
    header_pattern = re.compile(
        r"(?:^|\n)(#\s*[═=]{20,}\s*\n#\s*LIBRUS UPDATE SYNC:[^\n]+\n#\s*[═=]{20,}\s*(?:\n|$))",
        re.MULTILINE,
    )

    splits = header_pattern.split(text)
    if len(splits) <= 1:
        return text

    cleaned_parts = []
    if splits[0].strip():
        cleaned_parts.append(splits[0].strip())

    seen_fingerprints = set()

    def get_fingerprint(item_str: str) -> str:
        # Normalize whitespace across the item to produce a deterministic signature
        return re.sub(r"\s+", " ", item_str.strip())

    for i in range(1, len(splits), 2):
        header = splits[i].strip()
        content = splits[i + 1].strip() if i + 1 < len(splits) else ""

        # Extract items in this batch: each item begins with '## ['
        raw_items = [b.strip() for b in re.split(r"(?:^|\n)(?=##\s+\[)", content) if b.strip()]

        if not raw_items:
            # Batch has no items (or arbitrary text); preserve if non-empty
            if content:
                cleaned_parts.append(f"{header}\n\n{content}")
            continue

        retained_items = []
        for it in raw_items:
            fp = get_fingerprint(it)
            if fp not in seen_fingerprints:
                seen_fingerprints.add(fp)
                retained_items.append(it)

        # If all items in this batch were duplicates, drop the entire batch
        if not retained_items:
            continue

        # If some items were deduplicated, update the header item count
        if len(retained_items) != len(raw_items):
            header = re.sub(
                r"\(\d+\s+new items\)",
                f"({len(retained_items)} new items)",
                header,
            )
            cleaned_parts.append(f"{header}\n\n" + "\n\n".join(retained_items))
        else:
            cleaned_parts.append(f"{header}\n\n{content}")

    return "\n\n".join(cleaned_parts).strip() + "\n"
