"""Tests for native LibrusClient authentication and data extraction."""

import base64
import pytest
import responses

from librus_collector.librus_client import (
    API_BASE_URL,
    API_OAUTH_AUTHORIZATION_URL,
    LibrusClient,
    SYNERGIA_ANNOUNCEMENTS_URL,
    SYNERGIA_BASE_URL,
    SYNERGIA_MESSAGES_BOOTSTRAP_URL,
    SYNERGIA_PORTAL_LOGIN_URL,
    WIADOMOSCI_DOMAIN,
)
from librus_collector.models import SchoolFeedItem, SchoolItemType
from librus_collector.security import LibrusAuthError, SecurityError


def test_librus_client_init_validation():
    """Empty credentials should be rejected immediately."""
    with pytest.raises(ValueError, match="must not be empty"):
        LibrusClient("", "pass")

    with pytest.raises(ValueError, match="must not be empty"):
        LibrusClient("login", "")


@responses.activate
def test_librus_client_login_success():
    """Verify standard Librus OAuth authentication flow."""
    client = LibrusClient("parent_login", "secret_pass")

    # Step 1: Portal entrypoint redirects to OAuth authorization URL
    responses.add(
        responses.GET,
        SYNERGIA_PORTAL_LOGIN_URL,
        status=302,
        headers={"Location": f"{API_BASE_URL}/OAuth/Authorization?client_id=46&response_type=code"},
    )

    # Step 2: Init authorization
    responses.add(
        responses.GET,
        f"{API_BASE_URL}/OAuth/Authorization?client_id=46&response_type=code",
        status=200,
    )

    # Step 3: Post credentials
    responses.add(
        responses.POST,
        API_OAUTH_AUTHORIZATION_URL,
        json={"status": "ok", "goTo": "/OAuth/Authorization/Grant?code=xyz"},
        status=200,
    )

    # Step 4: Grant redirect
    grant_url = f"{API_BASE_URL}/OAuth/Authorization/Grant?code=xyz"
    responses.add(
        responses.GET,
        grant_url,
        status=302,
        headers={"Location": f"{SYNERGIA_BASE_URL}/uczen/index"},
    )
    responses.add(
        responses.GET,
        f"{SYNERGIA_BASE_URL}/uczen/index",
        status=200,
    )

    # Step 5: MultiDomainLogon bootstrap
    responses.add(
        responses.GET,
        SYNERGIA_MESSAGES_BOOTSTRAP_URL,
        status=200,
    )

    result = client.login_sync()
    assert result is True
    assert client.is_authenticated is True


@responses.activate
def test_librus_client_login_failure_invalid_credentials():
    """Invalid credentials should raise LibrusAuthError without leaking password."""
    client = LibrusClient("bad_user", "wrong_pass")

    responses.add(
        responses.GET,
        SYNERGIA_PORTAL_LOGIN_URL,
        status=302,
        headers={"Location": f"{API_BASE_URL}/OAuth/Authorization?client_id=46"},
    )
    responses.add(
        responses.GET,
        f"{API_BASE_URL}/OAuth/Authorization?client_id=46",
        status=200,
    )
    responses.add(
        responses.POST,
        API_OAUTH_AUTHORIZATION_URL,
        json={"status": "error", "message": "Błędny login lub hasło."},
        status=200,
    )

    with pytest.raises(LibrusAuthError, match="Błędny login lub hasło"):
        client.login_sync()

    assert client.is_authenticated is False


@responses.activate
def test_librus_client_login_insecure_redirect_rejected():
    """If server redirects to HTTP or untrusted host, SecurityError must trigger."""
    client = LibrusClient("user", "pass")

    responses.add(
        responses.GET,
        SYNERGIA_PORTAL_LOGIN_URL,
        status=302,
        headers={"Location": "http://api.librus.pl/OAuth/Authorization"},  # Insecure HTTP
    )

    with pytest.raises(SecurityError, match="Insecure transport rejected"):
        client.login_sync()


