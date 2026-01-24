import logging
import asyncio
import db
from telegram import Update
from constants import active_shared_collections
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    AIORateLimiter,
)
from telegram.request import HTTPXRequest
from config import BOT_TOKEN
from admin_panel import admin_panel, handle_admin_callback
from utils import error_handler, UserActionFilter, logger

from handlers import (
    start, new_collection, list_collections, manage_collections, browse, 
    remove, id_file, access_shared,
    handle_select_collection_callback, handle_browse_page_callback,
    handle_scroll_view_callback, handle_page_info_callback,
    handle_back_to_info_callback,
    handle_browse_group_or_select_all_callback, handle_page_file_send_choice_callback,
    handle_batch_status_callback, handle_collection_send_all_callback,
    handle_stop_collect_callback, handle_delete_select_collection_callback,
    handle_main_menu_button, handle_back_to_main_callback,
    handle_manage_collection_callback, handle_share_collection_callback,
    handle_share_stats_callback, handle_regenerate_share_callback,
    handle_revoke_share_callback, handle_export_collection_callback,
    handle_delete_collection_callback, handle_back_to_manage_callback,
    handle_exit_shared_collection_callback, handle_cancel_share_access_callback,
    handle_exit_delete_mode_callback, handle_import_collection_mode_callback,
    handle_select_item_delete_col_callback,
    handle_set_share_expiration_callback, handle_save_share_expiration_callback,
    handle_custom_share_expiration_callback, handle_save_custom_share_expiration_callback,
    handle_message
)

def setup_logging():
    # File handler - only user actions and errors
    file_handler = logging.FileHandler("bot.log", encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s]: %(message)s"))
    file_handler.addFilter(UserActionFilter())

    # Console handler - everything
    import sys
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

    # Define root logger
    logging.basicConfig(
        level=logging.INFO,
        handlers=[file_handler, console_handler]
    )


async def check_expired_shares_job(context):
    """
    Background job that runs every minute to:
    1. Find expired shares
    2. Delete tracked messages from Telegram
    3. Mark shares as inactive
    4. Clean up tracking records
    """
    try:
        expired_shares = db.get_expired_shares()
        
        if not expired_shares:
            return
            
        logger.info(f"Found {len(expired_shares)} expired shares to process")
        
        for share_code, collection_id, owner_id, expires_at in expired_shares:
            try:
                # 1. Deactivate immediately to prevent double-processing in next run
                db.deactivate_share_by_code(share_code)
                
                # 2. Get messages
                messages = db.get_messages_for_share(share_code)
                deleted_count = 0
                
                # 3. Delete messages with rate limiting
                # 3. Delete messages with rate limiting
                for user_id, chat_id, message_id in messages:
                    try:
                        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
                        deleted_count += 1
                        await asyncio.sleep(0.05)
                    except Exception as e:
                        error_str = str(e).lower()
                        # Treat as success if message already deleted or not deletable
                        if "400" in str(e) or "not found" in error_str or "deleted" in error_str:
                            deleted_count += 1  # Count as success
                        else:
                            logger.warning(f"Failed to delete message {message_id}: {e}")
                    finally:
                        # CRITICAL: Always remove from tracking DB
                        db.delete_single_message_record(message_id, chat_id)
                
                logger.info(f"Expired share {share_code[:8]}...: deleted {deleted_count}/{len(messages)} messages")
                
            except Exception as e:
                logger.error(f"Error processing expired share {share_code}: {e}")
                
    except Exception as e:
        logger.error(f"Error in check_expired_shares_job: {e}")

async def track_user_messages(update: Update, context):
    """
    Global handler to track ALL user messages during shared sessions.
    Runs before all other handlers with group=-1.
    """
    user = update.effective_user
    message = update.effective_message
    if user and message:
        share_code = active_shared_collections.get(user.id)
        if share_code:
            try:
                db.log_shared_message(share_code, user.id, message.chat_id, message.message_id)
            except Exception:
                pass  # Silently ignore tracking errors


async def post_init(application):
    """Set up bot commands menu after bot is initialized."""
    from telegram import BotCommand
    commands = [
        BotCommand("start", "התחל - תפריט ראשי"),
        BotCommand("access", "גישה לאוסף משותף"),
        BotCommand("browse", "דפדוף באוספים"),
        BotCommand("newcollection", "יצירת אוסף חדש"),
    ]
    await application.bot.set_my_commands(commands)
    logger.info("Bot commands menu set")


