# admin_panel.py
# ממשק ניהול מלא - פיצול מ-bot.py

import asyncio
import logging

import constants
import db
from config import ADMIN_IDS, is_admin
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from datetime import datetime
import html

from utils import check_file_id as _check_file_id

logger = logging.getLogger(__name__)


def build_admin_main_menu():
    """Build the admin main menu message and keyboard"""
    stats = db.get_global_stats()
    
    message_text = (
        "🔧 <b>ממשק ניהול מערכת</b>\n\n"
        f"📊 סטטיסטיקות כלליות:\n"
        f"• משתמשים: {stats['users']}\n"
        f"• אוספים: {stats['collections']}\n"
        f"• פריטים: {stats['items']}\n\n"
        "בחר אפשרות:"
    )
    
    keyboard = [
        [InlineKeyboardButton("👥 משתמשים", callback_data="admin_users")],
        [InlineKeyboardButton("📦 אוספים", callback_data="admin_collections")],
        [InlineKeyboardButton("🔗 שיתופים", callback_data="admin_shares")],
        [InlineKeyboardButton("📊 סטטיסטיקות", callback_data="admin_stats")],
        [InlineKeyboardButton("🔍 סריקת קבצים פגומים", callback_data="admin_scan_files")],
        [InlineKeyboardButton("🏠 מסך הבית", callback_data="back_to_main")]
    ]
    
    return message_text, InlineKeyboardMarkup(keyboard)


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """פקודת /adminpanel - תפריט ניהול ראשי"""
    user = update.effective_user
    
    if not is_admin(user.id):
        await update.message.reply_text("⛔ אין לך הרשאות גישה לממשק הניהול.")
        return
    
    message_text, reply_markup = build_admin_main_menu()
    
    await update.message.reply_text(
        message_text,
        reply_markup=reply_markup,
        parse_mode="HTML"
    )


async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """טיפול בכל ה-callbacks של admin"""
    query = update.callback_query
    user = query.from_user

    if not is_admin(user.id):
        await query.answer()
        await query.edit_message_text("⛔ אין לך הרשאות גישה.")
        return

    # Sub-handlers may answer with their own alert, so the default empty answer
    # comes last; a callback query can only be answered once.
    try:
        await _dispatch_admin_callback(query, context, query.data)
    except (ValueError, IndexError):
        # Malformed callback data (non-numeric id, missing part): ignore silently
        pass
    finally:
        try:
            await query.answer()
        except Exception:  # pylint: disable=broad-exception-caught
            pass  # Already answered by a sub-handler


async def _dispatch_admin_callback(query, context: ContextTypes.DEFAULT_TYPE, data: str):
    """Route an admin callback to its handler. May raise ValueError/IndexError on bad data."""
    # Main menu callbacks
    if data == "admin_users":
        await show_users_list(query, context, page=1)
    elif data.startswith("admin_users_page:"):
        page = int(data.split(":")[1])
        await show_users_list(query, context, page=page)
    elif data == "admin_collections":
        await show_collections_list(query, context, page=1)
    elif data.startswith("admin_collections_page:"):
        page = int(data.split(":")[1])
        await show_collections_list(query, context, page=page)
    elif data == "admin_shares":
        await show_shares_dashboard(query, context, page=1)
    elif data.startswith("admin_shares_page:"):
        page = int(data.split(":")[1])
        await show_shares_dashboard(query, context, page=page)
    elif data == "admin_stats":
        await show_global_stats(query, context)
    elif data == "admin_back_to_main":
        await show_main_menu(query, context)
    
    # User-related callbacks
    elif data.startswith("admin_user_card:"):
        user_id = int(data.split(":")[1])
        await show_user_card(query, context, user_id)
    elif data.startswith("admin_block_user:"):
        user_id = int(data.split(":")[1])
        await block_user_action(query, context, user_id)
    
    # Collection-related callbacks
    elif data.startswith("admin_collection_card:"):
        collection_id = int(data.split(":")[1])
        await show_collection_card(query, context, collection_id)
    elif data.startswith("admin_clone_collection:"):
        collection_id = int(data.split(":")[1])
        await clone_collection_action(query, context, collection_id)
    
    # Shares-related callbacks
    elif data.startswith("admin_share_card:"):
        share_id = int(data.split(":")[1])
        await show_share_card(query, context, share_id)
    elif data.startswith("admin_share_disable:"):
        share_code = data.split(":")[1]
        await disable_share_action(query, context, share_code)
    elif data.startswith("admin_share_create:"):
        collection_id = int(data.split(":")[1])
        await create_new_share_action(query, context, collection_id)
    elif data.startswith("admin_share_logs:"):
        share_code = data.split(":")[1]
        await show_share_access_log(query, context, share_code)
    
    # Scan files callbacks
    elif data == "admin_scan_files":
        await show_scan_files_menu(query, context)
    elif data == "admin_scan_files_confirm":
        await run_scan_files_inline(query, context)

    # Additional callbacks
    elif data == "admin_close":
        await query.delete_message()
    elif data.startswith("admin_user_cols:"):
        user_id = int(data.split(":")[1])
        await show_user_collections(query, context, user_id)
    elif data.startswith("admin_manage_col:"):
        collection_id = int(data.split(":")[1])
        await show_admin_collection_management(query, context, collection_id)
    elif data.startswith("admin_delete_col:"):
        collection_id = int(data.split(":")[1])
        await confirm_delete_collection(query, context, collection_id)
    elif data.startswith("admin_confirm_delete:"):
        collection_id = int(data.split(":")[1])
        await delete_collection_action(query, context, collection_id)


