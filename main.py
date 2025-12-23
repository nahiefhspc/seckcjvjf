import logging
import asyncio
import nest_asyncio
import re
from difflib import SequenceMatcher
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
from telegram.error import TelegramError
import os

# Allow reuse of existing event loop
nest_asyncio.apply()

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
START_LINK, END_LINK, TARGET_CHANNEL, INDEX_RULES = range(4)

def create_progress_bar(done, total):
    bar_length = 20
    filled = int(bar_length * done / total)
    return "█" * filled + "▒" * (bar_length - filled)

def is_approx_match(a, b, threshold=0.6):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() >= threshold

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hello! Use /now to start forwarding with indexing.")

async def now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Send the starting message link (e.g., https://t.me/c/123456789/2)")
    return START_LINK

async def get_start_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    match = re.match(r"https://t\.me/c/(\d+)/(\d+)", update.message.text)
    if not match:
        await update.message.reply_text("Invalid link format. Try again.")
        return START_LINK
    context.user_data["source_channel"] = f"-100{match.group(1)}"
    context.user_data["source_channel_id"] = match.group(1)  # Store for txt file links
    context.user_data["start_id"] = int(match.group(2))
    context.user_data["message_id_map"] = []  # Initialize message ID mapping list
    await update.message.reply_text("Send the ending message link.")
    return END_LINK

async def get_end_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    match = re.match(r"https://t\.me/c/(\d+)/(\d+)", update.message.text)
    if not match or f"-100{match.group(1)}" != context.user_data["source_channel"]:
        await update.message.reply_text("Invalid link or different channel.")
        return END_LINK
    context.user_data["end_id"] = int(match.group(2))
    if context.user_data["end_id"] < context.user_data["start_id"]:
        await update.message.reply_text("End ID must be >= Start ID.")
        return END_LINK
    await update.message.reply_text("Send the target channel ID (e.g., -1001234567890)")
    return TARGET_CHANNEL

async def get_target_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not re.match(r"-100\d+", update.message.text):
        await update.message.reply_text("Invalid channel ID.")
        return TARGET_CHANNEL
    context.user_data["target_channel"] = update.message.text
    context.user_data["target_channel_id"] = update.message.text.replace("-100", "")  # Store for txt file links
    await update.message.reply_text(
        "Please upload a .txt file containing index rules, one per line in this format:\n"
        "Ch - 01 : Mole Concept >> chapterId\n"
        "Homework Discussion >> chapterId\n"
        "PYQ Practice Sheet || Only PDF >> ChapterId\n"
        "Mind Maps || Only PDF >> chapterId"
    )
    return INDEX_RULES

async def get_index_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Check if a document was sent
    if not update.message.document or not update.message.document.file_name.endswith('.txt'):
        await update.message.reply_text("Please upload a valid .txt file containing index rules.")
        return INDEX_RULES

    try:
        # Download the .txt file
        file = await update.message.document.get_file()
        file_path = f"index_rules_{update.message.message_id}.txt"
        await file.download_to_drive(file_path)

        # Read and parse the index rules
        with open(file_path, "r", encoding="utf-8") as f:
            rules_text = f.readlines()

        index_rules = []
        for line in rules_text:
            line = line.strip()
            if not line:  # Skip empty lines
                continue
            match = re.match(r"(.+?)\s*>>\s*(\S+)", line)
            if match:
                index_rules.append({
                    "keyword": match.group(1).strip(),
                    "chapter_id": match.group(2).strip(),
                    "found": None
                })

        # Delete the downloaded file
        try:
            os.remove(file_path)
        except OSError as e:
            logger.error(f"Error deleting index rules file: {e}")

        if not index_rules:
            await update.message.reply_text("No valid rules found in the file. Please upload a valid .txt file.")
            return INDEX_RULES

        context.user_data["index_rules"] = index_rules
        await update.message.reply_text("Index rules processed successfully. Starting forwarding with indexing...")
        await forward_messages_with_indexing(update, context)
        return ConversationHandler.END

    except TelegramError as e:
        logger.error(f"Error downloading or processing index rules file: {e}")
        await update.message.reply_text("Failed to process the uploaded file. Please try again.")
        return INDEX_RULES

async def forward_messages_with_indexing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    source_channel = context.user_data["source_channel"]
    source_channel_id = context.user_data["source_channel_id"]
    start_id = context.user_data["start_id"]
    end_id = context.user_data["end_id"]
    target_channel = context.user_data["target_channel"]
    target_channel_id = context.user_data["target_channel_id"]
    rules = context.user_data["index_rules"]
    user_chat_id = update.effective_chat.id

    total = end_id - start_id + 1
    done = 0
    last_update_time = asyncio.get_event_loop().time()
    progress_message = await context.bot.send_message(chat_id=user_chat_id, text="Starting...")

    for msg_id in range(start_id, end_id + 1):
        try:
            msg = await context.bot.forward_message(
                chat_id=user_chat_id, from_chat_id=source_channel, message_id=msg_id
            )
            caption = msg.caption or ""

            # Remove "ChapterId > ..." from caption
            new_caption = re.sub(r"ChapterId\s*>\s*\S+", "", caption).strip()

            # Apply HTML formatting (e.g., bold)
            if new_caption:
                formatted_caption = f"<b>{new_caption}</b>"
            else:
                formatted_caption = ""

            sent = None
            if msg.video:
                sent = await context.bot.send_video(
                    chat_id=target_channel,
                    video=msg.video.file_id,
                    caption=formatted_caption,
                    parse_mode="HTML"
                )
                await asyncio.sleep(8)  # delay for video
            elif msg.document:
                sent = await context.bot.send_document(
                    chat_id=target_channel,
                    document=msg.document.file_id,
                    caption=formatted_caption,
                    parse_mode="HTML"
                )
                await asyncio.sleep(8)  # delay for document
            else:
                sent = await context.bot.copy_message(
                    chat_id=target_channel,
                    from_chat_id=source_channel,
                    message_id=msg_id
                )

            # Store message ID mapping
            context.user_data["message_id_map"].append(
                f"https://t.me/c/{source_channel_id}/{msg_id} = https://t.me/c/{target_channel_id}/{sent.message_id}"
            )

            # Store target channel msg_id if matches indexing rule
            for rule in rules:
                if rule["found"] is None and rule["chapter_id"].lower() in caption.lower():
                    rule["found"] = sent.message_id

            await asyncio.sleep(0.1)
            await context.bot.delete_message(chat_id=user_chat_id, message_id=msg.message_id)

            done += 1

            if asyncio.get_event_loop().time() - last_update_time >= 10:
                pb = create_progress_bar(done, total)
                await context.bot.edit_message_text(
                    chat_id=user_chat_id,
                    message_id=progress_message.message_id,
                    text=f"Progress: {pb} {done}/{total}"
                )
                last_update_time = asyncio.get_event_loop().time()

        except TelegramError as e:
            logger.error(f"Error at {msg_id}: {e}")
            done += 1
            continue

    # Create and save the message ID mapping to a .txt file
    txt_filename = "message_id_mapping.txt"
    with open(txt_filename, "w", encoding="utf-8") as f:
        f.write("\n".join(context.user_data["message_id_map"]))

    # Upload the message ID mapping .txt file
    try:
        with open(txt_filename, "rb") as f:
            await context.bot.send_document(
                chat_id=user_chat_id,
                document=f,
                caption="Message ID Mapping"
            )
    except TelegramError as e:
        logger.error(f"Error uploading message ID mapping file: {e}")
        await update.message.reply_text("Failed to upload message ID mapping file.")

    # Delete the local message ID mapping .txt file
    try:
        os.remove(txt_filename)
    except OSError as e:
        logger.error(f"Error deleting message ID mapping file: {e}")

    # Prepare and save indexing results to a .txt file
    index_filename = "index_results.txt"
    summary = "Index Results:\n"
    for r in rules:
        if r["found"]:
            summary += f"{r['keyword']} > https://t.me/c/{target_channel_id}/{r['found']}\n"
        else:
            summary += f"{r['keyword']} > Not Found\n"

    with open(index_filename, "w", encoding="utf-8") as f:
        f.write(summary)

    # Upload the index results .txt file
    try:
        with open(index_filename, "rb") as f:
            await context.bot.send_document(
                chat_id=user_chat_id,
                document=f,
                caption="Index Results"
            )
    except TelegramError as e:
        logger.error(f"Error uploading index results file: {e}")
        await update.message.reply_text("Failed to upload index results file.")

    # Delete the local index results .txt file
    try:
        os.remove(index_filename)
    except OSError as e:
        logger.error(f"Error deleting index results file: {e}")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END

async def main():
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "dockscjdjcj")
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("now", now)],
        states={
            START_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_start_link)],
            END_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_end_link)],
            TARGET_CHANNEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_target_channel)],
            INDEX_RULES: [MessageHandler(filters.Document.TXT, get_index_rules)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)

    await app.run_polling(allowed_updates=Update.ALL_TYPES, close_loop=False)

if __name__ == "__main__":
    asyncio.run(main())
