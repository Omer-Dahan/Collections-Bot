# db.py
import sqlite3
from datetime import datetime, timedelta
from contextlib import contextmanager
from config import ADMIN_IDS

DB_PATH = "bot_data.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db_transaction(commit=True):
    """
    Context manager for database transactions.
    Automatically handles connection, commit/rollback, and cleanup.
    
    Args:
        commit: Whether to commit on success (default True)
        
    Usage:
        with db_transaction() as (conn, cur):
            cur.execute("INSERT ...")
    """
    conn = get_connection()
    cur = conn.cursor()
    try:
        yield conn, cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def migrate_db():
    """Add user_id column to collections if it doesn't exist."""
    conn = get_connection()
    cur = conn.cursor()
    
    # Check if user_id column exists
    cur.execute("PRAGMA table_info(collections)")
    columns = [info[1] for info in cur.fetchall()]
    
    if "user_id" not in columns:
        print("Migrating database: Adding user_id to collections...")
        # Add the column
        cur.execute("ALTER TABLE collections ADD COLUMN user_id INTEGER")
        
        # Assign existing collections to the first admin
        default_admin_id = ADMIN_IDS[0] if ADMIN_IDS else 0
        cur.execute("UPDATE collections SET user_id = ?", (default_admin_id,))
        conn.commit()
        print(f"Migration complete. All existing collections assigned to {default_admin_id}")
    
    # Check if blocked column exists in users table
    cur.execute("PRAGMA table_info(users)")
    user_columns = [info[1] for info in cur.fetchall()]
    
    if "blocked" not in user_columns:
        print("Migrating database: Adding blocked column to users...")
        cur.execute("ALTER TABLE users ADD COLUMN blocked INTEGER DEFAULT 0")
        conn.commit()
        print("Migration complete. Added blocked column to users table.")
    
    
    # Check if current_share_code column exists in users table (Migration for Session Persistence)
    cur.execute("PRAGMA table_info(users)")
    user_columns = [info[1] for info in cur.fetchall()]
    
    if "current_share_code" not in user_columns:
        print("Migrating database: Adding current_share_code to users...")
        cur.execute("ALTER TABLE users ADD COLUMN current_share_code TEXT")
        conn.commit()
        print("Migration complete. Added current_share_code column to users table.")

    # Check if expires_at column exists in shared_collections table
    cur.execute("PRAGMA table_info(shared_collections)")
    shared_cols = [info[1] for info in cur.fetchall()]
    
    if "expires_at" not in shared_cols:
        print("Migrating database: Adding expires_at column to shared_collections...")
        cur.execute("ALTER TABLE shared_collections ADD COLUMN expires_at TEXT")
        conn.commit()
        print("Migration complete. Added expires_at column.")
    
    # Add file_unique_id column to items table if missing
    cur.execute("PRAGMA table_info(items)")
    item_columns = [info[1] for info in cur.fetchall()]

    if "file_unique_id" not in item_columns:
        print("Migrating database: Adding file_unique_id to items...")
        cur.execute("ALTER TABLE items ADD COLUMN file_unique_id TEXT")
        conn.commit()
        print("Migration complete. Existing items will have NULL file_unique_id.")

    # Add unique index on shared_messages_to_delete for INSERT OR IGNORE support
    try:
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_shared_messages_unique 
            ON shared_messages_to_delete(share_code, chat_id, message_id)
        """)
        conn.commit()
    except Exception:
        pass  # Index may already exist or table doesn't exist yet
    
    conn.close()


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS collections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        UNIQUE(name, user_id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        collection_id INTEGER NOT NULL,
        content_type TEXT NOT NULL,  -- "video", "photo", "document", "text"
        file_id TEXT,
        file_unique_id TEXT,
        text_content TEXT,
        file_name TEXT,
        file_size INTEGER,
        added_at TEXT NOT NULL,
        FOREIGN KEY (collection_id) REFERENCES collections(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        first_seen TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS shared_collections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        collection_id INTEGER NOT NULL,
        share_code TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        created_by INTEGER NOT NULL,
        is_active INTEGER DEFAULT 1,
        FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS shared_collection_access_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        share_code TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        accessed_at TEXT NOT NULL,
        FOREIGN KEY (share_code) REFERENCES shared_collections(share_code)
    )
    """)
    
    # Archive tracking table - stores where items were archived
    cur.execute("""
    CREATE TABLE IF NOT EXISTS archive_info (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id INTEGER NOT NULL,
        archive_channel_id INTEGER NOT NULL,
        archive_message_id INTEGER NOT NULL,
        archived_at TEXT NOT NULL,
        FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
    )
    """)
    
    # Shared messages tracking - for cleanup when share expires
    cur.execute("""
    CREATE TABLE IF NOT EXISTS shared_messages_to_delete (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        share_code TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        chat_id INTEGER NOT NULL,
        message_id INTEGER NOT NULL,
        UNIQUE(share_code, chat_id, message_id)
    )
    """)
    
    # Add indices for better performance
    cur.execute("CREATE INDEX IF NOT EXISTS idx_items_collection ON items(collection_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_items_file_id ON items(file_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_collections_user ON collections(user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_shared_collections_code ON shared_collections(share_code)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_archive_info_item ON archive_info(item_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_shared_messages_share_code ON shared_messages_to_delete(share_code)")
    
    conn.commit()
    conn.close()
    
    # Run migration to ensure existing DBs are updated
    migrate_db()


