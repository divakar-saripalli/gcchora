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

## 3. Pre-LLM request router/classifier (e.g. Jev)
Raised third, while investigating a 402 (our own app-quota exhaustion,
unrelated to Gemini billing -- see DefectBacklog for that thread) led into
a tangent about "Jev" (Jev Router, by TypeSafe AI, trending ~Sept 2026).
It's a small, fast classifier that sits in front of the real LLM call and
judges prompt difficulty (simple/medium/complex/reasoning), routing to an
appropriately-sized model instead of always hitting the expensive one --
shipped via LiteLLM's "Auto Router" or standalone on OpenRouter. It is
NOT a cache (doesn't store/replay answers, unlike idea #2 above) and
despite how it was initially described to Claude, it doesn't appear to
do anything with the user's emotional tone -- that was a loose
description of its difficulty-judging, not a real capability. Pricing is
usage-based per classification call, no flat subscription found.

Explicitly deferred: at this app's current scale (live testing, a
handful of users), the savings wouldn't be meaningful yet, and it adds a
dependency on another vendor (OpenRouter/TypeSafe) on top of
Gemini+GPT+Firebase+MongoDB Atlas already in play. Worth revisiting if
and when real usage volume makes LLM cost an actual line item -- at that
point, compare against just routing cheap/simple chat questions to
Gemini Flash-tier directly (no extra vendor) before reaching for this.

## 4. Speech-to-text question input + language/slang handling + ad-flow auto-resend (DONE)
Raised by Divakar as three add-ons once he was satisfied with the core app:
1. Microphone input on the chat screen (speech-to-text instead of typing).
2. Robust handling of any language and informal/slang phrasing in chat.
3. The ad-credit flow making the user tap Send a second time after
   unlocking a question.

**What shipped:**
- `speech_to_text` package added (mobile_app/pubspec.yaml); new
  `lib/services/speech_service.dart` wraps init/listen/stop. A mic
  IconButton on the chat screen (lib/screens/chat_screen.dart) toggles
  listening, live-fills the text field from partial transcripts, and
  leaves any text typed before the mic was tapped in place. Android
  manifest updated with RECORD_AUDIO permission and the
  android.speech.RecognitionService `<queries>` entry Android 11+ needs.
  Locale is left as the device default rather than hard-coded to English,
  so dictating in another language picks up that language's speech
  model automatically.
- Chat system prompt (`_build_context` in app/routers/chat.py) now
  explicitly tells the model to understand and reply in whatever
  language/dialect/slang/code-switched mix the question arrives in,
  including rougher speech-to-text transcripts, rather than defaulting
  to English or a formal register. (The LLM already mostly did this; this
  makes it explicit and consistent rather than relying on default
  behavior.)
- The "Add test question credit" button (the only ad-unlock UI that
  exists today -- the real rewarded-ad SDK integration is still a
  separate TODO) now automatically resends the question that triggered
  the quota block once credit is granted, instead of leaving it in the
  box for the user to tap Send again.

**Not done / caveats:**
- iOS: this Flutter project has no `ios/` platform folder generated at
  all yet (Android-only so far). The Dart-side mic code is
  platform-agnostic, but iOS needs `flutter create --platforms=ios .` run
  (needs a Mac with Xcode) before NSMicrophoneUsageDescription /
  NSSpeechRecognitionUsageDescription can even be added to an Info.plist
  that doesn't exist yet, and before it can be built/tested on iOS at
  all.
- No UI translation/localization was done (Divakar confirmed this is
  explicitly out of scope for now -- the ask was chat Q&A understanding
  any language, not translating app screens/buttons).
- The real rewarded-ad SDK (AdMob or similar) is still unimplemented --
  item 3 above only fixes the UX of the existing dev/QA test-credit
  bypass; the same "auto-resend after credit" behavior will need to be
  carried over once the real ad SDK replaces it.
