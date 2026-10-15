---
name: librus-school-sync
description: Syncs Librus Synergia messages, announcements, exams, and homework to a single Google Doc formatted for NotebookLM search and Q&A.
---

# Librus School Sync Skill

This tool connects to **Librus Synergia** once per hour, checks for new messages, announcements, upcoming exams/events, and homework, deduplicates them against a local state file, and appends them to a single **Google Doc** connected to **NotebookLM**.

## Why this Architecture makes sense for NotebookLM
- **Single Rolling Source:** NotebookLM supports up to 50 sources per notebook. Appending all notifications chronologically to a single Google Doc avoids clutter and never exhausts the source limit.
- **Deduplication:** State tracking (`.librus_state.json`) ensures only newly posted items are appended. No repetitive clutter in your NotebookLM queries.
- **Structured Headings & Metadata:** Entries have clear delimiters, dates, authors, categories, and full text, allowing NotebookLM to answer questions like:
  - *"What homework was given this week for biology?"*
  - *"When is the next parent-teacher conference?"*
  - *"Did the homeroom teacher send any messages about the upcoming class trip?"*
- **Security:** Librus credentials stored locally in `.env` are strictly transmitted only over HTTPS directly to authorized Librus endpoints (`api.librus.pl`, `synergia.librus.pl`).

## Quick Start with `uv`

### 1. Configuration
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Fill in your credentials:
```ini
LIBRUS_LOGIN=your_username
LIBRUS_PASSWORD=your_password
GOOGLE_DOC_ID=your_google_doc_id
GOOGLE_SERVICE_ACCOUNT_FILE=service_account.json
```

### 2. Available Commands

Run a single sync immediately (saves items to `data/items/` and updates documents):
```bash
uv run librus-sync run
```

Reassemble the entire unified compendium from all stored items:
```bash
uv run librus-sync assemble
# or flat chronological ordering without category grouping:
uv run librus-sync assemble --flat
```

Import existing historical messages from `librus_updates.md` into individual Markdown files:
```bash
uv run librus-sync import
```

Run as an hourly background daemon (every 3600 seconds):
```bash
uv run librus-sync daemon --interval 3600
```

Inspect sync status, state, and stored message count:
```bash
uv run librus-sync status
```

Open individual message files in macOS Finder (sorted chronologically):
```bash
open data/items
```

Create a new Google Doc automatically:
```bash
uv run librus-sync init-doc --title "Librus School Updates (NotebookLM)"
```

Generate hourly crontab entry:
```bash
uv run librus-sync install-cron
```

Install macOS launchd background service:
```bash
uv run librus-sync install-launchd --write
launchctl load ~/Library/LaunchAgents/com.librus.collector.plist
```

Verify encrypted connection to Librus:
```bash
uv run librus-sync test-login
```

Verify Telegram notifications:
```bash
uv run librus-sync test-telegram
# Or send a preview of the latest school item:
uv run librus-sync test-telegram --sample
```

## How to Connect to NotebookLM
1. Open [NotebookLM](https://notebooklm.google.com/).
2. Create a new notebook (e.g. *"School 2026 - Librus"*).
3. Click **Add Source** -> **Google Docs**.
4. Select the Google Doc updated by this tool.
5. Whenever you ask questions in NotebookLM, it will read the latest school communications!
