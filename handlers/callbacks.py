import math
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
import db
from config import is_admin
from constants import active_collections, active_shared_collections
from utils import (
    reset_user_modes, send_response, check_collection_access, 
    get_page_header, build_page_menu, show_collection_page,
    build_page_file_type_menu, logger, prepare_media_groups, 
    send_media_groups_in_chunks, verify_user_code,
    create_verification_code, update_batch_status, format_size,
    get_main_menu_text, build_main_menu_keyboard,
    parse_callback_data, validate_access_wrapper, send_info_page,
    track_shared_messages
)
from handlers.commands import (
    new_collection_flow, list_collections_flow, manage_collections_flow, 
    remove_flow, id_file_flow, show_browse_menu
)
from archive_logger import log_activity, ENABLE_ARCHIVING

async def handle_select_collection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """בחירת אוסף פעיל לשמירה (לא קשור לדפדוף)"""
    query = update.callback_query
    await query.answer()

    user = query.from_user
    data = query.data
    parts = parse_callback_data(data, "select_collection")
    if not parts:
        return

    collection_id = int(parts[0])

    is_allowed, collection = await validate_access_wrapper(update, context, collection_id)
    if not is_allowed:
        return

    active_collections[user.id] = collection_id

    if "batch_status" in context.user_data and collection_id in context.user_data["batch_status"]:
        del context.user_data["batch_status"][collection_id]

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(text="🛑 הפסק הוספה", callback_data="stop_collect")]]
    )

    await query.edit_message_text(
        text=f"אוסף פעיל הוגדר: {collection[1]}\nעכשיו שלח תוכן כדי לשמור.",
        reply_markup=keyboard,
    )

async def handle_select_item_delete_col_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """בחירת אוסף למחיקת פריטים (מצב מחיקה)"""
    query = update.callback_query
    await query.answer()

    user = query.from_user
    data = query.data
    parts = parse_callback_data(data, "select_item_del_col")
    if not parts:
        return

    try:
        collection_id = int(parts[0])
    except ValueError:
        return

    is_allowed, collection = await validate_access_wrapper(update, context, collection_id)
    if not is_allowed:
        return

    context.user_data["item_delete_mode"] = True
    context.user_data["delete_target_collection_id"] = collection_id
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏁 סיום מחיקה", callback_data="back_to_main")]
    ])

    text = (
        f"🗑 **מצב מחיקת פריטים הופעל עבור: {collection[1]}**\n\n"
        "שלח לי כעת תמונה, וידאו או קובץ שקיים באוסף זה, ואני אמחק אותו עבורך.\n"
        "תוכל למחוק מספר פריטים ברצף."
    )

    await query.edit_message_text(text=text, reply_markup=keyboard, parse_mode="Markdown")

async def handle_browse_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """תצוגת עמוד דפדוף: כל עמוד עד 100 פריטים, מחולק לקבוצות של 10"""
    query = update.callback_query
    await query.answer()

    user = query.from_user
    data = query.data
    parts = parse_callback_data(data, "browse_page")
    if not parts:
        return

    collection_id = int(parts[0])
    page = int(parts[1])

    await show_collection_page(
        update=update,
        context=context,
        collection_id=collection_id,
        page=page,
        edit_message_id=query.message.message_id
    )