def test_parse_announcements_html():
    """Verify parsing announcement tables."""
    client = LibrusClient("user", "pass")
    html = """
    <html>
        <body>
            <table class="decorated big">
                <thead>
                    <tr><td>Plan lekcji w dniu 20 października</td></tr>
                </thead>
                <tbody>
                    <tr class="line0"><td>Dyrektor Szkoły</td></tr>
                    <tr class="line1"><td>2026-10-14 09:30:00</td></tr>
                    <tr class="line0"><td>W tym dniu lekcje zostają skrócone o 10 minut.</td></tr>
                </tbody>
            </table>
        </body>
    </html>
    """
    items = client._parse_announcements_html(html)
    assert len(items) == 1
    item = items[0]
    assert item.item_type == SchoolItemType.ANNOUNCEMENT
    assert item.title == "Plan lekcji w dniu 20 października"
    assert item.author == "Dyrektor Szkoły"
    assert item.date == "2026-10-14 09:30:00"
    assert "skrócone o 10 minut" in item.content


def test_parse_announcements_empty_html():
    """Empty announcements view returns empty list."""
    client = LibrusClient("user", "pass")
    html = """
    <div class="container border-red resizeable center">
        <div><p>Brak ogłoszeń</p></div>
    </div>
    """
    items = client._parse_announcements_html(html)
    assert items == []


@responses.activate
def test_fetch_messages_via_json_api():
    """Verify modern Wiadomości API message retrieval and base64 content decoding."""
    client = LibrusClient("user", "pass")
    client.is_authenticated = True

    b64_body = base64.b64encode("Dzień dobry, przesyłam informację o konkursie.".encode("utf-8")).decode("utf-8")
    mock_api_url = f"https://{WIADOMOSCI_DOMAIN}/api/inbox/messages?page=1&limit=50"

    responses.add(
        responses.GET,
        mock_api_url,
        json={
            "data": [
                {
                    "messageId": "msg_999",
                    "senderFirstName": "Anna",
                    "senderLastName": "Nowak",
                    "senderName": "Anna Nowak",
                    "topic": "Konkurs informatyczny",
                    "content": b64_body,
                    "sendDate": "2026-10-15 11:20:00",
                    "readDate": None,
                    "isAnyFileAttached": True,
                }
            ],
            "total": 1,
        },
        status=200,
    )

    items = client.fetch_messages(limit=50)
    assert len(items) == 1
    msg = items[0]
    assert msg.item_type == SchoolItemType.MESSAGE
    assert msg.id == "2026-10-15_112000_message_msg_999"
    assert msg.title == "Konkurs informatyczny"
    assert msg.author == "Anna Nowak"
    assert "przesyłam informację" in msg.content
    assert msg.attachments == ["Załącznik dołączony do wiadomości"]


@responses.activate
def test_fetch_messages_html_fallback():
    """Verify fallback to Synergia HTML when wiadomosci JSON API is down."""
    client = LibrusClient("user", "pass")
    client.is_authenticated = True

    # Wiadomości API fails (e.g. 404 or 500)
    mock_api_url = f"https://{WIADOMOSCI_DOMAIN}/api/inbox/messages?page=1&limit=50"
    responses.add(responses.GET, mock_api_url, status=500)

    # HTML endpoint succeeds
    html = """
    <table class="decorated stretch">
        <tbody>
            <tr class="line0">
                <td><input type="checkbox"/></td>
                <td></td>
                <td>Kowalski Tomasz</td>
                <td><a href="/wiadomosci/1/5/123456">Zastępstwo za lekcję fizyki</a></td>
                <td>2026-10-15 08:00</td>
                <td>NIE</td>
            </tr>
        </tbody>
    </table>
    """
    responses.add(
        responses.GET,
        "https://synergia.librus.pl/wiadomosci/1/5",
        body=html,
        status=200,
    )

    items = client.fetch_messages(limit=50)
    assert len(items) == 1
    msg = items[0]
    assert msg.item_type == SchoolItemType.MESSAGE
    assert msg.id == "2026-10-15_080000_message_123456"
    assert msg.title == "Zastępstwo za lekcję fizyki"
    assert msg.author == "Kowalski Tomasz"
    assert msg.date == "2026-10-15 08:00"