def create_collection(name: str, user_id: int) -> int:
    with db_transaction() as (conn, cur):
        cur.execute("INSERT INTO collections (name, user_id) VALUES (?, ?)", (name, user_id))
        return cur.lastrowid


def get_collections(user_id: int) -> list:
    """Get collections for a specific user."""
    with db_transaction(commit=False) as (conn, cur):
        cur.execute("SELECT id, name FROM collections WHERE user_id = ? ORDER BY id", (user_id,))
        return cur.fetchall()


def get_collection_by_id(collection_id: int) -> tuple | None:
    """Get collection details including user_id."""
    with db_transaction(commit=False) as (conn, cur):
        cur.execute("SELECT id, name, user_id FROM collections WHERE id = ?", (collection_id,))
        return cur.fetchone()


def add_item(
    collection_id: int,
    content_type: str,
    file_id: str | None = None,
    text_content: str | None = None,
    file_name: str | None = None,
    file_size: int | None = None,
    file_unique_id: str | None = None,
) -> int:
    added_at = datetime.now().isoformat()
    with db_transaction() as (conn, cur):
        cur.execute(
            """
            INSERT INTO items
                (collection_id, content_type, file_id, file_unique_id,
                 text_content, file_name, file_size, added_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (collection_id, content_type, file_id, file_unique_id,
             text_content, file_name, file_size, added_at)
        )
        return cur.lastrowid


# is_duplicate_file removed (dead code)


def get_items_by_collection(collection_id: int, offset: int = 0, limit: int = 10) -> list:
    with db_transaction(commit=False) as (conn, cur):
        cur.execute(
            """
            SELECT id, content_type, file_id, text_content, file_name, file_size, added_at
            FROM items
            WHERE collection_id = ?
            ORDER BY id
            LIMIT ? OFFSET ?
            """,
            (collection_id, limit, offset)
        )
        return cur.fetchall()


def get_item_by_id(item_id: int) -> tuple | None:
    """Get a single item by its ID."""
    with db_transaction(commit=False) as (conn, cur):
        cur.execute(
            """
            SELECT id, collection_id, content_type, file_id, text_content, file_name, file_size, added_at
            FROM items
            WHERE id = ?
            """,
            (item_id,)
        )
        return cur.fetchone()


def count_items_in_collection(collection_id: int) -> int:
    with db_transaction(commit=False) as (conn, cur):
        cur.execute(
            "SELECT COUNT(*) FROM items WHERE collection_id = ?",
            (collection_id,)
        )
        (count,) = cur.fetchone()
        return count


# delete_item_by_id removed (dead code)


def get_all_file_items() -> list:
    """
    Return all items that have a file_id (excludes pure text items).
    Used for file validity scanning.
    Returns list of (item_id, content_type, file_id).
    """
    with db_transaction(commit=False) as (conn, cur):
        cur.execute(
            """
            SELECT id, content_type, file_id
            FROM items
            WHERE file_id IS NOT NULL AND file_id != ''
            ORDER BY id
            """
        )
        return cur.fetchall()


def count_file_items(collection_id: int | None = None) -> int:
    """Count file-backed items without materializing their file IDs."""
    with db_transaction(commit=False) as (conn, cur):
        query = "SELECT COUNT(*) FROM items WHERE file_id IS NOT NULL AND file_id != ''"
        params = ()
        if collection_id is not None:
            query += " AND collection_id = ?"
            params = (collection_id,)
        cur.execute(query, params)
        return cur.fetchone()[0]


def get_file_items_page(
    offset: int = 0, limit: int = 200, collection_id: int | None = None
) -> list:
    """Read a bounded page of file IDs for administrative validation."""
    with db_transaction(commit=False) as (conn, cur):
        query = """
            SELECT id, content_type, file_id
            FROM items
            WHERE file_id IS NOT NULL AND file_id != ''
        """
        params: tuple = ()
        if collection_id is not None:
            query += " AND collection_id = ?"
            params = (collection_id,)
        query += " ORDER BY id LIMIT ? OFFSET ?"
        cur.execute(query, params + (limit, offset))
        return cur.fetchall()


# delete_items_by_file_id removed (dead code)


def delete_all_items_in_collection(collection_id: int) -> int:
    """Delete all items in a collection"""
    with db_transaction() as (conn, cur):
        cur.execute("DELETE FROM items WHERE collection_id = ?", (collection_id,))
        return cur.rowcount


def delete_collection(collection_id: int) -> bool:
    """Delete a collection (after deleting all its items)"""
    import logging
    logger = logging.getLogger(__name__)
    
    with db_transaction() as (conn, cur):
        try:
            # Step 1: Delete access logs for share codes of this collection
            cur.execute("""
                DELETE FROM shared_collection_access_log 
                WHERE share_code IN (
                    SELECT share_code FROM shared_collections 
                    WHERE collection_id = ?
                )
            """, (collection_id,))
            deleted_logs = cur.rowcount
            if deleted_logs > 0:
                logger.info(f"Deleted {deleted_logs} access log(s) for collection {collection_id}")
            
            # Step 2: Delete any shared_collections records
            cur.execute("DELETE FROM shared_collections WHERE collection_id = ?", (collection_id,))
            deleted_shares = cur.rowcount
            if deleted_shares > 0:
                logger.info(f"Deleted {deleted_shares} share record(s) for collection {collection_id}")
            
            # Step 1.5: Delete all items in the collection
            cur.execute("DELETE FROM items WHERE collection_id = ?", (collection_id,))
            deleted_items = cur.rowcount
            if deleted_items > 0:
                logger.info(f"Deleted {deleted_items} item(s) from collection {collection_id}")

            # Step 3: Delete the collection itself
            cur.execute("DELETE FROM collections WHERE id = ?", (collection_id,))
            success = cur.rowcount > 0
            
            if success:
                logger.info(f"Collection {collection_id} deleted successfully")
            return success
        except Exception as e:
            logger.error(f"Error deleting collection {collection_id}: {e}")
            raise


def delete_item(collection_id: int, file_id: str) -> bool:
    """Delete a specific item from a collection by file_id"""
    with db_transaction() as (conn, cur):
        try:
            cur.execute("DELETE FROM items WHERE collection_id = ? AND file_id = ?", (collection_id, file_id))
            return cur.rowcount > 0
        except Exception:
            logger.exception("delete_item failed (collection_id=%s, file_id=%s)", collection_id, file_id)
            return False


def get_all_collections_paginated(offset: int = 0, limit: int = 12) -> list:
    """
    Get all collections with user details, paginated.
    Returns list of tuples: (col_id, col_name, user_id, username, first_name)
    """
    with db_transaction(commit=False) as (conn, cur):
        cur.execute(
            """
            SELECT c.id, c.name, c.user_id, u.username, u.first_name
            FROM collections c
            LEFT JOIN users u ON c.user_id = u.user_id
            ORDER BY c.id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset)
        )
        return cur.fetchall()