async def handle_scroll_view_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """צפייה בפריט יחיד עם כפתורי הבא/הקודם - מחיקת הודעה ושליחה חדשה"""
    query = update.callback_query
    await query.answer()

    user = query.from_user
    data = query.data
    
    parts = parse_callback_data(data, "scroll_view")
    if not parts:
        return

    try:
        collection_id = int(parts[0])
        item_index = int(parts[1])
    except ValueError:
        return

    is_allowed, collection = await validate_access_wrapper(update, context, collection_id)
    if not is_allowed:
        return

    # Get total items count
    total_items = db.count_items_in_collection(collection_id)
    
    if total_items == 0:
        await query.edit_message_text("אין פריטים באוסף הזה.")
        return

    # Ensure index is within bounds
    if item_index < 0:
        item_index = 0
    elif item_index >= total_items:
        item_index = total_items - 1

    # Get single item at the current index
    items = db.get_items_by_collection(collection_id, offset=item_index, limit=1)
    if not items:
        await query.edit_message_text("פריט לא נמצא.")
        return

    item = items[0]
    # item structure: (id, content_type, file_id, text_content, file_name, file_size, added_at)
    item_id, content_type, file_id, text_content, file_name, file_size, added_at = item

    chat_id = query.message.chat_id
    
    # Build navigation keyboard
    nav_buttons = []
    if item_index > 0:
        nav_buttons.append(InlineKeyboardButton("⬅ הקודם", callback_data=f"scroll_view:{collection_id}:{item_index - 1}"))
    if item_index < total_items - 1:
        nav_buttons.append(InlineKeyboardButton("הבא ➡", callback_data=f"scroll_view:{collection_id}:{item_index + 1}"))
    
    keyboard = []
    if nav_buttons:
        keyboard.append(nav_buttons)
    keyboard.append([InlineKeyboardButton("🔙 חזור לתפריט דפדוף", callback_data=f"browse_page:{collection_id}:1")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Build header text
    header_text = f"📄 פריט {item_index + 1} מתוך {total_items}"
    if text_content:
        header_text += f"\n\n{text_content}"

    # Delete the old message first
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=query.message.message_id)
    except Exception:
        pass

    # Send item based on content type
    try:
        if content_type == "text" or not file_id:
            # Text-only item
            await context.bot.send_message(
                chat_id=chat_id,
                text=header_text,
                reply_markup=reply_markup
            )
        elif content_type == "photo":
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=file_id,
                caption=header_text,
                reply_markup=reply_markup
            )
        elif content_type == "video":
            await context.bot.send_video(
                chat_id=chat_id,
                video=file_id,
                caption=header_text,
                reply_markup=reply_markup
            )
        elif content_type == "document":
            await context.bot.send_document(
                chat_id=chat_id,
                document=file_id,
                caption=header_text,
                reply_markup=reply_markup
            )
        else:
            # Fallback for unknown content types
            await context.bot.send_message(
                chat_id=chat_id,
                text=header_text,
                reply_markup=reply_markup
            )
    except Exception as e:
        logger.error(f"Error sending scroll item: {e}")
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"שגיאה בטעינת הפריט.\n\n{header_text}",
            reply_markup=reply_markup
        )

async def handle_page_info_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """תצוגת מידע מפורט על קבצים בדף - 10 קבצים בכל פעם"""
    query = update.callback_query
    await query.answer()

    user = query.from_user
    data = query.data
    
    parts = parse_callback_data(data, "page_info")
    if not parts:
        return

    try:
        collection_id = int(parts[0])
        page = int(parts[1])
        info_page = int(parts[2])
    except ValueError:
        return

    is_allowed, collection = await validate_access_wrapper(update, context, collection_id)
    if not is_allowed:
        return

    await send_info_page(
        bot=context.bot,
        chat_id=query.message.chat_id,
        user_id=user.id,
        context=context,
        collection_id=collection_id,
        page=page,
        info_page=info_page,
        edit_message_id=query.message.message_id
    )

async def handle_back_to_info_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """חזרה לרשימת המידע - מוחק את הודעת המידע הישנה ושולח רשימה חדשה"""
    query = update.callback_query
    await query.answer()

    user = query.from_user
    data = query.data
    
    parts = parse_callback_data(data, "back_to_info")
    if not parts:
        return

    try:
        collection_id = int(parts[0])
        page = int(parts[1])
        info_page = int(parts[2])
    except ValueError:
        return

    is_allowed, collection = await validate_access_wrapper(update, context, collection_id)
    if not is_allowed:
        return

    chat_id = query.message.chat_id
    
    # Delete the OLD info message (stored in user_data), not the file message
    old_info_msg_id = context.user_data.get("info_message_id")
    if old_info_msg_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=old_info_msg_id)
        except Exception:
            pass
    
    await send_info_page(
        bot=context.bot,
        chat_id=chat_id,
        user_id=user.id,
        context=context,
        collection_id=collection_id,
        page=page,
        info_page=info_page
    )

