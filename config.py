# config.py - Secure Environment-Based Configuration
# ⚠️ NOTE: Previous tokens should be considered leaked. Rotate via @BotFather!
import os
from typing import List, Optional

# Load .env file if it exists (install: pip install python-dotenv)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not installed, rely on system environment variables

# ============================================================
# Environment Variable Helper
# ============================================================

def get_env(name: str, default: Optional[str] = None, required: bool = False) -> Optional[str]:
    """
    Read environment variable with validation.
    
    Args:
        name: Environment variable name
        default: Default value if not set (only for non-sensitive vars)
        required: If True, raise exception when missing
        
    Returns:
        The environment variable value or default
        
    Raises:
        EnvironmentError: If required=True and variable is missing
    """
    value = os.environ.get(name, default)
    if required and not value:
        raise EnvironmentError(
            f"❌ Missing required environment variable: {name}\n"
            f"Please set it in your .env file or system environment."
        )
    return value


def _parse_int_list(value: Optional[str]) -> List[int]:
    """Parse comma-separated string to list of integers."""
    if not value:
        return []
    return [int(x.strip()) for x in value.split(",") if x.strip()]


# ============================================================
# Telegram / Bot Configuration
# ============================================================

# REQUIRED - No default value allowed for security
BOT_TOKEN: str = get_env("BOT_TOKEN", required=True)

# ============================================================
# User Access Control
# ============================================================

# Admin user IDs (comma-separated in ENV, e.g., "845330686,7329344302")
ADMIN_IDS: List[int] = _parse_int_list(get_env("ADMIN_IDS", default=""))


def is_admin(user_id: int) -> bool:
    """Check if user is an admin."""
    return user_id in ADMIN_IDS


# ============================================================
# Feature Flags & Limits (non-sensitive, defaults allowed)
# ============================================================

# Maximum caption length in characters
MAX_CAPTION_LENGTH: int = int(get_env("MAX_CAPTION_LENGTH", default="1000"))

# Debug mode - enables verbose logging
DEBUG: bool = get_env("DEBUG", default="false").lower() == "true"