def count_all_collections() -> int:
    """Count total number of collections in the system."""
    with db_transaction(commit=False) as (conn, cur):
        cur.execute("SELECT COUNT(*) FROM collections")
        return cur.fetchone()[0]


# --- Admin / Global Functions ---

def get_all_users_with_collections() -> list:
    """Get list of distinct user_ids that have collections."""
    with db_transaction(commit=False) as (conn, cur):
        cur.execute("SELECT DISTINCT user_id FROM collections")
        return [row[0] for row in cur.fetchall()]



# transfer_collection_ownership removed (dead code)


def clone_collection_for_user(source_collection_id: int, target_user_id: int) -> int:
    """
    Create a copy of an existing collection and all its items
    for another user (for example: the admin).

    Returns the new collection id, or 0 on failure.
    """
    with db_transaction() as (conn, cur):
        try:
            # Get collection name
            cur.execute("SELECT name FROM collections WHERE id = ?", (source_collection_id,))
            row = cur.fetchone()
            if not row:
                return 0

            original_name = row[0]

            # Create new collection for target user
            cur.execute(
                "INSERT INTO collections (name, user_id) VALUES (?, ?)",
                (original_name, target_user_id),
            )
            new_collection_id = cur.lastrowid

            # Copy all items from source collection to new collection
            cur.execute(
                """
                INSERT INTO items (collection_id, content_type, file_id, text_content, file_name, file_size, added_at)
                SELECT ?, content_type, file_id, text_content, file_name, file_size, added_at
                FROM items
                WHERE collection_id = ?
                """,
                (new_collection_id, source_collection_id),
            )
            return new_collection_id
        except Exception:
            return 0