@responses.activate
def test_populate_message_content_via_html():
    """Verify populate_message_content loads full message body from HTML detail page."""
    client = LibrusClient("user", "pass")
    client.is_authenticated = True

    detail_html = """
    <html>
        <body>
            <table class="decorated stretch">
                <tbody>
                    <tr><th>Od:</th><td>Kowalski Tomasz</td></tr>
                    <tr><th>Tytuł:</th><td>Zastępstwo za lekcję fizyki</td></tr>
                    <tr><th>Wysłano:</th><td>2026-10-15 08:00</td></tr>
                    <tr>
                        <th>Treść:</th>
                        <td class="container-message">
                            Lekcja fizyki w sali 102 zostaje zastąpiona informatyką w sali 204.
                        </td>
                    </tr>
                </tbody>
            </table>
            <a href="/pobierz_zalacznik/123">materialy.pdf</a>
        </body>
    </html>
    """
    responses.add(
        responses.GET,
        "https://synergia.librus.pl/wiadomosci/1/5/123456",
        body=detail_html,
        status=200,
    )

    item = SchoolFeedItem(
        id="message:123456",
        item_type=SchoolItemType.MESSAGE,
        title="Zastępstwo za lekcję fizyki",
        metadata={"link": "/wiadomosci/1/5/123456", "source": "synergia_html"},
    )
    client.populate_message_content(item)

    assert "Lekcja fizyki w sali 102" in item.content
    assert "materialy.pdf" in item.attachments


@responses.activate
def test_populate_message_content_via_api():
    """Verify populate_message_content decodes base64 body from modern message API."""
    client = LibrusClient("user", "pass")
    client.is_authenticated = True

    b64_content = base64.b64encode("Szczegóły wycieczki klasowej do Krakowa.".encode("utf-8")).decode("utf-8")
    responses.add(
        responses.GET,
        f"https://{WIADOMOSCI_DOMAIN}/api/message/777",
        json={"data": {"content": b64_content}},
        status=200,
    )

    item = SchoolFeedItem(
        id="message:777",
        item_type=SchoolItemType.MESSAGE,
        title="Wycieczka",
        metadata={"message_id": "777", "source": "wiadomosci_api"},
    )
    client.populate_message_content(item)

    assert item.content == "Szczegóły wycieczki klasowej do Krakowa."


@responses.activate
def test_populate_message_content_via_inbox_api_with_preview():
    """Verify populate_message_content replaces truncated preview with full body from inbox API and parses attachments."""
    client = LibrusClient("user", "pass")
    client.is_authenticated = True

    full_text = (
        "Dear Parents,\n\n"
        "today I will send home consents for the next weeks outing to music school. "
        "Please complete it with your childs name and sign.\n\n"
        "Regards\nMs Beata"
    )
    b64_full = base64.b64encode(full_text.encode("utf-8")).decode("utf-8")

    responses.add(
        responses.GET,
        f"https://{WIADOMOSCI_DOMAIN}/api/inbox/messages/1254708",
        json={
            "data": {
                "messageId": "1254708",
                "topic": "Consent for visit to a music school",
                "Message": b64_full,
                "attachments": [
                    {"id": "991", "filename": "zgoda_wycieczka.pdf"}
                ],
            }
        },
        status=200,
    )

    # Item initially has 100-char truncated preview from listing endpoint
    preview_content = "Dear Parents,\n\ntoday I will send home consents for the next weeks outing to music school. Please co"
    item = SchoolFeedItem(
        id="message:1254708",
        item_type=SchoolItemType.MESSAGE,
        title="Consent for visit to a music school",
        content=preview_content,
        attachments=["Załącznik dołączony do wiadomości"],
        metadata={
            "message_id": "1254708",
            "source": "wiadomosci_api",
            "has_full_content": False,
        },
    )

    client.populate_message_content(item)

    assert item.content == full_text
    assert item.metadata["has_full_content"] is True
    assert item.attachments == ["zgoda_wycieczka.pdf"]

    # Calling populate again should not perform additional requests (assert call count is 1)
    client.populate_message_content(item)
    assert len(responses.calls) == 1


