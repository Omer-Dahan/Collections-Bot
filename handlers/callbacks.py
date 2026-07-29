"""
Callback handlers for the Collections Bot.
This module contains general callbacks for collection management and sharing.
Browsing-related callbacks have been moved to browse_handlers.py.
"""
import asyncio
import tempfile
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
import db
from constants import (
    active_collections, active_shared_collections,
    active_shared_collection_timestamps,
)
from utils import (
    reset_user_modes,
    set_active_collection,
    create_verification_code,
    get_main_menu_text, build_main_menu_keyboard,
    parse_callback_data,
    parse_and_validate_access
)
from handlers.commands import (
    new_collection_flow, list_collections_flow, manage_collections_flow,
    remove_flow, id_file_flow, show_browse_menu
)
from config import is_admin
from archive_logger import log_activity, ENABLE_ARCHIVING

async def handle_select_collection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """בחירת אוסף פעיל לשמירה (לא קשור לדפדוף)"""
    query = update.callback_query
    await query.answer()

    parts = parse_callback_data(query.data, "select_collection")
    is_allowed, col_id, col = await parse_and_validate_access(update, context, parts)
    if not is_allowed:
        return

    user_id = query.from_user.id
    set_active_collection(user_id, col_id)

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(text="🛑 הפסק הוספה", callback_data="stop_collect")]]
    )

    await query.edit_message_text(
        text=f"✅ האוסף '**{col[1]}**' הוגדר כפעיל.\nכל קובץ שתשלח מעכשיו יתווסף אליו.",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

async def handle_select_item_delete_col_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """בחירת אוסף למחיקת פריטים (מצב מחיקה)"""
    query = update.callback_query
    await query.answer()

    parts = parse_callback_data(query.data, "select_item_del_col")
    is_allowed, col_id, col = await parse_and_validate_access(update, context, parts)
    if not is_allowed:
        return

    context.user_data["item_delete_mode"] = True
    context.user_data["delete_target_collection_id"] = col_id

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏁 סיום מחיקה", callback_data="back_to_main")]
    ])

    text = (
        f"🗑 **מצב מחיקת פריטים הופעל עבור: {col[1]}**\n\n"
        "שלח לי כעת תמונה, וידאו או קובץ שקיים באוסף זה, ואני אמחק אותו עבורך.\n"
        "תוכל למחוק מספר פריטים ברצף."
    )

    await query.edit_message_text(text=text, reply_markup=keyboard, parse_mode="Markdown")


async def handle_collection_send_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Request confirmation with verification code before sending all items in a collection."""
    query = update.callback_query
    await query.answer()

    parts = parse_callback_data(query.data, "collection_send_all")
    is_allowed, col_id, col = await parse_and_validate_access(update, context, parts)
    if not is_allowed:
        return

    total_items = db.count_items_in_collection(col_id)
    if total_items == 0:
        await query.answer("האוסף ריק", show_alert=True)
        return

    code = create_verification_code(
        context,
        "send_collection",
        {
            "collection_id": col_id,
            "msg_id": query.message.message_id
        }
    )

    text = (
        f"⚠️ **אישור שליחת אוסף מלא**\n\n"
        f"אתה עומד לשלוח את כל האוסף: {col[1]} ({total_items} פריטים).\n"
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
            [InlineKeyboardButton("❌ ביטול", callback_data=f"browse_page:{col_id}:1")]
        ])
    )

async def handle_stop_collect_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop active collection mode (adding items) for a user."""
    query = update.callback_query
    await query.answer("מצב איסוף נעצר")

    reset_user_modes(context, query.from_user.id)

    try:
        await query.edit_message_text(
            "🛑 מצב איסוף נעצר.\nתוכל לחזור ולהוסיף קבצים דרך התפריט הראשי.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 תפריט ראשי", callback_data="back_to_main")]
            ])
        )
    except Exception as e: # pylint: disable=broad-exception-caught
        if "message is not modified" not in str(e).lower():
            logger.debug("Could not edit batch stop message: %s", e)

