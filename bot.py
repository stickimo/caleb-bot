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
from docs import list_documents, fetch_and_parse

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
ALLOWED_USER_ID = os.getenv("ALLOWED_USER_ID")

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

FILLER = {"ok", "okay", "thanks", "thank you", "got it", "sure", "yep", "nope",
          "yes", "no", "lol", "haha", "cool", "nice", "great", "k"}

def _is_substantive(text: str) -> bool:
    words = text.lower().split()
    return len(words) >= 5 and not all(w in FILLER for w in words)

async def _background_extract():
    try:
        facts = await claude.extract_facts()
        if facts:
            await memory.save_facts()
            logger.info("Extracted facts: %s", facts)
    except Exception as e:
        logger.warning("Background extraction failed: %s", e)

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

    if wants_save or _is_substantive(text):
        asyncio.create_task(_background_extract())


async def cmd_docs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    docs, err = await memory._async(list_documents, memory._dbx())
    if err:
        await update.message.reply_text(err)
    elif docs:
        await update.message.reply_text("Docs available:\n" + "\n".join(f"/read {d}" for d in docs))
    else:
        await update.message.reply_text("No documents found in /CalebBot/documents/")


async def cmd_read(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /read <filename>\nUse /docs to see available files.")
        return
    filename = " ".join(context.args)
    await update.message.reply_text(f"Reading {filename}...")
    await context.bot.send_chat_action(update.effective_chat.id, "typing")
    text = await memory._async(fetch_and_parse, memory._dbx(), filename)
    if any(text.startswith(p) for p in ("File not found", "Download error", "Unsupported", "PDF parse error", "Parse error")):
        await update.message.reply_text(text)
        return
    await _process(update, context, f"[Document loaded: {filename}]\n\n{text}")


async def cmd_reflect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await _process(update, context,
        "Let's do a quick end-of-day reflection. Ask me one good question to get started — "
        "what happened today, what's unresolved, what's on my mind. Keep it simple."
    )


async def cmd_journal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /journal <entry> — use #tags to tag entries.")
        return
    import re
    text = " ".join(context.args)
    tags = re.findall(r"#(\w+)", text)
    await memory.save_journal_entry(text, tags)
    tag_str = "  " + " ".join(f"#{t}" for t in tags) if tags else ""
    await update.message.reply_text(f"Saved.{tag_str}")


async def cmd_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    tag = context.args[0].lstrip("#") if context.args else None
    entries = await memory.get_journal_entries(days=7, tag=tag)
    if not entries:
        msg = f"No entries found for #{tag}." if tag else "No journal entries in the last 7 days."
        await update.message.reply_text(msg)
        return
    lines = []
    current_date = None
    for e in reversed(entries):
        if e["date"] != current_date:
            current_date = e["date"]
            lines.append(f"\n{current_date}")
        tag_str = " " + " ".join(f"#{t}" for t in e.get("tags", [])) if e.get("tags") else ""
        lines.append(f"[{e['timestamp']}]{tag_str} {e['text']}")
    await update.message.reply_text("\n".join(lines).strip())


async def cmd_weather(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await _process(update, context, "What's the current weather in the San Luis Valley, CO?")


async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await _process(update, context, "What are the top news stories right now? Give me a brief rundown.")


async def cmd_holidays(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    from datetime import date
    today = date.today().strftime("%B %d, %Y")
    await _process(update, context, f"Today is {today}. Are there any US holidays today or in the next few days?")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await _process(update, context, update.message.text)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await update.message.reply_text("Ready.")


HELP_SECTIONS = {
    "journal": (
        "JOURNAL\n"
        "/reflect — start an end-of-day reflection\n"
        "/journal <entry> — save a tagged note (use #tags)\n"
        "/log [#tag] — view recent entries"
    ),
    "docs": (
        "DOCUMENTS\n"
        "/docs — list available documents\n"
        "/read <filename> — load a document"
    ),
    "info": (
        "INFO\n"
        "/weather — current weather in the San Luis Valley\n"
        "/news — top news stories\n"
        "/holidays — US holidays around today"
    ),
    "memory": (
        "MEMORY\n"
        "/memory — show all stored facts\n"
        "/status — fact counts and session info\n"
        "/summary — recent daily summaries\n"
        "/search <term> — search stored facts"
    ),
    "manage": (
        "MANAGE\n"
        "/remember [category] fact — save a fact\n"
        "    categories: projects, preferences, notes, wellbeing\n"
        "/forget <exact fact> — remove a fact\n"
        "/wipe <category> — clear an entire category\n"
        "/extract — trigger fact extraction\n"
        "/clear — wipe today's conversation"
    ),
}

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    if context.args and context.args[0].lower() in HELP_SECTIONS:
        await update.message.reply_text(HELP_SECTIONS[context.args[0].lower()])
    else:
        await update.message.reply_text(
            "Available sections:\n"
            "/help journal\n"
            "/help docs\n"
            "/help info\n"
            "/help memory\n"
            "/help manage"
        )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    facts = memory.facts
    lines = []
    for cat, items in facts.items():
        if cat == "summaries":
            continue
        lines.append(f"{cat.replace('_', ' ').title()}: {len(items)} facts")
    lines.append(f"Summaries: {len(facts.get('summaries', []))}")
    lines.append(f"Conversation messages today: {memory._message_count}")
    await update.message.reply_text("\n".join(lines))


async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    text = memory.get_summaries_text(n=10)
    await update.message.reply_text(text or "No summaries yet.")


async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /search <term>")
        return
    term = " ".join(context.args).lower()
    results = []
    for cat, items in memory.facts.items():
        if cat == "summaries":
            continue
        if isinstance(items, list):
            for item in items:
                if term in item.lower():
                    results.append(f"[{cat.replace('_', ' ').title()}] {item}")
    await update.message.reply_text("\n".join(results) if results else f"Nothing found for '{term}'.")


async def cmd_wipe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    known = {"projects", "preferences", "notes", "about_caleb", "wellbeing"}
    if not context.args or context.args[0] not in known:
        await update.message.reply_text(
            "Usage: /wipe <category>\nCategories: projects, preferences, notes, about_caleb"
        )
        return
    category = context.args[0]
    memory.facts[category] = []
    await memory.save_facts()
    await update.message.reply_text(f"Cleared all {category} facts.")


async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    text = memory.get_memory_text()
    await update.message.reply_text(text or "Memory is empty.")


async def cmd_remember(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage: /remember [category] fact\nCategories: projects, preferences, notes"
        )
        return

    known = {"projects", "preferences", "notes", "about_caleb", "wellbeing"}
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
    if not allowed(update):
        return
    facts = await claude.extract_facts()
    if facts:
        await memory.save_facts()
    memory.clear_today()
    await memory.save_conversation()
    await update.message.reply_text("Conversation cleared.")


async def cmd_extract(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    await update.message.reply_text(f"[voice] {transcribed}")
    await _process(update, context, transcribed)


# ── Init ─────────────────────────────────────────────────────────────────────


async def summarize_past_days():
    try:
        dates = await memory.get_unsummarized_dates()
        if not dates:
            return
        for date_str in dates[-5:]:
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
    if memory.conversation_history:
        asyncio.create_task(_background_extract())
    logger.info("Bot ready.")


def main():
    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("reflect", cmd_reflect))
    app.add_handler(CommandHandler("journal", cmd_journal))
    app.add_handler(CommandHandler("log", cmd_log))
    app.add_handler(CommandHandler("docs", cmd_docs))
    app.add_handler(CommandHandler("read", cmd_read))
    app.add_handler(CommandHandler("weather", cmd_weather))
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(CommandHandler("holidays", cmd_holidays))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("summary", cmd_summary))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("wipe", cmd_wipe))
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
