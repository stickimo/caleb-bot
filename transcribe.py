import io
import os
from openai import AsyncOpenAI

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


async def transcribe_voice(voice_bytes: bytes | bytearray) -> str:
    client = _get_client()
    buf = io.BytesIO(bytes(voice_bytes))
    buf.name = "voice.ogg"
    transcript = await client.audio.transcriptions.create(
        model="whisper-1",
        file=buf,
    )
    return transcript.text.strip()