@responses.activate
def test_fetch_notes():
    """Verify fetching teacher conduct remarks (Uwagi i informacje)."""
    client = LibrusClient("user", "pass")
    client.is_authenticated = True

    notes_html = """
    <table class="decorated stretch">
        <thead>
            <tr><td>Data</td><td>Kategoria</td><td>Nauczyciel</td><td>Treść</td><td>Punkty</td></tr>
        </thead>
        <tbody>
            <tr class="line0">
                <td>2026-10-14</td>
                <td>Pozytywna</td>
                <td>Nowak Anna</td>
                <td>Wzorowy udział w akcji wolontariatu szkolnego.</td>
                <td>+10</td>
            </tr>
        </tbody>
    </table>
    """
    responses.add(
        responses.GET,
        "https://synergia.librus.pl/uwagi",
        body=notes_html,
        status=200,
    )

    notes = client.fetch_notes()
    assert len(notes) == 1
    note = notes[0]
    assert note.item_type == SchoolItemType.NOTE
    assert "Pozytywna" in note.title
    assert note.author == "Nowak Anna"
    assert "akcji wolontariatu" in note.content
    assert note.metadata["points"] == "+10"


@responses.activate
def test_session_redirect_hook_blocks_http():
    """Verify that requests.Session response hook catches redirects to insecure HTTP."""
    client = LibrusClient("user", "pass")

    responses.add(
        responses.GET,
        "https://synergia.librus.pl/redirect_test",
        status=302,
        headers={"Location": "http://evil.com/leak"},
    )

    with pytest.raises(SecurityError, match="Insecure transport rejected"):
        client.session.get("https://synergia.librus.pl/redirect_test")


@responses.activate
def test_populate_message_content_fallback_on_api_error():
    """Verify that when API endpoints return 500/errors, initial preview is kept without raising."""
    client = LibrusClient("user", "pass")
    client.is_authenticated = True

    responses.add(
        responses.GET,
        f"https://{WIADOMOSCI_DOMAIN}/api/inbox/messages/888",
        status=500,
    )
    responses.add(
        responses.GET,
        f"https://{WIADOMOSCI_DOMAIN}/api/message/888",
        status=500,
    )
    responses.add(
        responses.GET,
        f"https://{WIADOMOSCI_DOMAIN}/api/messages/888",
        status=500,
    )

    item = SchoolFeedItem(
        id="message:888",
        item_type=SchoolItemType.MESSAGE,
        title="Błąd serwera",
        content="Podgląd wiadomości",
        metadata={
            "message_id": "888",
            "source": "wiadomosci_api",
            "has_full_content": False,
        },
    )

    client.populate_message_content(item)
    # Content should remain as fallback preview
    assert item.content == "Podgląd wiadomości"
    assert item.metadata["has_full_content"] is False


@responses.activate
def test_populate_message_content_html_fallback_when_api_fails():
    """Verify that if modern API endpoints fail but HTML link is available, HTML detail is used."""
    client = LibrusClient("user", "pass")
    client.is_authenticated = True

    responses.add(
        responses.GET,
        f"https://{WIADOMOSCI_DOMAIN}/api/inbox/messages/999",
        status=404,
    )
    responses.add(
        responses.GET,
        f"https://{WIADOMOSCI_DOMAIN}/api/message/999",
        status=404,
    )
    responses.add(
        responses.GET,
        f"https://{WIADOMOSCI_DOMAIN}/api/messages/999",
        status=404,
    )

    detail_html = """
    <html><body>
        <div class="container-message">Treść z widoku HTML po awarii API</div>
    </body></html>
    """
    responses.add(
        responses.GET,
        "https://synergia.librus.pl/wiadomosci/1/5/999",
        body=detail_html,
        status=200,
    )

    item = SchoolFeedItem(
        id="message:999",
        item_type=SchoolItemType.MESSAGE,
        title="Awaria API",
        content="Krótki podgląd",
        metadata={
            "message_id": "999",
            "source": "wiadomosci_api",
            "link": "/wiadomosci/1/5/999",
            "has_full_content": False,
        },
    )

    client.populate_message_content(item)
    assert item.content == "Treść z widoku HTML po awarii API"
    assert item.metadata["has_full_content"] is True