async def handle_browse_group_or_select_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """כפתורי המספרים (קבוצות) ו'בחר הכל' בעמוד דפדוף"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    is_select_all = False
    idx = 0
    
    if data.startswith("browse_group:"):
        parts = parse_callback_data(data, "browse_group")
        collection_id = int(parts[0])
        page = int(parts[1])
        idx = int(parts[2])
    elif data.startswith("browse_page_select_all:"):
        is_select_all = True
        parts = parse_callback_data(data, "browse_page_select_all")
        collection_id = int(parts[0])
        page = int(parts[1])
    else:
        return

    # Permissions
    is_allowed, collection = await validate_access_wrapper(update, context, collection_id)
    if not is_allowed:
        return

    if is_select_all:
        header_text = f"בחרת את כל הפריטים בעמוד {page}.\nמה לעשות?"
    else:
        # Calculate range for group
        block_size = 100
        group_size = 10
        offset_block = (page - 1) * block_size
        
        # Determine group range
        start_offset = offset_block + (idx - 1) * group_size
        end_offset = start_offset + group_size
        
        header_text = f"בחרת את קבוצה {idx} (פריטים {start_offset+1}-{end_offset}).\nמה לעשות?"

    # Show options keyboard (Send Videos, Send Images, Send Files, Send All Main)
    # We call db to check counts to show nice numbers on buttons
    
    # Let's count totals for this scope to show on buttons
    block_size = 100
    offset_block = (page - 1) * block_size
    items_block = db.get_items_by_collection(collection_id, offset=offset_block, limit=block_size)
    
    if not is_select_all:
        # filter only group items
        group_size = 10
        g_start = (idx - 1) * group_size
        g_end = g_start + group_size
        items_scope = items_block[g_start:g_end] if g_start < len(items_block) else []
    else:
        items_scope = items_block

    # If it's a specific group (not select all), we send immediately as requested
    if not is_select_all:
        if not items_scope:
            await query.answer("אין פריטים בקבוצה זו.", show_alert=True)
            return

        # Prepare and send immediately
        media_visual, media_docs, text_items = prepare_media_groups(items_scope)
        
        chat_id = query.message.chat_id
        from message_tracker import track_if_shared
        msg = await context.bot.send_message(
            chat_id=chat_id, 
            text=f"🚀 שולח {len(items_scope)} פריטים מקבוצה {idx}..."
        )
        track_if_shared(user_id, chat_id, msg.message_id)
        
        sent_msg_ids = await send_media_groups_in_chunks(context.bot, chat_id, media_visual, media_docs, text_items, user_id=user_id)
        
        # After sending, we resend the collection page so it appears at the bottom
        await show_collection_page(
            update=update,
            context=context,
            collection_id=collection_id,
            page=page,
            force_resend=True,
            user_id=user_id
        )
        return

    # For Select All, we keep the menu logic
    video_count = sum(1 for x in items_scope if x[1] == 'video')
    image_count = sum(1 for x in items_scope if x[1] == 'photo')
    doc_count = sum(1 for x in items_scope if x[1] == 'document')
    
    # Store the scope in user_data so the next step knows what to send
    context.user_data[f"send_scope_{user_id}"] = {
        "collection_id": collection_id,
        "page": page,
        "is_select_all": is_select_all,
        "group_idx": idx,
        "items_ids": [x[0] for x in items_scope]  # Store IDs to send
    }

    reply_markup = build_page_file_type_menu(
        collection_id, page, video_count, image_count, doc_count
    )

    await query.edit_message_text(
        text=header_text,
        reply_markup=reply_markup
    )

async def handle_page_file_send_choice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """שליחת תכנים לפי סוג מתוך עמוד דפדוף, אחרי 'בחר הכל'"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    # data format: page_files_<type>:<collection_id>:<page>
    parts = parse_callback_data(data)
    if not parts or len(parts) < 3:
        return
        
    action = parts[0]
    
    try:
        # The caller sends ONE string.
        collection_id = int(parts[1])
        page = int(parts[2])
    except ValueError:
        return

    # Check permission again
    is_allowed, _, collection = check_collection_access(user_id, collection_id)
    if not is_allowed:
        return

    # Retrieve scope
    scope_key = f"send_scope_{user_id}"
    scope = context.user_data.get(scope_key)
    
    # If scope is missing or for wrong collection/page, we fallback to page-level or error
    if not scope or scope["collection_id"] != collection_id or scope["page"] != page:
        # Fallback: Just fetch all items in page
        block_size = 100
        offset_block = (page - 1) * block_size
        items = db.get_items_by_collection(collection_id, offset=offset_block, limit=block_size)
    else:
        # Fetch actual items by stored IDs
        # To avoid massive DB query with "IN (...)", we can just fetch the page and filter in python,
        # since we know the page anyway.
        block_size = 100
        offset_block = (page - 1) * block_size
        items_block = db.get_items_by_collection(collection_id, offset=offset_block, limit=block_size)
        target_ids = set(scope["items_ids"])
        items = [x for x in items_block if x[0] in target_ids]

    # Filter by type requested
    final_items = []
    if action == "page_files_videos":
        final_items = [x for x in items if x[1] == 'video']
    elif action == "page_files_images":
        final_items = [x for x in items if x[1] == 'photo']
    elif action == "page_files_document":
        final_items = [x for x in items if x[1] == 'document']
    elif action == "page_files_queue_all":
        final_items = items
    
    if not final_items:
        await query.edit_message_text("לא נמצאו פריטים מהסוג שנבחר בקבוצה זו.")
        # Restoration logic omitted for brevity, user can click back
        return

    # Send items
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    from message_tracker import track_if_shared
    msg = await context.bot.send_message(chat_id=chat_id, text=f"🚀 שולח {len(final_items)} פריטים...")
    track_if_shared(user_id, chat_id, msg.message_id)
    
    media_visual, media_docs, text_items = prepare_media_groups(final_items)
    sent_msg_ids = await send_media_groups_in_chunks(context.bot, chat_id, media_visual, media_docs, text_items, user_id=user_id)
    
    if media_visual or media_docs or text_items:
        # Show the collection page again (fresh message at bottom)
        await show_collection_page(
            update=update,
            context=context,
            collection_id=collection_id,
            page=page,
            edit_message_id=query.message.message_id,
            force_resend=True,
            user_id=user_id
        )
    else:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text="חלה שגיאה בעיבוד הפריטים.",
        )
        track_if_shared(user_id, chat_id, msg.message_id)

