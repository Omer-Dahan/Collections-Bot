import logging
import db

logger = logging.getLogger(__name__)

def track_if_shared(user_id: int, chat_id: int, message_id: int):
    """
    Track a message if the user is currently in a shared session.
    Used by unified send/edit helpers.
    """
    from constants import active_shared_collections
    share_code = active_shared_collections.get(user_id)
    if share_code:
        try:
            db.log_shared_message(share_code, user_id, chat_id, message_id)
        except Exception:
            pass  # Silently ignore tracking errors
