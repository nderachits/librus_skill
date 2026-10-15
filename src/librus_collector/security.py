"""Security and credential safety safeguards.

Guarantees:
1. Credentials are NEVER transmitted unencrypted (only TLS/HTTPS).
2. Credentials are NEVER transmitted to any host other than official Librus domains.
3. Secrets are redacted from logs, string representations, and error messages.
"""

from urllib.parse import quote, quote_plus, urlparse
from typing import Iterable, Optional, Set


class SecurityError(Exception):
    """Raised when an operation violates security policy."""
    pass


class LibrusAuthError(Exception):
    """Raised when authentication with Librus fails."""
    pass


ALLOWED_LIBRUS_HOSTS: Set[str] = {
    "api.librus.pl",
    "synergia.librus.pl",
    "wiadomosci.librus.pl",
    "portal.librus.pl",
    "konta.librus.pl",
    "librus.pl",
}


def assert_secure_librus_url(url: str, check_allowed_hosts: bool = True) -> None:
    """
    Ensure the target URL is encrypted via HTTPS and points to an authorized Librus domain.
    
    Raises:
        SecurityError: If url is not HTTPS or points to an unauthorized host.
    """
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        raise SecurityError(
            f"Insecure transport rejected: {parsed.scheme}://. "
            "Librus credentials and session data must ONLY be transmitted over encrypted HTTPS."
        )

    host = (parsed.hostname or "").lower()
    if check_allowed_hosts:
        if not host:
            raise SecurityError(f"Missing hostname in URL: {url}")
        
        # Verify host is either an exact registered host or an official *.librus.pl domain
        is_allowed = (
            host == "librus.pl"
            or host.endswith(".librus.pl")
            or host in ALLOWED_LIBRUS_HOSTS
        )
        if not is_allowed:
            raise SecurityError(
                f"Unauthorized host '{host}'! "
                f"Credentials must never be sent outside official Librus domains: {ALLOWED_LIBRUS_HOSTS}."
            )


def mask_secret(secret: str) -> str:
    """Return a masked representation of a sensitive string."""
    if not secret:
        return ""
    if len(secret) <= 4:
        return "****"
    return f"{secret[:1]}***{secret[-1:]}"


def sanitize_message(message: str, secrets: Iterable[Optional[str]]) -> str:
    """Scrub sensitive secrets (both plaintext and URL-encoded) from log messages or exceptions."""
    result = message
    for sec in secrets:
        if not sec or not isinstance(sec, str) or len(sec) < 2:
            continue
        variants = {sec, quote(sec), quote_plus(sec)}
        for var in variants:
            if var in result:
                result = result.replace(var, "[REDACTED_CREDENTIAL]")
    return result