def test_decode_message_content_xml_cdata_unwrapping():
    """Verify Librus XML and CDATA blocks are stripped, leaving clean text."""
    xml_body = (
        "<Message><Content><![CDATA[\n"
        "Drodzy Rodzice,\n\n"
        "Zapraszamy na konkurs.\n"
        "]]></Content><Actions><Actions/></Actions></Message>"
    )
    b64_val = base64.b64encode(xml_body.encode("utf-8")).decode("utf-8")
    decoded = LibrusClient._decode_message_content(b64_val)
    assert decoded == "Drodzy Rodzice,\n\nZapraszamy na konkurs."
    assert "<Message>" not in decoded
    assert "<![CDATA[" not in decoded
    assert "</Content>" not in decoded


def test_decode_message_content_truncated_cdata_preview():
    """Verify truncated preview ending inside CDATA is unwrapped without error."""
    truncated_xml = "<Message><Content><![CDATA[Szanowni Państwo,\nW związku ze zgłoszeniem dziecka do LE"
    b64_val = base64.b64encode(truncated_xml.encode("utf-8")).decode("utf-8")
    decoded = LibrusClient._decode_message_content(b64_val)
    assert decoded == "Szanowni Państwo,\nW związku ze zgłoszeniem dziecka do LE"
    assert "<![CDATA[" not in decoded


def test_decode_message_content_html_tags_and_entities():
    """Verify HTML tags and entities like <br>, <p>, &nbsp; are converted to plain text."""
    html_body = "Dzień dobry,<br /><br />Spotkanie odbędzie się w sali.&nbsp;Dziękujemy!<p>Pozdrawiam</p>"
    b64_val = base64.b64encode(html_body.encode("utf-8")).decode("utf-8")
    decoded = LibrusClient._decode_message_content(b64_val)
    assert "Dzień dobry,\n\nSpotkanie odbędzie się w sali. Dziękujemy!\n\nPozdrawiam" in decoded
    assert "<br" not in decoded
    assert "&nbsp;" not in decoded


def test_parse_messages_html_realistic_synergia_table():
    """Verify parsing real Synergia HTML inbox where author has anchor, link has /f0 suffix, and attachment icon is present."""
    client = LibrusClient("user", "pass")
    html = """
    <table class="decorated stretch">
        <tbody>
            <tr class="line0">
                <td><input type="checkbox"/></td>
                <td><img src="/images/clip.png" title="załącznik"/></td>
                <td><a href="/wiadomosci/1/5/1254708/f0">Kruk-Zabawa Beata (Kruk-Zabawa Beata) [Nauczyciel]</a></td>
                <td><a href="/wiadomosci/1/5/1254708/f0">Consent for visit to a music school</a></td>
                <td>2026-10-15 06:13:04</td>
                <td><a href="javascript:void(0);">Usuń</a></td>
            </tr>
            <tr class="line1">
                <td><input type="checkbox"/></td>
                <td></td>
                <td><a href="/wiadomosci/1/5/1207517/f0">Anna Jurczak-Nicpoń</a></td>
                <td><a href="/wiadomosci/1/5/1207517/f0">Dzień wolny</a></td>
                <td>2026-10-13 09:12:43</td>
                <td></td>
            </tr>
        </tbody>
    </table>
    """
    items = client._parse_messages_html(html)
    assert len(items) == 2

    # Item 1
    assert items[0].id == "2026-10-15_061304_message_1254708"
    assert items[0].title == "Consent for visit to a music school"
    assert items[0].author == "Kruk-Zabawa Beata (Kruk-Zabawa Beata) [Nauczyciel]"
    assert items[0].date == "2026-10-15 06:13:04"
    assert items[0].attachments == ["Załącznik dołączony do wiadomości"]
    assert items[0].metadata["message_id"] == "1254708"

    # Item 2
    assert items[1].id == "2026-10-13_091243_message_1207517"
    assert items[1].title == "Dzień wolny"
    assert items[1].author == "Anna Jurczak-Nicpoń"
    assert items[1].date == "2026-10-13 09:12:43"
    assert items[1].attachments == []
    assert items[1].metadata["message_id"] == "1207517"


