"""
Handlers for scanning and removing invalid items in a specific collection.
Runs as an asynchronous background task to avoid blocking the bot.
"""

import asyncio
import html
import logging
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import db
from utils import parse_callback_data, parse_and_validate_access, check_file_id

logger = logging.getLogger(__name__)

# State TTL (seconds): Finished or idle scan results kept for 1 hour for deletion action
SCAN_STATE_TTL = 3600


def cleanup_invalid_scans(context: ContextTypes.DEFAULT_TYPE):
    """Clean up stale or completed scan states from bot_data."""
    scans = context.bot_data.get("invalid_scans")
    if not scans:
        return

    now = time.time()
    to_delete = []
    for col_id, state in list(scans.items()):
        status = state.get("status")
        updated_at = state.get("updated_at", 0)
        # Remove finished/cancelled/error states older than SCAN_STATE_TTL
        if status in ("finished", "cancelling", "error") and (now - updated_at > SCAN_STATE_TTL):
            to_delete.append(col_id)

    for col_id in to_delete:
        scans.pop(col_id, None)


async def _safe_edit_message(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str = "HTML"
) -> bool:
    """Helper to edit a scan status message safely without raising exceptions on race/no-change errors."""
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup
        )
        return True
    except Exception as e:
        err_str = str(e).lower()
        if "message is not modified" in err_str:
            return True
        logger.warning(f"Failed to edit scan message ({chat_id}/{message_id}): {e}")
        return False


def _get_scan_keyboard(col_id: int, status: str) -> InlineKeyboardMarkup:
    buttons = []
    if status == "running":
        buttons.append([
            InlineKeyboardButton("⏸ השהה", callback_data=f"scan_invalid_pause:{col_id}"),
            InlineKeyboardButton("🛑 בטל", callback_data=f"scan_invalid_cancel:{col_id}")
        ])
    elif status == "paused":
        buttons.append([
            InlineKeyboardButton("▶️ המשך", callback_data=f"scan_invalid_resume:{col_id}"),
            InlineKeyboardButton("🛑 בטל", callback_data=f"scan_invalid_cancel:{col_id}")
        ])

    buttons.append([InlineKeyboardButton("🔙 חזור לאוסף (ירוץ ברקע)", callback_data=f"manage_collection:{col_id}")])
    return InlineKeyboardMarkup(buttons)


