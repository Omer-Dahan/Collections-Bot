"""
Duplicate file scanner handlers for the Collections Bot.

Scanning logic:
- NEW items (with file_unique_id stored): compared by (content_type, file_unique_id).
  This is the accurate path — Telegram guarantees file_unique_id is stable per file.
- LEGACY items (file_unique_id is NULL, added before this feature):
  compared by (content_type, file_size) as a best-effort fallback.

Within each duplicate group the first item (lowest ID) is kept as the "original"
and all subsequent items are marked as duplicates to be deleted.
"""
import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

import db
from utils import parse_callback_data, parse_and_validate_access

logger = logging.getLogger(__name__)

# Storage key used in context.user_data to persist the list of duplicate IDs
_PENDING_DELETE_KEY = "pending_duplicate_ids"


# ---------------------------------------------------------------------------
# Duplicate detection logic
# ---------------------------------------------------------------------------

def _compute_duplicates(items: list) -> tuple[list[list[tuple]], int, int]:
    """
    Detect duplicate groups from a flat list of item tuples.

    Tuple format: (id, content_type, file_id, file_size, file_name, file_unique_id)

    Strategy (per item type):
    - If file_unique_id is available → compare by (content_type, file_unique_id).
      This is the reliable path: Telegram guarantees this ID is stable per file content.
    - If file_unique_id is NULL (legacy items added before this feature) →
      compare by (content_type, file_size) as a best-effort fallback.
      Note: different files can theoretically share a size, so this may
      produce occasional false positives, but it is the only option
      without re-fetching metadata from Telegram.

    Returns:
        (groups, reliable_count, fallback_count)
        - groups: list of duplicate groups (each group ≥ 2 items)
        - reliable_count: items compared by file_unique_id
        - fallback_count: items compared by file_size (legacy)
    """
    reliable: dict[tuple, list] = {}   # key = (content_type, file_unique_id)
    fallback: dict[tuple, list] = {}   # key = (content_type, file_size)
    reliable_count = 0
    fallback_count = 0

    for item in items:
        item_id, content_type, file_id, file_size, file_name, file_unique_id = item

        # Skip non-scannable types
        if content_type not in ("video", "photo", "document", "audio"):
            continue

        if file_unique_id:
            # Reliable path
            key = (content_type, file_unique_id)
            reliable.setdefault(key, []).append(item)
            reliable_count += 1
        elif file_size is not None:
            # Fallback path for legacy items
            key = (content_type, file_size)
            fallback.setdefault(key, []).append(item)
            fallback_count += 1
        # else: no usable data — skip

    # Collect only groups with ≥ 2 members
    groups = (
        [g for g in reliable.values() if len(g) > 1] +
        [g for g in fallback.values() if len(g) > 1]
    )
    return groups, reliable_count, fallback_count


# Keep old per-type functions removed — replaced by _compute_duplicates above






# ---------------------------------------------------------------------------
# Report formatting & pagination
# ---------------------------------------------------------------------------

_TYPE_EMOJI = {
    "video": "🎬",
    "photo": "🖼️",
    "document": "📄",
    "audio": "🎵",
}

_PAGE_SIZE = 30          # duplicate groups per page
_GROUPS_KEY = "duplicate_groups_cache"   # key in context.user_data


def _build_page_text(
    groups_page: list,
    page: int,
    total_pages: int,
    total_groups: int,
    total_dupes: int,
    reliable_count: int,
    fallback_count: int,
    page_offset: int,      # index of the first group on this page (0-based)
) -> str:
    """Build the text for a single page of the duplicate report."""
    lines = [
        "🔍 *דוח כפולים*",
        f"נמצאו *{total_groups}* קבוצות | *{total_dupes}* קבצים לא נחוצים",
        f"עמוד *{page}/{total_pages}*",
    ]

    # Scan method footnote
    if fallback_count > 0 and reliable_count == 0:
        lines.append("⚠️ _סריקה לפי גודל קובץ (פריטים ישנים — ללא מזהה ייחודי)._")
    elif fallback_count > 0:
        lines.append(
            f"🔶 _{reliable_count} מדויק, {fallback_count} לפי גודל (ישנים)._"
        )

    lines += [
        "",
        "💡 שלח מספר ID כדי לצפות בקובץ",
        "─────────────────────",
    ]

    for local_idx, group in enumerate(groups_page):
        global_idx = page_offset + local_idx + 1
        content_type = group[0][1]
        emoji = _TYPE_EMOJI.get(content_type, "📁")
        count = len(group)
        lines.append(f"\n{emoji} *קבוצה {global_idx}* ({content_type}) — {count} עותקים")

        for pos, item in enumerate(group):
            item_id = item[0]
            label = "✅ מקור " if pos == 0 else "❌ כפול"
            lines.append(f"  {label} | ID: `{item_id}`")

    return "\n".join(lines)


def _build_page_keyboard(
    col_id: int,
    page: int,
    total_pages: int,
    total_dupes: int,
) -> InlineKeyboardMarkup:
    """Build navigation + action keyboard for a report page."""
    keyboard = []

    # Navigation row (prev / next)
    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("◀️", callback_data=f"dupes_page:{col_id}:{page - 1}"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("▶️", callback_data=f"dupes_page:{col_id}:{page + 1}"))
    if nav_row:
        keyboard.append(nav_row)

    # Delete + Cancel
    keyboard.append([InlineKeyboardButton(
        f"🗑 מחק {total_dupes} כפולים",
        callback_data=f"confirm_delete_dupes:{col_id}"
    )])
    keyboard.append([InlineKeyboardButton("❌ ביטול", callback_data=f"manage_collection:{col_id}")])

    return InlineKeyboardMarkup(keyboard)


