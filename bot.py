import os
import asyncio
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from memory import MemoryManager
from claude_client import ClaudeClient
from transcribe import transcribe_voice

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
DROPBOX_REFRESH_TOKEN = os.environ["DROPBOX_REFRESH_TOKEN"]
DROPBOX_APP_KEY = os.environ["DROPBOX_APP_KEY"]
DROPBOX_APP_SECRET = os.environ["DROPBOX_APP_SECRET"]
ALLOWED_USER_ID = os.getenv("ALLOWED_USER_ID")  # your Telegram numeric user ID

memory = MemoryManager(DROPBOX_REFRESH_TOKEN, DROPBOX_APP_KEY, DROPBOX_APP_SECRET)
claude: ClaudeClient | None = None


def allowed(update: Update) -> bool:
    if not ALLOWED_USER_ID:
        return True
    return str(update.effective_user.id) == ALLOWED_USER_ID


# ── Handlers ─────────────────────────────────────────────────────────────────


REMEMBER_TRIGGERS = (
    "remember that", "remember this", "save that", "save this",
    "keep that in mind", "note that", "note this", "don't forget",
    "make a note",
)

async def _process(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    wants_save = any(t in text.lower() for t in REMEMBER_TRIGGERS)

    async def keep_typing():
        while True:
            await context.bot.send_chat_action(update.effective_chat.id, "typing")
            await asyncio.sleep(4)

    typing_task = asyncio.create_task(keep_typing())
    try:
        reply = await claude.chat(text)
    finally:
        typing_task.cancel()

    await update.message.reply_text(reply)

    if memory.should_save_conversation:
        await memory.save_conversation()

    if wants_save or memory.should_extract:
        facts = await claude.extract_facts()
        if facts:
            await memory.save_facts()
            logger.info("Extracted facts: %s", facts)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await _process(update, context, update.message.text)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await update.message.reply_text("Ready.")


async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    text = memory.get_memory_text()
    await update.message.reply_text(text or "Memory is empty.")


async def cmd_remember(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /remember <fact>
    /remember projects <fact>
    /remember preferences <fact>
    /remember notes <fact>
    """
    if not allowed(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage: /remember [category] fact\nCategories: projects, preferences, notes"
        )
        return

    known = {"projects", "preferences", "notes", "about_caleb"}
    if args[0] in known and len(args) > 1:
        category, fact = args[0], " ".join(args[1:])
    else:
        category, fact = "notes", " ".join(args)

    added = memory.add_fact(fact, category)
    await memory.save_facts()
    await update.message.reply_text("Saved." if added else "Already in memory.")


async def cmd_forget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /forget <exact fact text>")
        return
    fact = " ".join(context.args)
    removed = memory.remove_fact(fact)
    if removed:
        await memory.save_facts()
        await update.message.reply_text("Removed.")
    else:
        await update.message.reply_text("Fact not found — use /memory to see exact text.")


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear conversation history (not facts)."""
    if not allowed(update):
        return
    facts = await claude.extract_facts()
    if facts:
        await memory.save_facts()
    memory.clear_today()
    await memory.save_conversation()
    await update.message.reply_text("Conversation cleared.")


async def cmd_extract(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually trigger fact extraction from recent conversation."""
    if not allowed(update):
        return
    await context.bot.send_chat_action(update.effective_chat.id, "typing")
    facts = await claude.extract_facts()
    if facts:
        await memory.save_facts()
        lines = [f"[{cat}] {item}" for cat, items in facts.items() for item in items]
        await update.message.reply_text("Extracted:\n" + "\n".join(lines))
    else:
        await update.message.reply_text("Nothing new to extract.")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    try:
        voice_file = await update.message.voice.get_file()
        voice_bytes = await voice_file.download_as_bytearray()
        transcribed = await transcribe_voice(voice_bytes)
    except Exception as e:
        logger.error("Transcription failed: %s", e)
        await update.message.reply_text("Couldn't transcribe that.")
        return

    if not transcribed:
        await update.message.reply_text("Got empty transcription.")
        return

    # Echo transcription so you know what it heard, then respond
    await update.message.reply_text(f"[voice] {transcribed}")
    await _process(update, context, transcribed)


# ── Init ─────────────────────────────────────────────────────────────────────


async def summarize_past_days():
    """Background task: summarize any unsummarized past conversation days."""
    try:
        dates = await memory.get_unsummarized_dates()
        if not dates:
            return
        for date_str in dates[-5:]:  # max 5 at startup
            messages = await memory.load_date(date_str)
            if messages:
                summary = await claude.summarize_day(messages)
                if summary:
                    memory.add_summary(date_str, summary)
                    logger.info("Summarized %s", date_str)
        await memory.save_facts()
    except Exception as e:
        logger.warning("summarize_past_days failed: %s", e)


async def post_init(application: Application):
    global claude
    logger.info("Loading memory from Dropbox...")
    await memory.load()
    claude = ClaudeClient(ANTHROPIC_API_KEY, memory)
    asyncio.create_task(summarize_past_days())
    logger.info("Bot ready.")


def main():
    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("memory", cmd_memory))
    app.add_handler(CommandHandler("remember", cmd_remember))
    app.add_handler(CommandHandler("forget", cmd_forget))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("extract", cmd_extract))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    logger.info("Starting polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()