async def start_invalid_scan_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the invalid items scan for a collection."""
    query = update.callback_query
    await query.answer()

    parts = parse_callback_data(query.data, "scan_invalid")
    is_allowed, col_id, col = await parse_and_validate_access(update, context, parts)
    if not is_allowed:
        return

    cleanup_invalid_scans(context)
    context.bot_data.setdefault("invalid_scans", {})

    existing_state = context.bot_data["invalid_scans"].get(col_id)
    if existing_state and existing_state.get("status") in ("running", "paused"):
        safe_name = html.escape(col[1])
        await query.edit_message_text(
            f"⚠️ סריקה כבר רצה או מושהית עבור האוסף <b>{safe_name}</b>.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזור לאוסף", callback_data=f"manage_collection:{col_id}")]])
        )
        return

    total_items = db.count_file_items(col_id)
    safe_col_name = html.escape(col[1])

    if total_items == 0:
        await query.edit_message_text(
            f"ℹ️ אין פריטים עם קבצים באוסף <b>{safe_col_name}</b> לסריקה.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזור לאוסף", callback_data=f"manage_collection:{col_id}")]])
        )
        return

    msg_text = (
        f"🔍 מתחיל סריקה לאיתור קבצים פגומים באוסף <b>{safe_col_name}</b>...\n"
        f"סה\"כ פריטים: {total_items:,}"
    )
    await query.edit_message_text(msg_text, parse_mode="HTML")

    user_id = query.from_user.id
    chat_id = query.message.chat_id
    message_id = query.message.message_id

    state = {
        "status": "running",
        "user_id": user_id,
        "col_id": col_id,
        "col_name": col[1],
        "total": total_items,
        "checked": 0,
        "invalid_count": 0,
        "invalid_ids": [],
        "chat_id": chat_id,
        "message_id": message_id,
        "last_update_text": "",
        "updated_at": time.time()
    }
    context.bot_data["invalid_scans"][col_id] = state

    task = asyncio.create_task(_scan_worker(col_id, context))
    state["task"] = task

    def _on_task_done(t: asyncio.Task):
        if not t.cancelled() and t.exception():
            logger.error(f"Scan worker task failed for col_id={col_id}: {t.exception()}")

    task.add_done_callback(_on_task_done)


async def _scan_worker(col_id: int, context: ContextTypes.DEFAULT_TYPE):
    state = context.bot_data.get("invalid_scans", {}).get(col_id)
    if not state:
        return

    total = state["total"]
    safe_col_name = html.escape(state["col_name"])
    chat_id = state["chat_id"]
    message_id = state["message_id"]

    offset = 0
    page_size = 100

    try:
        while state["checked"] < total:
            if state["status"] == "cancelling":
                break

            while state["status"] == "paused":
                await asyncio.sleep(1)
                if state["status"] == "cancelling":
                    break

            if state["status"] == "cancelling":
                break

            page = db.get_file_items_page(offset, min(page_size, total - state["checked"]), col_id)
            if not page:
                break

            for item_id, c_type, fid in page:
                if state["status"] == "cancelling":
                    break

                while state["status"] == "paused":
                    await asyncio.sleep(1)
                    if state["status"] == "cancelling":
                        break

                is_valid = await check_file_id(context.bot, fid, c_type)
                if not is_valid:
                    state["invalid_count"] += 1
                    state["invalid_ids"].append(item_id)

                state["checked"] += 1
                state["updated_at"] = time.time()
                await asyncio.sleep(0.05)  # Rate limiting / Network yield

                # Update UI every 50 items or on completion
                if state["checked"] % 50 == 0 or state["checked"] == total:
                    pct = int(state["checked"] / total * 100)
                    status_label = "▶️ רץ" if state["status"] == "running" else "⏸ מושהה"
                    text = (
                        f"🔍 <b>סריקת קבצים פגומים</b> — {safe_col_name}\n\n"
                        f"✅ נבדקו: {state['checked']:,} / {total:,} ({pct}%)\n"
                        f"❌ לא תקינים עד כה: {state['invalid_count']}\n\n"
                        f"מצב: {status_label}"
                    )
                    if text != state["last_update_text"]:
                        success = await _safe_edit_message(
                            context,
                            chat_id,
                            message_id,
                            text,
                            reply_markup=_get_scan_keyboard(col_id, state["status"]),
                            parse_mode="HTML"
                        )
                        if success:
                            state["last_update_text"] = text

            offset += len(page)

    except Exception as e:
        logger.error(f"Error in scan worker for col_id={col_id}: {e}", exc_info=True)
        state["status"] = "error"

    state["updated_at"] = time.time()

    if state["status"] == "cancelling":
        await _finish_scan(col_id, context, cancelled=True)
    elif state["status"] != "error":
        state["status"] = "finished"
        await _finish_scan(col_id, context, cancelled=False)


async def _finish_scan(col_id: int, context: ContextTypes.DEFAULT_TYPE, cancelled: bool):
    state = context.bot_data.get("invalid_scans", {}).get(col_id)
    if not state:
        return

    invalid_count = state["invalid_count"]
    total = state["total"]
    checked = state["checked"]
    safe_col_name = html.escape(state["col_name"])
    chat_id = state["chat_id"]
    message_id = state["message_id"]

    text = f"📋 <b>דוח סריקת קבצים פגומים</b> — {safe_col_name}\n\n"
    if cancelled:
        text += "⚠️ <b>הסריקה בוטלה באמצע.</b>\n"

    text += (
        f"📦 נסרקו: {checked:,} מתוך {total:,}\n"
        f"✅ תקינים: {checked - invalid_count:,}\n"
        f"❌ לא תקינים: {invalid_count}\n"
    )

    keyboard = []
    if invalid_count > 0:
        keyboard.append([
            InlineKeyboardButton(
                f"🗑 מחק פריטים פגומים ({invalid_count})",
                callback_data=f"scan_invalid_delete:{col_id}"
            )
        ])

    keyboard.append([InlineKeyboardButton("🔙 חזור לאוסף", callback_data=f"manage_collection:{col_id}")])

    await _safe_edit_message(
        context,
        chat_id,
        message_id,
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

    state["task"] = None


async def handle_invalid_scan_control_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle pause, resume, cancel for invalid scan with proper access check and error handling."""
    query = update.callback_query

    try:
        parts = parse_callback_data(query.data, "scan_invalid")
        action, col_id_str = query.data.split(":")
        col_id = int(col_id_str)
    except Exception:
        await query.answer("❌ בקשה לא תקינה.", show_alert=True)
        return

    is_allowed, col = await parse_and_validate_access(update, context, [str(col_id)], index=0)
    if not is_allowed:
        await query.answer("⛔ אין לך הרשאה לפעולה זו.", show_alert=True)
        return

    state = context.bot_data.get("invalid_scans", {}).get(col_id)
    if not state:
        await query.answer("❌ לא נמצאה סריקה פעילה עבור אוסף זה.", show_alert=True)
        return

    safe_col_name = html.escape(state["col_name"])
    chat_id = state["chat_id"]
    message_id = state["message_id"]

    if action == "scan_invalid_pause":
        if state["status"] == "running":
            state["status"] = "paused"
            state["updated_at"] = time.time()
            await query.answer("הסריקה הושהתה.")

            text = state.get("last_update_text", "").replace("מצב: ▶️ רץ", "מצב: ⏸ מושהה")
            if not text:
                pct = int(state["checked"] / state["total"] * 100) if state["total"] else 0
                text = (
                    f"🔍 <b>סריקת קבצים פגומים</b> — {safe_col_name}\n\n"
                    f"✅ נבדקו: {state['checked']:,} / {state['total']:,} ({pct}%)\n"
                    f"❌ לא תקינים עד כה: {state['invalid_count']}\n\n"
                    f"מצב: ⏸ מושהה"
                )
            await _safe_edit_message(
                context,
                chat_id,
                message_id,
                text,
                reply_markup=_get_scan_keyboard(col_id, "paused"),
                parse_mode="HTML"
            )
            state["last_update_text"] = text
        else:
            await query.answer("הסריקה אינה רצה כרגע.")

    elif action == "scan_invalid_resume":
        if state["status"] == "paused":
            state["status"] = "running"
            state["updated_at"] = time.time()
            await query.answer("הסריקה ממשיכה.")

            text = state.get("last_update_text", "").replace("מצב: ⏸ מושהה", "מצב: ▶️ רץ")
            if not text:
                pct = int(state["checked"] / state["total"] * 100) if state["total"] else 0
                text = (
                    f"🔍 <b>סריקת קבצים פגומים</b> — {safe_col_name}\n\n"
                    f"✅ נבדקו: {state['checked']:,} / {state['total']:,} ({pct}%)\n"
                    f"❌ לא תקינים עד כה: {state['invalid_count']}\n\n"
                    f"מצב: ▶️ רץ"
                )
            await _safe_edit_message(
                context,
                chat_id,
                message_id,
                text,
                reply_markup=_get_scan_keyboard(col_id, "running"),
                parse_mode="HTML"
            )
            state["last_update_text"] = text
        else:
            await query.answer("הסריקה אינה מושהית.")

    elif action == "scan_invalid_cancel":
        if state["status"] in ("running", "paused"):
            state["status"] = "cancelling"
            state["updated_at"] = time.time()
            await query.answer("מבטל את הסריקה...")
        else:
            await query.answer("לא ניתן לבטל סריקה זו.")
    else:
        await query.answer("פעולה לא מוכרת.")


async def handle_invalid_scan_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle deletion of invalid items after a scan."""
    query = update.callback_query

    parts = parse_callback_data(query.data, "scan_invalid_delete")
    is_allowed, col_id, col = await parse_and_validate_access(update, context, parts)
    if not is_allowed:
        return

    state = context.bot_data.get("invalid_scans", {}).get(col_id)
    if not state:
        await query.answer("❌ נתוני הסריקה פגו תוקף, אנא סרוק שוב.", show_alert=True)
        return

    invalid_ids = state.get("invalid_ids", [])
    if not invalid_ids:
        await query.answer("❌ אין פריטים פגומים למחיקה.", show_alert=True)
        return

    await query.answer("⏳ מוחק פריטים פגומים...")
    deleted_count = db.delete_items_by_ids(invalid_ids)

    # Cleanup state after deletion
    context.bot_data.get("invalid_scans", {}).pop(col_id, None)

    safe_col_name = html.escape(col[1])
    await query.edit_message_text(
        f"✅ נמחקו בהצלחה <b>{deleted_count}</b> פריטים פגומים מהאוסף <b>{safe_col_name}</b>.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזור לאוסף", callback_data=f"manage_collection:{col_id}")]])
    )