def get_global_stats() -> dict:
    """Get global statistics."""
    with db_transaction(commit=False) as (conn, cur):
        cur.execute("SELECT COUNT(*) FROM collections")
        total_collections = cur.fetchone()[0]
        
        cur.execute("SELECT COUNT(*) FROM items")
        total_items = cur.fetchone()[0]
        
        cur.execute("SELECT COUNT(DISTINCT user_id) FROM collections")
        total_users = cur.fetchone()[0]
        
        return {
            "collections": total_collections,
            "items": total_items,
            "users": total_users
        }


def upsert_user(user_id: int, username: str | None, first_name: str | None, last_name: str | None) -> bool:
    """Insert or update user details. Returns True if user is blocked."""
    from datetime import datetime

    with db_transaction() as (conn, cur):
        cur.execute("SELECT first_seen, blocked FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()

        if row:
            cur.execute("""
                UPDATE users
                SET username = ?, first_name = ?, last_name = ?
                WHERE user_id = ?
            """, (username, first_name, last_name, user_id))
            return bool(row[1])
        else:
            first_seen = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cur.execute("""
                INSERT INTO users (user_id, username, first_name, last_name, first_seen)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, username, first_name, last_name, first_seen))
            return False


def get_user(user_id: int):
    """Get user information including blocked status."""
    with db_transaction(commit=False) as (conn, cur):
        cur.execute("SELECT user_id, username, first_name, last_name, first_seen, blocked FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        
        if not row:
            return None
        
        # Return a simple object-like dict
        class UserInfo:
            def __init__(self, user_id, username, first_name, last_name, first_seen, blocked):
                self.user_id = user_id
                self.username = username
                self.first_name = first_name
                self.last_name = last_name
                self.first_seen = first_seen
                self.blocked = blocked
        
        return UserInfo(row[0], row[1], row[2], row[3], row[4], row[5] if len(row) > 5 else 0)


def block_user(user_id: int) -> bool:
    """Block a user from using the bot."""
    with db_transaction() as (conn, cur):
        try:
            cur.execute("UPDATE users SET blocked = 1 WHERE user_id = ?", (user_id,))
            return cur.rowcount > 0
        except Exception:
            return False


def get_user_details(user_id: int) -> dict | None:
    """Get detailed user info for admin panel."""
    with db_transaction(commit=False) as (conn, cur):
        cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user_row = cur.fetchone()
        
        if not user_row:
            return None
            
        #user_row: (user_id, username, first_name, last_name, first_seen, blocked)
        
        # Get stats
        cur.execute("SELECT COUNT(*) FROM collections WHERE user_id = ?", (user_id,))
        collections_count = cur.fetchone()[0]
        
        cur.execute("""
            SELECT COUNT(*) FROM items 
            JOIN collections ON items.collection_id = collections.id 
            WHERE collections.user_id = ?
        """, (user_id,))
        items_count = cur.fetchone()[0]
        
        # Get first collection date
        cur.execute("SELECT id FROM collections WHERE user_id = ? ORDER BY id ASC LIMIT 1", (user_id,))
        
        return {
            "user_id": user_row[0],
            "username": user_row[1],
            "first_name": user_row[2],
            "last_name": user_row[3],
            "first_seen": user_row[4],
            "blocked": user_row[5] if len(user_row) > 5 else 0,
            "current_share_code": user_row[6] if len(user_row) > 6 else None,
            "collections_count": collections_count,
            "items_count": items_count
        }


# --- Collection Sharing Functions ---

def generate_share_code() -> str:
    """
    Generate a unique random share code using [A-Za-z0-9] characters.
    Length: 15-20 characters.
    Ensures uniqueness by checking against existing codes.
    """
    import random
    import string
    
    chars = string.ascii_letters + string.digits  # A-Za-z0-9
    
    with db_transaction(commit=False) as (conn, cur):
        max_attempts = 100
        for _ in range(max_attempts):
            # Generate random length between 15-20
            length = random.randint(15, 20)
            code = ''.join(random.choice(chars) for _ in range(length))
            
            # Check if code already exists
            cur.execute("SELECT COUNT(*) FROM shared_collections WHERE share_code = ?", (code,))
            count = cur.fetchone()[0]
            
            if count == 0:
                return code
        
        raise RuntimeError("Failed to generate unique share code after maximum attempts")


def create_share_link(collection_id: int, user_id: int) -> str:
    """
    Create a share code for a collection.
    If a share code already exists for this collection, return it.
    Otherwise, generate a new one.
    """
    with db_transaction() as (conn, cur):
        # Check if active share code already exists
        cur.execute("""
            SELECT share_code FROM shared_collections 
            WHERE collection_id = ? AND is_active = 1
        """, (collection_id,))
        row = cur.fetchone()
        
        if row:
            return row[0]
        
        # Generate new share code
        share_code = generate_share_code()
        created_at = datetime.now().isoformat()
        
        cur.execute("""
            INSERT INTO shared_collections (collection_id, share_code, created_at, created_by, is_active)
            VALUES (?, ?, ?, ?, 1)
        """, (collection_id, share_code, created_at, user_id))
        
        return share_code


def get_collection_by_share_code(share_code: str) -> tuple | None:
    """
    Get collection details by share code.
    Returns (collection_id, collection_name, owner_user_id) if valid and active.
    Returns None if code is invalid or inactive.
    """
    with db_transaction(commit=False) as (conn, cur):
        cur.execute("""
            SELECT c.id, c.name, c.user_id
            FROM collections c
            JOIN shared_collections sc ON c.id = sc.collection_id
            WHERE sc.share_code = ? AND sc.is_active = 1
        """, (share_code,))
        return cur.fetchone()


def revoke_share_code(collection_id: int, user_id: int) -> bool:
    """
    Revoke (deactivate) the share code for a collection.
    Only the owner can revoke.
    """
    with db_transaction() as (conn, cur):
        # Verify ownership
        cur.execute("SELECT user_id FROM collections WHERE id = ?", (collection_id,))
        row = cur.fetchone()
        
        if not row or row[0] != user_id:
            return False
        
        # Deactivate share code
        cur.execute("""
            UPDATE shared_collections 
            SET is_active = 0 
            WHERE collection_id = ?
        """, (collection_id,))
        return cur.rowcount > 0


def regenerate_share_code(collection_id: int, user_id: int) -> str:
    """
    Regenerate a new share code for a collection.
    Deactivates the old code and creates a new one.
    """
    # Revoke old code
    revoke_share_code(collection_id, user_id)
    
    # Create new code
    return create_share_link(collection_id, user_id)


# get_share_code_for_collection removed (dead code)


def log_share_access(share_code: str, user_id: int):
    """
    Log when a user accesses a shared collection.
    """
    with db_transaction() as (conn, cur):
        accessed_at = datetime.now().isoformat()
        cur.execute("""
            INSERT INTO shared_collection_access_log (share_code, user_id, accessed_at)
            VALUES (?, ?, ?)
        """, (share_code, user_id, accessed_at))


def get_share_access_logs(collection_id: int) -> list:
    """
    Get access logs for a shared collection.
    Returns list of (user_id, username, first_name, accessed_at) tuples.
    """
    with db_transaction(commit=False) as (conn, cur):
        cur.execute("""
            SELECT sal.user_id, u.username, u.first_name, sal.accessed_at
            FROM shared_collection_access_log sal
            JOIN shared_collections sc ON sal.share_code = sc.share_code
            LEFT JOIN users u ON sal.user_id = u.user_id
            WHERE sc.collection_id = ? AND sc.is_active = 1
            ORDER BY sal.accessed_at DESC
        """, (collection_id,))
        return cur.fetchall()


# --- Admin Shares Management Functions ---

def get_all_active_shares() -> list:
    """
    Get all active shares for admin panel.
    Returns list with share details and access counts.
    """
    with db_transaction(commit=False) as (conn, cur):
        cur.execute("""
            SELECT 
                sc.id, sc.share_code, sc.collection_id, c.name as collection_name,
                sc.created_by, u.username as creator_username,
                sc.created_at,
                COUNT(DISTINCT sal.user_id) as unique_users,
                COUNT(sal.id) as total_accesses
            FROM shared_collections sc
            JOIN collections c ON sc.collection_id = c.id
            LEFT JOIN users u ON sc.created_by = u.user_id
            LEFT JOIN shared_collection_access_log sal ON sc.share_code = sal.share_code
            WHERE sc.is_active = 1
            GROUP BY sc.id, sc.share_code, sc.collection_id, c.name, sc.created_by, u.username, sc.created_at
            ORDER BY sc.created_at DESC
        """)
        return cur.fetchall()


def get_share_stats(share_code: str) -> dict:
    """
    Get statistics for a specific share code.
    Returns dict with: unique_users, total_accesses, last_access
    """
    with db_transaction(commit=False) as (conn, cur):
        # Count unique users
        cur.execute("""
            SELECT COUNT(DISTINCT user_id)
            FROM shared_collection_access_log
            WHERE share_code = ?
        """, (share_code,))
        unique_users = cur.fetchone()[0]
        
        # Count total accesses
        cur.execute("""
            SELECT COUNT(*)
            FROM shared_collection_access_log
            WHERE share_code = ?
        """, (share_code,))
        total_accesses = cur.fetchone()[0]
        
        # Get last access time
        cur.execute("""
            SELECT MAX(accessed_at)
            FROM shared_collection_access_log
            WHERE share_code = ?
        """, (share_code,))
        last_access_row = cur.fetchone()
        last_access = last_access_row[0] if last_access_row and last_access_row[0] else None
        
        return {
            "unique_users": unique_users,
            "total_accesses": total_accesses,
            "last_access": last_access
        }


def get_detailed_access_log(share_code: str, offset: int = 0, limit: int = 50) -> list:
    """
    Get detailed access log for a share code with pagination.
    Returns list of tuples: (user_id, username, first_name, accessed_at)
    """
    with db_transaction(commit=False) as (conn, cur):
        cur.execute("""
            SELECT sal.user_id, u.username, u.first_name, sal.accessed_at
            FROM shared_collection_access_log sal
            LEFT JOIN users u ON sal.user_id = u.user_id
            WHERE sal.share_code = ?
            ORDER BY sal.accessed_at DESC
            LIMIT ? OFFSET ?
        """, (share_code, limit, offset))
        return cur.fetchall()



# get_share_by_collection helper
# get_share_by_collection removed (dead code)


# --- User Session Persistence Functions ---

def set_user_active_share(user_id: int, share_code: str | None) -> bool:
    """
    Update the user's currently active share code in the DB.
    Used to restore session state on bot restart.
    """
    with db_transaction() as (conn, cur):
        try:
            cur.execute("UPDATE users SET current_share_code = ? WHERE user_id = ?", (share_code, user_id))
            return cur.rowcount > 0
        except Exception:
            return False


def get_users_with_active_shares() -> dict:
    """
    Get all users who have an active share code set.
    Returns dict: {user_id: share_code}
    """
    with db_transaction(commit=False) as (conn, cur):
        # Only select where current_share_code is not null
        # And ensure the share code is still active in shared_collections? 
        # For now, just load what's in users table, validation happens on access anyway eventually.
        cur.execute("SELECT user_id, current_share_code FROM users WHERE current_share_code IS NOT NULL")
        rows = cur.fetchall()
        return {row[0]: row[1] for row in rows}



# --- Archive Info Functions ---


# Archive Info Functions (Removed as dead code)


# --- Share Expiration Functions ---

def set_share_expiration(collection_id: int, user_id: int, expires_at: str | None) -> bool:
    """
    Set or update expiration time for a shared collection.
    
    Args:
        collection_id: The collection ID
        user_id: The owner user ID (for verification)
        expires_at: ISO format UTC datetime string, or None for no expiration
        
    Returns:
        True if updated successfully, False otherwise
    """
    with db_transaction() as (conn, cur):
        # Verify ownership
        cur.execute("SELECT user_id FROM collections WHERE id = ?", (collection_id,))
        row = cur.fetchone()
        if not row or row[0] != user_id:
            return False
        
        cur.execute("""
            UPDATE shared_collections 
            SET expires_at = ? 
            WHERE collection_id = ? AND is_active = 1
        """, (expires_at, collection_id))
        return cur.rowcount > 0


def get_share_expiration(collection_id: int) -> str | None:
    """
    Get the expiration time for a shared collection.
    
    Returns:
        ISO format datetime string, or None if no expiration set
    """
    with db_transaction(commit=False) as (conn, cur):
        cur.execute("""
            SELECT expires_at FROM shared_collections 
            WHERE collection_id = ? AND is_active = 1
        """, (collection_id,))
        row = cur.fetchone()
        return row[0] if row else None


def log_shared_message(share_code: str, user_id: int, chat_id: int, message_id: int):
    """
    Log a message sent during a shared session for later cleanup.
    Uses INSERT OR IGNORE to prevent duplicate entries.
    """
    with db_transaction() as (conn, cur):
        cur.execute("""
            INSERT OR IGNORE INTO shared_messages_to_delete (share_code, user_id, chat_id, message_id)
            VALUES (?, ?, ?, ?)
        """, (share_code, user_id, chat_id, message_id))


def get_expired_shares() -> list:
    """
    Get all active shares that have expired.
    
    Returns:
        List of tuples: (share_code, collection_id, created_by, expires_at)
    """
    with db_transaction(commit=False) as (conn, cur):
        now = datetime.now().isoformat()
        cur.execute("""
            SELECT share_code, collection_id, created_by, expires_at
            FROM shared_collections
            WHERE is_active = 1 
            AND expires_at IS NOT NULL 
            AND expires_at <= ?
        """, (now,))
        return cur.fetchall()


def get_messages_for_share(share_code: str, limit: int = 100) -> list:
    """
    Get all tracked messages for a share code.
    
    Returns:
        List of tuples: (user_id, chat_id, message_id)
    """
    with db_transaction(commit=False) as (conn, cur):
        cur.execute("""
            SELECT DISTINCT user_id, chat_id, message_id
            FROM shared_messages_to_delete
            WHERE share_code = ?
            LIMIT ?
        """, (share_code, limit))
        return cur.fetchall()


# delete_shared_messages_record removed (dead code)


def delete_single_message_record(message_id: int, chat_id: int) -> int:
    """
    Delete a single message record from tracking.
    """
    with db_transaction() as (conn, cur):
        cur.execute("""
            DELETE FROM shared_messages_to_delete 
            WHERE message_id = ? AND chat_id = ?
        """, (message_id, chat_id))
        return cur.rowcount


def purge_old_share_data(access_log_days: int = 90) -> tuple[int, int]:
    """Remove tracking rows for inactive shares and old access-log history."""
    with db_transaction() as (conn, cur):
        cur.execute("""
            DELETE FROM shared_messages_to_delete
            WHERE share_code IN (
                SELECT share_code FROM shared_collections WHERE is_active = 0
            )
        """)
        message_rows = cur.rowcount
        cutoff = (datetime.now() - timedelta(days=access_log_days)).isoformat()
        cur.execute(
            "DELETE FROM shared_collection_access_log WHERE accessed_at < ?", (cutoff,)
        )
        return message_rows, cur.rowcount


def deactivate_share_by_code(share_code: str) -> bool:
    """
    Deactivate a share directly by share_code.
    Used by the expiration cleanup job.
    
    Returns:
        True if share was deactivated
    """
    try:
        with db_transaction() as (conn, cur):
            cur.execute("""
                UPDATE shared_collections 
                SET is_active = 0 
                WHERE share_code = ?
            """, (share_code,))
            rows = cur.rowcount
            # commit handled by db_transaction()
            return rows > 0
    except Exception:
        return False


# count_active_shares_with_expiration removed (dead code)


# --- Duplicate Scanner Functions ---

def get_all_items_for_duplicate_scan(collection_id: int) -> list:
    """
    Fetch all items in a collection that are eligible for duplicate detection.
    Only returns items with a file_id (skips text-only items).

    The duplicate algorithm does not need Telegram's often-long ``file_id``,
    so it is intentionally omitted to keep a large scan compact in memory.

    Returns:
        List of tuples: (id, content_type, file_size, file_name, file_unique_id)
    """
    with db_transaction(commit=False) as (conn, cur):
        # Filter unique rows in SQLite first.  On a normal collection this
        # returns only a tiny subset, instead of building Python objects for
        # every stored file just to discard them as non-duplicates.
        cur.execute(
            """
            WITH
            forward_keys AS (
                SELECT content_type, file_unique_id
                FROM items
                WHERE collection_id = ? AND file_id IS NOT NULL
                  AND file_unique_id IS NOT NULL
                GROUP BY content_type, file_unique_id
                HAVING COUNT(*) > 1
            ),
            reupload_keys AS (
                SELECT content_type, file_size, COALESCE(file_name, '') AS file_name
                FROM items
                WHERE collection_id = ? AND file_id IS NOT NULL
                  AND file_size IS NOT NULL
                GROUP BY content_type, file_size, COALESCE(file_name, '')
                HAVING COUNT(*) > 1
            )
            SELECT DISTINCT i.id, i.content_type, i.file_size, i.file_name, i.file_unique_id
            FROM items i
            LEFT JOIN forward_keys f
              ON f.content_type = i.content_type AND f.file_unique_id = i.file_unique_id
            LEFT JOIN reupload_keys r
              ON r.content_type = i.content_type AND r.file_size = i.file_size
             AND r.file_name = COALESCE(i.file_name, '')
            WHERE i.collection_id = ? AND i.file_id IS NOT NULL
              AND (f.file_unique_id IS NOT NULL OR r.file_size IS NOT NULL)
            ORDER BY i.id
            """,
            (collection_id, collection_id, collection_id),
        )
        return cur.fetchall()


def delete_items_by_ids(item_ids: list[int]) -> int:
    """
    Delete multiple items by their IDs in batched transactions to avoid SQLite limits.

    Args:
        item_ids: List of item IDs to delete

    Returns:
        Number of rows deleted
    """
    if not item_ids:
        return 0

    total_deleted = 0
    batch_size = 500
    with db_transaction() as (conn, cur):
        for i in range(0, len(item_ids), batch_size):
            chunk = item_ids[i:i + batch_size]
            placeholders = ",".join("?" * len(chunk))
            cur.execute(
                f"DELETE FROM items WHERE id IN ({placeholders})",
                chunk
            )
            total_deleted += cur.rowcount
    return total_deleted



def get_collection_stats(collection_id: int) -> dict:
    """
    Get detailed statistics for a collection.

    Returns a dict with:
        - created_at: ISO datetime string of the earliest item added (or None)
        - collection_created_at: (not stored in DB directly, so same as first item)
        - video_count: number of video items
        - photo_count: number of photo items
        - document_count: number of document items
        - text_count: number of text-only items
        - total_count: total items
        - video_duration_seconds: total video duration in seconds (always 0 — Telegram doesn't store it)
        - video_size_bytes: total size of video files
        - photo_size_bytes: total size of photo files
        - document_size_bytes: total size of document files
        - total_size_bytes: total size of all files
        - first_item_date: ISO datetime of first added item
        - last_item_date: ISO datetime of most recently added item
    """
    with db_transaction(commit=False) as (conn, cur):
        # Per-type counts and sizes
        cur.execute(
            """
            SELECT
                content_type,
                COUNT(*) as cnt,
                SUM(COALESCE(file_size, 0)) as total_size
            FROM items
            WHERE collection_id = ?
            GROUP BY content_type
            """,
            (collection_id,)
        )
        rows = cur.fetchall()

        stats = {
            "video_count": 0,
            "photo_count": 0,
            "document_count": 0,
            "text_count": 0,
            "total_count": 0,
            "video_size_bytes": 0,
            "photo_size_bytes": 0,
            "document_size_bytes": 0,
            "total_size_bytes": 0,
            "first_item_date": None,
            "last_item_date": None,
        }

        for content_type, cnt, total_size in rows:
            size = total_size or 0
            stats["total_count"] += cnt
            stats["total_size_bytes"] += size
            if content_type == "video":
                stats["video_count"] = cnt
                stats["video_size_bytes"] = size
            elif content_type == "photo":
                stats["photo_count"] = cnt
                stats["photo_size_bytes"] = size
            elif content_type == "document":
                stats["document_count"] = cnt
                stats["document_size_bytes"] = size
            elif content_type == "text":
                stats["text_count"] = cnt

        # First and last item dates
        cur.execute(
            "SELECT MIN(added_at), MAX(added_at) FROM items WHERE collection_id = ?",
            (collection_id,)
        )
        date_row = cur.fetchone()
        if date_row:
            stats["first_item_date"] = date_row[0]
            stats["last_item_date"] = date_row[1]

        return stats


def search_items(user_id: int, query_text: str, is_admin_user: bool = False) -> list:
    """
    Search items in collections that the user has access to.
    If is_admin_user is True, searches all collections.
    Returns:
        List of tuples: (id, content_type, file_id, text_content, file_name, collection_name)
    """
    q = f"%{query_text}%"
    with db_transaction(commit=False) as (conn, cur):
        if is_admin_user:
            cur.execute(
                """
                SELECT i.id, i.content_type, i.file_id, i.text_content, i.file_name, c.name
                FROM items i
                JOIN collections c ON i.collection_id = c.id
                WHERE i.file_name LIKE ? OR i.text_content LIKE ?
                ORDER BY i.id DESC
                LIMIT 50
                """,
                (q, q)
            )
        else:
            # Get allowed collections: owned + active shared
            owned = get_collections(user_id)
            allowed_cols = [col[0] for col in owned]
            
            # Check active shared collection
            cur.execute("SELECT current_share_code FROM users WHERE user_id = ?", (user_id,))
            user_row = cur.fetchone()
            if user_row and user_row[0]:
                share_code = user_row[0]
                cur.execute("SELECT collection_id FROM shared_collections WHERE share_code = ? AND is_active = 1", (share_code,))
                sc_row = cur.fetchone()
                if sc_row:
                    allowed_cols.append(sc_row[0])
            
            if not allowed_cols:
                return []
                
            placeholders = ",".join("?" for _ in allowed_cols)
            params = allowed_cols + [q, q]
            cur.execute(
                f"""
                SELECT i.id, i.content_type, i.file_id, i.text_content, i.file_name, c.name
                FROM items i
                JOIN collections c ON i.collection_id = c.id
                WHERE i.collection_id IN ({placeholders})
                  AND (i.file_name LIKE ? OR i.text_content LIKE ?)
                ORDER BY i.id DESC
                LIMIT 50
                """,
                params
            )
        return cur.fetchall()


