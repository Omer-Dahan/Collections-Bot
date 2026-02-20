"""
Configuration settings for the Collections Bot.
Loads environment variables and defines feature flags and limits.
"""
import os
from dotenv import load_dotenv

# Load .env file
load_dotenv()

def get_env(key, default=None):
    """Retrieve environment variable or fallback to default."""
    return os.environ.get(key, default)

# ============================================================
# Core Bot Settings (Sensitive, usually in .env)
# ============================================================
BOT_TOKEN: str = get_env("BOT_TOKEN")

# Telegram IDs of bot admins
# Comma-separated list in .env: ADMIN_IDS=123,456
admin_ids_str = get_env("ADMIN_IDS", default="")
ADMIN_IDS: list[int] = [
    int(x.strip()) for x in admin_ids_str.split(",") if x.strip().isdigit()
]

# Hebrew UI Strings (Centralized here if needed)
MSG_NO_COLLECTIONS = "❌ אין לך אוספים עדיין. צור אוסף חדש תחילה."

# ============================================================
# Archiving Settings (Optional)
# ============================================================

# Channel/Group IDs for media archiving
# Format: CHANNEL_ID_1,CHANNEL_ID_2
archive_channels_str = get_env("ARCHIVE_CHANNELS", default="")
ARCHIVE_CHANNELS: list[int] = [
    int(x.strip()) for x in archive_channels_str.split(",") if x.strip().lstrip("-").isdigit()
]

# Admin Logging Channel (For events like share access, errors)
LOG_CHANNEL_ID: int = int(get_env("LOG_CHANNEL_ID", default="0"))

# ============================================================
# Feature Flags & Limits (non-sensitive, defaults allowed)
# ============================================================
ENABLE_ARCHIVING: bool = get_env("ENABLE_ARCHIVING", default="False").lower() == "true"

def is_admin(user_id: int) -> bool:
    """Check if a user is an admin."""
    return user_id in ADMIN_IDS
