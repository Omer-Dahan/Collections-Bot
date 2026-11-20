"""
Handlers for processing incoming messages, including item addition and mode-based interactions.
"""
import html
import io
import os
import tempfile
import time
from datetime import datetime
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
import db
from config import is_admin
from constants import (
    active_collections, active_shared_collections,
    active_shared_collection_timestamps, active_collection_timestamps,
)
from utils import (
    verify_user_code, update_batch_status,
    send_response, show_collection_page, logger,
    check_collection_access, check_owner_access, start_user_job, extract_file_info,
    prepare_media_groups, send_media_groups_in_chunks,
    get_stop_collect_keyboard, get_collect_mode_text, set_active_collection,
)
from archive_logger import (
    archive_file_to_channels, log_activity, ENABLE_ARCHIVING
)

MAX_COLLECTION_NAME_LEN = 64
MAX_IMPORT_FILE_SIZE = 5 * 1024 * 1024
IMPORT_BATCH_SIZE = 500
VALID_CONTENT_TYPES = {"video", "photo", "document", "audio", "text"}
MAX_SEARCH_MESSAGE_LEN = 3800

async def handle_new_collection_name_input(message, context: ContextTypes.DEFAULT_TYPE):
    """Handle text input for new collection name"""
    if not message.text:
        await message.reply_text("נא לשלוח שם אוסף כטקסט:")
        return

    name = message.text.strip()
    user = message.from_user

    if len(name) < 2:
        await message.reply_text("השם קצר מדי, נסה שוב:")
        return
    if len(name) > MAX_COLLECTION_NAME_LEN:
        await message.reply_text(
            f"השם ארוך מדי (מקסימום {MAX_COLLECTION_NAME_LEN} תווים), נסה שוב:"
        )
        return

    try:
        collection_id = db.create_collection(name, user.id)
        set_active_collection(user.id, collection_id)

        if "creating_collection_mode" in context.user_data:
            del context.user_data["creating_collection_mode"]

        # Ensure we exit import mode if it was somehow active
        context.user_data.pop("import_mode", None)

        await message.reply_text(
            get_collect_mode_text(name),
            reply_markup=get_stop_collect_keyboard()
        )

    except Exception as e: # pylint: disable=broad-exception-caught
        logger.error("Error creating collection: %s", e)
        if "UNIQUE constraint failed" in str(e):
            await message.reply_text(
                f"❌ כבר יש לך אוסף בשם '{name}'.",
                reply_markup=get_stop_collect_keyboard()
            )
        else:
            await message.reply_text("אירעה שגיאה ביצירת האוסף.")

