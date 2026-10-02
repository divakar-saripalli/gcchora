# Defect backlog

Informal defect/enhancement tracker for issues Divakar finds while
live-testing the app. No formal issue tracker in use, so this file is
it. Each entry: what was observed, its type, the root cause once found,
the fix, and status.

**Type** is one of:
- **Defect** -- something is broken or behaves wrong vs. what it should.
- **Enhancement** -- works, but should work better (UX, tooling, dev
  workflow).
- **Good-to-have** -- not blocking anything, nice if there's time.

**When adding a new observation:** append it at the bottom of the "Open"
section with today's date, your own words, and a best-guess Type (can be
corrected later once actually diagnosed).
**When one gets fixed:** move it down into "Done", fill in root cause +
fix + files touched, and mark `Status: DONE`.

---

## Open

_(none right now)_

---

## Done

### 17. LLM-unavailable errors shown as raw technical text ("LLM not available", provider stack traces)
**Type:** Enhancement
**Observed:** When the LLM call failed (misconfigured provider, outage,
quota exhausted), the app surfaced the raw backend error -- e.g. "No AI
provider is currently configured (...)" or "ApiException(503): ..." in a
SnackBar. Not user-friendly, breaks the astrology-app tone.
**Fix:** Backend (app/routers/chat.py) now returns a single friendly
message for any LLM failure in chat -- "Our expert astrologers are a
little busy right now. Please post your question again in a few minutes
and they'll get back to you." -- while logging the real technical reason
server-side via `logger.error` for diagnosis. Frontend
(mobile_app/lib/screens/chat_screen.dart) now catches `ApiException`
specifically and shows this as an assistant-style chat bubble instead of
a SnackBar, which reads naturally in context and matches how
QuotaExceededException already avoids showing raw errors. The user's
question stays visible (it's already persisted server-side by this
point, unlike the quota-exceeded case) so they don't need to retype it.
**Status:** DONE

