"""Independent Librus Synergia API and web scraping client.

Implements direct authentication and retrieval for:
- School announcements (Ogłoszenia)
- Messages (Wiadomości via modern API with HTML fallback)
- Calendar events / exams (Terminarz)
- Homework (Zadania domowe)

No third-party Librus dependencies are used.
Strict security policies ensure credentials are only transmitted via HTTPS to verified Librus domains.
"""

from base64 import b64decode
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
import requests

from librus_collector.models import SchoolFeedItem, SchoolItemType
from librus_collector.security import (
    assert_secure_librus_url,
    LibrusAuthError,
    SecurityError,
    sanitize_message,
)

logger = logging.getLogger(__name__)

CLIENT_ID = 46
SYNERGIA_DOMAIN = "synergia.librus.pl"
WIADOMOSCI_DOMAIN = "wiadomosci.librus.pl"
API_DOMAIN = "api.librus.pl"

SYNERGIA_BASE_URL = f"https://{SYNERGIA_DOMAIN}"
API_BASE_URL = f"https://{API_DOMAIN}"

API_OAUTH_AUTHORIZATION_URL = f"{API_BASE_URL}/OAuth/Authorization?client_id={CLIENT_ID}"
API_OAUTH_WITH_SCOPE_URL = f"{API_OAUTH_AUTHORIZATION_URL}&response_type=code&scope=mydata"
SYNERGIA_PORTAL_LOGIN_URL = f"{SYNERGIA_BASE_URL}/loguj/portalRodzina"
SYNERGIA_MESSAGES_BOOTSTRAP_URL = f"{SYNERGIA_BASE_URL}/wiadomosci3"
SYNERGIA_ANNOUNCEMENTS_URL = f"{SYNERGIA_BASE_URL}/ogloszenia"
SYNERGIA_MESSAGES_URL = f"{SYNERGIA_BASE_URL}/wiadomosci/1/5"
SYNERGIA_CALENDAR_URL = f"{SYNERGIA_BASE_URL}/terminarz"
SYNERGIA_HOMEWORK_URL = f"{SYNERGIA_BASE_URL}/moje_zadania"
SYNERGIA_NOTES_URL = f"{SYNERGIA_BASE_URL}/uwagi"

MAX_REDIRECTS = 10
DEFAULT_TIMEOUT = 25  # seconds