async def handle_import_col_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Handle the import mode activation"""
    query = update.callback_query
    await query.answer()

    context.user_data["import_mode"] = True
    context.user_data.pop("creating_collection_mode", None)

    await query.edit_message_text(
        "📂 <b>מצב יבוא אוסף</b>\n\n"
        "שלח לי עכשיו את קובץ הגיבוי (.txt) שקיבלת מהבוט.\n"
        "אני אצור אוסף חדש מהתוכן שלו.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 ביטול", callback_data="back_to_main")]
        ])
    )

def _create_imported_collection(original_name: str, user_id: int) -> tuple[int, str]:
    """Helper to create a collection with a unique name for import."""
    col_name = original_name
    counter = 1
    while True:
        try:
            collection_id = db.create_collection(col_name, user_id)
            return collection_id, col_name
        except Exception as e:
            if "UNIQUE constraint failed" in str(e):
                col_name = f"{original_name} ({counter})"
                counter += 1
            else:
                raise e

def _unescape_export_text(value: str) -> str:
    """Reverse the escaping applied by the export format."""
    return value.replace("<PIPE>", "|").replace("<NL>", "\n")

def _parse_import_line(line: str):
    """
    Parse one export line: type|file_id|text|file_name|size.
    Returns a row tuple (content_type, file_id, text, file_name, size) or None if invalid.
    """
    parts = line.split("|")
    if len(parts) < 5:
        return None

    c_type = parts[0].strip()
    f_id = parts[1].strip()
    if c_type not in VALID_CONTENT_TYPES or not f_id:
        return None

    text = _unescape_export_text(parts[2]) or None
    # File names may contain raw pipes, so they are everything between text and size
    f_name = _unescape_export_text("|".join(parts[3:-1])) or None
    try:
        f_size = int(parts[-1])
    except ValueError:
        f_size = 0
    return c_type, f_id, text, f_name, f_size

def _insert_imported_items(lines, collection_id: int) -> tuple[int, int]:
    """Lazily parse export lines and insert them in batches. Returns (imported, errors)."""
    imported_count = 0
    errors = 0
    batch = []

    def flush():
        nonlocal imported_count, errors
        if not batch:
            return
        try:
            imported_count += db.add_items_bulk(collection_id, batch)
        except Exception: # pylint: disable=broad-exception-caught
            logger.exception("Bulk import batch failed")
            errors += len(batch)
        batch.clear()

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        row = _parse_import_line(line)
        if row is None:
            errors += 1
            continue
        batch.append(row)
        if len(batch) >= IMPORT_BATCH_SIZE:
            flush()
    flush()
    return imported_count, errors

async def process_imported_collection(message, context: ContextTypes.DEFAULT_TYPE):
    """Process uploaded TXT file for collection import"""
    doc = message.document
    if not doc.file_name or not doc.file_name.lower().endswith(".txt"):
        # Clear mode if the file is wrong to allow normal additions
        context.user_data.pop("import_mode", None)
        await message.reply_text("❌ זה לא קובץ טקסט. אנא שלח קובץ גיבוי (.txt) תקני.")
        return

    if doc.file_size and doc.file_size > MAX_IMPORT_FILE_SIZE:
        context.user_data.pop("import_mode", None)
        await message.reply_text("❌ הקובץ גדול מדי. הגודל המקסימלי ליבוא הוא 5MB.")
        return

    status_msg = await message.reply_text("⏳ מוריד ומעבד את קובץ הגיבוי...")
    tmp_path = None

    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".txt")
        os.close(fd)
        file_obj = await doc.get_file()
        await file_obj.download_to_drive(custom_path=tmp_path)

        with open(tmp_path, "rb") as raw:
            lines = io.TextIOWrapper(raw, encoding="utf-8", errors="replace")
            header = lines.readline().lstrip("﻿").strip()
            if not header.startswith("# COLLECTION EXPORT:"):
                context.user_data.pop("import_mode", None)
                await status_msg.edit_text("❌ הקובץ לא נראה כמו גיבוי תקין של הבוט.")
                return

            col_name = header.replace("# COLLECTION EXPORT:", "").strip()
            if not col_name:
                col_name = doc.file_name[:-4].replace("_backup", "").strip()
            col_name = col_name[:MAX_COLLECTION_NAME_LEN] or "Imported"

            col_id, final_name = _create_imported_collection(col_name, message.from_user.id)
            imported_count, errors = _insert_imported_items(lines, col_id)

        # Finish
        context.user_data.pop("import_mode", None)
        set_active_collection(message.from_user.id, col_id)

        err_text = f"⚠️ שגיאות: {errors}\n" if errors > 0 else ""
        await status_msg.edit_text(
            "✅ <b>היבוא הושלם בהצלחה!</b>\n\n"
            f"📁 שם האוסף: {html.escape(final_name)}\n"
            f"📦 פריטים שיובאו: {imported_count}\n"
            f"{err_text}"
            "\nהאוסף הוגדר כפעיל. ניתן להוסיף לו עוד פריטים כעת.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(text="🛑 הפסק הוספה", callback_data="stop_collect")],
                [InlineKeyboardButton(
                    "📂 צפה באוסף", callback_data=f"browse_page:{col_id}:1"
                )]
            ]),
            parse_mode="HTML"
        )

    except Exception: # pylint: disable=broad-exception-caught
        # Ensure mode is cleared even on exception
        context.user_data.pop("import_mode", None)
        logger.exception("Import failed")
        await status_msg.edit_text("❌ אירעה שגיאה ביבוא הקובץ. ודא שזה קובץ גיבוי תקין ונסה שוב.")
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

CANCELLED_CONFIRMATION_TEXT = "❌ קוד שגוי. הפעולה בוטלה, התחל מחדש מהתפריט."

async def handle_send_collection_confirmation(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """
    Handle send collection confirmation logic.
    Returns True if the message was handled (confirmation process), False otherwise.
    """
    if (
        "verify_send_collection" not in context.user_data
        and not context.user_data.get("verify_send_collection_mode")
    ):
        return False

    message = update.message
    user_id = message.from_user.id
    if not message.text:
        # A file sent while a code is pending abandons the confirmation and is
        # handled normally (e.g. saved), instead of being swallowed as a bad code.
        context.user_data.pop("verify_send_collection", None)
        context.user_data.pop("verify_send_collection_mode", None)
        return False

    # Use helper to verify code and retrieve data
    is_valid, data = verify_user_code(message, context, "send_collection")

    if not is_valid:
        # A confirmation attempt must never fall through and become an item
        context.user_data.pop("verify_send_collection", None)
        context.user_data.pop("verify_send_collection_mode", None)
        await message.reply_text(CANCELLED_CONFIRMATION_TEXT)
        return True

    context.user_data.pop("verify_send_collection_mode", None)
    col_id = data["collection_id"]
    # Double check access
    is_allowed, _, col = check_collection_access(user_id, col_id)
    if not is_allowed:
        await message.reply_text("❌ שגיאת הרשאה.")
        return True

    col_name = col[1]
    chat_id = message.chat_id
    old_msg_id = data.get("msg_id")

    async def _send_all():
        status_msg = await context.bot.send_message(
            chat_id, f"🚀 מאמת קוד... מתחיל שליחה של אוסף '{col_name}'."
        )

        # Fetch and prepare a bounded page at a time.  A large collection no
        # longer creates InputMedia objects for every item simultaneously.
        offset = 0
        page_size = 100
        while True:
            items = db.get_items_by_collection(col_id, offset=offset, limit=page_size)
            if not items:
                break
            visual, docs, texts = prepare_media_groups(items)
            await send_media_groups_in_chunks(
                context.bot, chat_id, visual, docs, texts, user_id=user_id
            )
            offset += len(items)
            if len(items) < page_size:
                break

        # Cleanup temporary messages
        try:
            await status_msg.delete()
        except Exception: # pylint: disable=broad-exception-caught
            pass

        if old_msg_id:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=old_msg_id)
            except Exception: # pylint: disable=broad-exception-caught
                pass

        # Restore the collection page
        await show_collection_page(
            update=update, context=context, collection_id=col_id,
            page=1, force_resend=True
        )

    if not start_user_job(context, user_id, _send_all()):
        await message.reply_text("⏳ כבר מתבצעת שליחה, המתן לסיומה.")
    return True

async def handle_delete_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle collection deletion confirmation. Returns True if handled."""
    if (
        "verify_delete_collection" not in context.user_data
        and not context.user_data.get("verify_delete_collection_mode")
    ):
        return False

    message = update.message
    user_id = message.from_user.id
    if not message.text:
        context.user_data.pop("verify_delete_collection", None)
        context.user_data.pop("verify_delete_collection_mode", None)
        return False

    is_valid, data = verify_user_code(message, context, "delete_collection")

    if not is_valid:
        # A confirmation attempt must never fall through and become an item
        context.user_data.pop("verify_delete_collection", None)
        context.user_data.pop("verify_delete_collection_mode", None)
        await message.reply_text(CANCELLED_CONFIRMATION_TEXT)
        return True

    col_id = data["collection_id"]
    context.user_data.pop("verify_delete_collection_mode", None)
    # Only the owner may delete a collection
    is_owner, _, col = check_owner_access(user_id, col_id)
    if not is_owner:
        await message.reply_text("❌ שגיאה במחיקת האוסף.")
        return True

    db.delete_collection(col_id)
    if active_collections.get(user_id) == col_id:
        del active_collections[user_id]
        active_collection_timestamps.pop(user_id, None)

    await message.reply_text(
        f"🗑 האוסף '{col[1]}' נמחק בהצלחה.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 תפריט ראשי", callback_data="back_to_main")]
        ])
    )
    context.user_data.pop("delete_mode", None)
    return True