# === Main Menu Functions ===

async def show_main_menu(query, context: ContextTypes.DEFAULT_TYPE):
    """תצוגת תפריט ראשי"""
    message_text, reply_markup = build_admin_main_menu()
    
    await query.edit_message_text(
        message_text,
        reply_markup=reply_markup,
        parse_mode="HTML"
    )


async def show_global_stats(query, context: ContextTypes.DEFAULT_TYPE):
    """תצוגת סטטיסטיקות כלליות"""
    stats = db.get_global_stats()
    
    message_text = (
        "📊 <b>סטטיסטיקות מערכת</b>\n\n"
        f"👥 משתמשים: {stats['users']}\n"
        f"📦 אוספים: {stats['collections']}\n"
        f"📄 פריטים: {stats['items']}\n"
    )
    
    keyboard = [[InlineKeyboardButton("⬅️ חזור", callback_data="admin_back_to_main")]]
    
    await query.edit_message_text(
        message_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


# === Users Functions ===

async def show_users_list(query, context: ContextTypes.DEFAULT_TYPE, page: int = 1):
    """תצוגת רשימת משתמשים עם pagination"""
    items_per_page = 20
    total_users = db.count_users_with_collections()

    if total_users == 0:
        await query.edit_message_text(
            "אין משתמשים עם אוספים במערכת.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ חזור", callback_data="admin_back_to_main")]])
        )
        return

    total_pages = (total_users + items_per_page - 1) // items_per_page
    page = max(1, min(page, total_pages))
    users = db.get_users_with_collections_page(offset=(page - 1) * items_per_page, limit=items_per_page)

    message_text = f"👥 <b>רשימת משתמשים</b> (עמוד {page}/{total_pages})\n\nבחר משתמש לצפייה:"
    keyboard = []

    for uid, username, first_name in users:
        name = first_name or f"User_{uid}"
        display_name = f"{name} (@{username})" if username else name
        keyboard.append([InlineKeyboardButton(display_name, callback_data=f"admin_user_card:{uid}")])

    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton("◀️ הקודם", callback_data=f"admin_users_page:{page-1}"))
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton("הבא ▶️", callback_data=f"admin_users_page:{page+1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([InlineKeyboardButton("⬅️ חזור", callback_data="admin_back_to_main")])
    
    await query.edit_message_text(
        message_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def show_user_card(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """כרטיס משתמש בודד"""
    user_info = db.get_user_details(user_id)
    
    if not user_info:
        await query.answer("משתמש לא נמצא", show_alert=True)
        return
    
    username = user_info['username'] or "ללא"
    first_name = user_info['first_name'] or "ללא"
    
    # Safe HTML
    safe_username = html.escape(username)
    safe_first_name = html.escape(first_name)
    
    blocked_status = "🚫 חסום" if user_info.get('blocked', 0) == 1 else "✅ פעיל"
    
    # Create clickable user link
    user_link = f'<a href="tg://user?id={user_id}">{safe_first_name}</a>'
    
    message_text = (
        f"👤 <b>כרטיס משתמש</b>\n\n"
        f"📛 שם: {user_link}\n"
        f"🆔 משתמש: @{safe_username}\n"
        f"🔢 ID: <code>{user_id}</code>\n"
        f"📅 הצטרף: {user_info['first_seen']}\n"
        f"📦 אוספים: {user_info['collections_count']}\n"
        f"📄 פריטים: {user_info['items_count']}\n"
        f"🔒 סטטוס: {blocked_status}\n"
    )
    
    keyboard = [
        [InlineKeyboardButton("📁 צפייה באוספים", callback_data=f"admin_user_cols:{user_id}")],
        [InlineKeyboardButton("🚫 חסום משתמש", callback_data=f"admin_block_user:{user_id}")],
        [InlineKeyboardButton("⬅️ חזור לרשימה", callback_data="admin_users")]
    ]
    
    await query.edit_message_text(
        message_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def block_user_action(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """חסימת משתמש"""
    success = db.block_user(user_id)
    
    if success:
        await query.answer("✅ המשתמש נחסם בהצלחה!", show_alert=True)
        await show_user_card(query, context, user_id)
    else:
        await query.answer("❌ שגיאה בחסימת המשתמש", show_alert=True)


# === Collections Functions ===

async def show_collections_list(query, context: ContextTypes.DEFAULT_TYPE, page: int = 1):
    """תצוגת רשימת אוספים עם pagination"""
    items_per_page = 12
    total_items = db.count_all_collections()
    
    if total_items == 0:
        await query.edit_message_text(
            "אין אוספים במערכת.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ חזור", callback_data="admin_back_to_main")]])
        )
        return

    total_pages = (total_items + items_per_page - 1) // items_per_page
    page = max(1, min(page, total_pages))
    offset = (page - 1) * items_per_page
    
    collections = db.get_all_collections_paginated(offset=offset, limit=items_per_page)
    
    message_text = f"📦 <b>רשימת אוספים</b> (עמוד {page}/{total_pages})\n\n"
    keyboard = []
    
    for col_id, col_name, user_id, username, first_name in collections:
        display_name = username or first_name or f"User_{user_id}"
        # Truncate names if too long
        if len(col_name) > 25: 
            col_name = col_name[:23] + ".."
            
        safe_col_name = html.escape(col_name)
        safe_display_name = html.escape(display_name)
            
        keyboard.append([
            InlineKeyboardButton(
                f"{safe_col_name} ({safe_display_name})",
                callback_data=f"admin_collection_card:{col_id}"
            )
        ])
    
    # Navigation buttons
    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton("◀️ הקודם", callback_data=f"admin_collections_page:{page-1}"))
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton("הבא ▶️", callback_data=f"admin_collections_page:{page+1}"))
    
    if nav_buttons:
        keyboard.append(nav_buttons)
        
    keyboard.append([InlineKeyboardButton("⬅️ חזור", callback_data="admin_back_to_main")])
    
    await query.edit_message_text(
        message_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def show_collection_card(query, context: ContextTypes.DEFAULT_TYPE, collection_id: int):
    """כרטיס אוסף בודד"""
    collection = db.get_collection_by_id(collection_id)
    
    if not collection:
        await query.answer("אוסף לא נמצא", show_alert=True)
        return
    
    col_id, col_name, owner_id = collection
    item_count = db.count_items_in_collection(collection_id)
    
    owner_info = db.get_user(owner_id)
    owner_display = owner_info.username if owner_info and owner_info.username else f"User_{owner_id}"
    
    # Escape HTML characters in collection name and owner
    safe_col_name = html.escape(col_name)
    safe_owner_display = html.escape(owner_display)
    
    message_text = (
        f"📦 <b>כרטיס אוסף</b>\n\n"
        f"📛 שם: {safe_col_name}\n"
        f"🆔 ID: <code>{col_id}</code>\n"
        f"👤 בעלים: @{safe_owner_display}\n"
        f"📄 פריטים: {item_count}\n"
    )
    
    keyboard = [
        [InlineKeyboardButton("👁️ דפדף בתוכן", callback_data=f"browse_page:{collection_id}:1")],
        [InlineKeyboardButton("📋 שכפל לאדמין", callback_data=f"admin_clone_collection:{collection_id}")],
        [InlineKeyboardButton("⬅️ חזור לרשימה", callback_data="admin_collections")]
    ]
    
    await query.edit_message_text(
        message_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def clone_collection_action(query, context: ContextTypes.DEFAULT_TYPE, collection_id: int):
    """שכפול אוסף לאדמין"""
    admin_id = query.from_user.id
    new_collection_id = db.clone_collection_for_user(collection_id, admin_id)
    
    if new_collection_id > 0:
        await query.answer("✅ האוסף שוכפל בהצלחה!", show_alert=True)
        await show_collection_card(query, context, collection_id)
    else:
        await query.answer("❌ שגיאה בשכפול האוסף", show_alert=True)


# === Shares Functions ===

async def show_shares_dashboard(query, context: ContextTypes.DEFAULT_TYPE, page: int = 1):
    """מסך ראשי שיתופים - תצוגת טקסט עם pagination"""
    total_shares = db.count_active_shares()

    if total_shares == 0:
        await query.edit_message_text(
            "🔗 <b>שיתופים</b>\n\nאין קודי שיתוף פעילים במערכת.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ חזור", callback_data="admin_back_to_main")]]),
            parse_mode="HTML"
        )
        return

    # Pagination settings
    items_per_page = 20
    total_pages = (total_shares + items_per_page - 1) // items_per_page

    # Ensure page is in valid range
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * items_per_page
    page_shares = db.get_all_active_shares(offset=start_idx, limit=items_per_page)

    message_text = f"🔗 <b>שיתופים פעילים</b> (עמוד {page}/{total_pages})\n\n"
    
    for idx, (share_id, share_code, collection_id, collection_name, created_by, creator_username, created_at, unique_users, total_accesses) in enumerate(page_shares, start=start_idx + 1):
        # Format date
        try:
            date_obj = datetime.fromisoformat(created_at)
            date_str = date_obj.strftime("%d/%m/%Y")
        except ValueError:
            date_str = created_at[:10]
        
        creator_display = creator_username if creator_username else f"User_{created_by}"
        safe_creator_display = html.escape(creator_display)
        
        # Create clickable user link
        user_link = f'<a href="tg://user?id={created_by}">{safe_creator_display}</a>'
        
        # Escape HTML characters in collection name
        safe_collection_name = html.escape(collection_name)
        
        message_text += (
            f"📦 {idx}. {safe_collection_name}\n"
            f"   🔑 קוד: <code>{share_code}</code>\n"
            f"   👤 יוצר: {user_link}\n"
            f"   📅 תאריך: {date_str}\n"
            f"   👥 משתמשים: {unique_users} | 👁️ צפיות: {total_accesses}\n\n"
        )
    
    # Build keyboard with pagination
    keyboard = []
    
    # Pagination buttons
    if total_pages > 1:
        nav_buttons = []
        if page > 1:
            nav_buttons.append(InlineKeyboardButton("◀️ הקודם", callback_data=f"admin_shares_page:{page-1}"))
        if page < total_pages:
            nav_buttons.append(InlineKeyboardButton("הבא ▶️", callback_data=f"admin_shares_page:{page+1}"))
        if nav_buttons:
            keyboard.append(nav_buttons)
    
    # Back button
    keyboard.append([InlineKeyboardButton("⬅️ חזור", callback_data="admin_back_to_main")])
    
    await query.edit_message_text(
        message_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def show_share_card(query, context: ContextTypes.DEFAULT_TYPE, share_id: int):
    """כרטיס שיתוף בודד - עם כפתורי פעולה"""
    share_info = db.get_active_share_by_id(share_id)

    if not share_info:
        await query.answer("שיתוף לא נמצא", show_alert=True)
        return
    
    share_id, share_code, collection_id, collection_name, created_by, creator_username, created_at, unique_users, total_accesses = share_info
    
    # Get detailed stats
    stats = db.get_share_stats(share_code)
    item_count = db.count_items_in_collection(collection_id)
    
    # Get recent users (up to 10)
    recent_logs = db.get_detailed_access_log(share_code, limit=10)
    
    # Format date
    try:
        date_obj = datetime.fromisoformat(created_at)
        date_str = date_obj.strftime("%d/%m/%Y %H:%M")
    except ValueError:
        date_str = created_at[:16]
    
    creator_display = creator_username if creator_username else f"User_{created_by}"
    safe_creator_display = html.escape(creator_display)
    creator_link = f'<a href="tg://user?id={created_by}">{safe_creator_display}</a>'
    
    # Escape HTML
    safe_collection_name = html.escape(collection_name)
    
    message_text = (
        f"🔗 <b>כרטיס שיתוף</b>\n\n"
        f"📦 אוסף: {safe_collection_name}\n"
        f"🆔 קוד: <code>{share_code}</code>\n"
        f"👤 יוצר: {creator_link}\n"
        f"📅 נוצר: {date_str}\n"
        f"📁 קבצים: {item_count}\n"
        f"👥 גישות ייחודיות: {stats['unique_users']}\n"
        f"📊 גישות כולל: {stats['total_accesses']}\n\n"
    )
    
    if recent_logs:
        message_text += "<b>משתמשים אחרונים:</b>\n"
        for log_user_id, username, first_name, accessed_at in recent_logs[:5]:
            user_display = first_name or username or f"User_{log_user_id}"
            safe_user_display = html.escape(user_display)
            user_link = f'<a href="tg://user?id={log_user_id}">{safe_user_display}</a>'
            message_text += f"• {user_link}\n"
    
    keyboard = [
        [InlineKeyboardButton("🚫 השבת קוד", callback_data=f"admin_share_disable:{share_code}")],
        [InlineKeyboardButton("🔄 צור קוד חדש", callback_data=f"admin_share_create:{collection_id}")],
        [InlineKeyboardButton("📊 לוג מלא", callback_data=f"admin_share_logs:{share_code}")],
        [InlineKeyboardButton("⬅️ חזור", callback_data="admin_shares")]
    ]
    
    await query.edit_message_text(
        message_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def disable_share_action(query, context: ContextTypes.DEFAULT_TYPE, share_code: str):
    """השבתת קוד שיתוף"""
    # Get collection id from share code
    collection_info = db.get_collection_by_share_code(share_code)
    
    if not collection_info:
        await query.answer("❌ קוד שיתוף לא נמצא", show_alert=True)
        return
    
    collection_id, _, owner_id = collection_info
    
    success = db.revoke_share_code(collection_id, owner_id)
    
    if success:
        await query.answer("✅ קוד השיתוף הושבת!", show_alert=True)
        await show_shares_dashboard(query, context)
    else:
        await query.answer("❌ שגיאה בהשבתת הקוד", show_alert=True)


async def create_new_share_action(query, context: ContextTypes.DEFAULT_TYPE, collection_id: int):
    """יצירת קוד שיתוף חדש"""
    collection = db.get_collection_by_id(collection_id)
    
    if not collection:
        await query.answer("❌ אוסף לא נמצא", show_alert=True)
        return
    
    _, _, owner_id = collection
    admin_id = query.from_user.id
    
    # Create new share code
    new_code = db.create_share_link(collection_id, admin_id)
    
    await query.answer(f"✅ קוד חדש נוצר: {new_code}", show_alert=True)
    await show_shares_dashboard(query, context)


async def show_share_access_log(query, context: ContextTypes.DEFAULT_TYPE, share_code: str, offset: int = 0):
    """לוג גישות מפורט"""
    logs = db.get_detailed_access_log(share_code, offset=offset, limit=20)
    
    if not logs:
        await query.edit_message_text(
            "📊 <b>לוג גישות</b>\n\nאין גישות רשומות.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ חזור", callback_data=f"admin_shares")]]),
            parse_mode="HTML"
        )
        return
    
    # Get share info for title
    collection_info = db.get_collection_by_share_code(share_code)
    collection_name = collection_info[1] if collection_info else "Unknown"
    safe_collection_name = html.escape(collection_name)
    
    message_text = f"📊 <b>לוג גישות - {safe_collection_name}</b>\n\n"
    
    for user_id, username, first_name, accessed_at in logs:
        user_display = first_name or username or f"User_{user_id}"
        safe_user_display = html.escape(user_display)
        user_link = f'<a href="tg://user?id={user_id}">{safe_user_display}</a>'
        
        try:
            date_obj = datetime.fromisoformat(accessed_at)
            date_str = date_obj.strftime("%d/%m %H:%M")
        except ValueError:
            date_str = accessed_at[:16]
        
        message_text += f"• {user_link} - {date_str}\n"
    
    keyboard = [[InlineKeyboardButton("⬅️ חזור", callback_data=f"admin_shares")]]
    
    await query.edit_message_text(
        message_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


# === Additional Admin Functions ===

async def show_user_collections(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """תצוגת אוספים של משתמש ספציפי"""
    collections = db.get_collections(user_id)
    
    if not collections:
        await query.edit_message_text(
            f"למשתמש {user_id} אין אוספים.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ חזור", callback_data=f"admin_user_card:{user_id}")]])
        )
        return
    
    keyboard = []
    for col_id, name in collections:
        keyboard.append([InlineKeyboardButton(name, callback_data=f"admin_manage_col:{col_id}")])
    
    keyboard.append([InlineKeyboardButton("⬅️ חזור", callback_data=f"admin_user_card:{user_id}")])
    
    await query.edit_message_text(
        f"📦 <b>אוספים של משתמש {user_id}:</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def show_admin_collection_management(query, context: ContextTypes.DEFAULT_TYPE, collection_id: int):
    """ניהול אוסף ספציפי מתוך ממשק הניהול"""
    collection = db.get_collection_by_id(collection_id)
    
    if not collection:
        await query.edit_message_text(
            "האוסף לא נמצא.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ חזור", callback_data="admin_collections")]])
        )
        return
    
    col_id, col_name, owner_id = collection
    item_count = db.count_items_in_collection(collection_id)
    
    # Escape HTML characters in collection name
    safe_col_name = html.escape(col_name)
    
    keyboard = [
        [InlineKeyboardButton("👁️ צפה בתוכן", callback_data=f"browse_page:{collection_id}:1")],
        [InlineKeyboardButton("🗑️ מחק אוסף", callback_data=f"admin_delete_col:{collection_id}")],
        [InlineKeyboardButton("📋 שכפל אוסף אלי", callback_data=f"admin_clone_collection:{collection_id}")],
        [InlineKeyboardButton("⬅️ חזור לאוספי המשתמש", callback_data=f"admin_user_cols:{owner_id}")]
    ]
    
    await query.edit_message_text(
        f"📦 <b>ניהול אוסף: {safe_col_name}</b>\n\n"
        f"👤 בעלים: {owner_id}\n"
        f"📄 פריטים: {item_count}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def confirm_delete_collection(query, context: ContextTypes.DEFAULT_TYPE, collection_id: int):
    """אישור מחיקת אוסף"""
    collection = db.get_collection_by_id(collection_id)
    
    if not collection:
        await query.answer("האוסף לא נמצא", show_alert=True)
        return
    
    col_name = collection[1]
    # Escape HTML characters in collection name
    safe_col_name = html.escape(col_name)
    
    keyboard = [
        [InlineKeyboardButton("✅ כן, מחק", callback_data=f"admin_confirm_delete:{collection_id}")],
        [InlineKeyboardButton("❌ ביטול", callback_data=f"admin_manage_col:{collection_id}")]
    ]
    
    await query.edit_message_text(
        f"⚠️ <b>אזהרה!</b>\n\n"
        f"האם אתה בטוח שברצונך למחוק את האוסף <b>{safe_col_name}</b>?\n"
        f"פעולה זו תמחק גם את כל הפריטים באוסף.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def delete_collection_action(query, context: ContextTypes.DEFAULT_TYPE, collection_id: int):
    """מחיקת אוסף בפועל"""
    # Delete all items first
    db.delete_all_items_in_collection(collection_id)
    # Delete collection
    success = db.delete_collection(collection_id)

    if success:
        # Drop stale "active collection" pointers to the deleted collection
        for uid in list(constants.active_collections):
            if constants.active_collections.get(uid) == collection_id:
                constants.active_collections.pop(uid, None)
                constants.active_collection_timestamps.pop(uid, None)

        await query.edit_message_text(
            "✅ האוסף נמחק בהצלחה!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ חזור לתפריט ראשי", callback_data="admin_back_to_main")]])
        )
    else:
        await query.edit_message_text(
            "❌ שגיאה במחיקת האוסף.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ חזור", callback_data="admin_back_to_main")]])
        )


