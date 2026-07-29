from typing import Dict

# Message constants
MSG_NO_COLLECTIONS = "אין עדיין אוספים. צור אחד עם /newcollection."

# Global state
active_collections: Dict[int, int] = {}  # user_id -> collection_id
# Unix timestamps are kept separately to avoid changing existing collection-id
# consumers.  The periodic cleanup job expires abandoned collection modes.
active_collection_timestamps: Dict[int, float] = {}
active_shared_collections: Dict[int, str] = {}  # user_id -> share_code
active_shared_collection_timestamps: Dict[int, float] = {}
