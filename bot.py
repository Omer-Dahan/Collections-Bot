"""
Main entry point for the Collections Bot.
Configures logging, restores sessions, and sets up the Telegram application.
"""
import asyncio
import logging
from logging.handlers import RotatingFileHandler
import sys
import os
import time
import psutil
import subprocess

from telegram import Update, BotCommand
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    AIORateLimiter,
    ContextTypes,
)
from telegram.request import HTTPXRequest

import db
from config import BOT_TOKEN
from admin_panel import admin_panel, handle_admin_callback, scan_file_ids
from utils import error_handler, UserActionFilter, logger, touch_user_activity
from constants import (
    active_collections, active_collection_timestamps,
    active_shared_collections, active_shared_collection_timestamps,
)
from handlers import (
    start, new_collection, list_collections, manage_collections, browse,
    remove, id_file, access_shared,
    handle_select_collection_callback, handle_browse_page_callback,
    handle_scroll_view_callback, handle_page_info_callback,
    handle_back_to_info_callback, handle_search_collection_callback,
    handle_browse_group_or_select_all_callback, handle_page_file_send_choice_callback,
    handle_batch_status_callback, handle_collection_send_all_callback,
    handle_stop_collect_callback,
    handle_main_menu_button, handle_back_to_main_callback,
    handle_manage_collection_callback, handle_share_collection_callback,
    handle_share_stats_callback, handle_regenerate_share_callback,
    handle_revoke_share_callback, handle_export_collection_callback,
    handle_delete_collection_callback, handle_back_to_manage_callback,
    handle_exit_shared_collection_callback, handle_cancel_share_access_callback,
    handle_exit_delete_mode_callback, handle_import_col_callback,
    handle_select_item_delete_col_callback,
    handle_set_share_expiration_callback, handle_save_share_expiration_callback,
    handle_custom_share_expiration_callback, handle_save_custom_share_expiration_callback,
    handle_scan_duplicates_callback, handle_confirm_delete_dupes_callback,
    handle_dupes_page_callback,
    handle_random_video_scroll_callback,
    handle_collection_info_callback,
    handle_message
)

# הגדרת הקובץ
_LOCKFILE = os.path.join(os.path.dirname(__file__), ".bot.pid")

def _kill_old_instance():
    """Kill any previous bot instance that's still running."""
    if not os.path.exists(_LOCKFILE):
        return
    try:
        old_pid = int(open(_LOCKFILE).read().strip())
        if old_pid == os.getpid():
            return
        old_proc = psutil.Process(old_pid)
        # Verify it's actually our bot (not some other process reusing the PID)
        cmdline = " ".join(old_proc.cmdline())
        if "bot.py" in cmdline:
            logging.warning("⚠️ Killing old bot instance (PID %d) to prevent database lock", old_pid)
            # Kill children first (sub-processes)
            for child in old_proc.children(recursive=True):
                child.kill()
            old_proc.kill()
            old_proc.wait(timeout=5)
            logging.info("✅ Old instance killed successfully")
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        pass  # Process already gone or can't access
    except Exception as e:
        logging.warning("Could not kill old instance: %s", e)

def setup_logging():
    """Configure bot logging with file and console handlers."""
    # File handler - only user actions and errors
    file_handler = RotatingFileHandler(
        "bot.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(logging.INFO)
    format_str = "%(asctime)s [%(levelname)s]: %(message)s"
    file_handler.setFormatter(logging.Formatter(format_str))
    file_handler.addFilter(UserActionFilter())

    # Console handler - everything
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )

    # Define root logger
    logging.basicConfig(
        level=logging.INFO,
        handlers=[file_handler, console_handler]
    )

async def check_expired_shares_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Background job that runs every minute to clean up expired shares.
    """
    try:
        expired_shares = db.get_expired_shares()
        if not expired_shares:
            return

        logger.info("Found %d expired shares to process", len(expired_shares))

        for share_code, _, _, _ in expired_shares:
            try:
                db.deactivate_share_by_code(share_code)
                deleted_count = 0
                processed_count = 0
                while True:
                    messages = db.get_messages_for_share(share_code, limit=100)
                    if not messages:
                        break
                    for _, chat_id, message_id in messages:
                        processed_count += 1
                        try:
                            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
                            deleted_count += 1
                            await asyncio.sleep(0.05)
                        except Exception as e: # pylint: disable=broad-exception-caught
                            error_str = str(e).lower()
                            if "400" in str(e) or "not found" in error_str or "deleted" in error_str:
                                deleted_count += 1
                            else:
                                logger.warning("Failed to delete message %s: %s", message_id, e)
                        finally:
                            db.delete_single_message_record(message_id, chat_id)

                logger.info(
                    "Expired share %s...: deleted %d/%d messages",
                    share_code[:8], deleted_count, processed_count
                )
            except Exception as e: # pylint: disable=broad-exception-caught
                logger.error("Error processing expired share %s: %s", share_code, e)
    except Exception as e: # pylint: disable=broad-exception-caught
        logger.error("Error in check_expired_shares_job: %s", e)

async def track_user_messages(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    """Global handler to track ALL user messages during shared sessions."""
    user = update.effective_user
    message = update.effective_message
    if user:
        touch_user_activity(_context)
        if user.id in active_shared_collection_timestamps:
            active_shared_collection_timestamps[user.id] = time.time()
    if user and message:
        share_code = active_shared_collections.get(user.id)
        if share_code:
            try:
                db.log_shared_message(share_code, user.id, message.chat_id, message.message_id)
            except Exception: # pylint: disable=broad-exception-caught
                pass


async def track_callback_activity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refresh user-data TTL for callback-only interactions as well."""
    if update.effective_user:
        touch_user_activity(context)
        user_id = update.effective_user.id
        if user_id in active_shared_collection_timestamps:
            active_shared_collection_timestamps[user_id] = time.time()