# === File ID Scan ===


async def show_scan_files_menu(query, context: ContextTypes.DEFAULT_TYPE):
    """
    Show the file scan confirmation screen from the admin menu.
    Displays the number of items that will be scanned and asks for confirmation.
    """
    total = db.count_file_items()
    DELAY = 0.05
    est_seconds = int(total * (DELAY + 0.07))
    if est_seconds < 60:
        eta_str = f"~{est_seconds} שניות"
    else:
        eta_str = f"~{est_seconds // 60} דקות ו-{est_seconds % 60} שניות"

    text = (
        "🔍 <b>סריקת קבצים פגומים</b>\n\n"
        f"סה\"כ פריטים עם file_id: <b>{total:,}</b>\n"
        f"זמן משוער: <b>{eta_str}</b>\n\n"
        "הסריקה בודקת האם כל ה-file_ids \"במסד הנתונים\" עדיין תקינים \"בשרתי טלגרם\".\n"
        "קבצים לא תקינים <b>לא יימחקו</b> — תקבל דוח בלבד.\n\n"
        "⚠️ הסריקה עשויה לקחת זמן. הבוט ימשיך לעבוד תקין במהלכה."
    )
    keyboard = [
        [InlineKeyboardButton("✅ התחל סריקה", callback_data="admin_scan_files_confirm")],
        [InlineKeyboardButton("⬅️ חזור", callback_data="admin_back_to_main")],
    ]
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