async def handle_delete_select_collection_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Handle collection selection for delete mode"""
    query = update.callback_query
    await query.answer()

    data = query.data
    parts = parse_callback_data(data, "delete_collection")
    is_allowed, col_id, col = await parse_and_validate_access(update, context, parts)
    if not is_allowed:
        return

    code = create_verification_code(
        context,
        "delete_collection",
        {"collection_id": col_id}
    )

    item_count = db.count_items_in_collection(col_id)

    text = (
        f"⚠️ **בטוח שאתה רוצה למחוק את האוסף?**\n\n"
        f"📌 שם האוסף: **{col[1]}**\n"
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
    """טיפול בכפתורי התפריט הראשי"""
    query = update.callback_query
    await query.answer()

    data = query.data
    action = data.split(":")[1]

    if action == "newcollection":
        await new_collection_flow(
            query.message, query.from_user, context, [],
            edit_message_id=query.message.message_id
        )

    elif action == "browse":
        await show_browse_menu(
            query.message.chat_id, query.from_user.id, context,
            edit_message_id=query.message.message_id
        )

    elif action == "collections":
        await list_collections_flow(update, context, edit_message_id=query.message.message_id)

    elif action == "manage":
        await manage_collections_flow(update, context, edit_message_id=query.message.message_id)

    elif action == "remove":
        await remove_flow(
            query.message, query.from_user, context, [],
            edit_message_id=query.message.message_id
        )

    elif action == "id_file":
        await id_file_flow(
            query.message, query.from_user, context,
            edit_message_id=query.message.message_id
        )

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
        await new_collection_flow(
            query.message, query.from_user, context, [],
            edit_message_id=query.message.message_id
        )

    elif action == "select_collection":
        await list_collections_flow(update, context, edit_message_id=query.message.message_id)

async def handle_back_to_main_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """חזרה לתפריט הראשי"""
    query = update.callback_query
    await query.answer()

    # Reset all modes and active collections when returning to main menu
    reset_user_modes(context, query.from_user.id)

    try:
        await query.edit_message_text(
            text=get_main_menu_text(),
            reply_markup=build_main_menu_keyboard()
        )
    except Exception: # pylint: disable=broad-exception-caught
        await query.message.reply_text(
            text=get_main_menu_text(),
            reply_markup=build_main_menu_keyboard()
        )

async def handle_manage_collection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show management options for a specific collection"""
    query = update.callback_query
    await query.answer()

    parts = parse_callback_data(query.data, "manage_collection")
    is_allowed, col_id, col = await parse_and_validate_access(update, context, parts)
    if not is_allowed:
        return

    is_admin_v = is_admin(query.from_user.id) and col[2] != query.from_user.id

    keyboard = []

    if not is_admin_v:
        keyboard = [
            [InlineKeyboardButton(
                "📊 מידע על האוסף", callback_data=f"collection_info:{col_id}"
            )],
            [InlineKeyboardButton(
                "📤 ייצוא לקובץ (גיבוי)", callback_data=f"export_collection:{col_id}"
            )],
            [InlineKeyboardButton(
                "🔗 יצירת קישור שיתוף", callback_data=f"share_collection:{col_id}"
            )],
            [InlineKeyboardButton(
                "🔎 סריקת קבצים כפולים", callback_data=f"scan_duplicates:{col_id}"
            )],
            [InlineKeyboardButton(
                "🗑 מחיקת אוסף", callback_data=f"delete_collection:{col_id}"
            )],
            [InlineKeyboardButton("🔙 חזור לרשימה", callback_data="back_to_manage")],
        ]
    else:
        keyboard = [
            [InlineKeyboardButton(
                "📊 מידע על האוסף", callback_data=f"collection_info:{col_id}"
            )],
            [InlineKeyboardButton(
                "📂 צפייה בתוכן (Admin)", callback_data=f"browse_page:{col_id}:1"
            )],
            [InlineKeyboardButton(
                "🗑 מחיקת אוסף (Admin)", callback_data=f"delete_collection:{col_id}"
            )],
            [InlineKeyboardButton("🔙 חזור לרשימה", callback_data="back_to_manage")],
        ]

    await query.edit_message_text(
        f"ניהול אוסף: **{col[1]}**",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def handle_collection_info_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show detailed statistics about a collection."""
    query = update.callback_query
    await query.answer()

    parts = parse_callback_data(query.data, "collection_info")
    is_allowed, col_id, col = await parse_and_validate_access(update, context, parts)
    if not is_allowed:
        return

    stats = db.get_collection_stats(col_id)

    def fmt_size(size_bytes: int) -> str:
        """Format bytes into a human-readable string."""
        if size_bytes == 0:
            return "0 B"
        for unit in ["B", "KB", "MB", "GB"]:
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f} TB"

    def fmt_date(iso_str: str | None) -> str:
        """Format an ISO datetime string to a readable short date."""
        if not iso_str:
            return "לא ידוע"
        try:
            return iso_str[:16].replace("T", " ")
        except Exception:  # pylint: disable=broad-exception-caught
            return iso_str

    lines = [
        f"📊 **מידע על האוסף: {col[1]}**\n",
        f"📦 **סה\"כ פריטים:** {stats['total_count']}",
    ]

    if stats["video_count"] > 0:
        lines.append(
            f"🎬 **סרטונים:** {stats['video_count']} "
            f"({fmt_size(stats['video_size_bytes'])})"
        )

    if stats["photo_count"] > 0:
        lines.append(
            f"🖼 **תמונות:** {stats['photo_count']} "
            f"({fmt_size(stats['photo_size_bytes'])})"
        )

    if stats["document_count"] > 0:
        lines.append(
            f"📄 **קבצים:** {stats['document_count']} "
            f"({fmt_size(stats['document_size_bytes'])})"
        )

    if stats["text_count"] > 0:
        lines.append(f"💬 **הודעות טקסט:** {stats['text_count']}")

    lines.append(f"\n💾 **גודל כולל:** {fmt_size(stats['total_size_bytes'])}")

    lines.append(f"\n📅 **פריט ראשון נוסף:** {fmt_date(stats['first_item_date'])}")
    lines.append(f"🕐 **פריט אחרון נוסף:** {fmt_date(stats['last_item_date'])}")

    text = "\n".join(lines)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 חזור לניהול", callback_data=f"manage_collection:{col_id}")]
    ])

    await query.edit_message_text(text=text, reply_markup=keyboard, parse_mode="Markdown")


async def handle_share_collection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate or display share code for a collection."""
    query = update.callback_query
    await query.answer()
    user = query.from_user

    parts = parse_callback_data(query.data, "share_collection")
    is_allowed, col_id, col = await parse_and_validate_access(update, context, parts)
    if not is_allowed:
        return

    share_code = db.create_share_link(col_id, user.id)
    _log_share_creation(context.bot, user, col_id, share_code)

    logs = db.get_share_access_logs(col_id)
    expiry = db.get_share_expiration(col_id)
    expiry_txt = f"⏱️ תפוגה: {expiry[:16].replace('T', ' ')}" if expiry else "⏱️ ללא תפוגה"

    text = (
        f"קוד שיתוף לאוסף: {col[1]}\n\n"
        f"📋 קוד: `{share_code}`\n\n"
        f"👥 מספר גישות: {len(logs)}\n"
        f"{expiry_txt}\n\n"
        "💡 שלח את הקוד הזה למשתמשים אחרים.\n"
        "הם יוכלו לגשת לאוסף באמצעות הפקודה /access."
    )

    await query.edit_message_text(
        text=text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(_build_share_keyboard(col_id))
    )

def _log_share_creation(bot, user, col_id, share_code):
    """Helper to log share creation activity."""
    if ENABLE_ARCHIVING:
        asyncio.create_task(
            log_activity(
                bot=bot, action="SHARE_CREATED", user_id=user.id,
                collection_id=col_id, extra={"share_code": share_code[:10] + "..."},
                user_name=user.full_name, username=user.username
            )
        )

def _build_share_keyboard(col_id: int) -> list:
    """Helper to build share management keyboard."""
    return [
        [InlineKeyboardButton("📊 סטטיסטיקות גישה", callback_data=f"share_stats:{col_id}")],
        [InlineKeyboardButton("🔄 החלף קוד שיתוף", callback_data=f"regenerate_share:{col_id}")],
        [InlineKeyboardButton("⏰ הגדר תפוגה", callback_data=f"set_share_expiration:{col_id}")],
        [InlineKeyboardButton("🚫 ביטול שיתוף", callback_data=f"revoke_share:{col_id}")],
        [InlineKeyboardButton("🔙 חזור לניהול", callback_data=f"manage_collection:{col_id}")]
    ]


async def handle_share_stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show access statistics for a shared collection"""
    query = update.callback_query
    await query.answer()

    parts = parse_callback_data(query.data, "share_stats")
    is_allowed, col_id, _ = await parse_and_validate_access(update, context, parts)
    if not is_allowed:
        return

    logs = db.get_share_access_logs(col_id)
    text = _build_share_stats_text(logs)
    keyboard = [[InlineKeyboardButton("🔙 חזור", callback_data=f"share_collection:{col_id}")]]

    await query.edit_message_text(
        text=text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

def _build_share_stats_text(logs: list) -> str:
    """Helper to format share access logs."""
    if not logs:
        return "📊 אין עדיין צפיות באוסף המשותף הזה."

    text = f"📊 **סטטיסטיקות צפייה ({len(logs)} משתמשים):**\n\n"
    for _, username, first_name, accessed_at in logs:
        safe_username = username.replace("_", "\\_") if username else ""
        name = f"{first_name} " + (f"(@{safe_username})" if username else "")
        try:
            date_str = accessed_at[:16].replace("T", " ")
        except (TypeError, ValueError, IndexError): # pylint: disable=broad-exception-caught
            date_str = accessed_at
        text += f"👤 {name} - {date_str}\n"
    return text

async def handle_regenerate_share_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Regenerate share code for a collection"""
    query = update.callback_query
    await query.answer("קוד שיתוף הוחלף")

    parts = parse_callback_data(query.data, "regenerate_share")
    is_allowed, col_id, _ = await parse_and_validate_access(update, context, parts)
    if not is_allowed:
        return

    new_code = db.regenerate_share_code(col_id, query.from_user.id)

    if new_code:
        # Provide same view as initial share creation but updated
        text = (
            f"🔄 **הקוד הוחלף בהצלחה!**\n\n"
            f"הקוד החדש:\n`{new_code}`\n\n"
            f"הקוד הישן מבוטל ולא יעבוד יותר."
        )
        keyboard = [
             [InlineKeyboardButton("🔙 חזור", callback_data=f"manage_collection:{col_id}")]
        ]
        await query.edit_message_text(
            text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )

async def handle_revoke_share_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Revoke share code for a collection"""
    query = update.callback_query
    await query.answer("שיתוף בוטל")
    parts = parse_callback_data(query.data, "revoke_share")
    is_allowed, col_id, _ = await parse_and_validate_access(update, context, parts)
    if not is_allowed:
        return

    db.revoke_share_code(col_id, query.from_user.id)

    # Log share revocation
    if ENABLE_ARCHIVING:
        asyncio.create_task(
            log_activity(
                bot=context.bot,
                action="SHARE_REVOKED",
                user_id=query.from_user.id,
                collection_id=col_id,
                user_name=query.from_user.full_name,
                username=query.from_user.username
            )
        )

    await query.edit_message_text(
        "🚫 השיתוף בוטל בהצלחה.\nאף אחד לא יוכל לגשת לאוסף יותר דרך קוד.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 חזור לניהול", callback_data=f"manage_collection:{col_id}")]
        ])
    )

async def handle_export_collection_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Export whole collection to a text file for backup."""
    query = update.callback_query
    await query.answer("מכין קובץ גיבוי...")

    parts = parse_callback_data(query.data, "export_collection")
    is_allowed, col_id, col = await parse_and_validate_access(update, context, parts)
    if not is_allowed:
        return

    if db.count_items_in_collection(col_id) == 0:
        await query.edit_message_text("האוסף ריק, אין מה לייצא.")
        return

    filename = f"Build_Collection_{col_id}_backup.txt"
    # Spill to disk after 1 MiB rather than holding the entire export in RAM.
    with tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b") as export_file:
        header = (
            f"# COLLECTION EXPORT: {col[1]}\n"
            f"# DATE: {db.datetime.now()}\n"
            "# DO NOT EDIT THIS FILE\n\n"
        )
        export_file.write(header.encode("utf-8"))
        offset = 0
        page_size = 500
        while True:
            items = db.get_items_by_collection(col_id, offset=offset, limit=page_size)
            if not items:
                break
            for item in items:
                text = (item[3] or "").replace("|", "<PIPE>").replace("\n", "<NL>")
                line = f"{item[1]}|{item[2]}|{text}|{item[4] or ''}|{item[5] or 0}\n"
                export_file.write(line.encode("utf-8"))
            offset += len(items)
            if len(items) < page_size:
                break
        export_file.seek(0)
        await context.bot.send_document(
            chat_id=query.message.chat_id,
            document=export_file,
            caption=f"📦 גיבוי מלא לאוסף: {col[1]}",
            filename=filename
        )

def _generate_export_content(col_name: str, items: list) -> str:
    """Helper to generate export file content."""
    lines = [
        f"# COLLECTION EXPORT: {col_name}",
        f"# DATE: {db.datetime.now()}",
        "# DO NOT EDIT THIS FILE",
        ""
    ]
    for item in items:
        # Format: CONTENT_TYPE|FILE_ID|TEXT|FILENAME|SIZE
        text = (item[3] or "").replace("|", "<PIPE>").replace("\n", "<NL>")
        f_name = item[4] or ""
        f_size = str(item[5]) if item[5] else "0"
        lines.append(f"{item[1]}|{item[2]}|{text}|{f_name}|{f_size}")
    return "\n".join(lines)

async def handle_delete_collection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete a collection with confirmation"""
    # Simply redirects to shared handle_delete_select_collection_callback logic or similar
    # But since we have the ID already, we can reuse logic.
    await handle_delete_select_collection_callback(update, context)

async def handle_back_to_manage_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Go back to manage collections list"""
    query = update.callback_query
    await query.answer()

    await manage_collections_flow(
        update, context, edit_message_id=query.message.message_id
    )

async def handle_exit_shared_collection_callback(
    update: Update, _context: ContextTypes.DEFAULT_TYPE
):
    """Exit from viewing a shared collection"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if user_id in active_shared_collections:
        del active_shared_collections[user_id]
        active_shared_collection_timestamps.pop(user_id, None)
        db.set_user_active_share(user_id, None)  # Clear from DB persistence
    await query.edit_message_text(
        "יצאת מהאוסף המשותף.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
            "🏠 תפריט ראשי", callback_data="back_to_main"
        )]])
    )

async def handle_cancel_share_access_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel share code input"""
    query = update.callback_query
    await query.answer()

    reset_user_modes(context, query.from_user.id)
    await query.edit_message_text(
        "ביטלת את הכניסה לאוסף.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
            "🏠 תפריט ראשי", callback_data="back_to_main"
        )]])
    )