class LibrusClient:
    """Client for authenticating and fetching data from Librus."""

    def __init__(self, login: str, password: str, session: Optional[requests.Session] = None):
        if not login or not password:
            raise ValueError("Librus login and password must not be empty.")
        self.login = login
        self._password = password
        self.session = session or requests.Session()
        self.is_authenticated = False
        self.user_agent = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )
        self.session.headers.update({
            "User-Agent": self.user_agent,
            "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
        })

        # Intercept every request and redirect to guarantee encrypted HTTPS to official Librus domains
        def _security_redirect_hook(response, *args, **kwargs):
            assert_secure_librus_url(response.url)
            if response.is_redirect and "Location" in response.headers:
                target = urljoin(response.url, response.headers["Location"])
                assert_secure_librus_url(target)
            return response

        self.session.hooks.setdefault("response", []).append(_security_redirect_hook)

    def __repr__(self) -> str:
        return f"<LibrusClient login={self.login!r} authenticated={self.is_authenticated}>"

    def login_sync(self) -> bool:
        """
        Authenticate with Librus using OAuth flow with strict HTTPS & host validation.
        """
        try:
            # Step 1: Security check on entrypoint
            assert_secure_librus_url(SYNERGIA_PORTAL_LOGIN_URL)
            
            resp = self.session.get(
                SYNERGIA_PORTAL_LOGIN_URL,
                allow_redirects=False,
                timeout=DEFAULT_TIMEOUT,
            )
            
            oauth_auth_url = resp.headers.get("Location")
            if not oauth_auth_url:
                # If no redirect header, use standard OAuth authorization URL
                oauth_auth_url = API_OAUTH_WITH_SCOPE_URL
            else:
                oauth_auth_url = urljoin(SYNERGIA_BASE_URL, oauth_auth_url)

            # Step 2: Validate OAuth target URL
            assert_secure_librus_url(oauth_auth_url)
            init_resp = self.session.get(
                oauth_auth_url,
                allow_redirects=False,
                timeout=DEFAULT_TIMEOUT,
            )

            # Step 3: Submit login credentials strictly over HTTPS to api.librus.pl
            assert_secure_librus_url(API_OAUTH_AUTHORIZATION_URL)
            login_payload = {
                "action": "login",
                "login": self.login,
                "pass": self._password,
            }
            login_headers = {
                "Accept": "*/*",
                "Origin": API_BASE_URL,
                "Referer": oauth_auth_url,
                "X-Requested-With": "XMLHttpRequest",
            }
            
            post_resp = self.session.post(
                API_OAUTH_AUTHORIZATION_URL,
                data=login_payload,
                headers=login_headers,
                timeout=DEFAULT_TIMEOUT,
            )

            if post_resp.status_code != 200:
                raise LibrusAuthError(
                    f"Login request failed with HTTP {post_resp.status_code}."
                )

            try:
                login_data = post_resp.json()
            except ValueError as e:
                # Some versions respond with HTML error or redirect
                if "Błędny login lub hasło" in post_resp.text or "error" in post_resp.text.lower():
                    raise LibrusAuthError("Invalid Librus login or password.") from e
                raise LibrusAuthError("Unexpected response format from Librus auth.") from e

            if login_data.get("status") != "ok" and not login_data.get("goTo"):
                msg = login_data.get("message") or login_data.get("error") or "Authentication failed"
                raise LibrusAuthError(f"Librus authentication rejected: {msg}")

            go_to = login_data.get("goTo")
            if not go_to:
                raise LibrusAuthError("OAuth response missing redirect target ('goTo').")

            # Step 4: Follow OAuth redirect chain to complete session establishment
            current_url = urljoin(API_OAUTH_WITH_SCOPE_URL, go_to)
            for _ in range(MAX_REDIRECTS):
                assert_secure_librus_url(current_url)
                step_resp = self.session.get(
                    current_url,
                    allow_redirects=False,
                    timeout=DEFAULT_TIMEOUT,
                )
                loc = step_resp.headers.get("Location")
                if not loc:
                    break
                current_url = urljoin(step_resp.url, loc)
            else:
                raise LibrusAuthError("OAuth redirect limit exceeded during login.")

            # Step 5: MultiDomainLogon bootstrap for wiadomości API (best effort)
            try:
                assert_secure_librus_url(SYNERGIA_MESSAGES_BOOTSTRAP_URL)
                bootstrap_resp = self.session.get(
                    SYNERGIA_MESSAGES_BOOTSTRAP_URL,
                    timeout=DEFAULT_TIMEOUT,
                )
            except Exception as e:
                logger.warning("Messages bootstrap warning: %s", e)

            self.is_authenticated = True
            logger.info("Successfully authenticated with Librus as '%s'", self.login)
            return True

        except (SecurityError, LibrusAuthError):
            raise
        except Exception as e:
            scrubbed = sanitize_message(str(e), [self._password])
            raise LibrusAuthError(f"Authentication failed: {scrubbed}") from None

    def fetch_announcements(self) -> List[SchoolFeedItem]:
        """
        Fetch school announcements from https://synergia.librus.pl/ogloszenia.
        """
        if not self.is_authenticated:
            self.login_sync()

        assert_secure_librus_url(SYNERGIA_ANNOUNCEMENTS_URL)
        resp = self.session.get(SYNERGIA_ANNOUNCEMENTS_URL, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()

        return self._parse_announcements_html(resp.text)

    def _parse_announcements_html(self, html: str) -> List[SchoolFeedItem]:
        """Parse announcements HTML into normalized feed items."""
        soup = BeautifulSoup(html, "html.parser")
        items: List[SchoolFeedItem] = []

        # Check for empty container (e.g. "Brak ogłoszeń")
        empty_container = soup.select_one("div.container.border-red.resizeable.center > div > p")
        if empty_container and "brak" in empty_container.text.lower():
            return []

        # Find announcement tables
        tables = soup.select("table.decorated.big, table.decorated")
        for table in tables:
            # Verify table has the announcement structure
            thead_td = table.select_one("thead tr td, thead th")
            title = thead_td.get_text(strip=True) if thead_td else ""
            if not title:
                continue

            rows = table.find_all("tr", attrs={"class": ["line0", "line1"]})
            data_cells = []
            for r in rows:
                tds = r.find_all("td")
                if len(tds) >= 2:
                    # In Synergia tables, col 0 is label ("Dodał", "Data publikacji"), col 1 is the actual value
                    data_cells.append(tds[1].get_text("\n", strip=True))
                elif len(tds) == 1:
                    data_cells.append(tds[0].get_text("\n", strip=True))

            if len(data_cells) >= 3:
                author = data_cells[0]
                date_str = data_cells[1]
                content = "\n".join(data_cells[2:])
            elif len(data_cells) == 2:
                author = data_cells[0]
                date_str = ""
                content = data_cells[1]
            elif len(data_cells) == 1:
                author = ""
                date_str = ""
                content = data_cells[0]
            else:
                continue

            # Deterministic ID with content hash to prevent collisions on same author/date
            raw_id = f"{title}_{date_str}_{author}_{content[:60]}"
            feed_id = SchoolFeedItem.make_id(SchoolItemType.ANNOUNCEMENT, raw_id)

            items.append(
                SchoolFeedItem(
                    id=feed_id,
                    item_type=SchoolItemType.ANNOUNCEMENT,
                    title=title,
                    author=author,
                    date=date_str,
                    content=content,
                    metadata={"source": "synergia_ogloszenia"},
                )
            )

        return items

    def fetch_messages(self, limit: int = 50) -> List[SchoolFeedItem]:
        """
        Fetch received messages.
        Tries the modern JSON API (https://wiadomosci.librus.pl/api/inbox/messages) first.
        Falls back to HTML scraping of Synergia messages if API is unavailable.
        """
        if not self.is_authenticated:
            self.login_sync()

        # Try API first
        try:
            return self._fetch_messages_via_api(limit=limit)
        except Exception as e:
            logger.info("Librus wiadomości API unavailable (%s), falling back to Synergia HTML", e)
            return self._fetch_messages_via_html(limit=limit)

    def _fetch_messages_via_api(self, limit: int = 50) -> List[SchoolFeedItem]:
        """Fetch messages using modern JSON API."""
        api_url = f"https://{WIADOMOSCI_DOMAIN}/api/inbox/messages?page=1&limit={limit}"
        assert_secure_librus_url(api_url)

        resp = self.session.get(api_url, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()

        data = resp.json()
        messages_data = data.get("data", [])
        if isinstance(messages_data, dict) and "messages" in messages_data:
            messages_data = messages_data["messages"]

        items: List[SchoolFeedItem] = []
        for msg in messages_data:
            msg_id = str(msg.get("messageId") or msg.get("id") or "")
            if not msg_id:
                continue

            # Sender
            sender_name = msg.get("senderName") or f"{msg.get('senderFirstName', '')} {msg.get('senderLastName', '')}".strip()
            topic = msg.get("topic") or "Bez tematu"
            raw_content = msg.get("content") or ""
            
            # Content is typically base64 encoded in the modern API
            body = self._decode_message_content(raw_content)

            send_date = msg.get("sendDate") or msg.get("date") or ""
            has_attachment = bool(msg.get("isAnyFileAttached"))
            attachments = []
            if has_attachment:
                attachments.append("Załącznik dołączony do wiadomości")

            feed_id = SchoolFeedItem.make_id(SchoolItemType.MESSAGE, msg_id)
            items.append(
                SchoolFeedItem(
                    id=feed_id,
                    item_type=SchoolItemType.MESSAGE,
                    title=topic,
                    author=sender_name,
                    date=send_date,
                    content=body,
                    attachments=attachments,
                    metadata={
                        "message_id": msg_id,
                        "unread": msg.get("readDate") is None,
                        "source": "wiadomosci_api",
                    },
                )
            )

        return items

    def _fetch_messages_via_html(self, limit: int = 50) -> List[SchoolFeedItem]:
        """Fallback: scrape Synergia messages inbox table."""
        assert_secure_librus_url(SYNERGIA_MESSAGES_URL)
        resp = self.session.get(SYNERGIA_MESSAGES_URL, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()

        return self._parse_messages_html(resp.text, limit=limit)

    def _parse_messages_html(self, html: str, limit: int = 50) -> List[SchoolFeedItem]:
        """Parse Synergia messages HTML."""
        soup = BeautifulSoup(html, "html.parser")
        items: List[SchoolFeedItem] = []

        table = soup.find("table", attrs={"class": re.compile(r"decorated.*stretch")})
        if not table:
            return items

        tbody = table.find("tbody") or table
        rows = tbody.find_all("tr", attrs={"class": ["line0", "line1"]})

        for row in rows[:limit]:
            cells = row.find_all("td")
            if len(cells) < 5:
                continue

            # Row structure usually:
            # [checkbox, attachment_icon, author, title_link, date, unread/trash]
            # Find the title and link
            title_cell = None
            link_href = ""
            author_cell = None
            date_cell = None

            for c in cells:
                a_tag = c.find("a")
                if a_tag and "wiadomosci" in a_tag.get("href", ""):
                    title_cell = c
                    link_href = a_tag["href"]
                    break

            if not title_cell:
                # Standard positions fallback
                if len(cells) >= 6:
                    author_cell = cells[2]
                    title_cell = cells[3]
                    date_cell = cells[4]
                else:
                    continue
            else:
                idx = cells.index(title_cell)
                if idx > 0:
                    author_cell = cells[idx - 1]
                if idx + 1 < len(cells):
                    date_cell = cells[idx + 1]

            title = title_cell.get_text(strip=True) if title_cell else "Bez tematu"
            author = author_cell.get_text(strip=True) if author_cell else ""
            date_str = date_cell.get_text(strip=True) if date_cell else ""

            # Extract message ID from link if present
            # e.g., /wiadomosci/1/5/123456
            msg_id = ""
            if link_href:
                parts = [p for p in link_href.split("/") if p]
                if parts:
                    msg_id = parts[-1]
            if not msg_id:
                msg_id = f"{author}_{title}_{date_str}"

            feed_id = SchoolFeedItem.make_id(SchoolItemType.MESSAGE, msg_id)

            items.append(
                SchoolFeedItem(
                    id=feed_id,
                    item_type=SchoolItemType.MESSAGE,
                    title=title,
                    author=author,
                    date=date_str,
                    content=f"Wiadomość z systemu Synergia: {title}",
                    metadata={"link": link_href, "source": "synergia_html"},
                )
            )

        return items

    def populate_message_content(self, item: SchoolFeedItem) -> None:
        """
        Fetch full body content for a message that only has summary metadata.
        Supports both modern API (/api/message/{id}) and Synergia HTML message view.
        """
        if not self.is_authenticated:
            self.login_sync()

        # If item already has substantive text (not empty and not default placeholder), nothing to do
        if item.content and not item.content.startswith("Wiadomość z systemu Synergia"):
            return

        source = item.metadata.get("source")
        msg_id = item.metadata.get("message_id")
        link = item.metadata.get("link")

        # 1. Try modern JSON API if message came from API or has numeric msg_id
        if source == "wiadomosci_api" or (msg_id and msg_id.isdigit()):
            for endpoint_tpl in [
                f"https://{WIADOMOSCI_DOMAIN}/api/message/{msg_id}",
                f"https://{WIADOMOSCI_DOMAIN}/api/messages/{msg_id}",
            ]:
                try:
                    assert_secure_librus_url(endpoint_tpl)
                    resp = self.session.get(endpoint_tpl, timeout=DEFAULT_TIMEOUT)
                    if resp.status_code == 200:
                        payload = resp.json()
                        msg_obj = payload.get("data", payload)
                        if isinstance(msg_obj, dict):
                            raw_body = msg_obj.get("content") or msg_obj.get("body") or ""
                            if raw_body:
                                item.content = self._decode_message_content(raw_body)
                                return
                except Exception as e:
                    logger.debug("API message detail fetch failed for %s: %s", endpoint_tpl, e)

        # 2. Try HTML scraping of the specific message view
        if link:
            target_url = urljoin(SYNERGIA_BASE_URL, link)
            try:
                assert_secure_librus_url(target_url)
                resp = self.session.get(target_url, timeout=DEFAULT_TIMEOUT)
                if resp.status_code == 200:
                    body_text, attachments = self._parse_single_message_html(resp.text)
                    if body_text:
                        item.content = body_text
                    if attachments:
                        item.attachments = list(set(item.attachments + attachments))
                    return
            except Exception as e:
                logger.warning("Failed to fetch HTML message body for %s: %s", target_url, e)

    def _parse_single_message_html(self, html: str) -> tuple[str, List[str]]:
        """Parse message body and attachment list from single message HTML page."""
        soup = BeautifulSoup(html, "html.parser")
        body_text = ""
        attachments: List[str] = []

        # Synergia puts message body in container-message, wysiwyg, or table cell after Treść
        content_container = soup.select_one("div.container-message, td.container-message, div.wysiwyg")
        if content_container:
            body_text = content_container.get_text("\n", strip=True)

        if not body_text:
            table = soup.find("table", attrs={"class": re.compile(r"decorated")})
            if table:
                for row in table.find_all("tr"):
                    header = row.find(["th", "td"])
                    if header and "treść" in header.get_text(strip=True).lower():
                        cells = row.find_all("td")
                        if cells:
                            target_cell = cells[-1]
                            body_text = target_cell.get_text("\n", strip=True)
                            break

        for a_tag in soup.select("a[href*='pobierz'], a[href*='zalacznik'], a[href*='attachment']"):
            txt = a_tag.get_text(strip=True)
            if txt and txt not in attachments:
                attachments.append(txt)

        return body_text, attachments

    def fetch_calendar_events(self) -> List[SchoolFeedItem]:
        """
        Fetch school calendar events / exams (Terminarz).
        """
        if not self.is_authenticated:
            self.login_sync()

        assert_secure_librus_url(SYNERGIA_CALENDAR_URL)
        resp = self.session.get(SYNERGIA_CALENDAR_URL, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()

        return self._parse_calendar_html(resp.text)

    def _parse_calendar_html(self, html: str) -> List[SchoolFeedItem]:
        """Parse Terminarz HTML for upcoming events."""
        soup = BeautifulSoup(html, "html.parser")
        items: List[SchoolFeedItem] = []

        # Find event cells or tables in the calendar view
        event_elements = soup.select("table.decorated tr, div.kalendarz-dzien, div.center.margin-top")
        for el in event_elements:
            text = el.get_text(" ", strip=True)
            if not text or len(text) < 10:
                continue
            if any(
                k in text.lower()
                for k in [
                    "sprawdzian",
                    "kartkówka",
                    "praca klasowa",
                    "zebranie",
                    "wywiadówka",
                    "egzamin",
                    "wycieczka",
                    "dzień wolny",
                    "konkurs",
                    "apel",
                    "uroczystość",
                ]
            ):
                raw_id = f"event_{text[:60]}"
                feed_id = SchoolFeedItem.make_id(SchoolItemType.EVENT, raw_id)
                items.append(
                    SchoolFeedItem(
                        id=feed_id,
                        item_type=SchoolItemType.EVENT,
                        title="Terminarz: Wydarzenie / Sprawdzian",
                        content=text,
                        metadata={"source": "synergia_terminarz"},
                    )
                )

        return items

    def fetch_homework(self) -> List[SchoolFeedItem]:
        """
        Fetch homework entries (Zadania domowe).
        """
        if not self.is_authenticated:
            self.login_sync()

        assert_secure_librus_url(SYNERGIA_HOMEWORK_URL)
        resp = self.session.get(SYNERGIA_HOMEWORK_URL, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()

        return self._parse_homework_html(resp.text)

    def _parse_homework_html(self, html: str) -> List[SchoolFeedItem]:
        """Parse Zadania domowe HTML."""
        soup = BeautifulSoup(html, "html.parser")
        items: List[SchoolFeedItem] = []

        tables = soup.select("table.decorated")
        for table in tables:
            rows = table.find_all("tr", attrs={"class": ["line0", "line1"]})
            for r in rows:
                cells = [c.get_text(strip=True) for c in r.find_all("td")]
                if len(cells) >= 4:
                    subject = cells[0]
                    teacher = cells[1]
                    hw_title = cells[2]
                    hw_date = cells[3]
                    raw_id = f"{subject}_{hw_title}_{hw_date}"
                    feed_id = SchoolFeedItem.make_id(SchoolItemType.HOMEWORK, raw_id)
                    items.append(
                        SchoolFeedItem(
                            id=feed_id,
                            item_type=SchoolItemType.HOMEWORK,
                            title=f"Zadanie domowe: {subject} - {hw_title}",
                            author=teacher,
                            date=hw_date,
                            content=f"Przedmiot: {subject}\nZadanie: {hw_title}\nTermin: {hw_date}",
                            metadata={"subject": subject, "source": "synergia_homework"},
                        )
                    )

        return items

    def fetch_notes(self) -> List[SchoolFeedItem]:
        """
        Fetch conduct remarks and teacher notes (Uwagi i informacje).
        """
        if not self.is_authenticated:
            self.login_sync()

        assert_secure_librus_url(SYNERGIA_NOTES_URL)
        resp = self.session.get(SYNERGIA_NOTES_URL, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()

        return self._parse_notes_html(resp.text)

    def _parse_notes_html(self, html: str) -> List[SchoolFeedItem]:
        """Parse Uwagi i informacje HTML table."""
        soup = BeautifulSoup(html, "html.parser")
        items: List[SchoolFeedItem] = []

        tables = soup.select("table.decorated")
        for table in tables:
            rows = table.find_all("tr", attrs={"class": ["line0", "line1"]})
            for r in rows:
                cells = [c.get_text(" ", strip=True) for c in r.find_all("td")]
                if len(cells) >= 4:
                    date_str = cells[0]
                    category = cells[1]
                    teacher = cells[2]
                    remark_text = cells[3]
                    points = cells[4] if len(cells) >= 5 else ""

                    title = f"Uwaga [{category}]: {remark_text[:50]}"
                    content = f"Kategoria: {category}\nNauczyciel: {teacher}\nData: {date_str}"
                    if points:
                        content += f"\nPunkty: {points}"
                    content += f"\nTreść: {remark_text}"

                    raw_id = f"{date_str}_{teacher}_{remark_text[:50]}"
                    feed_id = SchoolFeedItem.make_id(SchoolItemType.NOTE, raw_id)

                    items.append(
                        SchoolFeedItem(
                            id=feed_id,
                            item_type=SchoolItemType.NOTE,
                            title=title,
                            author=teacher,
                            date=date_str,
                            content=content,
                            metadata={
                                "category": category,
                                "points": points,
                                "source": "synergia_uwagi",
                            },
                        )
                    )

        return items

    def collect_all(self, limit_messages: int = 30) -> List[SchoolFeedItem]:
        """
        Collect all announcements, messages, events, homework, and notes.
        Errors on secondary items do not fail the main collection.
        """
        all_items: List[SchoolFeedItem] = []

        # 1. Announcements
        try:
            announcements = self.fetch_announcements()
            all_items.extend(announcements)
            logger.info("Fetched %d announcements", len(announcements))
        except Exception as e:
            logger.error("Failed to fetch announcements: %s", e)

        # 2. Messages
        try:
            messages = self.fetch_messages(limit=limit_messages)
            all_items.extend(messages)
            logger.info("Fetched %d messages", len(messages))
        except Exception as e:
            logger.error("Failed to fetch messages: %s", e)

        # 3. Calendar events (best effort)
        try:
            events = self.fetch_calendar_events()
            all_items.extend(events)
            logger.info("Fetched %d calendar events", len(events))
        except Exception as e:
            logger.debug("Calendar events unavailable: %s", e)

        # 4. Homework (best effort)
        try:
            homework = self.fetch_homework()
            all_items.extend(homework)
            logger.info("Fetched %d homework items", len(homework))
        except Exception as e:
            logger.debug("Homework unavailable: %s", e)

        # 5. Teacher notes / remarks (Uwagi i informacje - best effort)
        try:
            notes = self.fetch_notes()
            all_items.extend(notes)
            logger.info("Fetched %d teacher notes/remarks", len(notes))
        except Exception as e:
            logger.debug("Notes unavailable: %s", e)

        return all_items

    @staticmethod
    def _decode_message_content(raw: str) -> str:
        """Decode base64 encoded message content with utf-8 fallback."""
        if not raw:
            return ""
        try:
            # Check if looks like base64
            decoded = b64decode(raw).decode("utf-8", errors="replace")
            # If successfully decoded, return decoded text
            return decoded
        except Exception:
            return raw