def _render_page_data(context, col_id: int, page: int) -> tuple:
    """
    Read cached groups from user_data and compute all values needed to render a page.
    Returns (text, keyboard) or (None, None) if no cached data.
    """
    all_groups = context.user_data.get(_GROUPS_KEY)
    duplicate_ids = context.user_data.get(_PENDING_DELETE_KEY, [])
    reliable_count = context.user_data.get("dup_reliable_count", 0)
    fallback_count = context.user_data.get("dup_fallback_count", 0)

    if all_groups is None:
        return None, None

    total_groups = len(all_groups)
    total_pages = max(1, -(-total_groups // _PAGE_SIZE))  # ceiling division
    page = max(1, min(page, total_pages))

    page_offset = (page - 1) * _PAGE_SIZE
    groups_page = all_groups[page_offset: page_offset + _PAGE_SIZE]

    text = _build_page_text(
        groups_page, page, total_pages, total_groups,
        len(duplicate_ids), reliable_count, fallback_count, page_offset
    )
    keyboard = _build_page_keyboard(col_id, page, total_pages, len(duplicate_ids))
    return text, keyboard


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def handle_scan_duplicates_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """
    Entry point: user pressed the 'Scan Duplicates' button inside collection management.
    Scans the collection and sends the first page of the duplicate report.
    """
    query = update.callback_query
    await query.answer("⏳ סורק כפולים...")

    parts = parse_callback_data(query.data, "scan_duplicates")
    is_allowed, col_id, _ = await parse_and_validate_access(update, context, parts)
    if not is_allowed:
        return

    # Fetch all scannable items
    items = db.get_all_items_for_duplicate_scan(col_id)

    if not items:
        await query.edit_message_text(
            "האוסף ריק או שאין בו קבצים הניתנים לסריקה.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 חזור לניהול", callback_data=f"manage_collection:{col_id}")]
            ])
        )
        return

    duplicate_groups, reliable_count, fallback_count = _compute_duplicates(items)

    if not duplicate_groups:
        note = ""
        if fallback_count > 0:
            note = "\n\n⚠️ _הסריקה בוצעה לפי גודל קובץ (פריטים ישנים)._"
        await query.edit_message_text(
            f"✅ לא נמצאו קבצים כפולים באוסף זה!{note}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 חזור לניהול", callback_data=f"manage_collection:{col_id}")]
            ])
        )
        return

    # Collect IDs of all duplicates (not originals)
    duplicate_ids = [item[0] for group in duplicate_groups for item in group[1:]]

    # Cache in session for pagination and deletion
    context.user_data[_GROUPS_KEY] = duplicate_groups
    context.user_data[_PENDING_DELETE_KEY] = duplicate_ids
    context.user_data["duplicate_scan_col_id"] = col_id
    context.user_data["dup_reliable_count"] = reliable_count
    context.user_data["dup_fallback_count"] = fallback_count

    # Render page 1
    text, keyboard = _render_page_data(context, col_id, page=1)
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)


async def handle_dupes_page_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Navigate between pages of the duplicate report."""
    query = update.callback_query
    await query.answer()

    parts = parse_callback_data(query.data, "dupes_page")
    is_allowed, col_id, _ = await parse_and_validate_access(update, context, parts)
    if not is_allowed:
        return

    try:
        page = int(parts[1])
    except (ValueError, IndexError):
        return

    text, keyboard = _render_page_data(context, col_id, page)

    if text is None:
        # Cache expired (bot restarted) — ask user to rescan
        await query.edit_message_text(
            "⚠️ פג תוקף תוצאות הסריקה. אנא סרוק שוב.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 חזור לניהול", callback_data=f"manage_collection:{col_id}")]
            ])
        )
        return

    try:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
    except Exception:  # pylint: disable=broad-exception-caught
        pass  # Ignore "message not modified" errors


async def handle_confirm_delete_dupes_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """
    User confirmed deletion of all duplicate items.
    Deletes them from the database and sends a summary.
    """
    query = update.callback_query
    await query.answer("🗑 מוחק כפולים...")

    parts = parse_callback_data(query.data, "confirm_delete_dupes")
    is_allowed, col_id, col = await parse_and_validate_access(update, context, parts)
    if not is_allowed:
        return

    # Retrieve and clear pending data
    duplicate_ids = context.user_data.pop(_PENDING_DELETE_KEY, [])
    context.user_data.pop(_GROUPS_KEY, None)
    context.user_data.pop("duplicate_scan_col_id", None)
    context.user_data.pop("dup_reliable_count", None)
    context.user_data.pop("dup_fallback_count", None)

    if not duplicate_ids:
        await query.edit_message_text(
            "⚠️ לא נמצאו נתונים לסריקה. אנא סרוק שוב.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 חזור לניהול", callback_data=f"manage_collection:{col_id}")]
            ])
        )
        return

    deleted_count = db.delete_items_by_ids(duplicate_ids)
    remaining = db.count_items_in_collection(col_id)

    text = (
        f"✅ *{deleted_count} קבצים כפולים נמחקו בהצלחה!*\n\n"
        f"📦 האוסף *{col[1]}* מכיל כעת *{remaining}* קבצים."
    )

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 חזור לניהול", callback_data=f"manage_collection:{col_id}")]
        ])
    )

    logger.info(
        "User %s deleted %d duplicate items from collection %d (%s)",
        query.from_user.id, deleted_count, col_id, col[1]
    )

