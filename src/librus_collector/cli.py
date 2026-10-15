"""Command-line interface for Librus Collector."""

import argparse
import json
import logging
from pathlib import Path
import sys

from librus_collector.config import get_config
from librus_collector.google_docs import GoogleDocsSync
from librus_collector.item_store import ItemStore
from librus_collector.librus_client import LibrusClient
from librus_collector.scheduler import DaemonScheduler, generate_crontab_line, generate_launchd_plist
from librus_collector.security import mask_secret
from librus_collector.state import SyncStateManager
from librus_collector.sync_engine import LibrusSyncEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("librus_collector")


def cmd_run(args):
    """Run a single synchronization cycle right now."""
    config = get_config()
    try:
        config.validate_librus_credentials()
    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)

    engine = LibrusSyncEngine(config=config)
    kwargs = {"force": getattr(args, "force", False)}
    if getattr(args, "rebuild", False):
        kwargs["rebuild"] = True
    result = engine.run_sync(**kwargs)

    if result.success:
        print(f"\n✅ Synchronization completed successfully!")
        print(f"• Items found: {result.items_found}")
        print(f"• New items synced: {result.items_synced}")
        print(f"• Delivery target: {result.target_type}")
        if result.doc_id:
            print(f"• Google Doc ID: {result.doc_id}")
            print(f"• Google Doc URL: https://docs.google.com/document/d/{result.doc_id}/edit")
            print(f"• Local mirror: {config.local_export_path}")
        elif result.target_type == "local_file":
            print(f"• Local file path: {config.local_export_path}")
    else:
        print(f"\n❌ Synchronization failed: {result.error_message}")
        sys.exit(1)


def cmd_clean(args):
    """Clean duplicate sync batches from local updates file and deduplicate state history."""
    config = get_config()
    docs = GoogleDocsSync(local_fallback_file=config.local_export_path)
    state = SyncStateManager(config.state_file_path)

    saved_chars = docs.clean_local_file()
    removed_history = state.clean_history()

    if saved_chars > 0 or removed_history > 0:
        print(f"\n✅ Cleanup completed!")
        if saved_chars > 0:
            print(f"• Cleaned duplicate batches from: {config.local_export_path} ({saved_chars} redundant characters removed)")
        if removed_history > 0:
            print(f"• Cleaned {removed_history} duplicate history entries from: {config.state_file_path}")
    else:
        print(f"\n✅ Everything is already clean (no duplicate batches or state history found).")
        print(f"• Local updates file: {config.local_export_path}")
        print(f"• State file: {config.state_file_path}")


def cmd_assemble(args):
    """Assemble all stored individual message markdown files into a single compendium document."""
    config = get_config()
    engine = LibrusSyncEngine(config=config)
    group_by_type = not getattr(args, "flat", False)

    print(f"Assembling compendium from structured storage ({config.items_dir})...")
    res = engine.assemble(group_by_type=group_by_type)

    total = res.get("total_items_assembled", 0)
    target = res.get("target", "unknown")
    print(f"\n✅ Compendium assembled successfully!")
    print(f"• Items included: {total}")
    print(f"• Structure: {'Categorized sections' if group_by_type else 'Flat chronological'}")
    print(f"• Delivery target: {target}")
    if res.get("doc_id"):
        print(f"• Google Doc: https://docs.google.com/document/d/{res['doc_id']}/edit")
    if res.get("local_mirror"):
        print(f"• Local file: {res['local_mirror']}")
    elif res.get("file_path"):
        print(f"• Local file: {res['file_path']}")


def cmd_import(args):
    """Import existing messages from markdown export (librus_updates.md) into individual item files."""
    config = get_config()
    source_file = getattr(args, "source", None) or config.local_export_path
    state = SyncStateManager(config.state_file_path)
    store = ItemStore(config.items_dir)

    print(f"Importing historical items from {source_file} into {config.items_dir}...")
    imported = store.import_from_markdown(source_file, state_history=state.history)

    print(f"\n✅ Import completed!")
    print(f"• Items imported: {len(imported)}")
    print(f"• Stored in: {config.items_dir}")
    print(f"• View in Finder: open {config.items_dir}")


