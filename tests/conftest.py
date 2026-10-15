"""Pytest fixtures and configuration for hermetic test execution."""

import pytest


@pytest.fixture(autouse=True)
def isolate_test_environment(monkeypatch):
    """Isolate tests from ambient developer credentials in .env."""
    monkeypatch.delenv("GOOGLE_DOC_ID", raising=False)
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_FILE", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRETS_FILE", raising=False)
    monkeypatch.delenv("GOOGLE_TOKEN_FILE", raising=False)
