"""
Handlers for processing incoming messages, including item addition and mode-based interactions.
"""
import asyncio
from io import BytesIO
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
import db
from constants import active_collections, active_shared_collections
from utils import (
    verify_user_code, update_batch_status,
    send_response, show_collection_page, logger,
    check_collection_access, extract_file_info,
    prepare_media_groups, send_media_groups_in_chunks,
    get_stop_collect_keyboard, get_collect_mode_text
)
from archive_logger import (
    archive_file_to_channels, log_activity, ENABLE_ARCHIVING
)

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

    try:
        collection_id = db.create_collection(name, user.id)
        active_collections[user.id] = collection_id

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
        "📂 **מצב יבוא אוסף**\n\n"
        "שלח לי עכשיו את קובץ הגיבוי (.txt) שקיבלת מהבוט.\n"
        "אני אצור אוסף חדש מהתוכן שלו.",
        parse_mode="Markdown",
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

def _parse_and_insert_imported_items(lines: list[str], collection_id: int) -> tuple[int, int]:
    """Helper to parse lines from export and insert into DB."""
    imported_count = 0
    errors = 0
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split("|")
        if len(parts) < 5:
            continue

        # c_type|f_id|text|f_name|f_size
        c_type = parts[0]
        f_id = parts[1]
        text = parts[2].replace("<PIPE>", "|").replace("<NL>", "\n")
        text = text if text else None
        f_name = parts[3] if parts[3] else None

        f_size = 0
        try:
            f_size = int(parts[4])
        except ValueError:
            pass

        try:
            db.add_item(collection_id, c_type, f_id, text, f_name, f_size)
            imported_count += 1
        except Exception: # pylint: disable=broad-exception-caught
            errors += 1
    return imported_count, errors

async def process_imported_collection(message, context: ContextTypes.DEFAULT_TYPE):
    """Process uploaded TXT file for collection import"""
    doc = message.document
    if not doc.file_name.endswith(".txt"):
        # Clear mode if the file is wrong to allow normal additions
        context.user_data.pop("import_mode", None)
        await message.reply_text("❌ זה לא קובץ טקסט. אנא שלח קובץ גיבוי (.txt) תקני.")
        return

    status_msg = await message.reply_text("⏳ מוריד ומעבד את קובץ הגיבוי...")

    try:
        file_obj = await doc.get_file()
        data = BytesIO()
        await file_obj.download_to_memory(data)
        content = data.getvalue().decode('utf-8')
        lines = content.splitlines()

        # Verify header
        header = lines[0] if lines else ""
        if not header.startswith("# COLLECTION EXPORT:"):
            context.user_data.pop("import_mode", None)
            await status_msg.edit_text("❌ הקובץ לא נראה כמו גיבוי תקין של הבוט.")
            return

        # Extract name
        col_name = lines[0].replace("# COLLECTION EXPORT:", "").strip() if lines else ""
        if not col_name:
            col_name = doc.file_name.replace(".txt", "").replace("_backup", "")

        col_id, final_name = _create_imported_collection(col_name, message.from_user.id)
        imported_count, errors = _parse_and_insert_imported_items(lines, col_id)

        # Finish
        context.user_data.pop("import_mode", None)
        active_collections[message.from_user.id] = col_id

        err_text = f"⚠️ שגיאות: {errors}\n" if errors > 0 else ""
        await status_msg.edit_text(
            "✅ **היבוא הושלם בהצלחה!**\n\n"
            f"📁 שם האוסף: {final_name}\n"
            f"📦 פריטים שיובאו: {imported_count}\n"
            f"{err_text}"
            "\nהאוסף הוגדר כפעיל. ניתן להוסיף לו עוד פריטים כעת.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(text="🛑 הפסק הוספה", callback_data="stop_collect")],
                [InlineKeyboardButton(
                    "📂 צפה באוסף", callback_data=f"browse_page:{col_id}:1"
                )]
            ]),
            parse_mode="Markdown"
        )

    except Exception as e: # pylint: disable=broad-exception-caught
        # Ensure mode is cleared even on exception
        context.user_data.pop("import_mode", None)
        logger.exception("Import failed: %s", e)
        await status_msg.edit_text(f"❌ שגיאה ביבוא הקובץ:\n{str(e)}")

