import json
import os
from anthropic import AsyncAnthropic
from memory import MemoryManager

SYSTEM_PROMPT = """You're talking to Caleb. He's a geotechnical field/lab tech in the San Luis Valley, CO, building a hyperadobe compound on his own land as a long-term project. Engaged with Advaita Vedanta (Adyashanti, Robert Adams, Shankara) and has a Zen practice background.

Style: Be direct and concise. Skip preambles, summaries of what he just said, and "that's a great question" type filler. Dry humor is welcome. Don't hedge everything with caveats. Don't ask if he wants to explore something further — if you have something worth saying, say it. Match his register — he's sharp and talks like a normal person, not a LinkedIn post.

When he's working through ideas, engage like a peer who happens to know a lot, not like a coach or advisor. Push back when something doesn't hold up. Don't moralize.

You have access to a web_search tool. Use it when the question requires current information, recent events, prices, weather, or anything time-sensitive. Don't use it for general knowledge you already have.

You are part of a multi-bot network. You always know about these bots — this is permanent knowledge, not something from conversation history:
- ScheduleBot — tracks field schedules and concrete cylinder break due dates
- FieldOpsBot — files field notes, photos, batch tickets, and reports by job
- MetQueryBot — answers natural language queries about field data and generates reports

You can pull live data from any of these bots on demand by reading their shared Dropbox files. When Caleb asks about schedules, breaks, jobs, or field activity, the system automatically loads the relevant data and passes it to you — you don't need to tell him to use a separate command. For casual questions about the bots, treat them like colleagues. Never claim you can't access their data or that the interface isn't built — it is.

All responses go to Telegram. Do not use markdown tables or headers — plain text only."""

EXTRACTION_PROMPT = """Review this conversation and extract any facts worth remembering long-term about Caleb. Return ONLY valid JSON:
{
  "projects": [],
  "preferences": [],
  "notes": [],
  "wellbeing": []
}
Guidelines:
- projects: specific work, build tasks, goals, decisions made
- preferences: how he likes things done, tools he uses, opinions
- notes: anything specific and useful that doesn't fit elsewhere
- wellbeing: emotional state, energy level, stress, mood, mental patterns — only if clearly expressed, not inferred. Be specific ("feeling behind on exam prep", "good flow state on the build today") not generic ("seems stressed").
Only include genuinely new, specific facts. Empty lists are fine. No explanation, just JSON."""

SUMMARY_PROMPT = """Summarize this conversation in 3-5 bullet points. Focus on specific facts, decisions, topics discussed, and anything actionable or worth remembering. Be concrete. No filler. Return plain text bullets only."""

WEB_SEARCH_TOOL = [
    {
        "name": "web_search",
        "description": "Search the web for current information. Use for news, prices, weather, recent events, or anything that may have changed recently.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query",
                }
            },
            "required": ["query"],
        },
    }
]


class ClaudeClient:
    def __init__(self, api_key: str, memory: MemoryManager):
        self.client = AsyncAnthropic(api_key=api_key)
        self.memory = memory
        self._tavily_key = os.getenv("TAVILY_API_KEY")

    def _system_prompt(self) -> str:
        parts = [SYSTEM_PROMPT]
        memory_text = self.memory.get_memory_text()
        if memory_text:
            parts.append(f"## Memory\n{memory_text}")
        summaries_text = self.memory.get_summaries_text(5)
        if summaries_text:
            parts.append(f"## Recent Session Summaries\n{summaries_text}")
        return "\n\n".join(parts)

    async def _web_search(self, query: str) -> str:
        if not self._tavily_key:
            return "Web search is not configured."
        try:
            from tavily import AsyncTavilyClient
            client = AsyncTavilyClient(api_key=self._tavily_key)
            results = await client.search(query, max_results=5)
            lines = []
            for r in results.get("results", []):
                lines.append(f"{r['title']}\n{r['content']}\n{r['url']}")
            return "\n\n".join(lines) or "No results found."
        except Exception as e:
            return f"Search failed: {e}"

    @staticmethod
    def _clean_messages(messages: list) -> list:
        """Strip any messages with non-string content (tool-use artifacts)."""
        cleaned = [m for m in messages if isinstance(m.get("content"), str)]
        # Ensure alternating roles — drop leading assistant messages
        while cleaned and cleaned[0]["role"] == "assistant":
            cleaned.pop(0)
        return cleaned

    async def chat(self, user_message: str) -> str:
        self.memory.add_message("user", user_message)
        # Work on a clean copy — no tool-use intermediate messages
        messages = self._clean_messages(list(self.memory.get_context_messages(20)))

        tools = WEB_SEARCH_TOOL if self._tavily_key else []

        while True:
            response = await self.client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2048,
                system=self._system_prompt(),
                messages=messages,
                tools=tools or None,
            )

            if response.stop_reason == "tool_use":
                tool_block = next(b for b in response.content if b.type == "tool_use")
                search_results = await self._web_search(tool_block.input["query"])
                # Manually serialize to avoid model_dump() producing unexpected fields
                assistant_content = []
                for b in response.content:
                    if b.type == "tool_use":
                        assistant_content.append({
                            "type": "tool_use",
                            "id": b.id,
                            "name": b.name,
                            "input": b.input,
                        })
                    elif b.type == "text":
                        assistant_content.append({"type": "text", "text": b.text})
                messages.append({"role": "assistant", "content": assistant_content})
                messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": tool_block.id,
                        "content": search_results,
                    }],
                })
            else:
                break

        reply = next(
            (b.text for b in response.content if hasattr(b, "text")),
            "[no response]",
        )
        self.memory.add_message("assistant", reply)
        return reply

    async def extract_facts(self) -> dict:
        """Use Haiku to extract notable facts from recent conversation."""
        recent = self.memory.get_context_messages(30)
        if not recent:
            return {}

        convo_text = "\n".join(
            f"{m['role'].title()}: {m['content']}" for m in recent
        )

        response = await self.client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{
                "role": "user",
                "content": f"{EXTRACTION_PROMPT}\n\nConversation:\n{convo_text}",
            }],
        )

        try:
            facts = json.loads(response.content[0].text)
            added = {}
            for category, items in facts.items():
                for item in items:
                    if item and self.memory.add_fact(item, category):
                        added.setdefault(category, []).append(item)
            return added
        except Exception:
            return {}

    async def ask_bot(self, bot_name: str, query: str, data: dict) -> str:
        from met_client import BOT_CONFIG
        config = BOT_CONFIG.get(bot_name, {})
        context_prompt = config.get("prompt", "")
        data_text = "\n\n".join(
            f"### {filename}\n{json.dumps(content, indent=2)}"
            for filename, content in data.items()
        )
        response = await self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=f"{context_prompt}\n\n## Data\n{data_text}",
            messages=[{"role": "user", "content": query}],
        )
        return next((b.text for b in response.content if hasattr(b, "text")), "[no response]")

    async def summarize_day(self, messages: list) -> str:
        if not messages:
            return ""
        convo_text = "\n".join(
            f"{m['role'].title()}: {m['content']}" for m in messages
        )
        response = await self.client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": f"{SUMMARY_PROMPT}\n\nConversation:\n{convo_text}",
            }],
        )
        return response.content[0].text.strip()
