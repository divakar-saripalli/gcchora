"""Provider-agnostic LLM gateway. The mobile app never calls Gemini/GPT
directly -- everything routes through here so provider keys stay
server-side and providers can be swapped without touching the app.

Two request shapes are supported:
  - generate_answer / generate_structured_answer: single-provider, with
    failover to the other configured provider if the primary fails. Used
    for chat (free-text) and for the cheap single-ayanamsa recheck
    app.routers.rectify runs once a profile is locked.
  - generate_structured_from_all: fans the same prompt out to every
    configured provider concurrently (no failover -- each is independent)
    and returns whatever succeeded, keyed by provider name. Used for the
    full multi-model rectification comparison.

Both adapters are functional stubs: they build the right request and call
the real API when a key is configured. Both generate() and
generate_structured() raise ProviderNotConfigured when the provider has no
key set -- generate() used to return a placeholder string instead, but
that let an unconfigured fallback provider's placeholder silently pass as
a real answer during failover (see generate_answer). Raising lets the
caller tell "nothing is configured" apart from "here is an answer".
"""
import asyncio
import json
import logging
import os
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


# Transient-overload status codes worth a couple of short retries (e.g.
# Gemini's 503 "This model is currently experiencing high demand" -- a
# genuine temporary blip that usually clears within a second or two).
# 429 is deliberately excluded: that means "you are being rate-limited",
# and the free-tier daily-quota case specifically suggests waits of tens
# of seconds to a day, far too long to block a live request on.
_RETRYABLE_STATUS_CODES = {500, 502, 503, 504}


async def _post_with_retry(
    client: httpx.AsyncClient,
    url: str,
    json_payload: dict,
    headers: dict | None = None,
    retries: int = 3,
    base_delay: float = 2.0,
) -> httpx.Response:
    """POSTs with backoff retries for transient server-side overload or
    network hiccups, so a momentary "high demand, try again" from the
    provider doesn't have to surface all the way up as a failed
    rectification/chat turn. Does not retry 4xx errors other than none --
    those (bad request, unauthorized, rate-limited) won't fix themselves
    by trying again immediately.

    retries=3/base_delay=2.0 gives 4 attempts total with 2s/4s/6s backoff
    between them (~12s of retrying before giving up) -- bumped up from an
    earlier 2 retries/1s base (~3s total) after a real Gemini "high
    demand" 503 outlasted that shorter window and surfaced as a failure
    that a bit more patience would have ridden out. The mobile app shows
    a "typing..." indicator for the whole duration (see chat_screen.dart)
    so this extra wait reads as normal chat latency, not a hang."""
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = await client.post(url, headers=headers, json=json_payload)
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
            last_exc = exc
            if attempt < retries:
                await asyncio.sleep(base_delay * (attempt + 1))
                continue
            raise
        if resp.status_code in _RETRYABLE_STATUS_CODES and attempt < retries:
            logger.info(
                "Provider POST to %s returned %s (attempt %d/%d) -- retrying shortly.",
                url.split("?", 1)[0], resp.status_code, attempt + 1, retries + 1,
            )
            await asyncio.sleep(base_delay * (attempt + 1))
            continue
        return resp
    raise last_exc  # pragma: no cover -- loop always returns or raises above


def _raise_for_status_with_body(resp: httpx.Response) -> None:
    """Like resp.raise_for_status(), but includes the response body in the
    exception message -- the provider's actual error text (e.g. Google's
    "model not found" or OpenAI's schema-validation message) is what
    actually explains a 4xx/5xx, and the plain httpx exception drops it,
    which turns every such failure into a guessing game from the status
    code alone."""
    if resp.is_success:
        return
    body_preview = resp.text[:2000]
    kind = "Client" if resp.is_client_error else "Server"
    raise httpx.HTTPStatusError(
        f"{kind} error '{resp.status_code}' for url '{resp.url}' -- body: {body_preview}",
        request=resp.request,
        response=resp,
    )