async def handle_exit_delete_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exit from delete mode"""
    query = update.callback_query
    await query.answer()

    reset_user_modes(context, query.from_user.id)
    await query.edit_message_text(
        "יצאת ממצב מחיקה.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
            "🏠 תפריט ראשי", callback_data="back_to_main"
        )]])
    )


async def handle_set_share_expiration_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show expiration time options for a shared collection"""
    query = update.callback_query
    await query.answer()

    parts = parse_callback_data(query.data, "set_share_expiration")
    is_allowed, col_id, _ = await parse_and_validate_access(update, context, parts)
    if not is_allowed:
        return

    keyboard = [
        [InlineKeyboardButton(
            "⌨️ זמן מותאם אישית", callback_data=f"custom_share_exp:{col_id}"
        )],
        [InlineKeyboardButton("שעה", callback_data=f"save_share_exp:{col_id}:1h")],
        [InlineKeyboardButton("12 שעות", callback_data=f"save_share_exp:{col_id}:12h")],
        [InlineKeyboardButton("24 שעות", callback_data=f"save_share_exp:{col_id}:24h")],
        [InlineKeyboardButton(
            "47 שעות (מקסימום)", callback_data=f"save_share_exp:{col_id}:47h"
        )],
        [InlineKeyboardButton("ללא תפוגה", callback_data=f"save_share_exp:{col_id}:never")],
        [InlineKeyboardButton("⬅️ חזור", callback_data=f"share_collection:{col_id}")]
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
    is_allowed, collection_id, _ = await parse_and_validate_access(update, context, parts)
    if not is_allowed:
        return

    try:
        duration = parts[1]
    except (ValueError, IndexError):
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
            [InlineKeyboardButton(
                "⬅️ חזור לשיתוף",
                callback_data=f"share_collection:{collection_id}"
            )]
        ])
    )