def cmd_push(args):
    """Push local mirror file (librus_updates.md) to the configured Google Doc."""
    config = get_config()
    docs = GoogleDocsSync(
        doc_id=config.google_doc_id,
        service_account_file=config.google_service_account_file,
        client_secrets_file=config.google_client_secrets_file,
        token_file=config.google_token_file,
        local_fallback_file=config.local_export_path,
    )
    if not docs.is_google_configured:
        print("❌ Google Docs is not configured. Set GOOGLE_DOC_ID and credentials first.")
        sys.exit(1)
    try:
        res = docs.push_local_mirror_to_google()
        print(f"\n✅ Successfully pushed local mirror to Google Doc!")
        print(f"• Document ID: {docs.doc_id}")
        print(f"• Document URL: {res.get('doc_url')}")
        print(f"• Local mirror source: {config.local_export_path}")
    except Exception as e:
        print(f"\n❌ Failed to push to Google Doc: {e}")
        sys.exit(1)


def cmd_daemon(args):
    """Run continuous hourly synchronization daemon."""
    config = get_config()
    try:
        config.validate_librus_credentials()
    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)

    interval = args.interval or config.sync_interval_seconds
    engine = LibrusSyncEngine(config=config)
    scheduler = DaemonScheduler(engine=engine, interval_seconds=interval)
    scheduler.start()


def cmd_status(args):
    """Show configuration, state summary, and setup guidance."""
    config = get_config()
    state = SyncStateManager(config.state_file_path)
    docs = GoogleDocsSync(
        doc_id=config.google_doc_id,
        service_account_file=config.google_service_account_file,
        client_secrets_file=config.google_client_secrets_file,
        token_file=config.google_token_file,
        local_fallback_file=config.local_export_path,
    )

    print("═══════════════════════════════════════════════════════")
    print(" LIBRUS COLLECTOR STATUS")
    print("═══════════════════════════════════════════════════════")
    print(f"• Librus Login: {config.librus_login or '(not configured)'}")
    print(f"• Librus Password: {mask_secret(config.librus_password) if config.librus_password else '(not configured)'}")
    print(f"• Sync Interval: {config.sync_interval_seconds}s ({config.sync_interval_seconds / 3600:0.1f}h)")
    print(f"• Google Doc ID: {config.google_doc_id or '(not configured - using local fallback)'}")
    print(f"• Google Service Account: {config.google_service_account_file or '(not configured)'}")
    print(f"• Google Docs Configured: {'Yes' if docs.is_google_configured else 'No (using local file ' + config.local_export_path + ')'}")
    print(f"• Local Mirror Path: {config.local_export_path}")
    print("───────────────────────────────────────────────────────")
    summary = state.get_summary()
    store = ItemStore(config.items_dir)
    print(f"• State File: {summary['state_file']}")
    print(f"• Total Synced Items: {summary['total_synced_items']}")
    print(f"• Last Sync Run: {summary['last_sync_timestamp'] or 'Never'}")
    print("───────────────────────────────────────────────────────")
    print(f"• Item Store Directory: {config.items_dir}")
    print(f"• Individual Message Files: {store.count()}")
    print("═══════════════════════════════════════════════════════")


def cmd_test_login(args):
    """Verify Librus credentials over encrypted HTTPS."""
    config = get_config()
    try:
        config.validate_librus_credentials()
    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)

    print(f"Testing encrypted HTTPS connection to Librus for user '{config.librus_login}'...")
    client = LibrusClient(login=config.librus_login, password=config.librus_password)
    try:
        client.login_sync()
        print("✅ Authentication successful! Session established.")
    except Exception as e:
        print(f"❌ Authentication failed: {e}")
        sys.exit(1)


def cmd_init_doc(args):
    """Create a new Google Doc and display its ID."""
    config = get_config()
    docs = GoogleDocsSync(
        service_account_file=config.google_service_account_file,
        client_secrets_file=config.google_client_secrets_file,
        token_file=config.google_token_file,
    )
    title = args.title or "Librus School Updates (NotebookLM)"
    try:
        doc_id = docs.create_document(title=title)
        print(f"\n✅ Created Google Doc '{title}'!")
        print(f"• Document ID: {doc_id}")
        print(f"• Document URL: https://docs.google.com/document/d/{doc_id}/edit")
        print(f"\nTo use this doc, add to your .env:")
        print(f"GOOGLE_DOC_ID={doc_id}\n")
    except Exception as e:
        print(f"❌ Failed to create Google Doc: {e}")
        sys.exit(1)