def main():
    setup_logging()
    
    # Initialize DB
    db.init_db()
    
    logger.info("Bot starting...")

    request = HTTPXRequest(connection_pool_size=8, read_timeout=60.0, write_timeout=60.0, connect_timeout=60.0, pool_timeout=60.0)
    app = ApplicationBuilder().token(BOT_TOKEN).rate_limiter(AIORateLimiter()).request(request).post_init(post_init).build()

    # --- Error Handler ---
    app.add_error_handler(error_handler)

    # --- Commands ---
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("newcollection", new_collection))
    app.add_handler(CommandHandler("collections", list_collections))
    app.add_handler(CommandHandler("manage", manage_collections))
    app.add_handler(CommandHandler("browse", browse))
    app.add_handler(CommandHandler("remove", remove))
    app.add_handler(CommandHandler("id_file", id_file))
    app.add_handler(CommandHandler("access", access_shared))
    
    app.add_handler(CommandHandler("admin", admin_panel)) # External module
    app.add_handler(CommandHandler("adminpanel", admin_panel))

    # --- Callback Handlers ---
    
    # Main Menu
    app.add_handler(CallbackQueryHandler(handle_main_menu_button, pattern="^main_menu:"))
    app.add_handler(CallbackQueryHandler(handle_back_to_main_callback, pattern="^back_to_main$"))
    
    # Collection Selection & Creation
    app.add_handler(CallbackQueryHandler(handle_select_collection_callback, pattern="^select_collection:"))
    app.add_handler(CallbackQueryHandler(handle_import_collection_mode_callback, pattern="^import_collection_mode$"))
    app.add_handler(CallbackQueryHandler(handle_stop_collect_callback, pattern="^stop_collect$"))
    
    # Browsing & Viewing
    app.add_handler(CallbackQueryHandler(handle_browse_page_callback, pattern="^browse_page:"))
    app.add_handler(CallbackQueryHandler(handle_scroll_view_callback, pattern="^scroll_view:"))
    app.add_handler(CallbackQueryHandler(handle_page_info_callback, pattern="^page_info:"))
    app.add_handler(CallbackQueryHandler(handle_back_to_info_callback, pattern="^back_to_info:"))
    app.add_handler(CallbackQueryHandler(handle_browse_group_or_select_all_callback, pattern="^(browse_group|browse_page_select_all):"))
    app.add_handler(CallbackQueryHandler(handle_page_file_send_choice_callback, pattern="^page_files_"))
    app.add_handler(CallbackQueryHandler(handle_collection_send_all_callback, pattern="^collection_send_all:"))
    app.add_handler(CallbackQueryHandler(handle_batch_status_callback, pattern="^batch_status:"))

    # Management
    app.add_handler(CallbackQueryHandler(handle_manage_collection_callback, pattern="^manage_collection:"))
    app.add_handler(CallbackQueryHandler(handle_share_collection_callback, pattern="^share_collection:"))
    app.add_handler(CallbackQueryHandler(handle_share_stats_callback, pattern="^share_stats:"))
    app.add_handler(CallbackQueryHandler(handle_regenerate_share_callback, pattern="^regenerate_share:"))
    app.add_handler(CallbackQueryHandler(handle_revoke_share_callback, pattern="^revoke_share:"))
    app.add_handler(CallbackQueryHandler(handle_export_collection_callback, pattern="^export_collection:"))
    app.add_handler(CallbackQueryHandler(handle_delete_collection_callback, pattern="^delete_collection:"))
    app.add_handler(CallbackQueryHandler(handle_back_to_manage_callback, pattern="^back_to_manage$"))
    
    # Deletion & Sharing Access
    app.add_handler(CallbackQueryHandler(handle_select_item_delete_col_callback, pattern="^select_item_del_col:"))
    app.add_handler(CallbackQueryHandler(handle_exit_delete_mode_callback, pattern="^exit_delete_mode$"))
    app.add_handler(CallbackQueryHandler(handle_exit_shared_collection_callback, pattern="^exit_shared_collection$"))
    app.add_handler(CallbackQueryHandler(handle_cancel_share_access_callback, pattern="^cancel_share_access$"))
    
    # External Admin Handlers
    app.add_handler(CallbackQueryHandler(handle_admin_callback, pattern="^(admin_|user_stats|system_stats|broadcast|backup_db)"))
    
    # Share Expiration
    app.add_handler(CallbackQueryHandler(handle_set_share_expiration_callback, pattern="^set_share_expiration:"))
    app.add_handler(CallbackQueryHandler(handle_save_share_expiration_callback, pattern="^save_share_exp:"))
    app.add_handler(CallbackQueryHandler(handle_custom_share_expiration_callback, pattern="^custom_share_exp:"))
    app.add_handler(CallbackQueryHandler(handle_save_custom_share_expiration_callback, pattern="^save_share_exp_custom:"))
    
    # Background Job for Expired Shares Cleanup
    if app.job_queue:
        app.job_queue.run_repeating(check_expired_shares_job, interval=60, first=10)
        logger.info("Share expiration cleanup job scheduled")
    else:
        logger.warning("JobQueue not available. Install with: pip install 'python-telegram-bot[job-queue]'")

    # GLOBAL USER MESSAGE TRACKER - runs before all other handlers (group=-1)
    # Tracks all incoming messages for users in shared sessions
    app.add_handler(MessageHandler(filters.ALL, track_user_messages), group=-1)

    # Everything else (Text, Photo, Video, Document, etc.)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))

    logger.info("Polling...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()