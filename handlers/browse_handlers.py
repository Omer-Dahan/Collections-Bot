from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
import db
from utils import (
    show_collection_page, parse_callback_data, validate_access_wrapper,
    send_info_page, prepare_media_groups, send_media_groups_in_chunks,
    build_page_file_type_menu, check_collection_access, logger,
    parse_and_validate_access
)

async def handle_browse_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """תצוגת עמוד דפדוף: כל עמוד עד 100 פריטים, מחולק לקבוצות של 10"""
    query = update.callback_query
    await query.answer()

    parts = parse_callback_data(query.data, "browse_page")
    if not parts or len(parts) < 2:
        return

    await show_collection_page(
        update=update, context=context, collection_id=int(parts[0]),
        page=int(parts[1]), edit_message_id=query.message.message_id
    )

async def handle_scroll_view_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """צפייה בפריט יחיד עם כפתורי הבא/הקודם - מחיקת הודעה ושליחה חדשה"""
    query = update.callback_query
    await query.answer()

    parts = parse_callback_data(query.data, "scroll_view")
    if not parts or len(parts) < 2:
        return

    is_allowed, col_id, _ = await parse_and_validate_access(update, context, parts)
    if not is_allowed:
        return

    try:
        item_idx = int(parts[1])
    except (ValueError, IndexError):
        return

    total = db.count_items_in_collection(col_id)
    if total == 0:
        await query.edit_message_text("אין פריטים באוסף הזה.")
        return

    item_idx = max(0, min(item_idx, total - 1))
    items = db.get_items_by_collection(col_id, offset=item_idx, limit=1)
    if not items:
        await query.edit_message_text("פריט לא נמצא.")
        return

    item = items[0]
    # Build text/keyboard and send
    header = f"📄 פריט {item_idx + 1} מתוך {total}"
    if item[3]: # text_content
        header += f"\n\n{item[3]}"

    reply_markup = _build_scroll_keyboard(col_id, item_idx, total)

    try:
        await context.bot.delete_message(
            chat_id=query.message.chat_id, message_id=query.message.message_id
        )
    except Exception: # pylint: disable=broad-exception-caught
        pass

    try:
        await _send_scroll_item(context, query.message.chat_id, item, header, reply_markup)
    except Exception as e: # pylint: disable=broad-exception-caught
        logger.error("Error sending scroll item: %s", e)
        await context.bot.send_message(
            chat_id=query.message.chat_id, text=f"שגיאה בטעינת הפריט.\n\n{header}",
            reply_markup=reply_markup
        )

def _build_scroll_keyboard(col_id, idx, total):
    """Build navigation keyboard for scroll view."""
    nav = []
    if idx > 0:
        nav.append(InlineKeyboardButton("⬅ הקודם", callback_data=f"scroll_view:{col_id}:{idx-1}"))
    if idx < total - 1:
        nav.append(InlineKeyboardButton("הבא ➡", callback_data=f"scroll_view:{col_id}:{idx+1}"))

    kb = []
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton(
        "🎲", callback_data=f"random_video:{col_id}"
    )])
    kb.append([InlineKeyboardButton(
        "🔙 חזור לתפריט דפדוף", callback_data=f"browse_page:{col_id}:1"
    )])
    return InlineKeyboardMarkup(kb)

async def _send_scroll_item(context, chat_id, item, header, markup):
    """Helper to send the specific media type for scroll view."""
    c_type, f_id = item[1], item[2]
    bot = context.bot
    if c_type == "photo":
        await bot.send_photo(chat_id=chat_id, photo=f_id, caption=header, reply_markup=markup)
    elif c_type == "video":
        await bot.send_video(chat_id=chat_id, video=f_id, caption=header, reply_markup=markup)
    elif c_type == "document":
        await bot.send_document(chat_id=chat_id, document=f_id, caption=header, reply_markup=markup)
    else:
        await bot.send_message(chat_id=chat_id, text=header, reply_markup=markup)