async def handle_send_collection_confirmation(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """
    Handle send collection confirmation logic.
    Returns True if the message was handled (confirmation process), False otherwise.
    """
    if "verify_send_collection" not in context.user_data:
        return False

    message = update.message
    # Use helper to verify code and retrieve data
    is_valid, data = verify_user_code(message, context, "send_collection")

    if not is_valid:
        if "verify_send_collection" in context.user_data:
            await message.reply_text("❌ קוד שגוי. נסה שוב או לחץ על ביטול למעלה.")
            return True
        return False

    col_id = data["collection_id"]
    # Double check access
    is_allowed, _, col = check_collection_access(message.from_user.id, col_id)
    if not is_allowed:
        await message.reply_text("❌ שגיאת הרשאה.")
        return True

    status_msg = await message.reply_text(f"🚀 מאמת קוד... מתחיל שליחה של אוסף '{col[1]}'.")

    # Start sending
    items = db.get_items_by_collection(col_id, limit=10000)
    visual, docs, texts = prepare_media_groups(items)

    await send_media_groups_in_chunks(
        context.bot, message.chat_id, visual, docs, texts, user_id=message.from_user.id
    )

    # Cleanup temporary messages
    try:
        await status_msg.delete()
    except Exception:
        pass
    
    old_msg_id = data.get("msg_id")
    if old_msg_id:
        try:
            await context.bot.delete_message(chat_id=message.chat_id, message_id=old_msg_id)
        except Exception:
            pass

    # Restore the collection page
    await show_collection_page(
        update=update, context=context, collection_id=col_id,
        page=1, force_resend=True
    )
    return True

async def handle_delete_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle collection deletion confirmation. Returns True if handled."""
    if "verify_delete_collection" not in context.user_data:
        return False

    message = update.message
    is_valid, data = verify_user_code(message, context, "delete_collection")

    if is_valid:
        col_id = data["collection_id"]
        # Verify ownership/access again
        is_allowed, _, col = check_collection_access(message.from_user.id, col_id)
        if is_allowed:
            db.delete_collection(col_id)
            if active_collections.get(message.from_user.id) == col_id:
                del active_collections[message.from_user.id]

            await message.reply_text(
                f"🗑 האוסף '{col[1]}' נמחק בהצלחה.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏠 תפריט ראשי", callback_data="back_to_main")]
                ])
            )
            # Exit delete mode state
            context.user_data.pop("verify_delete_collection_mode", None)
            context.user_data.pop("delete_mode", None)
        else:
            await message.reply_text("❌ שגיאה במחיקת האוסף.")
        return True

    # Check if mode is explicitly active (user typed something else)
    if context.user_data.get("verify_delete_collection_mode"):
        await message.reply_text("❌ קוד שגוי. נסה שוב או לחץ ביטול.")
        return True
    return False

async def handle_share_code_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle share code input from user. Returns True if handled."""
    if not context.user_data.get("waiting_for_share_code"):
        return False

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
    db.set_user_active_share(user.id, share_code)
    db.log_share_access(share_code, user.id)

    if ENABLE_ARCHIVING:
        asyncio.create_task(
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
        from datetime import datetime as dt
        try:
            exp_dt = dt.fromisoformat(expires_at)
            rem = exp_dt - dt.now()
            if rem.total_seconds() > 0:
                hours = int(rem.total_seconds() // 3600)
                mins = int((rem.total_seconds() % 3600) // 60)
                time_str = f"{hours} שעות ו-{mins} דקות" if hours > 0 else f"{mins} דקות"
                expiry_warning = (f"\n\n⏳ **שים לב:** אוסף זה יפוג בעוד {time_str}.\n"
                                  "ההודעות שתקבל יימחקו אוטומטית.")
        except ValueError:
            pass

    await send_response(
        context.bot, chat_id,
        f"✅ **גישה אושרה!**\nאתה צופה באוסף המשותף: **{col_name}**{expiry_warning}",
        parse_mode="Markdown", user_id=user.id
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
        f"✅ **File ID Detected:**\n`{file_id}`",
        parse_mode="Markdown",
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

    success = db.delete_item(col_id, file_id)
    btn = InlineKeyboardButton("🏁 סיום מחיקה", callback_data="back_to_main")
    keyboard = InlineKeyboardMarkup([[btn]])

    if success:
        await message.reply_text(
            "✅ **הפריט נמחק בהצלחה.**\nשלח פריט נוסף למחיקה או לחץ על סיום.",
            reply_markup=keyboard, parse_mode="Markdown"
        )
    else:
        await message.reply_text(
            "⚠️ **הפריט לא נמצא באוסף הפעיל.**\nודא שאתה שולח את הקובץ הנכון מאותו האוסף.",
            reply_markup=keyboard, parse_mode="Markdown"
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
                caption=f"🔍 תצוגה מקדימה — ID: `{target_id}`",
                parse_mode="Markdown", reply_markup=back_button
            )
        elif content_type == "video":
            await context.bot.send_video(
                chat_id=message.chat_id, video=file_id,
                caption=f"🔍 תצוגה מקדימה — ID: `{target_id}`",
                parse_mode="Markdown", reply_markup=back_button
            )
        elif content_type == "document":
            await context.bot.send_document(
                chat_id=message.chat_id, document=file_id,
                caption=f"🔍 תצוגה מקדימה — ID: `{target_id}`",
                parse_mode="Markdown", reply_markup=back_button
            )
        else:
            await message.reply_text(
                f"סוג קובץ `{content_type}` — ID: `{target_id}`",
                parse_mode="Markdown", reply_markup=back_button
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
            "⚠️ **אין אוסף פעיל.**\n\n"
            "כדי לשמור דברים, צריך לבחור לאיזה אוסף להוסיף אותם,\n"
            "או ליצור אוסף חדש.",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        return True

    file_info = extract_file_info(message)
    if not file_info:
        return False

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
        col_name = col_data[1] if col_data else "Unknown"

        if ENABLE_ARCHIVING:
            asyncio.create_task(
                archive_file_to_channels(
                    bot=context.bot, item_id=item_id, file_id=file_id,
                    content_type=content_type, user_id=user.id, collection_id=collection_id,
                    collection_name=col_name, file_name=f_name, original_caption=text_content,
                    user_name=user.full_name, username=user.username
                )
            )
        await update_batch_status(message, context, col_name)
        return True
    except Exception as e: # pylint: disable=broad-exception-caught
        logger.error("Error adding item: %s", e)
        await message.reply_text("שגיאה בשמירת הפריט.")
        return True

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main generic message handler for tracking input and matching modes."""
    user = update.effective_user
    if user:
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


