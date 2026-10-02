# Deferred: LLM cost/request optimization

Intentionally not implemented yet -- explicitly deferred by Divakar until
the initial app setup is hardened (calibration flow, chat, quota all
working reliably end-to-end first). Revisit this file when that's done.

## 1. Minimize LLM request volume (rectification fan-out)
Raised first. Ideas noted so far:
- Batch multiple new follow-up life events before triggering a
  recalibration fan-out, instead of recalibrating on every single one.
- Skip the fan-out below a minimum life-event count (not enough signal
  to be worth a full multi-provider call yet).
- Cache/reuse a recent fan-out result if nothing material has changed
  since the last one.

## 2. Session-scoped chat context (avoid resending context every message)
Raised second, while discussing "one chat window per active user session."
Today `_build_context()` in app/routers/chat.py rebuilds the full system
instruction (birth details, calibration result, life events, tone rules)
and resends it in full on every single chat message, because Gemini/GPT's
API is stateless -- there's no server-side memory of a prior call unless
you resend what the model needs to know.

The real mechanism for this is Gemini's **explicit context caching**
(POST /v1beta/cachedContents): upload the context once per conversation
session, get a `cachedContent` handle back with a TTL, and reference the
handle on subsequent messages instead of resending the full text --
billed at a much lower per-token rate on reuse. Design sketched (not yet
built):
- `ensure_gemini_context_cache(context, existing)` in app/llm_gateway.py:
  hashes the context, reuses a still-valid cached handle if the hash
  matches, otherwise creates a new one; remembers "unsupported" (without
  retrying) if the content is too small for Gemini's minimum cacheable
  token count -- our context (birth details + a handful of events) is
  short and may fall under that minimum, so this needs to degrade
  gracefully to "just send it normally" rather than fail chat.
  Returns None to mean "don't use caching" whenever the provider isn't
  configured or caching creation fails.
- Store the returned {hash, name, expiresAt} (or {hash, unsupported}) on
  the conversation document (`contextCache` field) so it persists across
  requests/restarts, not just in memory.
- `GeminiProvider.generate` gains an optional `cached_content_name` param:
  if set, sends `cachedContent: <name>` instead of `systemInstruction`
  (conversation `history` + the new `question` are still sent every
  message as normal -- only the static context block gets cached).
- Recalibration or new life events naturally invalidate the cache, since
  they change `context`'s text and therefore its hash -- no separate
  invalidation step needed.
- OpenAI/GPT doesn't need an equivalent code change: it already does
  automatic prompt-prefix caching server-side with no API surface to
  manage.
- Important caveat: this could not be tested live from this environment
  (no external HTTPS access from the sandbox or the linked device's
  shell), so it needs to be built defensively (never breaks chat if
  caching isn't usable) and verified against the real Gemini API once
  implemented.