async def cleanup_memory_job(context: ContextTypes.DEFAULT_TYPE):
    """Bound inactive in-process state and prune old database bookkeeping."""
    now = time.time()
    session_ttl = 60 * 60
    user_data_ttl = 60 * 60

    for user_id, last_seen in list(active_collection_timestamps.items()):
        if now - last_seen > session_ttl:
            active_collection_timestamps.pop(user_id, None)
            active_collections.pop(user_id, None)

    for user_id, last_seen in list(active_shared_collection_timestamps.items()):
        if now - last_seen > session_ttl:
            active_shared_collection_timestamps.pop(user_id, None)
            active_shared_collections.pop(user_id, None)
            db.set_user_active_share(user_id, None)

    for user_id, data in list(context.application.user_data.items()):
        last_seen = data.get("_last_activity_at", 0)
        if now - last_seen > user_data_ttl:
            context.application.drop_user_data(user_id)


async def cleanup_share_data_job(context: ContextTypes.DEFAULT_TYPE):
    """Keep historical share tables from growing indefinitely on disk/RAM reads."""
    messages, logs = db.purge_old_share_data()
    if messages or logs:
        logger.info("Purged %d stale shared-message rows and %d old access logs", messages, logs)

async def post_init(application):
    """Set up bot commands menu after bot is initialized."""
    commands = [
        BotCommand("start", "התחל - תפריט ראשי"),
        BotCommand("access", "גישה לאוסף משותף"),
        BotCommand("browse", "דפדוף באוספים"),
        BotCommand("newcollection", "יצירת אוסף חדש"),
    ]
    await application.bot.set_my_commands(commands)
    logger.info("Bot commands menu set")