async def handle_batch_status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """הצגת התראה קופצת עם מספר הקבצים שנוספו"""
    query = update.callback_query
    data = query.data
    
    parts = parse_callback_data(data, "batch_status")
    if not parts:
        return
        
    try:
        collection_id = int(parts[0])
        
        user_data = context.user_data
        count = 0
        if "batch_status" in user_data and collection_id in user_data["batch_status"]:
            count = user_data["batch_status"][collection_id]["count"]
            
        await query.answer(f"עד כה נוספו {count} קבצים בסשן הנוכחי", show_alert=True)
        
    except Exception:
        await query.answer()

async def handle_collection_send_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Request confirmation with verification code before sending all items in a collection."""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    parts = parse_callback_data(data, "collection_send_all")
    if not parts:
        return
        
    try:
        collection_id = int(parts[0])
    except ValueError:
        return
        
    user = query.from_user
    is_allowed, collection = await validate_access_wrapper(update, context, collection_id)
    if not is_allowed:
        return
        
    total_items = db.count_items_in_collection(collection_id)
    if total_items == 0:
        await query.answer("האוסף ריק", show_alert=True)
        return

    code = create_verification_code(
        context, 
        "send_collection", 
        {
            "collection_id": collection_id,
            "msg_id": query.message.message_id
        }
    )
    
    text = (
        f"⚠️ **אישור שליחת אוסף מלא**\n\n"
        f"אתה עומד לשלוח את כל האוסף: {collection[1]} ({total_items} פריטים).\n"
        f"זה עשוי לקחת זמן וליצור עומס.\n\n"
        f"כדי לאשר, שלח את הקוד הבא לבוט:\n"
        f"`{code}`"
    )
    
    # We set a state to expect text input
    context.user_data["verify_send_collection_mode"] = True
    
    await query.edit_message_text(
        text=text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ ביטול", callback_data=f"browse_page:{collection_id}:1")]
        ])
    )

async def handle_stop_collect_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("מצב איסוף נעצר")
    
    user_id = query.from_user.id
    
    if user_id in active_collections:
        del active_collections[user_id]
    
    try:
        await query.edit_message_text(
            "🛑 מצב איסוף נעצר.\nתוכל לחזור ולהוסיף קבצים דרך התפריט הראשי.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 תפריט ראשי", callback_data="back_to_main")]
            ])
        )
    except Exception:
        pass

async def handle_delete_select_collection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle collection selection for delete mode"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    parts = parse_callback_data(data, "delete_collection")
    if not parts:
        return

    try:
        collection_id = int(parts[0])
    except ValueError:
        return
        
    user_id = query.from_user.id
    is_allowed, collection = await validate_access_wrapper(update, context, collection_id)
    if not is_allowed:
        return
        
    code = create_verification_code(
        context, 
        "delete_collection", 
        {"collection_id": collection_id}
    )
    
    item_count = db.count_items_in_collection(collection_id)
    
    text = (
        f"⚠️ **בטוח שאתה רוצה למחוק את האוסף?**\n\n"
        f"📌 שם האוסף: **{collection[1]}**\n"
        f"📦 מספר פריטים: **{item_count}**\n\n"
        f"כדי לאשר מחיקה, שלח את הקוד הבא:\n"
        f"`{code}`"
    )
    
    context.user_data["verify_delete_collection_mode"] = True
    
    await query.edit_message_text(
        text=text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
             [InlineKeyboardButton("❌ ביטול", callback_data="exit_delete_mode")]
        ])
    )

async def handle_main_menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    action = data.split(":")[1]
    
    if action == "newcollection":
        await new_collection_flow(query.message, query.from_user, context, [], edit_message_id=query.message.message_id)
        
    elif action == "browse":
        await show_browse_menu(query.message.chat_id, query.from_user.id, context, edit_message_id=query.message.message_id)
        
    elif action == "collections":
        await list_collections_flow(update, context, edit_message_id=query.message.message_id)
        
    elif action == "manage":
        await manage_collections_flow(update, context, edit_message_id=query.message.message_id)
        
    elif action == "remove":
        await remove_flow(query.message, query.from_user, context, [], edit_message_id=query.message.message_id)
        
    elif action == "id_file":
        await id_file_flow(query.message, query.from_user, context, edit_message_id=query.message.message_id)

    elif action == "enter_code":
        reset_user_modes(context)
        context.user_data["waiting_for_share_code"] = True
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ ביטול", callback_data="cancel_share_access")]
        ])
        await query.edit_message_text(
            "🔗 **גישה לאוסף משותף**\n\nאנא שלח את קוד השיתוף שקיבלת:",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
    
    elif action == "new_collection":
        await new_collection_flow(query.message, query.from_user, context, [], edit_message_id=query.message.message_id)

    elif action == "select_collection":
         await list_collections_flow(update, context, edit_message_id=query.message.message_id)

async def handle_back_to_main_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # Remove any lingering Message IDs from user_data that might confuse flows
    # (But keep main_menu_msg_id if we want to reuse it)
    
    try:
        await query.edit_message_text(
            text=get_main_menu_text(),
            reply_markup=build_main_menu_keyboard()
        )
    except Exception:
        await query.message.reply_text(
            text=get_main_menu_text(),
            reply_markup=build_main_menu_keyboard()
        )

async def handle_manage_collection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show management options for a specific collection"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    parts = parse_callback_data(data, "manage_collection")
    if not parts:
        return
    
    try:
        collection_id = int(parts[0])
    except ValueError:
        return
        
    is_allowed, collection = await validate_access_wrapper(update, context, collection_id)
    if not is_allowed:
        return

    is_admin_view = is_admin(query.from_user.id) and collection[2] != query.from_user.id
    
    keyboard = []
    
    if not is_admin_view:
        keyboard = [
            [InlineKeyboardButton("📤 ייצוא לקובץ (גיבוי)", callback_data=f"export_collection:{collection_id}")],
            [InlineKeyboardButton("🔗 יצירת קישור שיתוף", callback_data=f"share_collection:{collection_id}")],
            [InlineKeyboardButton("🗑 מחיקת אוסף", callback_data=f"delete_collection:{collection_id}")],
            [InlineKeyboardButton("🔙 חזור לרשימה", callback_data="back_to_manage")],
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("📂 צפייה בתוכן (Admin)", callback_data=f"browse_page:{collection_id}:1")],
            [InlineKeyboardButton("🗑 מחיקת אוסף (Admin)", callback_data=f"delete_collection:{collection_id}")],
            [InlineKeyboardButton("🔙 חזור לרשימה", callback_data="back_to_manage")],
        ]
    
    await query.edit_message_text(
        f"ניהול אוסף: **{collection[1]}**",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def handle_share_collection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate or display share code for a collection"""
    query = update.callback_query
    await query.answer()
    user = query.from_user
    
    parts = parse_callback_data(query.data, "share_collection")
    if not parts:
        return

    try:
        collection_id = int(parts[0])
    except ValueError:
        return

    is_allowed, collection = await validate_access_wrapper(update, context, collection_id)
    if not is_allowed:
        return
        
    collection_name = collection[1]
    share_code = db.create_share_link(collection_id, user.id)
    
    if ENABLE_ARCHIVING:
        asyncio.create_task(
            log_activity(
                bot=context.bot,
                action="SHARE_CREATED",
                user_id=user.id,
                collection_id=collection_id,
                extra={"share_code": share_code[:10] + "..."},
                user_name=user.full_name,
                username=user.username
            )
        )
    
    logs = db.get_share_access_logs(collection_id)
    access_count = len(logs)
    
    # Get current expiration
    expires_at = db.get_share_expiration(collection_id)
    if expires_at:
        expiry_text = f"⏱️ תפוגה: {expires_at[:16].replace('T', ' ')}"
    else:
        expiry_text = "⏱️ ללא תפוגה"
    
    text = (
        f"קוד שיתוף לאוסף: {collection_name}\n\n"
        f"📋 קוד: `{share_code}`\n\n"
        f"👥 מספר גישות: {access_count}\n"
        f"{expiry_text}\n\n"
        "💡 שלח את הקוד הזה למשתמשים אחרים.\n"
        "הם יוכלו לגשת לאוסף באמצעות הפקודה /access."
    )

    keyboard = [
        [InlineKeyboardButton("⏱️ הגדרת זמן תפוגה", callback_data=f"set_share_expiration:{collection_id}")],
        [InlineKeyboardButton("📊 סטטיסטיקות גישה", callback_data=f"share_stats:{collection_id}")],
        [InlineKeyboardButton("🔄 חידוש קוד", callback_data=f"regenerate_share:{collection_id}")],
        [InlineKeyboardButton("❌ ביטול שיתוף", callback_data=f"revoke_share:{collection_id}")],
        [InlineKeyboardButton("⬅️ חזור", callback_data=f"manage_collection:{collection_id}")]
    ]

    await query.edit_message_text(
        text=text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def handle_share_stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show access statistics for a shared collection"""
    query = update.callback_query
    await query.answer()
    
    parts = parse_callback_data(query.data, "share_stats")
    if not parts:
        return

    try:
        collection_id = int(parts[0])
    except ValueError:
        return

    is_allowed, collection = await validate_access_wrapper(update, context, collection_id)
    if not is_allowed:
        return
        
    logs = db.get_share_access_logs(collection_id)
    
    if not logs:
        text = "📊 אין עדיין צפיות באוסף המשותף הזה."
    else:
        text = f"📊 **סטטיסטיקות צפייה ({len(logs)} משתמשים):**\n\n"
        for user_id, username, first_name, accessed_at in logs:
            # Escape underscores in username for Markdown
            safe_username = username.replace("_", "\\_") if username else ""
            name = f"{first_name} " + (f"(@{safe_username})" if username else "")
            # accessed_at format from SQLite is typically "YYYY-MM-DD HH:MM:SS..."
            # We want "YYYY-MM-DD HH:MM"
            try:
                date_str = accessed_at[:16].replace("T", " ")
            except Exception:
                date_str = accessed_at 
                
            text += f"👤 {name} - {date_str}\n"

    keyboard = [[InlineKeyboardButton("🔙 חזור", callback_data=f"share_collection:{collection_id}")]]
    
    await query.edit_message_text(
        text=text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def handle_regenerate_share_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Regenerate share code for a collection"""
    query = update.callback_query
    await query.answer("קוד שיתוף הוחלף")
    
    parts = parse_callback_data(query.data, "regenerate_share")
    if not parts:
        return

    try:
        collection_id = int(parts[0])
    except ValueError:
        return
        
    new_code = db.regenerate_share_code(collection_id, query.from_user.id)
    
    if new_code:
        # Provide same view as initial share creation but updated
        text = (
            f"🔄 **הקוד הוחלף בהצלחה!**\n\n"
            f"הקוד החדש:\n`{new_code}`\n\n"
            f"הקוד הישן מבוטל ולא יעבוד יותר."
        )
        keyboard = [
             [InlineKeyboardButton("🔙 חזור", callback_data=f"manage_collection:{collection_id}")]
        ]
        await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def handle_revoke_share_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Revoke share code for a collection"""
    query = update.callback_query
    await query.answer("שיתוף בוטל")
    
    parts = parse_callback_data(query.data, "revoke_share")
    if not parts:
        return

    try:
        collection_id = int(parts[0])
    except ValueError:
        return
        
    db.revoke_share_code(collection_id, query.from_user.id)
    
    # Log share revocation
    if ENABLE_ARCHIVING:
        asyncio.create_task(
            log_activity(
                bot=context.bot,
                action="SHARE_REVOKED",
                user_id=query.from_user.id,
                collection_id=collection_id,
                user_name=query.from_user.full_name,
                username=query.from_user.username
            )
        )
    
    await query.edit_message_text(
        "🚫 השיתוף בוטל בהצלחה.\nאף אחד לא יוכל לגשת לאוסף יותר דרך קוד.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 חזור לניהול", callback_data=f"manage_collection:{collection_id}")]
        ])
    )

async def handle_export_collection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("מכין קובץ גיבוי...")
    
    parts = parse_callback_data(query.data, "export_collection")
    if not parts:
        return

    try:
        collection_id = int(parts[0])
    except ValueError:
        return
        
    # Get items
    items = db.get_items_by_collection(collection_id, limit=100000) # Get all
    if not items:
        await query.edit_message_text("האוסף ריק, אין מה לייצא.")
        return
        
    is_allowed, collection = await validate_access_wrapper(update, context, collection_id)
    if not is_allowed:
        return
        
    # Generate TXT content
    # Format: CONTENT_TYPE|FILE_ID|TEXT|FILENAME|SIZE
    lines = []
    lines.append(f"# COLLECTION EXPORT: {collection[1]}")
    lines.append(f"# DATE: {db.datetime.now()}")
    lines.append("# DO NOT EDIT THIS FILE")
    lines.append("")
    
    for item in items:
        # item: id, content_type, file_id, text_content, file_name, file_size, added_at
        c_type = item[1]
        f_id = item[2]
        text = item[3] or ""
        text = text.replace("|", "<PIPE>") # Escape pipe
        text = text.replace("\n", "<NL>") # Escape newline
        f_name = item[4] or ""
        f_size = str(item[5]) if item[5] else "0"
        
        line = f"{c_type}|{f_id}|{text}|{f_name}|{f_size}"
        lines.append(line)
        
    content = "\n".join(lines)
    
    # Send as document
    from io import BytesIO
    bio = BytesIO(content.encode('utf-8'))
    bio.name = f"Build_Collection_{collection_id}_backup.txt"
    
    await context.bot.send_document(
        chat_id=query.message.chat_id,
        document=bio,
        caption=f"📦 גיבוי מלא לאוסף: {collection[1]}",
        filename=bio.name
    )

async def handle_delete_collection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete a collection with confirmation"""
    # Simply redirects to shared handle_delete_select_collection_callback logic or similar
    # But since we have the ID already, we can reuse logic.
    await handle_delete_select_collection_callback(update, context)

async def handle_back_to_manage_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Go back to manage collections list"""
    query = update.callback_query
    await query.answer()
    
    await manage_collections_flow(update, context, edit_message_id=query.message.message_id)

async def handle_exit_shared_collection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exit from viewing a shared collection"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if user_id in active_shared_collections:
        del active_shared_collections[user_id]
        db.set_user_active_share(user_id, None)  # Clear from DB persistence
        
    await query.edit_message_text(
        "יצאת מהאוסף המשותף.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 תפריט ראשי", callback_data="back_to_main")]])
    )

async def handle_cancel_share_access_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel share code input"""
    query = update.callback_query
    await query.answer()
    
    if "waiting_for_share_code" in context.user_data:
        del context.user_data["waiting_for_share_code"]
        
    await query.edit_message_text(
        "ביטלת את הכניסה לאוסף.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 תפריט ראשי", callback_data="back_to_main")]])
    )