async def handle_custom_share_expiration_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Prompt user to enter custom expiration time in minutes"""
    query = update.callback_query
    await query.answer()

    parts = parse_callback_data(query.data, "custom_share_exp")
    is_allowed, col_id, _ = await parse_and_validate_access(update, context, parts)
    if not is_allowed:
        return

    # Initial duration: 60 minutes
    duration = 60

    # If there's an existing value in callback data,
    # use it (format: custom_share_exp:col_id:duration)
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
            InlineKeyboardButton(
                "- שעה", callback_data=f"custom_share_exp:{col_id}:{duration - 60}"
            ),
            InlineKeyboardButton(
                "- דקה", callback_data=f"custom_share_exp:{col_id}:{duration - 1}"
            ),
            InlineKeyboardButton(
                "+ דקה", callback_data=f"custom_share_exp:{col_id}:{duration + 1}"
            ),
            InlineKeyboardButton(
                "+ שעה", callback_data=f"custom_share_exp:{col_id}:{duration + 60}"
            ),
        ],
        [
            InlineKeyboardButton(
                "- 10 דק'", callback_data=f"custom_share_exp:{col_id}:{duration - 10}"
            ),
            InlineKeyboardButton(
                "+ 10 דק'", callback_data=f"custom_share_exp:{col_id}:{duration + 10}"
            ),
        ],
        [InlineKeyboardButton(
            "✅ שמירה", callback_data=f"save_share_exp_custom:{col_id}:{duration}"
        )],
        [InlineKeyboardButton("⬅️ חזור", callback_data=f"set_share_expiration:{col_id}")]
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
    except Exception as e: # pylint: disable=broad-exception-caught
        if "message is not modified" not in str(e).lower():
            logger.debug("Could not edit expiry time message: %s", e)


async def handle_save_custom_share_expiration_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Save the custom expiration time selected via interactive buttons"""
    query = update.callback_query
    await query.answer()
    user = query.from_user

    parts = parse_callback_data(query.data, "save_share_exp_custom")
    is_allowed, col_id, _ = await parse_and_validate_access(update, context, parts)
    if not is_allowed:
        return

    try:
        minutes = int(parts[1])
    except (ValueError, IndexError):
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
    success = db.set_share_expiration(col_id, user.id, expires_at)

    if success:
        text = f"✅ זמן תפוגה עודכן: {expiry_text}"
    else:
        text = "❌ לא הצלחנו לעדכן את זמן התפוגה"

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "⬅️ חזור לשיתוף", callback_data=f"share_collection:{col_id}"
            )]
        ])
    )