### 20. "Run birth-time calibration" button buried at the bottom
**Type:** Enhancement
**Observed:** Same scrolling problem as #19 -- the calibration button sat
after the full recorded-events list, so it wasn't visible without
scrolling down once you had several events logged.
**Fix:** Moved the "Run birth-time calibration" button (and its "add N
more events" hint text) up to directly under the screen's intro text, so
it's visible immediately on opening the screen regardless of how many
events are recorded. mobile_app/lib/screens/life_events_screen.dart.
**Status:** DONE

### 21. Post-calibration follow-ups asked for "add more plausible events" instead of a simple did-this-happen rating
**Type:** Enhancement
**Observed:** After a successful (if unresolved) calibration, the app's
follow-up flow asked the user to log a new, precisely-dated life event --
awkward when they're not sure of an exact date, and framed as "add more
data" rather than "does this sound like you."
**Fix:** Reworked the whole follow-up loop:
- The LLM's suggested_followups now include a `time_frame_hint` -- its
  plain-language guess at when an event like this would plausibly fall,
  chosen from where the *candidate birth times actually disagree* (so
  the answer is genuinely disambiguating). app/routers/rectify.py's
  prompt, schema, and `_candidate_from_raw` all updated;
  `DisambiguationQuestion` gained the field (app/models.py,
  mobile_app/lib/models.dart).
- The follow-up dialog (mobile_app/lib/screens/rectify_screen.dart) now
  shows that hint ("This may have happened sometime between 2012 and
  2015.") and asks for a 1-5 rating (circular number picker, labeled "Not
  at all" to "Very much") instead of requiring a date. An exact date and
  a notes field are still there but both optional now.
- `RectifyAnswer` (app/models.py) replaced its required `exact_date` with
  `occurred_score` (1-5, required) plus optional `exact_date` and
  `time_frame_hint`. New `DisambiguationAnswer` Dart class replaces
  reusing `LifeEvent` for this call (api_client.dart, app_provider.dart).
- `_format_events` (app/routers/rectify.py) now phrases these events
  differently depending on the rating: a high score (3-5) reads as
  confirming evidence around the given time/hint; a low score (1-2) is
  phrased as explicit *disconfirming* evidence ("user says X did NOT
  happen around Y") rather than silently dropped, since ruling a
  candidate out is just as useful as confirming one. Importance weight is
  now scaled from the rating (`occurred_score / 5`) instead of a flat
  1.0. Verified with functional tests covering all three branches (plain
  event, confirmed, disconfirmed) plus schema/candidate parsing.
**Status:** DONE

### 22. No "typing" feedback while waiting for a chat reply; retries too brief for real Gemini demand spikes
**Type:** Enhancement
**Observed:** A real Gemini 503 ("This model is currently experiencing
high demand... status UNAVAILABLE") surfaced as a failure after only
~3 seconds of retrying, and there was nothing in the chat itself showing
a reply was on the way -- just a small spinner on the send button.
Confirmed via server logs this was a genuine transient outage on
Google's side, not a bug in the app.
**Fix:** Two changes:
- app/llm_gateway.py: `_post_with_retry` is more patient -- 3 retries at
  2s/4s/6s backoff (4 attempts, ~12s total) instead of 2 retries at
  1s/2s (~3s total), so a short-lived demand spike is more likely to
  clear before we give up. Verified with a functional test asserting
  attempt counts and exact backoff delays for a persistent failure, a
  recovery on the 3rd attempt, and that 429 still isn't retried.
- mobile_app/lib/screens/chat_screen.dart: added an animated "typing..."
  bubble (a Timer-driven dot-count, styled like a normal assistant
  message bubble) shown at the bottom of the chat for the entire time a
  reply is in flight -- covering the backend's retries too, so the extra
  patience above reads as normal chat latency instead of the UI looking
  stuck. It's cleanly replaced by the real reply, or by the existing
  friendly failure bubble (#17) if all retries are exhausted.
**Status:** DONE

### 18. Raw "pending_review" shown to the user under the birth time
**Type:** Defect
**Observed:** The home screen's profile card, right under the birth
date/time, showed the literal backend status string (e.g.
"pending_review", "locked") instead of readable text.
**Root cause:** `home_screen.dart` interpolated `profile.calibration.status`
directly into the subtitle text in two places -- the profile card and the
profile-switcher bottom sheet -- with no display formatting. This is a
separate issue from the earlier "stuck in pending_review forever" bug
(#16): that one was about the status never being resolvable; this one is
about the raw enum text being shown at all, which is expected wording
while a profile genuinely is pending review, just not user-friendly
wording.
**Fix:** Added `CalibrationInfo.displayLabel` in models.dart, mapping each
status to readable text ("not calibrated yet", "ready for your review",
"calibrated", "locked in"), and used it in both places in
home_screen.dart instead of the raw `status` field.
**Status:** DONE

### 19. "Add an event" required scrolling to the bottom of the list
**Type:** Enhancement
**Observed:** To add a new life event, had to scroll all the way past the
recorded-events list to reach the form at the bottom.
**Fix:** Replaced the always-visible inline form with a circular
FloatingActionButton ("+") on the Life events screen that opens the same
form (event type, date, notes) in a modal dialog -- reachable from
anywhere in the list without scrolling. An "Add your first event" button
is shown in the empty state too, for discoverability before any events
exist. mobile_app/lib/screens/life_events_screen.dart.
**Status:** DONE

### 1. Calibration follow-ups only offered as rigid predefined categories
**Type:** Enhancement
**Observed:** Wanted free-text/voice input for calibration follow-up
answers instead of picking from a fixed category list, for more precise
detail than a category alone captures.
**Fix:** Added a `notes` field end-to-end -- `RectifyAnswer.notes` in
app/models.py, persisted on the life event in app/routers/rectify.py,
and a multi-line notes `TextField` in the mobile follow-up dialog
(rectify_screen.dart).
**Status:** DONE

### 2. Clicking "Run birth-time calibration" showed no visible change on failure
**Type:** Defect
**Observed:** Tapped calibrate, nothing appeared to happen.
**Root cause:** The button's `onPressed` had no `else` branch -- a failed
call just silently did nothing visible.
**Fix:** Added a `SnackBar` showing `data.errorMessage` on failure, plus
a `context.mounted` guard, in life_events_screen.dart.
**Status:** DONE

### 3. Chat answered with "[gpt not configured]" instead of using Gemini
**Type:** Defect
**Observed:** Configured Gemini, but a chat question came back with a
GPT placeholder string.
**Root cause:** `GPTProvider.generate` returned a placeholder string
instead of raising when unconfigured, so `generate_answer`'s failover
silently "succeeded" with GPT's fake placeholder after a real Gemini
failure.
**Fix:** Both providers now raise `ProviderNotConfigured` from
`generate()` (matching `generate_structured()`'s existing behavior);
chat.py catches it explicitly and returns a real 503 instead. Also made
chat prefer whichever provider actually determined the locked chart
(`profile.calibration.source_provider`) rather than always using the
global default. app/llm_gateway.py, app/routers/chat.py.
**Status:** DONE

### 4. LLM chat responses too long / hard to scroll
**Type:** Enhancement
**Observed:** Responses filled most of the screen.
**Fix:** Added a hard conciseness instruction to the chat system context
("3 to 5 sentences by default, no long lists or headers... only go
longer if the user explicitly asks"). app/routers/chat.py `_build_context`.
**Status:** DONE

### 5. Ad-reward quota unlock not wired up yet, blocking testing
**Type:** Enhancement
**Observed:** "Watch an ad" button did nothing real; wanted a way to
test the quota-unlock flow without a real ad SDK integration.
**Fix:** Added a dev-only `POST /ads/test-credit` endpoint gated behind
`ALLOW_TEST_AD_BYPASS` (default false, set true in `.env` for testing);
real `/reward-callback` signature verification left untouched. Wired the
button in chat_screen.dart to call it. app/routers/ads.py, app/config.py,
mobile_app/lib/services/api_client.dart, mobile_app/lib/screens/chat_screen.dart.
**Status:** DONE

### 6. Gemini 429 "quota exceeded" -> 502 from the app
**Type:** Defect (error surfacing; the underlying quota cap itself is
expected free-tier behavior, not a defect)
**Observed:** Server log showed Gemini 429 (20 requests/day free-tier
cap), surfaced to the app as a generic 502.
**Root cause:** Not a bug in the cap itself -- Google's free-tier daily
cap, separate from the unmetered usage seen when using the Gemini
consumer app directly. The defect was that the app gave no indication
of *why* it failed.
**Fix:** Improved error surfacing (`_summarize_provider_error`) so a
quota exhaustion shows a clear reason instead of a bare 502; later
guided enabling pay-as-you-go billing on the API key's GCP project to
lift the cap. app/llm_gateway.py.
**Status:** DONE

### 7. Gemini 503 "high demand" -> LLM not available
**Type:** Defect
**Observed:** Transient Gemini overload surfaced as a hard failure.
**Fix:** Added `_post_with_retry` with 1s/2s backoff for retryable
transient errors (500/502/503/504), explicitly NOT retrying 429 (whose
suggested retry delay can be a full day). Wired into all 4 provider->API
call sites. Verified with a functional test using fake HTTP responses.
app/llm_gateway.py.
**Status:** DONE

### 8. No way to see the exact prompt sent to the LLM
**Type:** Enhancement
**Observed:** Wanted to test prompts directly against Gemini to compare
behavior.
**Fix:** Added `_log_prompt()`, gated by `LOG_LLM_PROMPTS` (default on)
writing to `LLM_PROMPT_LOG_PATH` (default `logs/llm_prompts.log`).
Logs outgoing prompts/schemas for all 4 provider methods, and -- added
after a later observation (#9) -- the raw response text too, so both
sides of the exchange are captured. app/llm_gateway.py, app/config.py.
**Status:** DONE

### 9. App's structured response looked different from manually testing in the Gemini app
**Type:** Good-to-have (turned out to be a misread, not a real defect --
logged here because it drove a real tooling improvement)
**Observed:** Compared what looked like "the app's response" against a
real Gemini app reply and they didn't match.
**Root cause:** What was pasted as "the app response" was actually the
JSON *schema* (`_full_schema()`'s type definitions), not a real response
-- no actual values, just field types. Happened twice before it was
caught.
**Fix:** No code bug here; motivated adding response-body logging (see
#8) so the real response could be captured and compared directly instead
of relying on memory/copy-paste.
**Status:** DONE

### 10. LLM "reasoning" field too verbose despite prompt saying "briefly"
**Type:** Defect
**Observed:** Reasoning text was long even though the prompt asked for
brevity.
**Root cause:** "Briefly explain" was a soft suggestion the model
ignored.
**Fix:** Hard cap -- "explain your reasoning in at most 2-3 sentences --
keep it tight, not a full walkthrough" -- in both `_build_prompt` and
`_build_recheck_prompt`. app/routers/rectify.py.
**Status:** DONE

### 11. Auto-generated follow-up question text read awkwardly
**Type:** Defect
**Observed:** e.g. "Can you add a Exact Dates Of Vehicle Accidents Or
Purchases event?" -- clearly machine-title-cased from a category slug.
**Root cause:** Client-side title-casing of arbitrary LLM-invented
category slugs.
**Fix:** Schema changed so the model authors its own natural
`question_text` directly alongside the category
(`suggested_followups: [{category, question_text}]`), with a fallback
if `question_text` is missing. Verified with a functional parsing test
including the fallback path. app/routers/rectify.py.
**Status:** DONE

### 12. Confidence showing as "0%" / fields showing "-" despite a healthy real result
**Type:** Defect
**Observed:** "Why am I still seeing response as 0% CONFIDENCE in the
app?"
**Root cause:** `run_rectification`'s `pending_review` return path never
set the top-level `chosen_provider` / `chosen_ayanamsa` /
`rectified_birth_time_24h` / `confidence_score` fields (only
per-candidate ones), because that path was designed assuming 2+
candidates. With only one LLM provider configured, `candidates` is
always length <=1, so these top-level fields -- which the app's
non-scoring UI branch reads -- stayed null.
**Fix:** `pending_review` now always populates those top-level fields
from the best candidate (`top`), even while status stays
`pending_review`. Verified with a Pydantic-level functional test.
app/routers/rectify.py.
**Status:** DONE

### 13. Gemini free-tier limit exhausted, needed a way to pay
**Type:** Good-to-have
**Observed:** "exhausted the limit, guide me to pay as you go."
**Fix:** Not a code change -- walked through enabling billing on the
Gemini API key's underlying Google Cloud project.
**Status:** DONE (guidance given; user has not confirmed whether billing
was actually enabled -- worth checking if 429s recur)

### 14. Follow-up dialog: "BOTTOM OVERFLOWED BY N PIXELS"
**Type:** Defect
**Observed:** Screenshot of a Flutter overflow warning banner in the
add-followup dialog. First fix attempt (wrapping the dialog's `content`
in `SingleChildScrollView`) didn't resolve it even after a full
`flutter run` restart.
**Root cause:** Found only once the real Flutter exception stack trace
was pasted -- the overflowing `RenderFlex` was `AlertDialog`'s own
internal title+content+actions `Column`, not the one that got wrapped.
**Fix:** Removed the manual `SingleChildScrollView`, set
`AlertDialog(scrollable: true, ...)` instead -- Flutter's built-in
mechanism for this exact case. mobile_app/lib/screens/rectify_screen.dart.
**Status:** DONE

### 15. Follow-up dialog "Add & recalibrate" button stayed disabled
**Type:** Defect
**Observed:** Could not submit the follow-up dialog -- button greyed
out.
**Root cause:** Submit was gated on `pickedDate != null`, but
`pickedDate` started as `null` ("Tap to choose") with no default, unlike
the main life-events form elsewhere which defaults to today.
**Fix:** Defaulted `pickedDate` to `DateTime.now()`, removed the
null-gated disable condition -- button is now always enabled.
mobile_app/lib/screens/rectify_screen.dart.
**Status:** DONE

### 16. Stuck on "pending_review" forever, no matter how many events added
**Type:** Defect
**Observed:** Kept getting "adding more life events can provide more
precise answer" regardless of how much data was added.
**Root cause:** `needsUserScoring => status == 'pending_review' &&
candidates.length > 1` in mobile_app/lib/models.dart. With only Gemini
configured (no `OPENAI_API_KEY`), `candidates.length` can never exceed
1, so the rate/confirm screen -- the only way out of `pending_review`
short of a 95%+ auto-lock, which an honest rectification LLM will rarely
claim -- was structurally unreachable. Backend's `score_candidates`
already handled a single candidate correctly; the gate was purely
client-side.
**Fix:** Changed the getter to `candidates.isNotEmpty`; updated
rectify_screen.dart's copy to read naturally for a single candidate
("Rate this reading" / "Accept this reading" instead of "Compare and
rate" / "Confirm my pick"). mobile_app/lib/models.dart,
mobile_app/lib/screens/rectify_screen.dart.
**Status:** DONE
