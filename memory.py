import json
import logging
from datetime import date
from concurrent.futures import ThreadPoolExecutor
import asyncio
import dropbox
from dropbox.exceptions import ApiError, AuthError

logger = logging.getLogger(__name__)

DROPBOX_BASE = "/CalebBot"
FACTS_PATH = f"{DROPBOX_BASE}/memory/facts.json"

DEFAULT_FACTS = {
    "about_caleb": [
        "Geotechnical field/lab tech in the San Luis Valley, CO",
        "Building a hyperadobe compound on his own land — long-term project",
        "Engaged with Advaita Vedanta: Adyashanti, Robert Adams, Shankara",
        "Zen practice background",
    ],
    "projects": [],
    "preferences": [],
    "notes": [],
    "summaries": [],  # [{"date": "YYYY-MM-DD", "text": "..."}]
}


class MemoryManager:
    def __init__(self, dropbox_refresh_token: str, app_key: str, app_secret: str):
        self._refresh_token = dropbox_refresh_token
        self._app_key = app_key
        self._app_secret = app_secret
        self._executor = ThreadPoolExecutor(max_workers=1)
        self.facts: dict = {k: list(v) for k, v in DEFAULT_FACTS.items()}
        self.conversation_history: list = []
        self.today_str = str(date.today())
        self._message_count = 0

    def _dbx(self) -> dropbox.Dropbox:
        """Fresh client per call — avoids shared token state corruption."""
        return dropbox.Dropbox(
            oauth2_refresh_token=self._refresh_token,
            app_key=self._app_key,
            app_secret=self._app_secret,
        )

    # ── Dropbox helpers ──────────────────────────────────────────────────────

    def _download_json(self, path: str, default=None):
        try:
            _, res = self._dbx().files_download(path)
            return json.loads(res.content)
        except ApiError as e:
            logger.warning("Dropbox ApiError (%s): %s", path, e)
            return default
        except AuthError as e:
            logger.error("Dropbox AuthError (%s): %s", path, e)
            return default
        except Exception as e:
            logger.error("Dropbox download error (%s): %s", path, e)
            return default

    def _upload_json(self, path: str, data):
        try:
            content = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
            self._dbx().files_upload(
                content,
                path,
                mode=dropbox.files.WriteMode.overwrite,
                mute=True,
            )
            logger.info("Dropbox upload OK: %s", path)
        except Exception as e:
            logger.error("Dropbox upload error (%s): %s", path, e)

    async def _async(self, fn, *args):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, fn, *args)

    # ── Load / save ──────────────────────────────────────────────────────────

    async def load(self):
        saved_facts = await self._async(self._download_json, FACTS_PATH, None)
        if saved_facts:
            for k, v in DEFAULT_FACTS.items():
                saved_facts.setdefault(k, list(v))
            self.facts = saved_facts
            logger.info("Facts loaded from Dropbox.")
        else:
            logger.warning("No facts found in Dropbox, using defaults.")

        convo_path = f"{DROPBOX_BASE}/conversations/{self.today_str}.json"
        history = await self._async(self._download_json, convo_path, [])
        self.conversation_history = history[-60:] if history else []
        self._message_count = len(self.conversation_history)
        logger.info("Loaded %d conversation messages.", self._message_count)

    async def save_facts(self):
        await self._async(self._upload_json, FACTS_PATH, self.facts)

    async def save_conversation(self):
        convo_path = f"{DROPBOX_BASE}/conversations/{self.today_str}.json"
        await self._async(self._upload_json, convo_path, self.conversation_history)

    # ── Memory ops ───────────────────────────────────────────────────────────

    def add_message(self, role: str, content: str):
        self.conversation_history.append({"role": role, "content": content})
        self._message_count += 1

    def add_fact(self, fact: str, category: str = "notes") -> bool:
        bucket = self.facts.setdefault(category, [])
        if fact not in bucket:
            bucket.append(fact)
            return True
        return False

    def remove_fact(self, fact: str) -> bool:
        for bucket in self.facts.values():
            if isinstance(bucket, list) and fact in bucket:
                bucket.remove(fact)
                return True
        return False

    def _list_conversation_dates(self) -> list[str]:
        try:
            result = self._dbx().files_list_folder(f"{DROPBOX_BASE}/conversations")
            dates = []
            for entry in result.entries:
                if hasattr(entry, "name") and entry.name.endswith(".json"):
                    dates.append(entry.name[:-5])
            return sorted(dates)
        except Exception:
            return []

    async def get_unsummarized_dates(self) -> list[str]:
        all_dates = await self._async(self._list_conversation_dates)
        summarized = {s["date"] for s in self.facts.get("summaries", [])}
        return [d for d in all_dates if d != self.today_str and d not in summarized]

    async def load_date(self, date_str: str) -> list:
        convo_path = f"{DROPBOX_BASE}/conversations/{date_str}.json"
        history = await self._async(self._download_json, convo_path, [])
        return history or []

    def add_summary(self, date: str, text: str):
        summaries = self.facts.setdefault("summaries", [])
        if not any(s["date"] == date for s in summaries):
            summaries.append({"date": date, "text": text})
            self.facts["summaries"] = sorted(summaries, key=lambda s: s["date"])[-30:]

    def get_memory_text(self) -> str:
        lines = []
        for cat, items in self.facts.items():
            if cat == "summaries" or not items:
                continue
            lines.append(f"[{cat.replace('_', ' ').title()}]")
            lines.extend(f"- {item}" for item in items)
        return "\n".join(lines)

    def get_summaries_text(self, n: int = 5) -> str:
        summaries = self.facts.get("summaries", [])[-n:]
        if not summaries:
            return ""
        return "\n".join(f"[{s['date']}] {s['text']}" for s in summaries)

    def get_context_messages(self, n: int = 20) -> list:
        return self.conversation_history[-n:]

    def clear_today(self):
        self.conversation_history = []
        self._message_count = 0

    # ── Journal ──────────────────────────────────────────────────────────────

    def _append_journal_entry(self, entry: dict):
        path = f"{DROPBOX_BASE}/journal/{self.today_str}.json"
        existing = self._download_json(path, [])
        existing.append(entry)
        self._upload_json(path, existing)

    async def save_journal_entry(self, text: str, tags: list[str]):
        from datetime import datetime
        entry = {
            "timestamp": datetime.now().strftime("%H:%M"),
            "text": text,
            "tags": tags,
        }
        await self._async(self._append_journal_entry, entry)

    def _load_journal_days(self, days: int) -> list[dict]:
        from datetime import date, timedelta
        entries = []
        for i in range(days):
            d = (date.today() - timedelta(days=i)).isoformat()
            path = f"{DROPBOX_BASE}/journal/{d}.json"
            day_entries = self._download_json(path, [])
            for e in day_entries:
                e["date"] = d
            entries.extend(day_entries)
        return entries

    async def get_journal_entries(self, days: int = 7, tag: str | None = None) -> list[dict]:
        entries = await self._async(self._load_journal_days, days)
        if tag:
            entries = [e for e in entries if tag.lower() in [t.lower() for t in e.get("tags", [])]]
        return entries

    @property
    def should_save_conversation(self) -> bool:
        return self._message_count > 0 and self._message_count % 5 == 0
