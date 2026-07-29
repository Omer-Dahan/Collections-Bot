# archive_logger.py
"""
Archive and Activity Logging Module

This module handles:
1. Forwarding/copying files to dual archive channels
2. Sending structured activity logs to admin channel
3. Rate limiting and error handling for channel operations

Uses a queue-based approach to prevent Telegram rate limiting.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Literal
from telegram import Bot, InlineKeyboardMarkup
from telegram.error import TelegramError, Forbidden, RetryAfter

# Channel IDs - configured by admin
ADMIN_ACTIVITY_CHANNEL = -1003542497376  # ערוץ מידע - בוט אוספים

# Feature toggle
ENABLE_ARCHIVING = True

# Rate limiting - delays for activity logging
ACTIVITY_LOG_DELAY = 1.0  # seconds between activity logs (helps avoid 429 bursts)
RETRY_EXTRA_DELAY = 5.0   # extra delay after a RetryAfter error

logger = logging.getLogger(__name__)


# A bounded queue prevents activity logging from retaining every uploaded file
# in RAM when Telegram is slower than incoming uploads.
ARCHIVE_QUEUE_MAXSIZE = 200
_archive_queue: asyncio.Queue = asyncio.Queue(maxsize=ARCHIVE_QUEUE_MAXSIZE)
_archive_lock = asyncio.Lock()
_queue_processor_running = False

# Action types for activity logging
ActionType = Literal[
    "FILE_SAVED",
    "FILE_ARCHIVED",
    "ARCHIVE_FAILED",
    "FILES_SENT",
    "SHARE_CREATED",
    "SHARE_ACCESSED",
    "SHARE_REVOKED"
]

# Hebrew action names
ACTION_NAMES_HE = {
    "FILE_SAVED": "קובץ נשמר",
    "FILE_ARCHIVED": "קובץ התווסף לאוסף",
    "ARCHIVE_FAILED": "גיבוי נכשל",
    "FILES_SENT": "קבצים נשלחו",
    "SHARE_CREATED": "שיתוף נוצר",
    "SHARE_ACCESSED": "גישה לשיתוף",
    "SHARE_REVOKED": "שיתוף בוטל"
}


# format_archive_caption removed (dead code)


def format_activity_log(
    action: ActionType,
    user_id: int,
    success: bool = True,
    collection_id: Optional[int] = None,
    collection_name: Optional[str] = None,
    item_id: Optional[int] = None,
    extra: Optional[dict] = None,
    user_name: str = "Unknown",
    username: Optional[str] = None
) -> str:
    """
    Format structured log message for admin activity channel.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    status = "✅ נשמר בהצלחה" if success else "❌ נכשל"
    
    # Custom formatting for "FILE_ARCHIVED" to include Name/ID in the action line
    if action == "FILE_ARCHIVED":
        if collection_name:
             action_name = f"קובץ התווסף לאוסף \"{collection_name}\""
        elif collection_id:
             action_name = f"קובץ התווסף לאוסף {collection_id}"
        else:
             action_name = ACTION_NAMES_HE.get(action, action)
    else:
        action_name = ACTION_NAMES_HE.get(action, action)
    
    # Format: Name @Username, or ID if no username
    if username:
        user_display = f"{user_name} @{username}"
    else:
        user_display = str(user_id)
    
    lines = [
        f"🕐 {timestamp}",
        f"📌 פעולה: {action_name}",
        f"👤 משתמש: {user_display}",
    ]
    
    if item_id is not None:
        lines.append(f"📦 פריט: {item_id}")
    
    lines.append(f"מצב: {status}")
    
    if extra:
        for key, value in extra.items():
            lines.append(f"  └ {key}: {value}")
    
    return "\n".join(lines)


# get_message_link removed (dead code)


