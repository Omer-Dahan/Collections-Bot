# Project Context & Documentation

This document consolidates information regarding the code structure, workflow, and the purpose of key functions in the "Collections Bot" project.

## Overview
The project is a Telegram bot allowing users to create "Collections" (virtual folders) and save messages (images, videos, documents, text) within them.
The bot manages data in a local database (SQLite) and provides a button-based user interface (Inline Buttons) for managing, browsing, and sharing collections.

---

## Main File Structure

### 1. `bot.py` - Entry Point
This is the main file that runs the bot.
- **Responsibilities:**
  - Loading environment variables (`config.py`).
  - Initializing the database (`db.init_db`).
  - Setting up Logging.
  - **Registering Handlers:** Connects Telegram commands (like `/start`) with their handling functions (in the `handlers/` directory).
  - Running the `JobQueue` for background tasks (such as cleaning up expired shares).
  - Running Polling against Telegram servers.

### 2. `db.py` - Database Layer
Manages all communication with the `bot_data.db` database.
- **Key Tables:**
  - `collections`: List of collections (name, user ID).
  - `items`: The stored items (File ID, content type, collection association).
  - `users`: User information (blocks, first seen).
  - `shared_collections`: Managing share links (share code, expiration).
  - `shared_collection_access_log`: Access log for shared collections.
  - `shared_messages_to_delete`: Tracking messages sent in a shared session for automatic deletion.
- **Key Functions:**
  - `add_item(...)`: Adds a file to a collection.
  - `get_items_by_collection(...)`: Retrieves items (supports Pagination).
  - `create_share_link(...)`: Generates a unique code for sharing a collection.
  - `check_expired_shares_job`: (Defined in `bot.py` but uses queries from here) for expiration management.

### 3. `utils.py` - Utilities & Logic
Contains reusable helper functions for the entire project, primarily focused on UI construction.
- **User Interface (UI):**
  - `build_collection_keyboard`: Generates a button keyboard for selecting a collection.
  - `build_page_menu` & `get_page_header`: Manages collection browsing display (Page 1 of X, Next/Back buttons).
  - `send_response`: A smart function that sends a new message or edits an existing one to prevent screen flickering.
- **Media Sending:**
  - `safe_send_media_group`: Sends an album of photos/videos handling load errors (FloodLimits).
  - `send_media_groups_in_chunks`: Splits sending a large number of files into smaller batches.
- **Validation & Control:**
  - `check_collection_access`: Checks if a user has permission for a collection (owner or shared).
  - `track_and_reset_user`: Resets temporary states (like "delete" or "add" mode) when switching menus.

### 4. `handlers/` - Request Handlers
This directory contains business logic, categorized by topic (inferred from imports in `bot.py`):
- **Commands (`commands.py`):** `/start`, `/newcollection`, etc.
- **Callbacks (`callbacks.py`):** Button clicks (browsing, selecting collections, management).
- **Messages (`messages.py`):** Handling file reception from users and saving to the active collection.

---

## Core Workflows

### A. Creating a Collection & Adding Content
1. User clicks "Create New Collection".
2. Bot asks for a collection name and saves it to DB (`create_collection`).
3. Bot sets the collection as "active" for the user (`active_collections`).
4. Any file/message the user sends now:
   - Passes through `handle_message`.
   - File type is identified (video/photo/doc) via `extract_file_info` in `utils.py`.
   - Saved to DB in the `items` table.
   - User receives feedback (status message updated in background via `batch_status_loop`).

### B. Browsing Collections
1. User selects "Browse Collections".
2. `show_collections_menu` displays a list of collections.
3. Upon collection selection, the bot calls `show_collection_page`.
4. The function calculates pages (based on 100 items per virtual page, divided into groups of 10).
5. User sees a menu with numbers (1..10). Clicking a number sends that batch of files (`send_media_groups_in_chunks`).

### C. Sharing a Collection
1. User enters collection management and selects "Share".
2. Bot generates a unique code (`generate_share_code`) and saves it in `shared_collections`.
3. Another user clicks "Access Shared Collection" and enters the code.
4. System verifies validity, and if valid - allows browsing (read-only).
5. Messages sent to the viewer are logged in `shared_messages_to_delete` to allow automatic deletion when the share expires (security/privacy feature).

### D. Background Tasks
- `check_expired_shares_job`: Runs every minute. Checks for expired shares in DB, deletes messages sent to viewers, and invalidates the access code.

### E. Release Management
1. After every feature implementation or bug fix, the `/track-changes` workflow must be executed.
2. This workflow records a concise summary in `CHANGELOG_PENDING.md`.
3. This ensures that when a new version is released on GitHub, all changes are already documented and ready for the release notes.

---

## Summary
The bot is built in a modular and efficient manner, with a strong emphasis on user experience (editing messages instead of sending new ones), load handling (batched sending), and data security (permission management and temporary shares).
