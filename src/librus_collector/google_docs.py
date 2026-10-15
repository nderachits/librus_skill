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


_UNSET = object()


class GoogleDocsSync:
    """Manages appending or prepending content to a Google Doc."""

    def __init__(
        self,
        doc_id: Any = _UNSET,
        service_account_file: Any = _UNSET,
        client_secrets_file: Any = _UNSET,
        token_file: Any = _UNSET,
        local_fallback_file: Optional[str] = None,
        insert_position: Optional[str] = None,
    ):
        raw_doc_id = os.getenv("GOOGLE_DOC_ID") if doc_id is _UNSET else doc_id
        self.doc_id = extract_doc_id(raw_doc_id)
        if service_account_file is _UNSET:
            self.service_account_file = (
                os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
                or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
            )
        else:
            self.service_account_file = service_account_file

        if client_secrets_file is _UNSET:
            self.client_secrets_file = os.getenv("GOOGLE_CLIENT_SECRETS_FILE", "credentials.json")
        else:
            self.client_secrets_file = client_secrets_file

        if token_file is _UNSET:
            self.token_file = os.getenv("GOOGLE_TOKEN_FILE", "token.json")
        else:
            self.token_file = token_file

        self.local_fallback_file = Path(
            local_fallback_file or os.getenv("LOCAL_EXPORT_PATH", "librus_updates.md")
        )
        self.insert_position = (insert_position or os.getenv("INSERT_POSITION", "beginning")).lower()
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

    def append_text(self, text: str, rewrite: bool = False) -> Dict[str, Any]:
        """
        Write or insert text to the configured Google Doc.
        - If rewrite is True (e.g. forced resync or rebuild), replaces entire doc content.
        - If rewrite is False (incremental sync):
            - If insert_position == "beginning", inserts at the beginning (index 1).
            - If insert_position == "end", appends text to the end.
        In all cases, the local file (e.g. librus_updates.md) is kept updated as
        an exact mirror of the document content.
        """
        if not text:
            return {"status": "noop", "message": "No text to append."}

        action_name = "rewrite" if rewrite else ("prepend" if self.insert_position == "beginning" else "append")
        text_to_insert = text.lstrip("\n") if rewrite else text

        # Check if Google Docs is available
        if not self.doc_id or not self.is_google_configured:
            logger.info(
                "Google Docs not configured or missing credentials. %s to local file: %s",
                "Rewriting" if rewrite else ("Prepending" if self.insert_position == "beginning" else "Appending"),
                self.local_fallback_file,
            )
            return self._write_local(text_to_insert if rewrite else text, rewrite=rewrite)

        try:
            service = self.get_service()

            # Retrieve the current document to find its end index and content structure
            doc = service.documents().get(documentId=self.doc_id).execute()
            
            # Support both standard documents and tabbed documents
            body = doc.get("body")
            tab_id = None
            if not body and "tabs" in doc:
                for tab in doc.get("tabs", []):
                    tab_body = tab.get("documentTab", {}).get("body")
                    if tab_body:
                        body = tab_body
                        tab_id = tab.get("tabProperties", {}).get("tabId") or tab.get("tabId")
                        break
            content = (body or {}).get("content", [])

            end_index = 1
            if content:
                end_index = content[-1].get("endIndex", 1)

            requests_payload = []
            if rewrite:
                # Delete all existing content from index 1 to end_index - 1
                if end_index > 2:
                    del_range = {
                        "startIndex": 1,
                        "endIndex": end_index - 1,
                    }
                    if tab_id:
                        del_range["tabId"] = tab_id
                    requests_payload.append({
                        "deleteContentRange": {
                            "range": del_range
                        }
                    })
                # Insert replacement text at index 1
                insert_loc = {"index": 1}
                if tab_id:
                    insert_loc["tabId"] = tab_id
                requests_payload.append({
                    "insertText": {
                        "location": insert_loc,
                        "text": text_to_insert,
                    }
                })
            else:
                if self.insert_position == "beginning":
                    insert_loc = {"index": 1}
                    if tab_id:
                        insert_loc["tabId"] = tab_id
                    formatted_insert = text.strip() + "\n\n"
                    requests_payload.append({
                        "insertText": {
                            "location": insert_loc,
                            "text": formatted_insert,
                        }
                    })
                else:
                    # Insert at the end (subtract 1 for the mandatory trailing newline index)
                    insert_location = max(1, end_index - 1)
                    insert_loc = {"index": insert_location}
                    if tab_id:
                        insert_loc["tabId"] = tab_id
                    requests_payload.append({
                        "insertText": {
                            "location": insert_loc,
                            "text": text,
                        }
                    })

            result = (
                service.documents()
                .batchUpdate(documentId=self.doc_id, body={"requests": requests_payload})
                .execute()
            )

            # Keep local mirror file in exact sync with Google Doc
            if rewrite:
                self._write_local(text_to_insert, rewrite=True)
            else:
                if not self.local_fallback_file.exists() or self.local_fallback_file.stat().st_size == 0:
                    # Mirror was missing or empty: reconstruct full mirror from doc
                    existing_text = self._extract_text_from_doc(doc)
                    if self.insert_position == "beginning":
                        full_text = text.strip() + "\n\n" + existing_text.lstrip("\n")
                    else:
                        full_text = existing_text.rstrip("\n") + "\n\n" + text.lstrip("\n")
                    self._write_local(full_text.strip() + "\n", rewrite=True)
                else:
                    self._write_local(text, rewrite=False)

            logger.info(
                "Successfully %s %d characters to Google Doc %s (mirrored to %s)",
                action_name,
                len(text),
                self.doc_id,
                self.local_fallback_file,
            )
            return {
                "status": "success",
                "target": "google_doc",
                "doc_id": self.doc_id,
                "doc_url": f"https://docs.google.com/document/d/{self.doc_id}/edit",
                "result": result,
                "local_mirror": str(self.local_fallback_file),
                "mode": action_name,
            }

        except Exception as e:
            logger.error(
                "Failed to %s Google Doc %s: %s. Falling back to local file.",
                action_name,
                self.doc_id,
                e,
            )
            local_res = self._write_local(text_to_insert if rewrite else text, rewrite=rewrite)
            local_res["google_error"] = str(e)
            return local_res

    def _write_local(self, text: str, rewrite: bool = False) -> Dict[str, Any]:
        """Write formatted text to local fallback/mirror file."""
        self.local_fallback_file.parent.mkdir(parents=True, exist_ok=True)
        if rewrite or not self.local_fallback_file.exists():
            with open(self.local_fallback_file, "w", encoding="utf-8") as f:
                f.write(text)
        elif self.insert_position == "beginning":
            with open(self.local_fallback_file, "r", encoding="utf-8") as f:
                existing_text = f.read()
            if existing_text.strip():
                updated = text.strip() + "\n\n" + existing_text.lstrip("\n")
            else:
                updated = text.strip() + "\n"
            with open(self.local_fallback_file, "w", encoding="utf-8") as f:
                f.write(updated)
        else:
            with open(self.local_fallback_file, "a", encoding="utf-8") as f:
                f.write(text)

        action_verb = "Rewrote" if rewrite else ("Prepended" if self.insert_position == "beginning" else "Appended")
        target_desc = "local mirror" if self.is_google_configured else "local fallback file"
        logger.info(
            "%s %d characters to %s: %s",
            action_verb,
            len(text),
            target_desc,
            self.local_fallback_file,
        )
        return {
            "status": "success",
            "target": "local_file",
            "file_path": str(self.local_fallback_file),
            "mode": "rewrite" if rewrite else ("prepend" if self.insert_position == "beginning" else "append"),
        }

    def _append_local(self, text: str) -> Dict[str, Any]:
        """Append formatted text to local fallback file (backwards compatibility)."""
        return self._write_local(text, rewrite=False)

    def _extract_text_from_doc(self, doc: dict) -> str:
        """Extract plain text runs from a Google Docs document structure."""
        body = doc.get("body")
        if not body and "tabs" in doc:
            for tab in doc.get("tabs", []):
                tab_body = tab.get("documentTab", {}).get("body")
                if tab_body:
                    body = tab_body
                    break
        content = (body or {}).get("content", [])
        return self._extract_text_from_structural_elements(content)

    def _extract_text_from_structural_elements(self, elements: List[dict]) -> str:
        """Recursively extract plain text from paragraphs, tables, and nested structures."""
        text_pieces = []
        for el in elements:
            para = el.get("paragraph")
            if para:
                for elem in para.get("elements", []):
                    tr = elem.get("textRun")
                    if tr and "content" in tr:
                        text_pieces.append(tr["content"])
            table = el.get("table")
            if table:
                for row in table.get("tableRows", []):
                    for cell in row.get("tableCells", []):
                        cell_content = cell.get("content", [])
                        text_pieces.append(self._extract_text_from_structural_elements(cell_content))
        return "".join(text_pieces)



    def push_local_mirror_to_google(self) -> Dict[str, Any]:
        """Upload current local mirror file content to the configured Google Doc (rewrite)."""
        if not self.doc_id or not self.is_google_configured:
            raise RuntimeError("Google Docs is not configured or missing credentials.")
        if not self.local_fallback_file.exists():
            raise FileNotFoundError(f"Local mirror file not found: {self.local_fallback_file}")
        text = self.local_fallback_file.read_text(encoding="utf-8")
        if not text.strip():
            raise ValueError(f"Local mirror file is empty: {self.local_fallback_file}")
        return self.append_text(text, rewrite=True)

    def clean_local_file(self) -> int:
        """
        Deduplicate any repeated batches or items in local fallback/mirror file.
        Returns the number of duplicate characters removed.
        """
        if not self.local_fallback_file.exists():
            return 0
        from librus_collector.formatter import clean_markdown_batches
        original_text = self.local_fallback_file.read_text(encoding="utf-8")
        cleaned_text = clean_markdown_batches(original_text)
        saved = max(0, len(original_text) - len(cleaned_text))
        if cleaned_text != original_text:
            self.local_fallback_file.write_text(cleaned_text, encoding="utf-8")
            logger.info("Cleaned %d duplicate characters from %s", saved, self.local_fallback_file)
        return saved
