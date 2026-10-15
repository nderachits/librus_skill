"""Telegram notification module for forwarding new Librus updates to a chat or channel."""

import html
import logging
import re
from typing import Any, List, Optional, Union
import requests

from librus_collector.models import SchoolFeedItem, SchoolItemType
from librus_collector.security import mask_secret

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"
MAX_TELEGRAM_MESSAGE_LENGTH = 4000  # Telegram limit is 4096; keep a safe buffer

TYPE_META = {
    SchoolItemType.MESSAGE: ("✉️", "Wiadomość / Message"),
    SchoolItemType.ANNOUNCEMENT: ("📢", "Ogłoszenie / Announcement"),
    SchoolItemType.HOMEWORK: ("📝", "Zadanie domowe / Homework"),
    SchoolItemType.EVENT: ("📅", "Terminarz / Event"),
    SchoolItemType.NOTE: ("📋", "Uwaga / Note"),
}


class TelegramNotifier:
    """Dispatches formatted school feed items to a Telegram chat or channel."""

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[Union[str, int]] = None,
        session: Optional[requests.Session] = None,
        timeout: float = 15.0,
    ):
        self.bot_token = (bot_token or "").strip()
        self.chat_id = str(chat_id).strip() if chat_id is not None and str(chat_id).strip() else None
        self.session = session or requests.Session()
        self.timeout = timeout

    @property
    def is_configured(self) -> bool:
        """Return True if both bot_token and chat_id are present."""
        return bool(self.bot_token and self.chat_id)

    def _mask_token_in_error(self, message: str) -> str:
        """Ensure bot token is never leaked in log output or error strings."""
        if not self.bot_token:
            return message
        return message.replace(self.bot_token, mask_secret(self.bot_token))

    def format_item(self, item: SchoolFeedItem, max_len: int = MAX_TELEGRAM_MESSAGE_LENGTH) -> List[str]:
        """
        Format a SchoolFeedItem into one or more HTML-escaped Telegram messages.
        If the content exceeds Telegram's limit, it splits into numbered parts.
        """
        emoji, label = TYPE_META.get(item.item_type, ("📌", item.item_type.value))
        title = html.escape(item.title.strip() if item.title else "(Bez tytułu / No title)")

        header_lines = [
            f"{emoji} <b>[{label}] {title}</b>",
        ]
        if item.date:
            header_lines.append(f"📅 <b>Data:</b> {html.escape(str(item.date).strip())}")
        if item.author:
            header_lines.append(f"👤 <b>Od:</b> {html.escape(item.author.strip())}")
        if item.attachments:
            att_str = html.escape(", ".join(str(a) for a in item.attachments if a))
            if att_str:
                header_lines.append(f"📎 <b>Załączniki:</b> {att_str}")

        header_str = "\n".join(header_lines) + "\n\n"
        raw_body = item.content.strip() if item.content else "(Brak treści / No body text)"
        escaped_body = html.escape(raw_body)

        full_message = header_str + escaped_body
        if len(full_message) <= max_len:
            return [full_message]

        # Content exceeds max length; split content into chunks
        available_first_chunk = max_len - len(header_str) - 80  # buffer for part notice
        if available_first_chunk < 200:
            # Header is unusually large; send header as first message
            chunks = [header_str.strip()]
            remaining_text = escaped_body
        else:
            first_part, remaining_text = self._split_at_boundary(escaped_body, available_first_chunk)
            chunks = [header_str + first_part]

        subsequent_chunk_size = max_len - 80
        while remaining_text:
            chunk, remaining_text = self._split_at_boundary(remaining_text, subsequent_chunk_size)
            chunks.append(chunk)

        # Append part counter
        total_parts = len(chunks)
        numbered_chunks = []
        for idx, chunk in enumerate(chunks, 1):
            numbered_chunks.append(f"{chunk}\n\n<i>[Część / Part {idx}/{total_parts}]</i>")

        return numbered_chunks

    def _split_at_boundary(self, text: str, max_chars: int) -> tuple[str, str]:
        """Split text cleanly at newline or space boundary before max_chars."""
        if len(text) <= max_chars:
            return text, ""

        # Try splitting at newline first
        cut_index = text.rfind("\n", 0, max_chars)
        if cut_index == -1 or cut_index < max_chars // 2:
            # Fall back to space
            cut_index = text.rfind(" ", 0, max_chars)
        if cut_index == -1 or cut_index < max_chars // 2:
            cut_index = max_chars

        return text[:cut_index].rstrip(), text[cut_index:].lstrip()

    def send_message(self, text: str, parse_mode: Optional[str] = "HTML") -> bool:
        """
        Send a single text message to the configured Telegram chat or channel.
        Falls back to plain text if HTML parsing encounters entity errors.
        """
        if not self.is_configured:
            logger.debug("TelegramNotifier is not fully configured; skipping send.")
            return False

        url = f"{TELEGRAM_API_BASE}/bot{self.bot_token}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        try:
            resp = self.session.post(url, json=payload, timeout=self.timeout)
            if resp.status_code == 200:
                return True

            # If failed due to formatting entities in parse_mode, retry once as plain unformatted text
            if parse_mode and resp.status_code == 400:
                logger.warning(
                    "Telegram HTML parsing rejected (%s); retrying as plain text.",
                    resp.text,
                )
                # Strip simple HTML tags for plain text fallback
                clean_text = re.sub(r"<[^>]+>", "", text)
                payload["text"] = clean_text
                payload.pop("parse_mode", None)
                retry_resp = self.session.post(url, json=payload, timeout=self.timeout)
                return retry_resp.status_code == 200

            logger.error(
                "Telegram API error: HTTP %s %s",
                resp.status_code,
                self._mask_token_in_error(resp.text),
            )
            return False
        except Exception as e:
            logger.error(
                "Failed to send Telegram message: %s",
                self._mask_token_in_error(str(e)),
            )
            return False

    def send_item(self, item: SchoolFeedItem) -> bool:
        """Format and dispatch a single SchoolFeedItem."""
        if not self.is_configured:
            return False

        chunks = self.format_item(item)
        all_ok = True
        for chunk in chunks:
            ok = self.send_message(chunk, parse_mode="HTML")
            if not ok:
                all_ok = False
        return all_ok

    def send_items(self, items: List[SchoolFeedItem]) -> int:
        """
        Deliver a collection of items in chronological order (oldest first).
        Returns the count of successfully sent items.
        """
        if not self.is_configured or not items:
            return 0

        # Sort chronological (oldest first) so newest arrives at the bottom
        sorted_items = sorted(items, key=lambda it: it.sort_datetime)
        sent_count = 0
        for it in sorted_items:
            if self.send_item(it):
                sent_count += 1
            else:
                logger.warning("Failed to send item %s to Telegram.", it.id)
        return sent_count
