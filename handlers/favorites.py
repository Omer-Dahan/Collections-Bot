"""
Per-user favorites inside a collection: a paged list view (send by group or
whole page) and a scroll view that only walks the starred items.
"""
import math

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

import db
from message_tracker import track_if_shared
from utils import (
    parse_callback_data, parse_and_validate_access, send_response,
    prepare_media_groups, send_media_groups_in_chunks, logger, start_user_job,
)
from .browse_handlers import show_scroll_item, build_scroll_keyboard

FAV_PAGE_SIZE = 100
FAV_GROUP_SIZE = 10


async def show_favorites_page(context, chat_id, user_id, collection, page, edit_message_id=None):
    col_id, col_name = collection[0], collection[1]
    total = db.count_favorites(user_id, col_id)
    back_row = [InlineKeyboardButton("🔙 חזור לאוסף", callback_data=f"browse_page:{col_id}:1")]

    if total == 0:
        text = (
            f"⭐ מועדפים — {col_name}\n\n"
            "עדיין לא סימנת פריטים באוסף הזה.\n"
            "אפשר לסמן פריט בכפתור ☆ בתצוגת הגלילה."
        )
        return await send_response(
            context.bot, chat_id, text,
            reply_markup=InlineKeyboardMarkup([back_row]),
            edit_message_id=edit_message_id, allow_delete_on_edit_fail=True, user_id=user_id
        )

    total_pages = math.ceil(total / FAV_PAGE_SIZE)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * FAV_PAGE_SIZE
    in_page = min(FAV_PAGE_SIZE, total - offset)

    group_buttons = [
        InlineKeyboardButton(
            str((page - 1) * 10 + g), callback_data=f"fav_group:{col_id}:{page}:{g}"
        )
        for g in range(1, math.ceil(in_page / FAV_GROUP_SIZE) + 1)
    ]

    # Group 0 means "the whole page"
    keyboard = [[InlineKeyboardButton("📨 שלח את כל העמוד", callback_data=f"fav_group:{col_id}:{page}:0")]]
    keyboard += [row for row in (group_buttons[:5], group_buttons[5:]) if row]

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅ עמוד קודם", callback_data=f"fav_page:{col_id}:{page - 1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("עמוד הבא ➡", callback_data=f"fav_page:{col_id}:{page + 1}"))
    if nav:
        keyboard.append(nav)

    keyboard.append([InlineKeyboardButton("🔄 צפייה בגלילה", callback_data=f"fav_scroll:{col_id}:0")])
    keyboard.append(back_row)

    text = (
        f"⭐ מועדפים — {col_name}\n"
        f"✅ עמוד {page} מתוך {total_pages}\n"
        f"📦 מציג פריטים {offset + 1}-{offset + in_page} מתוך {total}"
    )
    return await send_response(
        context.bot, chat_id, text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        edit_message_id=edit_message_id, allow_delete_on_edit_fail=True, user_id=user_id
    )


async def handle_fav_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = parse_callback_data(query.data, "fav_page")
    if not parts or len(parts) < 2:
        return

    is_allowed, _, collection = await parse_and_validate_access(update, context, parts)
    if not is_allowed:
        return

    try:
        page = int(parts[1])
    except ValueError:
        return

    await show_favorites_page(
        context, query.message.chat_id, query.from_user.id, collection, page,
        edit_message_id=query.message.message_id
    )


async def handle_fav_group_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send one group of favorites (or the whole page when group is 0)."""
    query = update.callback_query

    parts = parse_callback_data(query.data, "fav_group")
    if not parts or len(parts) < 3:
        await query.answer()
        return

    is_allowed, col_id, collection = await parse_and_validate_access(update, context, parts)
    try:
        page, group = int(parts[1]), int(parts[2])
    except ValueError:
        is_allowed = False
    if not is_allowed:
        await query.answer()
        return

    user_id = query.from_user.id
    chat_id = query.message.chat_id

    items = db.get_favorite_items(
        user_id, col_id, offset=(page - 1) * FAV_PAGE_SIZE, limit=FAV_PAGE_SIZE
    )
    if group:
        start = (group - 1) * FAV_GROUP_SIZE
        items = items[start:start + FAV_GROUP_SIZE]

    if not items:
        await query.answer("אין פריטים לשליחה.", show_alert=True)
        return

    async def _send_job():
        status = await context.bot.send_message(chat_id=chat_id, text=f"🚀 שולח {len(items)} פריטים...")
        track_if_shared(user_id, chat_id, status.message_id)

        media_v, media_d, texts = prepare_media_groups(items)
        await send_media_groups_in_chunks(context.bot, chat_id, media_v, media_d, texts, user_id=user_id)

        # Drop the progress note and the old menu so the fresh menu lands under the media
        for msg in (status, query.message):
            try:
                await msg.delete()
            except Exception as e: # pylint: disable=broad-exception-caught
                logger.debug("Failed to delete message %s: %s", msg.message_id, e)

        await show_favorites_page(context, chat_id, user_id, collection, page)

    if not start_user_job(context, user_id, _send_job()):
        await query.answer("⏳ כבר מתבצעת שליחה, המתן לסיומה.", show_alert=True)
        return
    await query.answer()


async def handle_fav_scroll_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = parse_callback_data(query.data, "fav_scroll")
    if not parts or len(parts) < 2:
        return

    is_allowed, col_id, _ = await parse_and_validate_access(update, context, parts)
    if not is_allowed:
        return

    try:
        idx = int(parts[1])
    except ValueError:
        return

    await show_scroll_item(query, context, col_id, idx, favorites=True)


async def handle_fav_random_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = parse_callback_data(query.data, "fav_random")
    is_allowed, col_id, _ = await parse_and_validate_access(update, context, parts)
    if not is_allowed:
        return

    await show_scroll_item(query, context, col_id, idx=None, favorites=True)


async def handle_fav_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Star/unstar the item shown in scroll view.
    Callback: fav_toggle:{col_id}:{item_id}:{mode}:{idx}, mode is "a" (whole
    collection) or "f" (favorites scroll).
    """
    query = update.callback_query

    parts = parse_callback_data(query.data, "fav_toggle")
    if not parts or len(parts) < 4:
        await query.answer()
        return

    is_allowed, col_id, _ = await parse_and_validate_access(update, context, parts)
    try:
        item_id, mode, idx = int(parts[1]), parts[2], int(parts[3])
    except ValueError:
        is_allowed = False
    if not is_allowed:
        await query.answer()
        return

    # The item id comes from the client, so make sure it really lives in this collection
    item = db.get_item_by_id(item_id)
    if not item or item[1] != col_id:
        await query.answer("הפריט לא נמצא.", show_alert=True)
        return

    user_id = query.from_user.id
    starred = db.toggle_favorite(user_id, item_id)
    in_favorites_view = mode == "f"

    if in_favorites_view and not starred:
        # The next favorite slides into this index, so just show it
        await query.answer("הוסר מהמועדפים")
        await show_scroll_item(query, context, col_id, idx, favorites=True)
        return

    await query.answer("נוסף למועדפים ⭐" if starred else "הוסר מהמועדפים")

    if in_favorites_view:
        total = db.count_favorites(user_id, col_id)
    else:
        total = db.count_items_in_collection(col_id)

    await query.edit_message_reply_markup(
        reply_markup=build_scroll_keyboard(col_id, idx, total, item_id, starred, in_favorites_view)
    )
