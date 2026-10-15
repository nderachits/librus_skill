# Librus Collector for NotebookLM

A dedicated, lightweight Python tool (built with `uv` and `git`) that synchronizes **Librus Synergia** messages, school announcements, homework, and exam calendars once per hour into a single **Google Doc** designed for semantic search and Q&A inside **Google NotebookLM**.

---

## 💡 Does this make sense?
**Yes, absolutely.** Connecting a single rolling Google Doc to NotebookLM is one of the most effective ways to interact with school announcements and teacher messages:

1. **Overcomes NotebookLM Source Limits:** NotebookLM has a limit of 50 sources per notebook. If every school message were a separate document or file, you would quickly hit that limit. Appending chronologically to a single structured Google Doc creates an append-only school chronicle.
2. **Contextual & Semantic Search:** You can query NotebookLM in natural Polish or English:
   - *"Co jest zadane z matematyki na piątek?"* (*"What is assigned in math for Friday?"*)
   - *"Kiedy odbędzie się wywiadówka i jakie tematy będą poruszane?"* (*"When is the parent-teacher conference?"*)
   - *"Jakie wiadomości przesłała wychowawczyni w tym miesiącu?"* (*"What messages did the homeroom teacher send this month?"*)
3. **Automatic Deduplication:** The collector keeps track of previously synced message IDs, announcement hashes, and event keys in a local state file (`.librus_state.json`), so hourly runs only append genuine updates without duplicate noise.

---

## 🔒 Security & Privacy Guarantees
- **No Third-Party Librus Libraries:** The Librus connector is implemented in-house (`src/librus_collector/librus_client.py`), without unmaintained, insecure, or untrusted external scraping packages.
- **Strict Transport Encryption:** Credentials and session cookies are strictly transmitted over **TLS/HTTPS** directly to verified Librus domains (`api.librus.pl`, `synergia.librus.pl`, `wiadomosci.librus.pl`).
- **Target Verification:** The client enforces an assertion check prior to every HTTP call with credentials; attempts to send data to non-HTTPS endpoints or third-party domains will trigger a hard `SecurityError`.
- **Credential Protection:** Passwords stored in `.env` are excluded by `.gitignore` and never committed to git. Loggers and exception handlers automatically scrub and redact passwords (`[REDACTED_CREDENTIAL]`).

---

## 🛠️ Architecture

```
                 ┌────────────────────────────────┐
                 │        Librus Synergia         │
                 │  - Announcements (Ogłoszenia)  │
                 │  - Messages (Wiadomości)       │
                 │  - Calendar & Homework         │
                 └──────────────┬─────────────────┘
                                │ HTTPS (TLS encrypted)
                                ▼
                 ┌────────────────────────────────┐
                 │       Librus Collector         │
                 │  - LibrusClient (native)       │
                 │  - State Deduplication         │
                 │  - NotebookLM Formatter        │
                 └──────────────┬─────────────────┘
                                │
          ┌─────────────────────┴─────────────────────┐
          ▼                                           ▼
┌───────────────────────────┐               ┌───────────────────┐
│        Google Doc         │ (optional)    │ Local Markdown    │
│  Single rolling doc       │               │ librus_updates.md │
│             │             │               └───────────────────┘
│             ▼             │
│      Google NotebookLM    │
│  - Semantic Q&A & Search  │
└───────────────────────────┘
```

---

## 🚀 Installation & Setup

### Prerequisites
- Python 3.11+
- `uv` (modern Python package manager)
- Git

### 1. Clone & Install Dependencies
```bash
git clone <repo-url> librus_skill
cd librus_skill
uv sync
```

### 2. Configure Environment
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Edit `.env` with your settings:
```ini
# Librus Credentials
LIBRUS_LOGIN=your_username_or_email
LIBRUS_PASSWORD=your_password

# Target Google Doc ID (create one or use 'uv run librus-sync init-doc')
GOOGLE_DOC_ID=1abc...xyz

# Google Service Account JSON (Recommended for background sync)
GOOGLE_SERVICE_ACCOUNT_FILE=service_account.json
```

> **Note on Google Authentication:**
> 1. In Google Cloud Console, enable **Google Docs API** and create a **Service Account**.
> 2. Download the JSON key file as `service_account.json`.
> 3. Open your Google Doc in your browser and click **Share** -> add the Service Account's email address with **Editor** permission.
> 4. Alternatively, if no Google credentials are provided, the collector automatically falls back to appending entries to `librus_updates.md` locally.

---

## 📖 Usage

### Run on demand:
```bash
uv run librus-sync run
```

### Run continuous daemon (every hour):
```bash
uv run librus-sync daemon --interval 3600
```

### Check status and deduplication statistics:
```bash
uv run librus-sync status
```

### Test Librus connection securely:
```bash
uv run librus-sync test-login
```

### Automatically create a new Google Doc:
```bash
uv run librus-sync init-doc --title "Librus School Updates (NotebookLM)"
```

### Run once per hour via crontab:
```bash
uv run librus-sync install-cron
# Add the output line to: crontab -e
```

### Run once per hour on macOS via `launchd`:
```bash
uv run librus-sync install-launchd --write
launchctl load ~/Library/LaunchAgents/com.librus.collector.plist
```

---

## 🧪 Testing

Run unit and integration test suite:
```bash
uv run pytest -v
```
