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
from librus_collector.models import SchoolItemType
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
    assert msg.id == "message:msg_999"
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
    assert msg.id == "message:123456"
    assert msg.title == "Zastępstwo za lekcję fizyki"
    assert msg.author == "Kowalski Tomasz"
    assert msg.date == "2026-10-15 08:00"
