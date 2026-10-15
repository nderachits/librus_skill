"""Configuration management loaded from .env and environment variables."""

import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Load environment variables from .env file if present
load_dotenv()


class AppConfig(BaseModel):
    """Application configuration."""

    librus_login: str = Field(
        default_factory=lambda: (
            os.getenv("LIBRUS_LOGIN") or os.getenv("LIBRUS_USER") or ""
        ),
        description="Librus username/login",
    )
    librus_password: str = Field(
        default_factory=lambda: os.getenv("LIBRUS_PASSWORD") or "",
        description="Librus account password",
    )
    google_doc_id: Optional[str] = Field(
        default_factory=lambda: os.getenv("GOOGLE_DOC_ID"),
        description="Google Doc ID where information is appended",
    )
    google_service_account_file: Optional[str] = Field(
        default_factory=lambda: (
            os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
            or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        ),
        description="Path to Google Service Account JSON file",
    )
    google_client_secrets_file: str = Field(
        default_factory=lambda: os.getenv("GOOGLE_CLIENT_SECRETS_FILE", "credentials.json"),
        description="Path to OAuth2 client secrets JSON file",
    )
    google_token_file: str = Field(
        default_factory=lambda: os.getenv("GOOGLE_TOKEN_FILE", "token.json"),
        description="Path to OAuth2 token cache JSON file",
    )
    local_export_path: str = Field(
        default_factory=lambda: os.getenv("LOCAL_EXPORT_PATH", "librus_updates.md"),
        description="Path to local Markdown file fallback",
    )
    sync_interval_seconds: int = Field(
        default_factory=lambda: int(os.getenv("SYNC_INTERVAL_SECONDS", "3600")),
        description="Interval between synchronization runs in seconds (default: 3600 = 1 hour)",
    )
    fetch_messages_limit: int = Field(
        default_factory=lambda: int(os.getenv("FETCH_MESSAGES_LIMIT", "50")),
        description="Maximum number of recent messages to inspect per run",
    )
    state_file_path: str = Field(
        default_factory=lambda: os.getenv("LIBRUS_STATE_FILE", ".librus_state.json"),
        description="Path to deduplication state JSON file",
    )
    items_dir: str = Field(
        default_factory=lambda: os.getenv("LIBRUS_ITEMS_DIR", "data/items"),
        description="Path to directory where individual markdown items are stored",
    )
    telegram_bot_token: Optional[str] = Field(
        default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN"),
        description="Telegram Bot token for notifications",
    )
    telegram_chat_id: Optional[str] = Field(
        default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID"),
        description="Telegram chat ID or channel username for notifications",
    )


    def validate_librus_credentials(self) -> None:
        """Verify that Librus credentials have been configured."""
        if not self.librus_login or not self.librus_password:
            raise ValueError(
                "Librus credentials missing! Please configure LIBRUS_LOGIN and "
                "LIBRUS_PASSWORD in your .env file or environment variables."
            )


def get_config() -> AppConfig:
    """Retrieve application configuration instance."""
    return AppConfig()
