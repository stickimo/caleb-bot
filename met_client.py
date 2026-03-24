import json
import logging

logger = logging.getLogger(__name__)

MET_BASE = "/MET"

# Canonical bot names and their aliases
BOT_ALIASES = {
    "schedulebot": "schedulebot",
    "schedule": "schedulebot",
    "fieldbot": "fieldbot",
    "fieldopsbot": "fieldbot",
    "field": "fieldbot",
    "querybot": "querybot",
    "metquerybot": "querybot",
    "query": "querybot",
}

# Data paths and context prompt per bot
BOT_CONFIG = {
    "schedulebot": {
        "prompt": (
            "You have access to ScheduleBot's live data: the current field schedule and "
            "confirmed break records. Answer the query using this data. Be specific about "
            "dates, times, jobs, and break due dates. If something isn't in the data, say so. "
            "Plain text only — no markdown tables or headers. This is a Telegram message."
        ),
        "paths": [
            f"{MET_BASE}/schedule.json",
            f"{MET_BASE}/breaks_confirmed.json",
        ],
    },
    "fieldbot": {
        "prompt": (
            "You have access to FieldOpsBot's live data: the jobs registry and activity log. "
            "Answer the query using this data. Be specific about job numbers, file types, "
            "dates, and activities logged. "
            "Plain text only — no markdown tables or headers. This is a Telegram message."
        ),
        "paths": [
            f"{MET_BASE}/jobs.json",
            f"{MET_BASE}/activity_log.json",
        ],
    },
    "querybot": {
        "prompt": (
            "You have access to MetQueryBot's data sources: the jobs registry and full "
            "activity log. Answer the query analytically. Be thorough — include counts, "
            "dates, job breakdowns, and patterns where relevant. "
            "Plain text only — no markdown tables or headers. This is a Telegram message."
        ),
        "paths": [
            f"{MET_BASE}/jobs.json",
            f"{MET_BASE}/activity_log.json",
        ],
    },
}

# Natural language phrases that trigger bot routing
NL_TRIGGERS = {
    "schedulebot": [
        "ask schedulebot", "check schedulebot", "check the schedule",
        "what breaks are due", "breaks due", "schedule today", "what's on the schedule",
        "whats on the schedule", "field schedule",
    ],
    "fieldbot": [
        "ask fieldbot", "check fieldbot", "check field ops",
        "what's been filed", "whats been filed", "activity log",
    ],
    "querybot": [
        "ask querybot", "check querybot", "ask metquerybot",
        "query the field data", "look up job",
    ],
}


def resolve_bot(text: str) -> str | None:
    """Return canonical bot name from text, or None if not found."""
    lower = text.lower()
    for bot, triggers in NL_TRIGGERS.items():
        if any(t in lower for t in triggers):
            return bot
    return None


def load_bot_data(dbx_factory, bot_name: str) -> dict:
    """Load all Dropbox data files for the given bot."""
    config = BOT_CONFIG.get(bot_name)
    if not config:
        return {}
    data = {}
    for path in config["paths"]:
        try:
            _, res = dbx_factory().files_download(path)
            key = path.split("/")[-1]
            data[key] = json.loads(res.content)
        except Exception as e:
            logger.warning("Could not load %s: %s", path, e)
    return data