async def handle_exit_delete_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exit from delete mode"""
    query = update.callback_query
    await query.answer()
    
    reset_user_modes(context)
    
    await query.edit_message_text(
        "יצאת ממצב מחיקה.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 תפריט ראשי", callback_data="back_to_main")]])
    )


async def handle_set_share_expiration_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show expiration time options for a shared collection"""
    query = update.callback_query
    await query.answer()
    
    parts = parse_callback_data(query.data, "set_share_expiration")
    if not parts:
        return

    try:
        collection_id = int(parts[0])
    except ValueError:
        return

    is_allowed, collection = await validate_access_wrapper(update, context, collection_id)
    if not is_allowed:
        return

    keyboard = [
        [InlineKeyboardButton("⌨️ זמן מותאם אישית", callback_data=f"custom_share_exp:{collection_id}")],
        [InlineKeyboardButton("שעה", callback_data=f"save_share_exp:{collection_id}:1h")],
        [InlineKeyboardButton("12 שעות", callback_data=f"save_share_exp:{collection_id}:12h")],
        [InlineKeyboardButton("24 שעות", callback_data=f"save_share_exp:{collection_id}:24h")],
        [InlineKeyboardButton("47 שעות (מקסימום)", callback_data=f"save_share_exp:{collection_id}:47h")],
        [InlineKeyboardButton("ללא תפוגה", callback_data=f"save_share_exp:{collection_id}:never")],
        [InlineKeyboardButton("⬅️ חזור", callback_data=f"share_collection:{collection_id}")]
    ]

    await query.edit_message_text(
        "⏱️ בחר זמן תפוגה לשיתוף:\n\n"
        "כאשר השיתוף יפוג, כל ההודעות שנשלחו\n"
        "למשתמשים דרך שיתוף זה יימחקו אוטומטית.\n\n"
        "⚠️ הגבלת טלגרם: ניתן למחוק הודעות\n"
        "עד 48 שעות אחורה בלבד.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_save_share_expiration_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save the expiration time for a shared collection"""
    query = update.callback_query
    await query.answer()
    user = query.from_user
    
    parts = parse_callback_data(query.data, "save_share_exp")
    if not parts or len(parts) < 2:
        return

    try:
        collection_id = int(parts[0])
        duration = parts[1]
    except (ValueError, IndexError):
        return

    is_allowed, collection = await validate_access_wrapper(update, context, collection_id)
    if not is_allowed:
        return

    # Calculate expiration time (all within 48 hour limit)
    if duration == "never":
        expires_at = None
        expiry_text = "ללא תפוגה (הודעות לא יימחקו)"
    elif duration == "10m":
        expires_at = (datetime.now() + timedelta(minutes=10)).isoformat()
        expiry_text = "10 דקות"
    elif duration == "1h":
        expires_at = (datetime.now() + timedelta(hours=1)).isoformat()
        expiry_text = "שעה"
    elif duration == "12h":
        expires_at = (datetime.now() + timedelta(hours=12)).isoformat()
        expiry_text = "12 שעות"
    elif duration == "24h":
        expires_at = (datetime.now() + timedelta(hours=24)).isoformat()
        expiry_text = "24 שעות"
    elif duration == "47h":
        expires_at = (datetime.now() + timedelta(hours=47)).isoformat()
        expiry_text = "47 שעות"
    else:
        return

    # Save to database
    success = db.set_share_expiration(collection_id, user.id, expires_at)
    
    if success:
        text = f"✅ זמן תפוגה עודכן: {expiry_text}"
    else:
        text = "❌ לא הצלחנו לעדכן את זמן התפוגה"

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ חזור לשיתוף", callback_data=f"share_collection:{collection_id}")]
        ])
    )