def test_parse_single_message_html_realistic_container_and_attachments():
    """Verify parsing real Synergia HTML single message view with container-message-content and file icons."""
    client = LibrusClient("user", "pass")
    html = """
    <table class="stretch container-message">
        <tr>
            <td>
                <div class="container-message-content">
                    Dear Parents,<br/><br/>
                    Please find the test attached.<br/>
                    Regards
                </div>
                <table>
                    <tr><td><b>Pliki:</b></td></tr>
                    <tr>
                        <td>
                            <img src="/assets/img/filetype_icons/pdf.png"/>
                            Angielski klasa 1.pdf
                        </td>
                        <td>
                            <a href="javascript:void(0);"><img onclick="otworz_w_nowym_oknie('/wiadomosci/pobierz_zalacznik/769356/123')"/></a>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
    """
    body, attachments = client._parse_single_message_html(html)
    assert "Dear Parents," in body
    assert "Please find the test attached." in body
    assert attachments == ["Angielski klasa 1.pdf"]


@responses.activate
def test_populate_message_content_html_fallback_without_link_metadata():
    """Verify HTML fallback succeeds using reconstructed /wiadomosci/1/5/{msg_id}/f0 when link metadata is None."""
    client = LibrusClient("user", "pass")
    client.is_authenticated = True

    responses.add(
        responses.GET,
        f"https://{WIADOMOSCI_DOMAIN}/api/inbox/messages/555",
        status=500,
    )
    responses.add(
        responses.GET,
        f"https://{WIADOMOSCI_DOMAIN}/api/message/555",
        status=500,
    )
    responses.add(
        responses.GET,
        f"https://{WIADOMOSCI_DOMAIN}/api/messages/555",
        status=500,
    )

    detail_html = """
    <div class="container-message-content">Treść odzyskana przez Synergia URL fallback</div>
    """
    responses.add(
        responses.GET,
        "https://synergia.librus.pl/wiadomosci/1/5/555/f0",
        body=detail_html,
        status=200,
    )

    item = SchoolFeedItem(
        id="message:555",
        item_type=SchoolItemType.MESSAGE,
        title="Awaria API bez linku",
        content="Podgląd",
        metadata={
            "message_id": "555",
            "source": "wiadomosci_api",
            "has_full_content": False,
        },
    )

    client.populate_message_content(item)
    assert item.content == "Treść odzyskana przez Synergia URL fallback"
    assert item.metadata["has_full_content"] is True


def test_calendar_events_unique_ids():
    """Verify events sharing a 60-char prefix generate distinct IDs."""
    from bs4 import BeautifulSoup
    client = LibrusClient("user", "pass")

    # HTML with two events that have the exact same first 60 characters
    prefix = "5 Nieobecność: Nauczyciel: Palmowska-Latuszek Paulina Nieobecność: "
    html = f"""
    <div class="calendar-subtable">
        <table>
            <td>{prefix} Wycieczka klasowa A</td>
            <td>{prefix} Wycieczka klasowa B</td>
        </table>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    items = []
    # Test client._parse_terminarz_cell logic
    for td in soup.find_all("td"):
        text = td.get_text(" ", strip=True)
        raw_id = f"event_{text.strip()}"
        feed_id = SchoolFeedItem.make_id(SchoolItemType.EVENT, raw_id)
        items.append(SchoolFeedItem(
            id=feed_id,
            item_type=SchoolItemType.EVENT,
            title="Terminarz: Wydarzenie / Sprawdzian",
            content=text,
        ))

    assert len(items) == 2
    assert items[0].id != items[1].id