_SCAN_DELAY = 0.05  # seconds between Telegram requests

# Only one admin scan may run at a time (module-level reference to the running task)
_scan_task = None


def _scan_is_running() -> bool:
    return _scan_task is not None and not _scan_task.done()


def _format_eta(total: int) -> str:
    """Rough ETA string (about 0.07s of network time per item on top of the delay)."""
    est_seconds = int(total * (_SCAN_DELAY + 0.07))
    if est_seconds < 60:
        return f"~{est_seconds} שניות"
    return f"~{est_seconds // 60} דקות ו-{est_seconds % 60} שניות"


async def _run_scan_loop(context, total: int, target_col_id, scope_label: str, edit):
    """
    Check stored file_ids and report progress through `edit(text, final=False)`.
    Runs as a background task so the bot keeps handling other updates.
    """
    try:
        invalid_count = 0
        invalid_sample = []  # Keep only the rows shown in the final report.
        type_breakdown: dict = {}
        checked = 0
        update_every = max(10, total // 20)  # update ~20 times total

        offset = 0
        page_size = 100
        while checked < total:
            page = db.get_file_items_page(offset, min(page_size, total - checked), target_col_id)
            if not page:
                break
            for item_id, c_type, fid in page:
                is_valid = await _check_file_id(context.bot, fid, c_type)
                if not is_valid:
                    invalid_count += 1
                    type_breakdown[c_type] = type_breakdown.get(c_type, 0) + 1
                    if len(invalid_sample) < 20:
                        invalid_sample.append((item_id, c_type))
                checked += 1
                await asyncio.sleep(_SCAN_DELAY)

                if checked % update_every == 0 or checked == total:
                    pct = int(checked / total * 100)
                    await edit(
                        f"🔍 <b>סריקת file_ids</b> — {scope_label}\n"
                        f"✅ נבדקו: {checked:,} / {total:,} ({pct}%)\n"
                        f"❌ לא תקינים עד כה: {invalid_count}"
                    )
            offset += len(page)

        valid_count = checked - invalid_count

        breakdown_lines = "\n".join(
            f"  • {ct}: {cnt}" for ct, cnt in sorted(type_breakdown.items())
        ) or "  —"

        report = (
            f"📋 <b>דוח סריקת file_ids</b>\n"
            f"היקף: {scope_label}\n\n"
            f"📦 נסרקו: {checked:,}\n"
            f"✅ תקינים: {valid_count:,}\n"
            f"❌ לא תקינים: {invalid_count}\n\n"
            f"<b>לא תקינים לפי סוג:</b>\n{breakdown_lines}"
        )

        if invalid_sample:
            sample_lines = "\n".join(f"  ID {iid} ({ct})" for iid, ct in invalid_sample)
            report += f"\n\n<b>מזהי פריטים לא תקינים (עד 20):</b>\n{sample_lines}"
            if invalid_count > 20:
                report += f"\n  ... ועוד {invalid_count - 20} נוספים"

        await edit(report, final=True)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Admin file scan failed")
        await edit("❌ הסריקה נכשלה עקב שגיאה. פרטים בלוג.", final=True)


def _make_scan_editor(edit_text):
    """Wrap a message-edit coroutine so progress edits swallow errors and the final one is sent safely."""
    async def edit(text: str, final: bool = False):
        try:
            await edit_text(text, final)
        except Exception:  # pylint: disable=broad-exception-caught
            if final:
                # Most likely message too long: retry with a trimmed report
                try:
                    trimmed = text[:3800] + "\n\n⚠️ הדוח קוצר עקב מגבלת אורך הודעה."
                    await edit_text(trimmed, final)
                except Exception:  # pylint: disable=broad-exception-caught
                    logger.warning("Failed to deliver admin scan report")
            # Progress edits: ignore "message not modified" or flood-control errors
    return edit


async def run_scan_files_inline(query, context: ContextTypes.DEFAULT_TYPE):
    """
    Start the file_id validity scan triggered from the admin inline menu.
    The scan runs as a background task and edits the same message with live
    progress and a final report.
    """
    global _scan_task

    if _scan_is_running():
        await query.answer("⏳ כבר רצה סריקה. המתן לסיומה.", show_alert=True)
        return

    await query.answer("⏳ מתחיל סריקה...")

    total = db.count_file_items()

    if total == 0:
        await query.edit_message_text(
            "ℹ️ אין פריטים עם file_id לסריקה.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ חזור", callback_data="admin_back_to_main")]
            ])
        )
        return

    # Show initial progress message (no keyboard during scan — prevents double-click)
    try:
        await query.edit_message_text(
            f"🔍 <b>סריקת file_ids — כל המאגר</b>\n"
            f"סורק {total:,} פריטים | {_format_eta(total)}\n"
            f"0 / {total:,}",
            parse_mode="HTML"
        )
    except Exception:  # pylint: disable=broad-exception-caught
        pass

    back_markup = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ חזור לתפריט", callback_data="admin_back_to_main")]])

    async def edit_text(text: str, final: bool):
        await query.edit_message_text(
            text, parse_mode="HTML", reply_markup=back_markup if final else None
        )

    _scan_task = context.application.create_task(
        _run_scan_loop(context, total, None, "כל המאגר", _make_scan_editor(edit_text))
    )