def _log_prompt(provider: str, kind: str, **parts: str) -> None:
    """Appends the exact text sent to a provider to a local log file
    (LOG_LLM_PROMPTS / LLM_PROMPT_LOG_PATH in settings), so it can be
    copy-pasted and tried directly against the provider's own UI (Gemini's
    web app, AI Studio, the OpenAI playground) independent of this
    backend -- useful for telling "our prompt is bad" apart from "the
    provider/model itself is behaving oddly". Each part is written under
    its own labeled section (e.g. system/context, question, schema) since
    different providers split the same request differently."""
    if not settings.log_llm_prompts:
        return
    path = settings.llm_prompt_log_path
    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    lines = [f"\n{'=' * 80}", f"{ts}  provider={provider}  kind={kind}"]
    for label, text in parts.items():
        lines.append(f"--- {label} ---")
        lines.append(text)
    lines.append("=" * 80)
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as exc:  # never let logging itself break a real request
        logger.warning("Could not write LLM prompt log to %s: %s", path, exc)


class ProviderNotConfigured(Exception):
    """Raised by generate_structured when no API key is set for a provider."""


class LLMProvider(ABC):
    name: str

    @abstractmethod
    async def generate(
        self, context: str, question: str, history: list[dict] | None = None
    ) -> str: ...

    @abstractmethod
    async def generate_structured(self, context: str, question: str, schema: dict) -> dict[str, Any]: ...


def _strip_unsupported_gemini_schema_keys(node: Any) -> Any:
    """Recursively drops "additionalProperties" (and, defensively, any
    future unrecognized-by-Gemini keys added the same way) from a JSON
    schema dict before it's sent to Gemini's responseSchema, which uses a
    restricted OpenAPI-subset proto with no such field."""
    if isinstance(node, dict):
        return {
            k: _strip_unsupported_gemini_schema_keys(v)
            for k, v in node.items()
            if k != "additionalProperties"
        }
    if isinstance(node, list):
        return [_strip_unsupported_gemini_schema_keys(v) for v in node]
    return node


def _gemini_turn(turn: dict) -> dict:
    role = "model" if turn.get("role") == "assistant" else "user"
    return {"role": role, "parts": [{"text": turn["content"]}]}


class GeminiProvider(LLMProvider):
    name = "gemini"

    async def generate(
        self, context: str, question: str, history: list[dict] | None = None
    ) -> str:
        if not settings.gemini_api_key:
            raise ProviderNotConfigured("gemini_api_key not set")
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-3.6-flash:generateContent?key={settings.gemini_api_key}"
        )
        contents = [_gemini_turn(turn) for turn in (history or [])]
        contents.append({"role": "user", "parts": [{"text": question}]})
        payload = {
            "contents": contents,
            "systemInstruction": {"parts": [{"text": context}]},
        }
        _log_prompt(
            "gemini", "chat",
            system_instruction=context,
            history=json.dumps(history or [], indent=2),
            question=question,
        )
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await _post_with_retry(client, url, payload)
            _raise_for_status_with_body(resp)
            data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]

    async def generate_structured(self, context: str, question: str, schema: dict) -> dict[str, Any]:
        if not settings.gemini_api_key:
            raise ProviderNotConfigured("gemini_api_key not set")
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-3.6-flash:generateContent?key={settings.gemini_api_key}"
        )
        payload = {
            "contents": [{"parts": [{"text": f"{context}\n\nUser: {question}"}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": _strip_unsupported_gemini_schema_keys(schema),
            },
        }
        _log_prompt(
            "gemini", "structured",
            prompt=f"{context}\n\nUser: {question}",
            response_schema=json.dumps(_strip_unsupported_gemini_schema_keys(schema), indent=2),
        )
        async with httpx.AsyncClient(timeout=45) as client:
            resp = await _post_with_retry(client, url, payload)
            _raise_for_status_with_body(resp)
            data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        _log_prompt("gemini", "structured_response", raw_response_text=text)
        return json.loads(text)


class GPTProvider(LLMProvider):
    name = "gpt"

    async def generate(
        self, context: str, question: str, history: list[dict] | None = None
    ) -> str:
        if not settings.openai_api_key:
            raise ProviderNotConfigured("openai_api_key not set")
        url = "https://api.openai.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
        messages = [{"role": "system", "content": context}]
        messages.extend(
            {"role": turn.get("role") if turn.get("role") in ("user", "assistant") else "user",
             "content": turn["content"]}
            for turn in (history or [])
        )
        messages.append({"role": "user", "content": question})
        payload = {"model": "gpt-4o-mini", "messages": messages}
        _log_prompt("gpt", "chat", messages=json.dumps(messages, indent=2))
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await _post_with_retry(client, url, payload, headers=headers)
            _raise_for_status_with_body(resp)
            data = resp.json()
        return data["choices"][0]["message"]["content"]

    async def generate_structured(self, context: str, question: str, schema: dict) -> dict[str, Any]:
        if not settings.openai_api_key:
            raise ProviderNotConfigured("openai_api_key not set")
        url = "https://api.openai.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
        payload = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": context},
                {"role": "user", "content": question},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "rectification_result", "schema": schema, "strict": True},
            },
        }
        _log_prompt(
            "gpt", "structured",
            system=context,
            question=question,
            response_schema=json.dumps(schema, indent=2),
        )
        async with httpx.AsyncClient(timeout=45) as client:
            resp = await _post_with_retry(client, url, payload, headers=headers)
            _raise_for_status_with_body(resp)
            data = resp.json()
        text = data["choices"][0]["message"]["content"]
        _log_prompt("gpt", "structured_response", raw_response_text=text)
        return json.loads(text)