async def _send_with_retry(
    bot: Bot,
    send_func,
    max_retries: int = 3
) -> Optional[int]:
    """
    Execute a send function with retry logic for rate limits.
    Returns message_id on success, None on failure.
    """
    for attempt in range(max_retries):
        try:
            msg = await send_func()
            return msg.message_id
        except RetryAfter as e:
            wait_time = e.retry_after + RETRY_EXTRA_DELAY
            logger.warning(f"Rate limited (attempt {attempt+1}), waiting {wait_time}s")
            await asyncio.sleep(wait_time)
        except Forbidden:
            logger.error("Bot not authorized in channel")
            return None
        except (TelegramError, Exception) as e:
            logger.warning(f"Send error (attempt {attempt+1}): {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(RETRY_EXTRA_DELAY)
            else:
                logger.error(f"Failed to send after {max_retries} attempts: {e}")
                return None
    return None


async def safe_copy_file_to_channel(
    bot: Bot,
    channel_id: int,
    file_id: Optional[str],
    content_type: str,
    caption: Optional[str] = None,
    file_name: Optional[str] = None,
    reply_markup: Optional[InlineKeyboardMarkup] = None
) -> Optional[int]:
    """
    Copy a file or send a message to a channel.
    Unified function for all content types.
    Returns message_id on success, None on failure.
    """
    async def send():
        method_map = {
            "photo": "send_photo",
            "video": "send_video",
            "document": "send_document",
            "audio": "send_audio",
            "text": "send_message"
        }
        
        if content_type not in method_map:
            raise ValueError(f"Unknown content type: {content_type}")
            
        method = getattr(bot, method_map[content_type])
        kwargs = {"chat_id": channel_id}
        
        if content_type == "text":
            kwargs["text"] = caption or "[Empty text item]"
        else:
            # For non-text items, file_id is required
            if not file_id:
                 raise ValueError(f"file_id required for {content_type}")
            kwargs[content_type] = file_id
            kwargs["caption"] = caption
            if content_type == "document":
                kwargs["filename"] = file_name
        
        # Add reply_markup if provided
        if reply_markup:
            kwargs["reply_markup"] = reply_markup
                
        return await method(**kwargs)
    
    try:
        return await _send_with_retry(bot, send)
    except ValueError as e:
        logger.warning(str(e))
        return None


async def log_activity(
    bot: Bot,
    action: ActionType,
    user_id: int,
    success: bool = True,
    collection_id: Optional[int] = None,
    collection_name: Optional[str] = None,
    item_id: Optional[int] = None,
    extra: Optional[dict] = None,
    user_name: str = "Unknown",
    username: Optional[str] = None,
    reply_markup: Optional[InlineKeyboardMarkup] = None
) -> None:
    """
    Send activity log to admin channel.
    This is fire-and-forget - failures are logged but don't propagate.
    """
    if not ENABLE_ARCHIVING:
        return

    # No lock needed here - this is a single fire-and-forget send, not shared state
    try:
        log_text = format_activity_log(
            action, user_id, success,
            collection_id, collection_name,
            item_id, extra,
            user_name, username
        )
        # Use unified send function with content_type="text"
        await safe_copy_file_to_channel(
            bot=bot,
            channel_id=ADMIN_ACTIVITY_CHANNEL,
            file_id=None,
            content_type="text",
            caption=log_text,
            reply_markup=reply_markup
        )
        # Delay after send to reduce burst 429s
        await asyncio.sleep(ACTIVITY_LOG_DELAY)
    except Exception as e:
        # Never let activity logging crash the main flow
        logger.error(f"Activity log failed: {e}")

async def _do_archive_file(
    bot: Bot,
    item_id: int,
    file_id: Optional[str],
    content_type: str,
    user_id: int,
    collection_id: int,
    collection_name: Optional[str] = None,
    file_name: Optional[str] = None,
    original_caption: Optional[str] = None,
    user_name: str = "Unknown",
    username: Optional[str] = None
) -> bool:
    """
    Actually perform the archiving.
    MODIFIED: Only logs activity with "View File" deep link button.
    Does NOT send to archive channels anymore.
    """
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    
    # Generate Deep Link URL
    try:
        bot_username = bot.username
        deep_link = f"https://t.me/{bot_username}?start=view_{item_id}"
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("👀 צפיה בקובץ (מנהלים בלבד)", url=deep_link)]
        ])
    except Exception as e:
        logger.error(f"Failed to generate deep link: {e}")
        keyboard = None
    
    # Log activity with the button
    await log_activity(
        bot, "FILE_ARCHIVED", user_id,
        success=True,
        collection_id=collection_id,
        collection_name=collection_name,
        item_id=item_id,
        user_name=user_name,
        username=username,
        reply_markup=keyboard
    )
    
    return True


async def archive_file_to_channels(
    bot: Bot,
    item_id: int,
    file_id: Optional[str],
    content_type: str,
    user_id: int,
    collection_id: int,
    collection_name: Optional[str] = None,
    file_name: Optional[str] = None,
    original_caption: Optional[str] = None,
    user_name: str = "Unknown",
    username: Optional[str] = None
) -> bool:
    """
    Queue a file for logging to the activity channel (Archive channels disabled).
    Returns immediately after queueing.
    """
    global _queue_processor_running
    
    if not ENABLE_ARCHIVING:
        return True
    
    # Enqueue before starting the worker, while holding the lock, so a worker
    # can never observe an empty queue and exit between these two operations.
    # ``put`` deliberately applies backpressure when the queue is full.
    async with _archive_lock:
        await _archive_queue.put({
            "item_id": item_id,
            "file_id": file_id,
            "content_type": content_type,
            "user_id": user_id,
            "collection_id": collection_id,
            "collection_name": collection_name,
            "file_name": file_name,
            "original_caption": original_caption,
            "user_name": user_name,
            "username": username,
            "bot": bot  # Store bot reference for queue processor
        })
        
        # Start one worker only.  It stays alive while the application runs,
        # which also avoids creating a new task for every uploaded file.
        if not _queue_processor_running:
            _queue_processor_running = True
            asyncio.create_task(_process_archive_queue_safe())
    
    return True


async def _process_archive_queue_safe():
    """
    Wrapper for queue processing with proper cleanup on exit.
    """
    global _queue_processor_running
    
    try:
        while True:
            item = await _archive_queue.get()
            bot = item.pop("bot")  # Extract bot from item
            try:
                await _do_archive_file(bot=bot, **item)
            except Exception as e:
                logger.error(f"Error processing archive queue item: {e}")
            finally:
                _archive_queue.task_done()
    except Exception as e:
        logger.error(f"Queue processor crashed: {e}")
    finally:
        async with _archive_lock:
            _queue_processor_running = False