def cmd_install_cron(args):
    """Display crontab entry for hourly sync."""
    line = generate_crontab_line(Path.cwd())
    print("\nTo run Librus Collector once every hour via cron, run:")
    print("  crontab -e")
    print("and append the following line:")
    print(f"\n{line}\n")


def cmd_install_launchd(args):
    """Generate macOS launchd plist for hourly background service."""
    plist_content = generate_launchd_plist(Path.cwd())
    plist_path = Path.home() / "Library/LaunchAgents/com.librus.collector.plist"
    
    if args.write:
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        with open(plist_path, "w", encoding="utf-8") as f:
            f.write(plist_content)
        print(f"✅ Written plist to: {plist_path}")
        print("To load and start the hourly service, run:")
        print(f"  launchctl load {plist_path}")
        print("To stop and unload:")
        print(f"  launchctl unload {plist_path}")
    else:
        print(f"macOS launchd plist content (pass --write to save to {plist_path}):\n")
        print(plist_content)


def main():
    """Main CLI entrypoint."""
    parser = argparse.ArgumentParser(
        prog="librus-sync",
        description="Hourly collector for Librus messages and announcements synced to a Google Doc for NotebookLM.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # run
    p_run = subparsers.add_parser("run", help="Run a single sync right now")
    p_run.add_argument(
        "--force",
        action="store_true",
        help="Force sync all items, bypassing state deduplication",
    )
    p_run.add_argument(
        "--rebuild",
        action="store_true",
        help="Rebuild and reassemble the entire document compendium from stored items after syncing",
    )
    p_run.set_defaults(func=cmd_run)

    # assemble
    p_assemble = subparsers.add_parser(
        "assemble",
        help="Assemble all stored individual message markdown files into a single compendium",
    )
    p_assemble.add_argument(
        "--flat",
        action="store_true",
        help="Sort purely chronologically instead of grouping by category sections",
    )
    p_assemble.set_defaults(func=cmd_assemble)

    # import
    p_import = subparsers.add_parser(
        "import",
        help="Import historical items from markdown export (librus_updates.md) into individual item files",
    )
    p_import.add_argument(
        "--source",
        type=str,
        default=None,
        help="Path to markdown file to import (default: configured LOCAL_EXPORT_PATH)",
    )
    p_import.set_defaults(func=cmd_import)

    # daemon
    p_daemon = subparsers.add_parser("daemon", help="Run hourly daemon loop")
    p_daemon.add_argument(
        "--interval",
        type=int,
        default=None,
        help="Sync interval in seconds (default: 3600 = 1 hour)",
    )
    p_daemon.set_defaults(func=cmd_daemon)

    # status
    p_status = subparsers.add_parser("status", help="Show collector status and state summary")
    p_status.set_defaults(func=cmd_status)

    # test-login
    p_test = subparsers.add_parser("test-login", help="Verify Librus credentials securely")
    p_test.set_defaults(func=cmd_test_login)

    # init-doc
    p_doc = subparsers.add_parser("init-doc", help="Create a new Google Doc")
    p_doc.add_argument("--title", type=str, default="Librus School Updates (NotebookLM)", help="Title for Google Doc")
    p_doc.set_defaults(func=cmd_init_doc)

    # install-cron
    p_cron = subparsers.add_parser("install-cron", help="Show hourly crontab configuration")
    p_cron.set_defaults(func=cmd_install_cron)

    # install-launchd
    p_launchd = subparsers.add_parser("install-launchd", help="Generate macOS launchd service")
    p_launchd.add_argument("--write", action="store_true", help="Write plist file to ~/Library/LaunchAgents")
    p_launchd.set_defaults(func=cmd_install_launchd)

    # clean
    p_clean = subparsers.add_parser("clean", help="Clean duplicate batches from the local updates file")
    p_clean.set_defaults(func=cmd_clean)

    # push
    p_push = subparsers.add_parser("push", help="Push local mirror file content to the configured Google Doc")
    p_push.set_defaults(func=cmd_push)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