def _register_handlers(app):
    """Register all bot handlers to the application."""
    app.add_error_handler(error_handler)

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("newcollection", new_collection))
    app.add_handler(CommandHandler("collections", list_collections))
    app.add_handler(CommandHandler("manage", manage_collections))
    app.add_handler(CommandHandler("browse", browse))
    app.add_handler(CommandHandler("remove", remove))
    app.add_handler(CommandHandler("id_file", id_file))
    app.add_handler(CommandHandler("access", access_shared))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("adminpanel", admin_panel))
    app.add_handler(CommandHandler("scanfiles", scan_file_ids))

    # Callbacks
    app.add_handler(CallbackQueryHandler(handle_main_menu_button, pattern="^main_menu:"))
    app.add_handler(CallbackQueryHandler(handle_back_to_main_callback, pattern="^back_to_main$"))
    app.add_handler(CallbackQueryHandler(
        handle_select_collection_callback, pattern="^select_collection:"
    ))
    app.add_handler(CallbackQueryHandler(
        handle_import_col_callback, pattern="^import_collection_mode$"
    ))
    app.add_handler(CallbackQueryHandler(handle_stop_collect_callback, pattern="^stop_collect$"))
    app.add_handler(CallbackQueryHandler(handle_browse_page_callback, pattern="^browse_page:"))
    app.add_handler(CallbackQueryHandler(handle_search_collection_callback, pattern="^search_collection:"))
    app.add_handler(CallbackQueryHandler(handle_scroll_view_callback, pattern="^scroll_view:"))
    app.add_handler(CallbackQueryHandler(handle_page_info_callback, pattern="^page_info:"))
    app.add_handler(CallbackQueryHandler(handle_back_to_info_callback, pattern="^back_to_info:"))
    pattern = "^(browse_group|browse_page_select_all):"
    app.add_handler(CallbackQueryHandler(
        handle_browse_group_or_select_all_callback, pattern=pattern
    ))
    app.add_handler(CallbackQueryHandler(
        handle_page_file_send_choice_callback, pattern="^page_files_"
    ))
    app.add_handler(CallbackQueryHandler(
        handle_collection_send_all_callback, pattern="^collection_send_all:"
    ))
    app.add_handler(CallbackQueryHandler(handle_batch_status_callback, pattern="^batch_status:"))
    app.add_handler(CallbackQueryHandler(
        handle_manage_collection_callback, pattern="^manage_collection:"
    ))
    app.add_handler(CallbackQueryHandler(
        handle_share_collection_callback, pattern="^share_collection:"
    ))
    app.add_handler(CallbackQueryHandler(handle_share_stats_callback, pattern="^share_stats:"))
    app.add_handler(CallbackQueryHandler(
        handle_regenerate_share_callback, pattern="^regenerate_share:"
    ))
    app.add_handler(CallbackQueryHandler(handle_revoke_share_callback, pattern="^revoke_share:"))
    app.add_handler(CallbackQueryHandler(
        handle_export_collection_callback, pattern="^export_collection:"
    ))
    app.add_handler(CallbackQueryHandler(
        handle_delete_collection_callback, pattern="^delete_collection:"
    ))
    app.add_handler(CallbackQueryHandler(
        handle_back_to_manage_callback, pattern="^back_to_manage$"
    ))
    app.add_handler(CallbackQueryHandler(
        handle_select_item_delete_col_callback, pattern="^select_item_del_col:"
    ))
    app.add_handler(CallbackQueryHandler(
        handle_exit_delete_mode_callback, pattern="^exit_delete_mode$"
    ))
    app.add_handler(CallbackQueryHandler(
        handle_exit_shared_collection_callback, pattern="^exit_shared_collection$"
    ))
    app.add_handler(CallbackQueryHandler(
        handle_cancel_share_access_callback, pattern="^cancel_share_access$"
    ))
    admin_pattern = "^(admin_|user_stats|system_stats|broadcast|backup_db)"
    app.add_handler(CallbackQueryHandler(handle_admin_callback, pattern=admin_pattern))
    app.add_handler(CallbackQueryHandler(
        handle_set_share_expiration_callback, pattern="^set_share_expiration:"
    ))
    app.add_handler(CallbackQueryHandler(
        handle_save_share_expiration_callback, pattern="^save_share_exp:"
    ))
    app.add_handler(CallbackQueryHandler(
        handle_custom_share_expiration_callback, pattern="^custom_share_exp:"
    ))
    app.add_handler(CallbackQueryHandler(
        handle_save_custom_share_expiration_callback, pattern="^save_share_exp_custom:"
    ))
    app.add_handler(CallbackQueryHandler(
        handle_scan_duplicates_callback, pattern="^scan_duplicates:"
    ))
    app.add_handler(CallbackQueryHandler(
        handle_confirm_delete_dupes_callback, pattern="^confirm_delete_dupes:"
    ))
    app.add_handler(CallbackQueryHandler(
        handle_dupes_page_callback, pattern="^dupes_page:"
    ))
    app.add_handler(CallbackQueryHandler(
        handle_collection_info_callback, pattern="^collection_info:"
    ))
    app.add_handler(CallbackQueryHandler(
        handle_random_video_scroll_callback, pattern="^random_video:"
    ))

    # Messages
    app.add_handler(MessageHandler(filters.ALL, track_user_messages), group=-1)
    app.add_handler(CallbackQueryHandler(track_callback_activity), group=-2)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))

def main():
    """Start the bot application."""
    # Kill previous instance if exists
    _kill_old_instance()
    
    # Write our current PID to the lock file
    with open(_LOCKFILE, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))

    setup_logging()
    db.init_db()

    # Shared-access sessions are intentionally not restored.  Restoring every
    # historic session turns this dict into unbounded process memory; users can
    # re-enter a valid share code after a restart.

    logger.info("Bot starting...")

    req = HTTPXRequest(connection_pool_size=8, read_timeout=60.0, write_timeout=60.0)
    # max_retries=3: retry up to 3 times on 429 flood control instead of crashing immediately
    # A bounded incoming-update queue prevents a network burst from becoming
    # an unbounded in-process backlog on a small server. Polling pauses until
    # the bot catches up, leaving excess updates safely with Telegram.
    app = ApplicationBuilder().token(BOT_TOKEN).rate_limiter(AIORateLimiter(max_retries=3)) \
        .request(req).update_queue(asyncio.Queue(maxsize=100)).post_init(post_init).build()

    _register_handlers(app)

    if app.job_queue:
        app.job_queue.run_repeating(check_expired_shares_job, interval=60, first=10)
        app.job_queue.run_repeating(cleanup_memory_job, interval=600, first=600)
        app.job_queue.run_repeating(cleanup_share_data_job, interval=86400, first=3600)
        logger.info("Share expiration cleanup job scheduled")

    logger.info("Polling...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
