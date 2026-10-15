"""Tests for security safeguards, HTTPS enforcement, and secret masking."""

import pytest
from librus_collector.security import (
    assert_secure_librus_url,
    mask_secret,
    sanitize_message,
    SecurityError,
)


def test_assert_secure_librus_url_valid_domains():
    """Valid HTTPS URLs to official Librus domains must succeed."""
    assert_secure_librus_url("https://api.librus.pl/OAuth/Authorization")
    assert_secure_librus_url("https://synergia.librus.pl/ogloszenia")
    assert_secure_librus_url("https://wiadomosci.librus.pl/api/inbox/messages")
    assert_secure_librus_url("https://portal.librus.pl/rodzina")
    assert_secure_librus_url("https://konta.librus.pl/login")
    assert_secure_librus_url("https://sub.synergia.librus.pl/test")


def test_assert_secure_librus_url_rejects_http():
    """Unencrypted HTTP must raise SecurityError."""
    with pytest.raises(SecurityError, match="Insecure transport rejected: http://"):
        assert_secure_librus_url("http://api.librus.pl/OAuth/Authorization")

    with pytest.raises(SecurityError, match="Insecure transport rejected: ftp://"):
        assert_secure_librus_url("ftp://synergia.librus.pl/data")


def test_assert_secure_librus_url_rejects_unauthorized_hosts():
    """URLs outside allowed Librus domains must raise SecurityError."""
    with pytest.raises(SecurityError, match="Unauthorized host 'evil.com'"):
        assert_secure_librus_url("https://evil.com/OAuth/Authorization")

    with pytest.raises(SecurityError, match="Unauthorized host 'librus.fakedomain.org'"):
        assert_secure_librus_url("https://librus.fakedomain.org/login")

    with pytest.raises(SecurityError, match="Missing hostname"):
        assert_secure_librus_url("https://")


def test_mask_secret():
    """Secrets must be masked properly for safe display."""
    assert mask_secret("") == ""
    assert mask_secret("a") == "****"
    assert mask_secret("pass") == "****"
    assert mask_secret("supersecret") == "s***t"
    assert mask_secret("mypassword123") == "m***3"


def test_sanitize_message():
    """Sensitive passwords must be redacted from error messages and logs."""
    secret = "SuperSecretPassword123!"
    log_msg = f"Failed to login with user 'john' and password '{secret}' at endpoint"
    
    sanitized = sanitize_message(log_msg, [secret])
    assert secret not in sanitized
    assert "[REDACTED_CREDENTIAL]" in sanitized

    # Empty secret should not alter message
    assert sanitize_message("Normal log", ["", None]) == "Normal log"


def test_sanitize_message_url_encoded_secret():
    """URL-encoded variants of credentials must also be redacted."""
    secret = "pass&word@123!"
    url_encoded = "pass%26word%40123%21"
    raw_error = f"HTTP 401 error posting body: pass={url_encoded}&user=parent"

    sanitized = sanitize_message(raw_error, [secret])
    assert url_encoded not in sanitized
    assert "[REDACTED_CREDENTIAL]" in sanitized
