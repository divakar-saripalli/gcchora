"""Provider-agnostic LLM gateway. The mobile app never calls Gemini/GPT
directly -- everything routes through here so provider keys stay
server-side and providers can be swapped without touching the app.

Both adapters are functional stubs: they build the right request and call
the real API when a key is configured, and return a clearly-labeled
placeholder otherwise so the rest of the service can be developed and
tested without live provider credentials.
"""
from abc import ABC, abstractmethod
from typing import Any

import httpx

from app.config import settings


class LLMProvider(ABC):
    @abstractmethod
    async def generate(self, context: str, question: str) -> str: ...


class GeminiProvider(LLMProvider):
    async def generate(self, context: str, question: str) -> str:
        if not settings.gemini_api_key:
            return "[gemini not configured] " + question
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-2.0-flash:generateContent?key={settings.gemini_api_key}"
        )
        payload = {"contents": [{"parts": [{"text": f"{context}\n\nUser: {question}"}]}]}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]


class GPTProvider(LLMProvider):
    async def generate(self, context: str, question: str) -> str:
        if not settings.openai_api_key:
            return "[gpt not configured] " + question
        url = "https://api.openai.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
        payload = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": context},
                {"role": "user", "content": question},
            ],
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"]


_PROVIDERS: dict[str, LLMProvider] = {
    "gemini": GeminiProvider(),
    "gpt": GPTProvider(),
}


async def generate_answer(context: str, question: str, provider: str | None = None) -> str:
    chosen = _PROVIDERS.get(provider or settings.default_llm_provider, _PROVIDERS["gemini"])
    try:
        return await chosen.generate(context, question)
    except Exception as exc:  # pragma: no cover - network/provider failure path
        # Simple failover to the other provider before giving up.
        fallback_name = "gpt" if chosen is _PROVIDERS["gemini"] else "gemini"
        try:
            return await _PROVIDERS[fallback_name].generate(context, question)
        except Exception:
            raise exc
