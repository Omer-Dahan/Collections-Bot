"""
Main entry point for the Collections Bot.
Configures logging, restores sessions, and sets up the Telegram application.
"""
import asyncio
import logging
import sys

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
from admin_panel import admin_panel, handle_admin_callback
from utils import error_handler, UserActionFilter, logger
from constants import active_shared_collections
from handlers import (
    start, new_collection, list_collections, manage_collections, browse,
    remove, id_file, access_shared,
    handle_select_collection_callback, handle_browse_page_callback,
    handle_scroll_view_callback, handle_page_info_callback,
    handle_back_to_info_callback,
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
    handle_message
)

def setup_logging():
    """Configure bot logging with file and console handlers."""
    # File handler - only user actions and errors
    file_handler = logging.FileHandler("bot.log", encoding="utf-8")
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
                messages = db.get_messages_for_share(share_code)
                deleted_count = 0

                for _, chat_id, message_id in messages:
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
                    share_code[:8], deleted_count, len(messages)
                )
            except Exception as e: # pylint: disable=broad-exception-caught
                logger.error("Error processing expired share %s: %s", share_code, e)
    except Exception as e: # pylint: disable=broad-exception-caught
        logger.error("Error in check_expired_shares_job: %s", e)

async def track_user_messages(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    """Global handler to track ALL user messages during shared sessions."""
    user = update.effective_user
    message = update.effective_message
    if user and message:
        share_code = active_shared_collections.get(user.id)
        if share_code:
            try:
                db.log_shared_message(share_code, user.id, message.chat_id, message.message_id)
            except Exception: # pylint: disable=broad-exception-caught
                pass

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

    # Messages
    app.add_handler(MessageHandler(filters.ALL, track_user_messages), group=-1)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))

def main():
    """Start the bot application."""
    setup_logging()
    db.init_db()

    # Restore sessions
    try:
        loaded_shares = db.get_users_with_active_shares()
        active_shared_collections.update(loaded_shares)
        logger.info("Restored %d active shared sessions from DB", len(loaded_shares))
    except Exception as e: # pylint: disable=broad-exception-caught
        logger.error("Failed to restore active sessions: %s", e)

    logger.info("Bot starting...")

    req = HTTPXRequest(connection_pool_size=8, read_timeout=60.0, write_timeout=60.0)
    app = ApplicationBuilder().token(BOT_TOKEN).rate_limiter(AIORateLimiter()).request(req) \
        .post_init(post_init).build()

    _register_handlers(app)

    if app.job_queue:
        app.job_queue.run_repeating(check_expired_shares_job, interval=60, first=10)
        logger.info("Share expiration cleanup job scheduled")

    logger.info("Polling...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