async def handle_share_code_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle share code input from user. Returns True if handled."""
    if not context.user_data.get("waiting_for_share_code"):
        return False

    if not update.message.text:
        await update.message.reply_text("אנא שלח את קוד השיתוף כטקסט.")
        return True

    code = update.message.text.strip()
    await activate_shared_collection(update, context, code)
    return True

async def activate_shared_collection(
    update: Update, context: ContextTypes.DEFAULT_TYPE, share_code: str
):
    """
    Helper to activate shared collection access.
    Consolidates logic for both /access command and interactive flow.
    """
    col = db.get_collection_by_share_code(share_code)
    user = update.effective_user
    chat_id = update.effective_chat.id

    if not col:
        await send_response(context.bot, chat_id, "❌ קוד שיתוף לא תקין או פג תוקף.")
        return

    # Success
    col_id = col[0]
    col_name = col[1]

    # Store access
    active_shared_collections[user.id] = share_code
    active_shared_collection_timestamps[user.id] = time.time()
    db.set_user_active_share(user.id, share_code)
    db.log_share_access(share_code, user.id)

    if ENABLE_ARCHIVING:
        context.application.create_task(
            log_activity(
                bot=context.bot, action="SHARE_ACCESSED",
                user_id=user.id, collection_id=col_id,
                user_name=user.full_name, username=user.username
            )
        )

    # Clear waiting mode
    context.user_data.pop("waiting_for_share_code", None)

    # Check expiry
    expiry_warning = ""
    expires_at = db.get_share_expiration(col_id)
    if expires_at:
        try:
            exp_dt = datetime.fromisoformat(expires_at)
            rem = exp_dt - datetime.now()
            if rem.total_seconds() > 0:
                hours = int(rem.total_seconds() // 3600)
                mins = int((rem.total_seconds() % 3600) // 60)
                time_str = f"{hours} שעות ו-{mins} דקות" if hours > 0 else f"{mins} דקות"
                expiry_warning = (f"\n\n⏳ <b>שים לב:</b> אוסף זה יפוג בעוד {time_str}.\n"
                                  "ההודעות שתקבל יימחקו אוטומטית.")
        except (ValueError, TypeError):
            pass

    await send_response(
        context.bot, chat_id,
        f"✅ <b>גישה אושרה!</b>\nאתה צופה באוסף המשותף: "
        f"<b>{html.escape(col_name)}</b>{expiry_warning}",
        parse_mode="HTML", user_id=user.id
    )
    # Immediately show the collection
    await show_collection_page(
        update=update, context=context, collection_id=col_id, page=1, user_id=user.id
    )



async def handle_id_file_message(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    """Handle messages for identifying file IDs."""
    message = update.message
    # Only if file
    file_info = extract_file_info(message)
    file_id = file_info["file_id"] if file_info else None

    if not file_id:
        return

    # Reply with code
    await message.reply_text(
        f"✅ <b>File ID Detected:</b>\n<code>{html.escape(file_id)}</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 תפריט ראשי", callback_data="back_to_main")]
        ])
    )

async def handle_item_delete_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle messages when in Item Deletion Mode"""
    message = update.message
    user = update.effective_user

    # Extract file ID
    file_info = extract_file_info(message)
    file_id = file_info["file_id"] if file_info else None

    if not file_id:
        await message.reply_text("❌ לא זוהה קובץ. אנא שלח תמונה, וידאו או מסמך למחיקה.")
        return

    col_id = context.user_data.get("delete_target_collection_id")
    if not col_id:
        col_id = active_collections.get(user.id)

    if not col_id:
        await message.reply_text(
            "⚠️ לא נבחר אוסף למחיקה.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 תפריט ראשי", callback_data="back_to_main")]
            ])
        )
        context.user_data.pop("item_delete_mode", None)
        return

    success = db.delete_item(
        col_id, file_id, file_unique_id=file_info.get("file_unique_id")
    )
    btn = InlineKeyboardButton("🏁 סיום מחיקה", callback_data="back_to_main")
    keyboard = InlineKeyboardMarkup([[btn]])

    if success:
        await message.reply_text(
            "✅ <b>הפריט נמחק בהצלחה.</b>\nשלח פריט נוסף למחיקה או לחץ על סיום.",
            reply_markup=keyboard, parse_mode="HTML"
        )
    else:
        await message.reply_text(
            "⚠️ <b>הפריט לא נמצא באוסף הפעיל.</b>\nודא שאתה שולח את הקובץ הנכון מאותו האוסף.",
            reply_markup=keyboard, parse_mode="HTML"
        )