_PROVIDERS: dict[str, LLMProvider] = {
    "gemini": GeminiProvider(),
    "gpt": GPTProvider(),
}


async def generate_answer(
    context: str, question: str, provider: str | None = None, history: list[dict] | None = None
) -> str:
    chosen = _PROVIDERS.get(provider or settings.default_llm_provider, _PROVIDERS["gemini"])
    try:
        return await chosen.generate(context, question, history)
    except Exception as exc:  # pragma: no cover - network/provider failure path
        # Simple failover to the other provider before giving up.
        fallback_name = "gpt" if chosen is _PROVIDERS["gemini"] else "gemini"
        try:
            return await _PROVIDERS[fallback_name].generate(context, question, history)
        except Exception:
            raise exc


async def generate_structured_answer(
    context: str, question: str, schema: dict, provider: str | None = None
) -> dict[str, Any]:
    """Single-provider structured call, with failover to the other
    configured provider on failure -- mirrors generate_answer. Used where
    only one provider's opinion is wanted (e.g. the locked-profile
    recheck, which deliberately stays cheap and single-provider)."""
    chosen = _PROVIDERS.get(provider or settings.default_llm_provider, _PROVIDERS["gemini"])
    try:
        return await chosen.generate_structured(context, question, schema)
    except Exception as exc:
        fallback_name = "gpt" if chosen is _PROVIDERS["gemini"] else "gemini"
        try:
            return await _PROVIDERS[fallback_name].generate_structured(context, question, schema)
        except Exception:
            raise exc


def _summarize_provider_error(exc: Exception) -> str:
    """Turns a raw provider exception into a short, user-facing reason.
    Without this, a caller whose providers all failed only ever saw a
    generic "no provider available" -- the actual cause (rate-limited?
    misconfigured? a real outage?) was logged server-side only, so
    diagnosing it meant reading server logs instead of the API response."""
    if isinstance(exc, ProviderNotConfigured):
        return "not configured"
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        resp = exc.response
        if resp.status_code == 429:
            message = ""
            try:
                message = resp.json().get("error", {}).get("message", "")
            except Exception:
                pass
            if "free_tier" in message or "quota" in message.lower():
                return (
                    "rate-limited -- free-tier daily quota exceeded for this model. Try again "
                    "after the quota resets, or enable billing / configure a second provider."
                )
            return "rate-limited (429) -- try again shortly"
        if resp.status_code in _RETRYABLE_STATUS_CODES:
            return (
                f"provider temporarily overloaded (HTTP {resp.status_code}) -- already retried a "
                "couple of times automatically, still unavailable. Usually clears within a minute."
            )
        return f"HTTP {resp.status_code} error"
    return "request failed"


async def generate_structured_from_all(
    context: str, question: str, schema: dict
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Fans the same structured request out to every configured provider
    concurrently. No failover -- each call is independent. Returns a dict
    keyed by provider name of the providers that both have a key
    configured and returned a successfully-parsed result, plus a second
    dict of short human-readable failure reasons for every other
    provider (unconfigured or errored), so a caller whose fan-out came
    back empty can tell the user *why* rather than just "no provider
    available"."""
    names = list(_PROVIDERS.keys())
    results = await asyncio.gather(
        *(_PROVIDERS[name].generate_structured(context, question, schema) for name in names),
        return_exceptions=True,
    )
    out: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    for name, result in zip(names, results):
        if isinstance(result, Exception):
            if not isinstance(result, ProviderNotConfigured):
                logger.warning("generate_structured_from_all: %s failed: %s", name, result)
            errors[name] = _summarize_provider_error(result)
            continue
        out[name] = result
    return out, errors