async def handle_random_video_scroll_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a random item from the collection in scroll view."""
    import random
    query = update.callback_query

    parts = parse_callback_data(query.data, "random_video")
    is_allowed, col_id, _ = await parse_and_validate_access(update, context, parts)
    if not is_allowed:
        await query.answer("אין גישה.", show_alert=True)
        return

    total = db.count_items_in_collection(col_id)
    if total == 0:
        await query.answer("האוסף ריק.", show_alert=True)
        return

    # Pick a random index directly - O(1), no heavy query needed
    random_idx = random.randint(0, total - 1)
    items = db.get_items_by_collection(col_id, offset=random_idx, limit=1)
    if not items:
        await query.answer("שגיאה בטעינת הפריט.", show_alert=True)
        return

    # Acknowledge the callback only once, after we know we have data
    await query.answer()

    item = items[0]
    header = f"🎲 פריט {random_idx + 1} מתוך {total}"
    if item[3]:  # text_content
        header += f"\n\n{item[3]}"

    reply_markup = _build_scroll_keyboard(col_id, random_idx, total)

    try:
        await context.bot.delete_message(
            chat_id=query.message.chat_id, message_id=query.message.message_id
        )
    except Exception:  # pylint: disable=broad-exception-caught
        pass

    try:
        await _send_scroll_item(context, query.message.chat_id, item, header, reply_markup)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error sending random item: %s", e)
        await context.bot.send_message(
            chat_id=query.message.chat_id, text=f"שגיאה בטעינת הפריט.\n\n{header}",
            reply_markup=reply_markup
        )


async def handle_page_info_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """תצוגת מידע מפורט על קבצים בדף - 10 קבצים בכל פעם"""
    query = update.callback_query
    await query.answer()

    parts = parse_callback_data(query.data, "page_info")
    if not parts or len(parts) < 3:
        return

    is_allowed, col_id, _ = await parse_and_validate_access(update, context, parts)
    if not is_allowed:
        return

    try:
        page, i_page = int(parts[1]), int(parts[2])
    except (ValueError, IndexError):
        return

    await send_info_page(
        bot=context.bot, chat_id=query.message.chat_id, user_id=query.from_user.id,
        context=context, collection_id=col_id, page=page, info_page=i_page,
        edit_message_id=query.message.message_id
    )

async def handle_back_to_info_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """חזרה לרשימת המידע - מוחק את הודעת המידע הישנה ושולח רשימה חדשה"""
    query = update.callback_query
    await query.answer()

    parts = parse_callback_data(query.data, "back_to_info")
    if not parts or len(parts) < 3:
        return

    is_allowed, col_id, _ = await parse_and_validate_access(update, context, parts)
    if not is_allowed:
        return

    try:
        page, i_page = int(parts[1]), int(parts[2])
    except (ValueError, IndexError):
        return

    old_id = context.user_data.get("info_message_id")
    if old_id:
        try:
            await context.bot.delete_message(chat_id=query.message.chat_id, message_id=old_id)
        except Exception: # pylint: disable=broad-exception-caught
            pass

    await send_info_page(
        bot=context.bot, chat_id=query.message.chat_id, user_id=query.from_user.id,
        context=context, collection_id=col_id, page=page, info_page=i_page
    )

async def handle_browse_group_or_select_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """כפתורי המספרים (קבוצות) ו'בחר הכל' בעמוד דפדוף"""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("browse_group:"):
        parts = parse_callback_data(data, "browse_group")
        is_all = False
    elif data.startswith("browse_page_select_all:"):
        parts = parse_callback_data(data, "browse_page_select_all")
        is_all = True
    else:
        return

    is_allowed, col_id, _ = await parse_and_validate_access(update, context, parts)
    if not is_allowed:
        return

    try:
        page = int(parts[1])
        idx = int(parts[2]) if not is_all else 0
    except (ValueError, IndexError):
        return

    items = _get_items_scope(col_id, page, idx, is_all)
    if not is_all:
        await _process_group_send(update, context, col_id, page, idx, items)
    else:
        await _process_select_all_menu(query, context, col_id, page, items)

def _get_items_scope(col_id, page, idx, is_all):
    """Helper to get items for selected group/page."""
    block_size = 100
    items = db.get_items_by_collection(col_id, offset=(page - 1) * block_size, limit=block_size)
    if not is_all:
        start = (idx - 1) * 10
        return items[start:start + 10] if start < len(items) else []
    return items

async def _process_group_send(update, context, col_id, page, idx, items):
    """Immediate send for a specific group."""
    query = update.callback_query
    if not items:
        await query.answer("אין פריטים בקבוצה זו.", show_alert=True)
        return

    media_v, media_d, texts = prepare_media_groups(items)
    from message_tracker import track_if_shared
    msg = await context.bot.send_message(
        chat_id=query.message.chat_id, text=f"🚀 שולח {len(items)} פריטים מקבוצה {idx}..."
    )
    track_if_shared(query.from_user.id, query.message.chat_id, msg.message_id)
    await send_media_groups_in_chunks(
        context.bot, query.message.chat_id, media_v, media_d, texts, user_id=query.from_user.id
    )
    
    # Cleanup temporary messages
    try:
        await msg.delete()
    except Exception:
        pass
        
    try:
        await query.message.delete()
    except Exception:
        pass

    await show_collection_page(
        update=update, context=context, collection_id=col_id, page=page,
        force_resend=True, user_id=query.from_user.id
    )

async def _process_select_all_menu(query, context, col_id, page, items):
    """Show choice menu for Select All."""
    v_cnt = sum(1 for x in items if x[1] == 'video')
    p_cnt = sum(1 for x in items if x[1] == 'photo')
    d_cnt = sum(1 for x in items if x[1] == 'document')

    context.user_data[f"send_scope_{query.from_user.id}"] = {
        "collection_id": col_id, "page": page, "items_ids": [x[0] for x in items]
    }
    await query.edit_message_text(
        text=f"בחרת את כל הפריטים בעמוד {page}.\nמה לעשות?",
        reply_markup=build_page_file_type_menu(col_id, page, v_cnt, p_cnt, d_cnt)
    )

async def handle_page_file_send_choice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """שליחת תכנים לפי סוג מתוך עמוד דפדוף, אחרי 'בחר הכל'"""
    query = update.callback_query
    await query.answer()
    parts = parse_callback_data(query.data)
    if not parts or len(parts) < 3:
        return

    action, col_id, page = parts[0], int(parts[1]), int(parts[2])
    is_allowed, _, _ = check_collection_access(query.from_user.id, col_id)
    if not is_allowed:
        return

    items = _get_final_items_to_send(query.from_user.id, context, col_id, page, action)
    if not items:
        await query.edit_message_text("לא נמצאו פריטים מהסוג שנבחר.")
        return

    from message_tracker import track_if_shared
    msg = await context.bot.send_message(
        chat_id=query.message.chat_id, text=f"🚀 שולח {len(items)} פריטים..."
    )
    track_if_shared(query.from_user.id, query.message.chat_id, msg.message_id)

    media_v, media_d, texts = prepare_media_groups(items)
    await send_media_groups_in_chunks(
        context.bot, query.message.chat_id, media_v, media_d, texts, user_id=query.from_user.id
    )

    # Cleanup temporary messages
    try:
        await msg.delete()
    except Exception:
        pass
        
    try:
        await query.message.delete()
    except Exception:
        pass

    await show_collection_page(
        update=update, context=context, collection_id=col_id, page=page,
        force_resend=True, user_id=query.from_user.id
    )

def _get_final_items_to_send(u_id, context, col_id, page, action):
    """Retrieve and filter items to send."""
    scope = context.user_data.get(f"send_scope_{u_id}")
    if not scope or scope["collection_id"] != col_id or scope["page"] != page:
        items = db.get_items_by_collection(col_id, offset=(page-1)*100, limit=100)
    else:
        all_pg = db.get_items_by_collection(col_id, offset=(page-1)*100, limit=100)
        t_ids = set(scope["items_ids"])
        items = [x for x in all_pg if x[0] in t_ids]

    if action == "page_files_videos":
        return [x for x in items if x[1] == 'video']
    if action == "page_files_images":
        return [x for x in items if x[1] == 'photo']
    if action == "page_files_document":
        return [x for x in items if x[1] == 'document']
    return items

async def handle_batch_status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """הצגת התראה קופצת עם מספר הקבצים שנוספו"""
    query = update.callback_query
    parts = parse_callback_data(query.data, "batch_status")
    if not parts:
        return

    try:
        col_id = int(parts[0])
        count = context.user_data.get("batch_status", {}).get(col_id, {}).get("count", 0)
        await query.answer(f"עד כה נוספו {count} קבצים בסשן הנוכחי", show_alert=True)
    except Exception: # pylint: disable=broad-exception-caught
        await query.answer()


async def handle_search_collection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """בלחיצה על כפתור החיפוש באוסף, נבקש מהמשתמש להזין מילת חיפוש ונכניס אותו למצב המתנה"""
    query = update.callback_query
    await query.answer()

    parts = parse_callback_data(query.data, "search_collection")
    if not parts:
        return

    try:
        col_id = int(parts[0])
    except (ValueError, IndexError):
        return

    is_allowed, _, _ = check_collection_access(query.from_user.id, col_id)
    if not is_allowed:
        return

    # Set waiting state
    context.user_data["waiting_for_search_query"] = col_id
    context.user_data["search_from_message_id"] = query.message.message_id

    # Prompt user
    prompt_text = (
        "🔍 חיפוש במאגר\n\n"
        "אנא שלח/י את מילת החיפוש (שם קובץ או חלק מטקסט) כהודעת טקסט.\n"
        "החיפוש יתבצע בכל האוספים שיש לך גישה אליהם."
    )
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ ביטול", callback_data=f"browse_page:{col_id}:1")]
    ])

    await query.edit_message_text(prompt_text, reply_markup=keyboard)

