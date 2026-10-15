"""Google Docs API connector and document writer for Librus updates.

Supports:
- Google Cloud Service Account (recommended for headless hourly syncs)
- OAuth 2.0 User credentials (credentials.json / token.json)
- Local file fallback (e.g. librus_updates.md) when Google Docs is not configured
"""

import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DOCS_SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.file",
]


def extract_doc_id(raw_doc_id_or_url: Optional[str]) -> Optional[str]:
    """Extract clean Google Doc ID whether passed as a raw ID or full browser URL."""
    if not raw_doc_id_or_url:
        return None
    val = raw_doc_id_or_url.strip()
    match = re.search(r"/document/d/([a-zA-Z0-9-_]+)", val)
    if match:
        return match.group(1)
    return val


class GoogleDocsSync:
    """Manages appending content to a Google Doc."""

    def __init__(
        self,
        doc_id: Optional[str] = None,
        service_account_file: Optional[str] = None,
        client_secrets_file: Optional[str] = None,
        token_file: Optional[str] = None,
        local_fallback_file: Optional[str] = None,
    ):
        raw_doc_id = doc_id or os.getenv("GOOGLE_DOC_ID")
        self.doc_id = extract_doc_id(raw_doc_id)
        self.service_account_file = (
            service_account_file
            or os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
            or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        )
        self.client_secrets_file = (
            client_secrets_file
            or os.getenv("GOOGLE_CLIENT_SECRETS_FILE", "credentials.json")
        )
        self.token_file = token_file or os.getenv("GOOGLE_TOKEN_FILE", "token.json")
        self.local_fallback_file = Path(
            local_fallback_file or os.getenv("LOCAL_EXPORT_PATH", "librus_updates.md")
        )
        self._service = None

    @property
    def is_google_configured(self) -> bool:
        """Check if any Google credentials and doc ID are configured."""
        has_creds = bool(
            (self.service_account_file and Path(self.service_account_file).exists())
            or (self.client_secrets_file and Path(self.client_secrets_file).exists())
            or (self.token_file and Path(self.token_file).exists())
            or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        )
        return has_creds and bool(self.doc_id)

    def get_service(self):
        """Build and cache authenticated Google Docs API service resource."""
        if self._service is not None:
            return self._service

        creds = None

        # 1. Try Service Account file first (preferred for headless daemons)
        if self.service_account_file and Path(self.service_account_file).exists():
            from google.oauth2 import service_account
            logger.info("Authenticating with Google Docs using Service Account: %s", self.service_account_file)
            creds = service_account.Credentials.from_service_account_file(
                self.service_account_file, scopes=DOCS_SCOPES
            )

        # 2. Try User OAuth credentials (token.json or credentials.json)
        elif (self.token_file and Path(self.token_file).exists()) or (
            self.client_secrets_file and Path(self.client_secrets_file).exists()
        ):
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from google.auth.transport.requests import Request

            if self.token_file and Path(self.token_file).exists():
                logger.info("Loading existing Google OAuth token from %s", self.token_file)
                creds = Credentials.from_authorized_user_file(self.token_file, DOCS_SCOPES)

            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    logger.info("Refreshing expired Google OAuth token...")
                    creds.refresh(Request())
                elif self.client_secrets_file and Path(self.client_secrets_file).exists():
                    logger.info("Prompting user OAuth flow with %s...", self.client_secrets_file)
                    flow = InstalledAppFlow.from_client_secrets_file(
                        self.client_secrets_file, DOCS_SCOPES
                    )
                    creds = flow.run_local_server(port=0)

                # Persist updated token
                if creds and self.token_file:
                    Path(self.token_file).parent.mkdir(parents=True, exist_ok=True)
                    with open(self.token_file, "w", encoding="utf-8") as f:
                        f.write(creds.to_json())

        # 3. Fallback to Application Default Credentials (e.g. gcloud auth application-default login)
        if not creds:
            try:
                import google.auth
                creds, _ = google.auth.default(scopes=DOCS_SCOPES)
                logger.info("Loaded Google Application Default Credentials (ADC).")
            except Exception:
                pass

        if not creds:
            raise RuntimeError(
                "No valid Google credentials found. Provide GOOGLE_SERVICE_ACCOUNT_FILE "
                "or credentials.json, or use local file fallback."
            )

        from googleapiclient.discovery import build
        self._service = build("docs", "v1", credentials=creds)
        return self._service

    def create_document(self, title: str = "Librus School Updates (NotebookLM)") -> str:
        """Create a new Google Doc and return its documentId."""
        service = self.get_service()
        doc = service.documents().create(body={"title": title}).execute()
        doc_id = doc.get("documentId")
        self.doc_id = doc_id
        logger.info("Created new Google Doc '%s' with ID: %s", title, doc_id)
        return doc_id

    def append_text(self, text: str) -> Dict[str, Any]:
        """
        Append text to the configured Google Doc.
        If Google Docs is not configured or fails, falls back to the local markdown file.
        """
        if not text:
            return {"status": "noop", "message": "No text to append."}

        # Check if Google Docs is available
        if not self.doc_id or not self.is_google_configured:
            logger.info(
                "Google Docs not configured or missing credentials. Appending to local file: %s",
                self.local_fallback_file,
            )
            return self._append_local(text)

        try:
            service = self.get_service()

            # Retrieve the current document to find its end index
            doc = service.documents().get(documentId=self.doc_id).execute()
            
            # Support both standard documents and tabbed documents
            body = doc.get("body")
            if not body and "tabs" in doc:
                for tab in doc.get("tabs", []):
                    tab_body = tab.get("documentTab", {}).get("body")
                    if tab_body:
                        body = tab_body
                        break
            content = (body or {}).get("content", [])

            end_index = 1
            if content:
                end_index = content[-1].get("endIndex", 1)

            # Insert at the end (subtract 1 for the mandatory trailing newline index)
            insert_location = max(1, end_index - 1)

            requests_payload = [
                {
                    "insertText": {
                        "location": {"index": insert_location},
                        "text": text,
                    }
                }
            ]

            result = (
                service.documents()
                .batchUpdate(documentId=self.doc_id, body={"requests": requests_payload})
                .execute()
            )

            logger.info("Successfully appended %d characters to Google Doc %s", len(text), self.doc_id)
            return {
                "status": "success",
                "target": "google_doc",
                "doc_id": self.doc_id,
                "doc_url": f"https://docs.google.com/document/d/{self.doc_id}/edit",
                "result": result,
            }

        except Exception as e:
            logger.error("Failed to append to Google Doc %s: %s. Falling back to local file.", self.doc_id, e)
            local_res = self._append_local(text)
            local_res["google_error"] = str(e)
            return local_res

    def _append_local(self, text: str) -> Dict[str, Any]:
        """Append formatted text to local fallback file."""
        self.local_fallback_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.local_fallback_file, "a", encoding="utf-8") as f:
            f.write(text)

        logger.info("Appended %d characters to local fallback file: %s", len(text), self.local_fallback_file)
        return {
            "status": "success",
            "target": "local_file",
            "file_path": str(self.local_fallback_file),
        }