async def handle_duplicate_preview_request(message, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    If a duplicate scan is pending and the user sends a numeric ID,
    send the corresponding file as a preview.
    """
    if not (message.text and message.text.strip().isdigit()):
        return False

    pending = context.user_data.get("pending_duplicate_ids")
    col_id = context.user_data.get("duplicate_scan_col_id")

    if not pending or not col_id:
        return False

    target_id = int(message.text.strip())
    # target_id must be either a duplicate or the original of one of the groups.
    # We allow previewing any item from the scan result — fetch from DB directly.
    try:
        item = db.get_item_by_id(target_id)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Duplicate preview fetch error: %s", e)
        return False

    if not item:
        # ID was numeric but not found — let other handlers try
        return False

    # Verify item belongs to the correct collection
    if item[1] != col_id:
        return False

    back_button = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "🔙 חזור לדוח כפולים",
            callback_data=f"manage_collection:{col_id}"
        )]
    ])

    content_type = item[2]  # (id, collection_id, content_type, file_id, ...)
    file_id = item[3]

    try:
        if content_type == "photo":
            await context.bot.send_photo(
                chat_id=message.chat_id, photo=file_id,
                caption=f"🔍 תצוגה מקדימה — ID: <code>{target_id}</code>",
                parse_mode="HTML", reply_markup=back_button
            )
        elif content_type == "video":
            await context.bot.send_video(
                chat_id=message.chat_id, video=file_id,
                caption=f"🔍 תצוגה מקדימה — ID: <code>{target_id}</code>",
                parse_mode="HTML", reply_markup=back_button
            )
        elif content_type == "document":
            await context.bot.send_document(
                chat_id=message.chat_id, document=file_id,
                caption=f"🔍 תצוגה מקדימה — ID: <code>{target_id}</code>",
                parse_mode="HTML", reply_markup=back_button
            )
        else:
            await message.reply_text(
                f"סוג קובץ <code>{html.escape(str(content_type))}</code> "
                f"— ID: <code>{target_id}</code>",
                parse_mode="HTML", reply_markup=back_button
            )
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Failed to send duplicate preview: %s", e)
        await message.reply_text(f"❌ שגיאה בשליחת הקובץ (ID: {target_id}).")

    return True


async def handle_id_request(message, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle text messages consisting only of digits (retrieving item by ID)."""
    if not (message.text and message.text.isdigit()):
        return False

    target_id = int(message.text)
    allowed_ids = context.user_data.get("allowed_item_ids", [])
    info_col_id = context.user_data.get("info_page_collection_id")

    if not (info_col_id and target_id in allowed_ids):
        return False

    try:
        item = db.get_item_by_id(target_id)
        if not item:
            await message.reply_text("❌ הקובץ חיפשת לא נמצא במאגר.")
        else:
            page = context.user_data.get("info_page_page", 1)
            info_page = context.user_data.get("info_page_info_page", 0)

            if context.user_data.get("from_search_results"):
                back_button = InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "🔙 חזור לאוסף",
                        callback_data=f"browse_page:{info_col_id}:1"
                    )]
                ])
            else:
                back_button = InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "🔙 חזור לרשימת מידע",
                        callback_data=f"back_to_info:{info_col_id}:{page}:{info_page}"
                    )]
                ])

            content_type = item[2]
            file_id = item[3]

            if content_type == "photo":
                await context.bot.send_photo(
                    chat_id=message.chat_id, photo=file_id, reply_markup=back_button
                )
            elif content_type == "video":
                await context.bot.send_video(
                    chat_id=message.chat_id, video=file_id, reply_markup=back_button
                )
            elif content_type == "document":
                await context.bot.send_document(
                    chat_id=message.chat_id, document=file_id, reply_markup=back_button
                )
            elif content_type == "audio":
                await context.bot.send_audio(
                    chat_id=message.chat_id, audio=file_id, reply_markup=back_button
                )
            else:
                text_content = item[4] if item[4] else "תוכן לא זמין"
                await context.bot.send_message(
                    chat_id=message.chat_id, text=text_content, reply_markup=back_button
                )
    except Exception as e: # pylint: disable=broad-exception-caught
        logger.error("Failed to fetch item by ID: %s", e)
    return True

