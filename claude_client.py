import json
from anthropic import AsyncAnthropic
from memory import MemoryManager

SYSTEM_PROMPT = """You're talking to Caleb. He's a geotechnical field/lab tech in the San Luis Valley, CO, building a hyperadobe compound on his own land as a long-term project. Engaged with Advaita Vedanta (Adyashanti, Robert Adams, Shankara) and has a Zen practice background.

Style: Be direct and concise. Skip preambles, summaries of what he just said, and "that's a great question" type filler. Dry humor is welcome. Don't hedge everything with caveats. Don't ask if he wants to explore something further — if you have something worth saying, say it. Match his register — he's sharp and talks like a normal person, not a LinkedIn post.

When he's working through ideas, engage like a peer who happens to know a lot, not like a coach or advisor. Push back when something doesn't hold up. Don't moralize."""

EXTRACTION_PROMPT = """Review this conversation and extract any facts worth remembering long-term about Caleb: his projects, decisions, preferences, or anything specific and useful. Return ONLY valid JSON:
{
  "projects": [],
  "preferences": [],
  "notes": []
}
Only include genuinely new, specific facts. Skip anything vague or already obvious from the base context. Empty lists are fine. No explanation, just JSON."""


class ClaudeClient:
    def __init__(self, api_key: str, memory: MemoryManager):
        self.client = AsyncAnthropic(api_key=api_key)
        self.memory = memory

    def _system_prompt(self) -> str:
        memory_text = self.memory.get_memory_text()
        if memory_text:
            return f"{SYSTEM_PROMPT}\n\n## Memory\n{memory_text}"
        return SYSTEM_PROMPT

    async def chat(self, user_message: str) -> str:
        self.memory.add_message("user", user_message)
        messages = self.memory.get_context_messages(20)

        response = await self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=self._system_prompt(),
            messages=messages,
        )

        reply = response.content[0].text
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
            messages=[
                {
                    "role": "user",
                    "content": f"{EXTRACTION_PROMPT}\n\nConversation:\n{convo_text}",
                }
            ],
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