async def handle_custom_share_expiration_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompt user to enter custom expiration time in minutes"""
    query = update.callback_query
    await query.answer()
    
    parts = parse_callback_data(query.data, "custom_share_exp")
    if not parts:
        return

    try:
        collection_id = int(parts[0])
    except ValueError:
        return

    is_allowed, collection = await validate_access_wrapper(update, context, collection_id)
    if not is_allowed:
        return

    # Initial duration: 60 minutes
    duration = 60
    
    # If there's an existing value in callback data, use it (format: custom_share_exp:col_id:duration)
    if len(parts) > 1:
        try:
            duration = int(parts[1])
        except ValueError:
            pass
            
    # Cap duration between 1 min and 47 hours (2820 min)
    duration = max(1, min(duration, 2820))
    
    # Calculate display
    hours = duration // 60
    minutes = duration % 60
    
    time_str = f"{hours} שעות" if minutes == 0 else f"{hours} שעות ו-{minutes} דקות"
    if hours == 0:
        time_str = f"{minutes} דקות"

    keyboard = [
        [
            InlineKeyboardButton("- שעה", callback_data=f"custom_share_exp:{collection_id}:{duration - 60}"),
            InlineKeyboardButton("- דקה", callback_data=f"custom_share_exp:{collection_id}:{duration - 1}"),
            InlineKeyboardButton("+ דקה", callback_data=f"custom_share_exp:{collection_id}:{duration + 1}"),
            InlineKeyboardButton("+ שעה", callback_data=f"custom_share_exp:{collection_id}:{duration + 60}"),
        ],
        [
            InlineKeyboardButton("- 10 דק'", callback_data=f"custom_share_exp:{collection_id}:{duration - 10}"),
            InlineKeyboardButton("+ 10 דק'", callback_data=f"custom_share_exp:{collection_id}:{duration + 10}"),
        ],
        [InlineKeyboardButton("✅ שמירה", callback_data=f"save_share_exp_custom:{collection_id}:{duration}")],
        [InlineKeyboardButton("⬅️ חזור", callback_data=f"set_share_expiration:{collection_id}")]
    ]

    # Don't edit if nothing changed (to avoid Telegram errors)
    try:
        await query.edit_message_text(
            f"⏱️ **הגדרת זמן תפוגה מותאם אישית**\n\n"
            f"זמן נבחר: **{time_str}**\n\n"
            f"השתמש בכפתורים כדי לשנות:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    except Exception:
        # Ignore if content is same
        pass


async def handle_save_custom_share_expiration_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save the custom expiration time selected via interactive buttons"""
    query = update.callback_query
    await query.answer()
    user = query.from_user
    
    parts = parse_callback_data(query.data, "save_share_exp_custom")
    if not parts or len(parts) < 2:
        return

    try:
        collection_id = int(parts[0])
        minutes = int(parts[1])
    except (ValueError, IndexError):
        return

    is_allowed, collection = await validate_access_wrapper(update, context, collection_id)
    if not is_allowed:
        return

    # Validate range
    minutes = max(1, min(minutes, 2820))
    
    # Calculate expiration
    expires_at = (datetime.now() + timedelta(minutes=minutes)).isoformat()
    
    # Format display
    hours = minutes // 60
    mins = minutes % 60
    if mins > 0:
        expiry_text = f"{hours} שעות ו-{mins} דקות"
    elif hours > 0:
        expiry_text = f"{hours} שעות"
    else:
        expiry_text = f"{minutes} דקות"

    # Save to database
    success = db.set_share_expiration(collection_id, user.id, expires_at)
    
    if success:
        text = f"✅ זמן תפוגה עודכן: {expiry_text}"
    else:
        text = "❌ לא הצלחנו לעדכן את זמן התפוגה"

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ חזור לשיתוף", callback_data=f"share_collection:{collection_id}")]
        ])
    )
