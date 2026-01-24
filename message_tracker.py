# message_tracker.py
"""
Centralized message tracking for shared collection sessions.
All bot messages sent during shared sessions are tracked here for cleanup on expiration.
"""
from constants import active_shared_collections
import db


def track_if_shared(user_id: int, chat_id: int, message_id: int) -> None:
    """
    Track a message for cleanup if user is in a shared session.
    
    Args:
        user_id: The user receiving the message
        chat_id: Chat where message was sent
        message_id: ID of the message to track
    """
    share_code = active_shared_collections.get(user_id)
    if share_code:
        try:
            db.log_shared_message(share_code, user_id, chat_id, message_id)
        except Exception:
            pass  # Silently ignore tracking errors


async def send_message_tracked(bot, chat_id: int, user_id: int, **kwargs):
    """
    Wrapper for bot.send_message that tracks messages for shared sessions.
    
    Args:
        bot: The bot instance
        chat_id: Target chat ID
        user_id: User ID for tracking
        **kwargs: Arguments passed to bot.send_message
        
    Returns:
        The sent Message object
    """
    msg = await bot.send_message(chat_id=chat_id, **kwargs)
    track_if_shared(user_id, chat_id, msg.message_id)
    return msg


async def send_photo_tracked(bot, chat_id: int, user_id: int, **kwargs):
    """Wrapper for bot.send_photo that tracks messages."""
    msg = await bot.send_photo(chat_id=chat_id, **kwargs)
    track_if_shared(user_id, chat_id, msg.message_id)
    return msg


async def send_video_tracked(bot, chat_id: int, user_id: int, **kwargs):
    """Wrapper for bot.send_video that tracks messages."""
    msg = await bot.send_video(chat_id=chat_id, **kwargs)
    track_if_shared(user_id, chat_id, msg.message_id)
    return msg


async def send_document_tracked(bot, chat_id: int, user_id: int, **kwargs):
    """Wrapper for bot.send_document that tracks messages."""
    msg = await bot.send_document(chat_id=chat_id, **kwargs)
    track_if_shared(user_id, chat_id, msg.message_id)
    return msg


async def send_audio_tracked(bot, chat_id: int, user_id: int, **kwargs):
    """Wrapper for bot.send_audio that tracks messages."""
    msg = await bot.send_audio(chat_id=chat_id, **kwargs)
    track_if_shared(user_id, chat_id, msg.message_id)
    return msg


async def send_media_group_tracked(bot, chat_id: int, user_id: int, **kwargs):
    """
    Wrapper for bot.send_media_group that tracks all messages.
    
    Returns:
        List of Message objects sent
    """
    messages = await bot.send_media_group(chat_id=chat_id, **kwargs)
    for m in messages:
        track_if_shared(user_id, chat_id, m.message_id)
    return messages