async def handle_item_addition(message, user, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle the default behavior of adding received files to the active collection."""
    collection_id = active_collections.get(user.id)
    if not collection_id:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📁 צור אוסף חדש", callback_data="main_menu:new_collection")],
            [InlineKeyboardButton("📂 בחר אוסף קיים", callback_data="main_menu:select_collection")],
            [InlineKeyboardButton("🏠 תפריט ראשי", callback_data="back_to_main")]
        ])
        await message.reply_text(
            "⚠️ <b>אין אוסף פעיל.</b>\n\n"
            "כדי לשמור דברים, צריך לבחור לאיזה אוסף להוסיף אותם,\n"
            "או ליצור אוסף חדש.",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        return True

    file_info = extract_file_info(message)
    if not file_info:
        await message.reply_text("⚠️ סוג הודעה זה לא נתמך לשמירה.")
        return True

    content_type = file_info["content_type"]
    file_id = file_info["file_id"]
    file_unique_id = file_info.get("file_unique_id")
    text_content = file_info["text_content"]
    f_name = file_info["file_name"]
    f_size = file_info["file_size"]

    try:
        item_id = db.add_item(
            collection_id, content_type, file_id, text_content,
            f_name, f_size, file_unique_id=file_unique_id
        )
        col_data = db.get_collection_by_id(collection_id)
        set_active_collection(user.id, collection_id)
        col_name = col_data[1] if col_data else "Unknown"

        if ENABLE_ARCHIVING:
            await archive_file_to_channels(
                bot=context.bot, item_id=item_id, file_id=file_id,
                content_type=content_type, user_id=user.id, collection_id=collection_id,
                collection_name=col_name, file_name=f_name, original_caption=text_content,
                user_name=user.full_name, username=user.username
            )
        await update_batch_status(message, context, col_name)
        return True
    except Exception as e: # pylint: disable=broad-exception-caught
        logger.error("Error adding item: %s", e)
        await message.reply_text("שגיאה בשמירת הפריט.")
        return True

async def handle_search_query_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """קבלת מילת החיפוש מהמשתמש, ביצוע החיפוש והצגת התוצאות"""
    message = update.message
    query_text = message.text
    if not query_text:
        await message.reply_text("אנא שלח טקסט לחיפוש.")
        return

    query_text = query_text.strip()
    user_id = message.from_user.id
    col_id = context.user_data.pop("waiting_for_search_query", None)
    search_msg_id = context.user_data.pop("search_from_message_id", None)
    
    # Try to delete the search prompt message to keep chat clean
    if search_msg_id:
        try:
            await context.bot.delete_message(chat_id=message.chat_id, message_id=search_msg_id)
        except Exception:
            pass

    # Search database
    results = db.search_items(user_id, query_text, is_admin(user_id))

    back_callback = f"browse_page:{col_id}:1" if col_id else "back_to_main"
    if not results:
        # Provide return button
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 חזור לאוסף", callback_data=back_callback)]
        ])
        await message.reply_text(
            f"❌ לא נמצאו תוצאות עבור החיפוש: \"{html.escape(query_text)}\"",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        return

    # Format results
    content_type_map = {
        "video": "🎬",
        "photo": "🖼", 
        "document": "📄",
        "audio": "🎵",
        "text": "📝"
    }
    content_type_names = {
        "video": "סרטון",
        "photo": "תמונה", 
        "document": "קובץ",
        "audio": "אודיו",
        "text": "טקסט"
    }

    lines = [f"🔍 <b>תוצאות חיפוש עבור: \"{html.escape(query_text)}\"</b>\n"]
    allowed_ids = []
    
    total_len = len(lines[0])
    for item_id, content_type, file_id, text_content, file_name, col_name in results:
        icon = content_type_map.get(content_type, "📁")
        type_name = content_type_names.get(content_type, "קובץ")
        display_name = file_name or (text_content[:40] + "..." if text_content and len(text_content) > 40 else text_content) or "(ללא שם/תיאור)"
        
        item_block = (
            f"{icon}  <b>{html.escape(type_name)}</b>  |  {html.escape(display_name)}\n"
            f"🆔  מזהה (ID): <code>{item_id}</code>\n"
            f"📁  אוסף: {html.escape(col_name)}\n"
            f"──────────────────"
        )
        if total_len + len(item_block) + 1 > MAX_SEARCH_MESSAGE_LEN:
            break
        lines.append(item_block)
        allowed_ids.append(item_id)
        total_len += len(item_block) + 1

    if len(allowed_ids) < len(results):
        lines.append(f"\n<i>מוצגות {len(allowed_ids)} מתוך {len(results)} תוצאות</i>")
    lines.append("\n💡 <b>שלח/י את מספר ה-ID של הקובץ בהודעה כדי לקבל אותו.</b>")
    
    # Update allowed item ids in context.user_data
    context.user_data["allowed_item_ids"] = allowed_ids
    # Set info_page_collection_id to col_id so handle_id_request allows it
    context.user_data["info_page_collection_id"] = col_id
    # Set flag indicating that the user came from search results
    context.user_data["from_search_results"] = True

    response_text = "\n".join(lines)
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 חזור לאוסף", callback_data=back_callback)]
    ])

    await message.reply_text(response_text, reply_markup=keyboard, parse_mode="HTML")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main generic message handler for tracking input and matching modes."""
    if not update.message or not update.effective_user:
        return
    user = update.effective_user
    if db.upsert_user(user.id, user.username, user.first_name, user.last_name):
        return
    msg = update.message

    # 1. Intercepting flows
    if await _handle_intercepts(update, context):
        return

    # 2. Duplicate scan preview (must run before generic id_request)
    if await handle_duplicate_preview_request(msg, context):
        return

    # 3. File by ID request
    if await handle_id_request(msg, context):
        return

    # 3. Mode Flags (Simplified Dispatch)
    if await _evaluate_mode_flags(update, context):
        return

    # 4. Default: Add item
    await handle_item_addition(msg, user, context)

async def _handle_intercepts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check for active confirmation/input flows."""
    if await handle_send_collection_confirmation(update, context):
        return True
    if await handle_delete_confirmation(update, context):
        return True
    if await handle_share_code_input(update, context):
        return True
    return False

async def _evaluate_mode_flags(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check and dispatch specialized modes."""
    msg = update.message
    data = context.user_data
    if data.get("waiting_for_search_query"):
        await handle_search_query_input(update, context)
        return True
    if data.get("creating_collection_mode"):
        await handle_new_collection_name_input(msg, context)
        return True
    if data.get("id_mode"):
        await handle_id_file_message(update, context)
        return True
    if data.get("item_delete_mode"):
        await handle_item_delete_message(update, context)
        return True
    if data.get("import_mode") and msg.document:
        await process_imported_collection(msg, context)
        return True
    return False