async def scan_file_ids(update, context: ContextTypes.DEFAULT_TYPE):
    """
    Admin command /scanfiles — checks which stored file_ids are no longer valid.

    Usage:
        /scanfiles                   → scan all items (warns if >500)
        /scanfiles <col_id>          → scan a specific collection
        /scanfiles <col_id> <limit>  → scan at most <limit> items from that collection
        /scanfiles all <limit>       → scan at most <limit> items from the whole DB
    """
    global _scan_task

    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("⛔ אין לך הרשאות.")
        return

    if _scan_is_running():
        await update.message.reply_text("⏳ כבר רצה סריקה. המתן לסיומה.")
        return

    # ── Parse arguments ──────────────────────────────────────────────
    args = context.args or []
    target_col_id = None
    item_limit = None  # None = no limit

    if args:
        first = args[0]
        if first.lower() != "all":
            try:
                target_col_id = int(first)
            except ValueError:
                await update.message.reply_text(
                    "❌ שימוש:\n"
                    "/scanfiles\n"
                    "/scanfiles <col_id>\n"
                    "/scanfiles <col_id> <limit>\n"
                    "/scanfiles all <limit>"
                )
                return
        if len(args) >= 2:
            try:
                item_limit = max(1, int(args[1]))
            except ValueError:
                await update.message.reply_text("❌ limit חייב להיות מספר שלם חיובי.")
                return

    # ── Validate scope without loading all file IDs ────────────────────
    if target_col_id is not None:
        col = db.get_collection_by_id(target_col_id)
        if not col:
            await update.message.reply_text(f"❌ אוסף {target_col_id} לא נמצא.")
            return
        scope_label = f"אוסף #{target_col_id} ({html.escape(col[1])})"
    else:
        scope_label = "כל המאגר"

    available = db.count_file_items(target_col_id)
    total = min(available, item_limit) if item_limit else available
    if item_limit:
        scope_label += f" (ראשונים {item_limit})"

    if total == 0:
        await update.message.reply_text("אין פריטים עם file_id לסריקה.")
        return

    eta_str = _format_eta(total)

    # Warn if no limit was given and collection is large
    if item_limit is None and total > 500:
        await update.message.reply_text(
            f"⚠️ <b>שים לב:</b> יש {total:,} פריטים לסריקה — זמן משוער: {eta_str}.\n\n"
            f"טיפ: השתמש ב-\n"
            f"<code>/scanfiles {target_col_id or 'all'} 200</code>\n"
            f"כדי לסרוק רק את 200 הפריטים הראשונים.\n\n"
            f"שלח את הפקודה שוב עם limit אם תרצה להגביל.",
            parse_mode="HTML"
        )
        return

    status_msg = await update.message.reply_text(
        f"🔍 <b>סריקת file_ids</b> — {scope_label}\n"
        f"סורק {total:,} פריטים | זמן משוער: {eta_str}\n"
        f"0 / {total:,}",
        parse_mode="HTML"
    )

    async def edit_text(text: str, final: bool):
        await status_msg.edit_text(text, parse_mode="HTML")

    _scan_task = context.application.create_task(
        _run_scan_loop(context, total, target_col_id, scope_label, _make_scan_editor(edit_text))
    )
