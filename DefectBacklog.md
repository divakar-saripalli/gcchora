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

### 23. "Add an event" dialog: right-side overflow (yellow/black stripe)
**Type:** Defect
**Observed:** Opening the "Add an event" dialog (the FAB added in #19)
showed Flutter's "RIGHT OVERFLOWED BY 45 PIXELS" indicator down the
dialog's right edge, with "School graduation" selected in the event-type
dropdown.
**Root cause:** `DropdownButtonFormField` in life_events_screen.dart's
`_openAddEventDialog` had no `isExpanded: true`. Without it, Flutter
sizes the dropdown's selected-value row to its own intrinsic (unbounded)
width rather than the width actually available inside the dialog, so any
category label wider than the dialog -- several of kLifeEventCategories'
labels are, e.g. "Postgraduate / master's degree", "Career disruption /
job loss" -- overflows past the edge. Classic DropdownButton gotcha,
unrelated to which item happened to be selected when it was noticed.
**Fix:** Added `isExpanded: true` to the DropdownButtonFormField, plus
`overflow: TextOverflow.ellipsis` on each DropdownMenuItem's Text as a
second line of defense. mobile_app/lib/screens/life_events_screen.dart.
**Status:** DONE

### 24. Birth-time timezone offset trusted from manual entry, no DST handling
**Type:** Defect
**Observed:** Raised as a question ("is the local time correctly
converted using the right UTC offset?") that turned up a real gap --
`timezone_offset_hours` was a plain manually-typed field in
profile_setup_screen.dart (`_tzController`, defaulting to '5.5'),
completely disconnected from the auto-geocoded latitude/longitude.
**Root cause:** No timezone lookup existed at all. The field relied on
the person typing the correct offset from memory, with no historical
daylight-saving handling -- e.g. a July 1990 New York birth needs -4
(EDT), not the "standard" -5 (EST) most people would type. A wrong
offset silently shifts the derived UTC birth instant, which shifts the
Ascendant and house cusps -- exactly the kind of error rectification is
meant to correct for, except upstream and invisible.
**Fix:** Added app/timezone_lookup.py (`compute_timezone_offset_hours`),
using `timezonefinder` (lat/lng -> IANA tz name) + stdlib `zoneinfo`
(full historical DST-aware offset for a specific date), both fully
offline/free. Wired into app/routers/profiles.py so both create_profile
and update_profile always recompute and override the offset server-side
from the final lat/lng + birth_date + birth_time_24h, ignoring whatever
a client sends; update_profile recomputes against the *merged*
post-update values so changing just the birth date, for example, still
correctly re-derives DST status. Verified against real historical dates
(NYC July 1990 -> -4.0, NYC Jan 1990 -> -5.0, India -> always +5.5) and
via a fake-DB end-to-end test of create+update. Added
scripts/fix_timezone_offsets.py to backfill existing profiles (same
pattern as scripts/seed_rules.py) -- needs to be run once against the
real database (can't be run from this sandbox, no network route to
Atlas): `python -m scripts.fix_timezone_offsets`. Also removed the now-
pointless manual timezone field from profile_setup_screen.dart's UI,
replaced with a note that it's computed automatically.
app/timezone_lookup.py, app/routers/profiles.py, requirements.txt,
scripts/fix_timezone_offsets.py, mobile_app/lib/screens/profile_setup_screen.dart.
**Status:** DONE (backend + mobile UI); backfill script needs to be run
by Divakar against the real database.

### 25. Rectify follow-up answers vanished from the life events list (GET /events 500'd silently)
**Type:** Bug
**Observed:** Divakar noticed that answering the follow-up questions
rectification asks during calibration didn't show up anywhere in the
"Life events" screen, and asked whether they're stored at all for future
calibration.
**Root cause:** They ARE stored -- `add_followup_and_rectify`
(app/routers/rectify.py) inserts them into `life_events_collection` with
`source: "suggested_followup_answer"`, and they're fed back into every
later `/rectify` call exactly like a manually logged event. The bug was
on the read side: `RectifyAnswer.exact_date` is deliberately optional
(the follow-up flow only asks the user to rate 1-5 how much something
like this resonates around a given `time_frame_hint`, not to supply an
exact date), so these events are stored with `exact_date: None`. But
`LifeEventOut` inherited `exact_date: str` (required) from `LifeEventIn`,
so FastAPI's response_model validation raised the moment any single
event like that existed for a profile -- and because it's a `list[...]`
response model, that failed the ENTIRE `GET /profiles/{id}/events` call,
not just that one item. The mobile app's `loadEvents()` catches the
exception into `errorMessage` but never surfaces it on this screen's
auto-load, so the user just saw life events silently fail to load/update
with no visible error. The same bug existed in the `PATCH
.../events/{event_id}` handler (`existing["exact_date"]`), which would
have crashed if anyone tapped to edit one of these events.
**Fix:** `LifeEventOut` now overrides `exact_date` as optional and adds
`time_frame_hint`/`source` passthrough fields; both the GET list and
PATCH handlers in app/routers/profiles.py now read `exact_date` with
`.get()` instead of indexing. Mobile `LifeEvent` model
(mobile_app/lib/models.dart) made `exactDate` nullable and added
`timeFrameHint`/`source` + an `isFromFollowUpAnswer` helper. The life
events list screen (life_events_screen.dart) now shows "Date not given"
+ "Around: <time_frame_hint>" for these entries instead of crashing on
`DateTime.tryParse(null)`, with a small icon marking them as coming from
a calibration follow-up answer; tapping one still opens the normal edit
dialog, letting the user give it a real date if they want to.
**Files:** app/models.py, app/routers/profiles.py,
mobile_app/lib/models.dart, mobile_app/lib/screens/life_events_screen.dart.
**Status:** DONE -- verified by reconstructing the exact
pydantic-validation failure before the fix and confirming it passes
after; not yet exercised against the real app/DB by Divakar.

### 26. Calibration follow-up questions sometimes asked about future events
**Type:** Bug
**Observed:** Divakar noticed some of the follow-up questions rectification
poses during calibration had a `time_frame_hint` referring to a date range
that hadn't happened yet (i.e. in the future relative to today), when the
whole point is to ask about something the user could actually recall and
rate.
**Root cause:** `_build_prompt` (app/routers/rectify.py) never told the
model what today's actual real-world date is, so when reasoning about
which dasha/bhukti period to ask about next, it had no anchor to know
whether a given period was already in the past. For a profile where the
candidate birth times imply a dasha/bhukti period landing after today, the
model would still suggest it.
**Fix:** Two layers. (1) `_build_prompt` now states today's real date
up front and explicitly instructs the model to only ask about periods
that could already have happened by then, skipping ahead to an
already-past period instead if the "interesting" (differentiating)
dasha/bhukti windows are all still ahead. (2) A defensive backstop in
`_candidate_from_raw`: `_is_future_only_hint` scans a `time_frame_hint`
for 4-digit years and drops the suggestion (with a logged warning,
`_keep_followup_or_log`) if every year mentioned is still ahead of the
current year, in case a provider ignores the instruction anyway. A hint
straddling today, or with no year in it at all, is left alone since it
can't be judged unsafe.
**Files:** app/routers/rectify.py.
**Status:** DONE -- verified with a simulated provider response mixing a
past-dated, a future-dated, and a hint-less follow-up: the future one is
dropped and logged, the other two pass through. Not yet exercised against
a real LLM call by Divakar.

### 27. Speech input showed "listening" but never produced a transcript
**Type:** Bug
**Observed:** Divakar tapped the mic (added in FeatureBacklog #4) and
spoke, the listening indicator showed, but nothing ever appeared in the
question box.
**Root cause:** Three compounding issues in the first cut of
lib/services/speech_service.dart: (1) `listen()` was called with
`cancelOnError: true`, which makes the native side abandon the ENTIRE
listening session on any error event -- including harmless, common
transient ones (e.g. a brief "no speech detected yet" blip right as
someone starts talking) -- often before real speech was even captured;
(2) `listenMode` was left at the plugin default, `ListenMode.confirmation`
(tuned for short yes/no-style utterances), instead of
`ListenMode.dictation` for a full spoken sentence; (3) with no explicit
`pauseFor`, Android enforces its own un-overridable 1-3 second silence
cutoff, easily shorter than a natural pause mid-question.
**Fix:** `cancelOnError: false` (the plugin's own default -- and per its
docs, the app is then responsible for calling `.cancel()` on a genuinely
*permanent* error, which `onError` now does by checking
`error.permanent` instead of treating every error as fatal);
`listenMode: ListenMode.dictation`; explicit `pauseFor: 8s` and
`listenFor: 60s` so a short mid-sentence pause doesn't end the session.
Also added BLUETOOTH/BLUETOOTH_ADMIN/BLUETOOTH_CONNECT permissions to
AndroidManifest.xml per the package's README (for a connected Bluetooth
headset mic), and a SnackBar on the chat screen when a listening session
ends with literally no transcript captured, so this fails loudly instead
of silently next time.
**Files:** mobile_app/lib/services/speech_service.dart,
mobile_app/lib/screens/chat_screen.dart,
mobile_app/android/app/src/main/AndroidManifest.xml.
**Status:** DONE, pending Divakar re-testing on a real device --
unlike the backend fixes this session, speech recognition can't be
exercised in any sandbox available here at all.

### 28. Chat input box appeared not to clear after sending a dictated question
**Type:** Bug
**Observed:** After speech recognition started working (defect #27's fix),
Divakar noticed that once a message was sent, the input box didn't
actually clear.
**Root cause:** `_send()` already called `_controller.clear()`, and that
line was never the problem -- the box WAS clearing, then getting
silently refilled. If the mic was still listening when Send was tapped,
speech_to_text delivers one last "final" result shortly after
stopListening-equivalent calls happen, and that trailing onResult (see
_toggleListening's onResult handler) was writing the old transcript
straight back into `_controller.text` moments after clear() ran.
**Fix:** `_send()` now force-stops any active listening session (via the
same `_forceStopListening()` added for defect #27, which calls
`cancel()` rather than `stop()`) as its very first action, before
reading or clearing the text field. `cancel()` discards the session
without delivering that trailing final result, so there's nothing left
to race the clear.
**Files:** mobile_app/lib/screens/chat_screen.dart.
**Status:** DONE, logically verified (traced the exact callback
ordering); pending Divakar's confirmation on a real test.

### 29. Chat gave timing advice using a window that had already passed
**Type:** Bug
**Observed:** Divakar asked about career timing and got told the best
window to change was "late 2024 to May 2025" -- already in the past by
the time he asked.
**Root cause:** Same root cause pattern as defect #26 (rectify follow-up
questions suggesting future events), but on the chat side this time:
`_build_context` in app/routers/chat.py never told the model what
today's actual date is, so it had no anchor for "already happened" vs
"still upcoming" when reasoning about transit/dasha timing windows.
**Fix:** `_build_context` now opens with today's real date and an
explicit instruction: reason about timing relative to that actual date,
and if a window it would otherwise mention has already ended, say so
plainly and give the next upcoming one instead of presenting stale
advice as current. Unlike #26 (which had a structured field --
time_frame_hint -- a regex backstop could filter), chat replies are free
-form prose, so there's no equivalent programmatic backstop here; this
relies on the prompt instruction alone.
**Files:** app/routers/chat.py.
**Status:** DONE, verified the instruction is present in the generated
prompt; not yet exercised against a real LLM reply by Divakar.

### 30. Android build toolchain versions trailing Flutter's soon-to-be-dropped minimums
**Type:** Tech debt (not a bug -- build currently succeeds, these are warnings)
**Observed:** After bumping AGP 9.1.0 -> 9.1.1 to fix the earlier hard
build failure (defect from the previous turn), `flutter run` now
succeeds but prints three "will soon be dropped" warnings:
- Gradle wrapper is 9.3.1; Flutter wants at least 9.5.0 soon.
- AGP is 9.1.1; Flutter wants at least 9.3.1 soon.
- Kotlin (KGP) is 2.4.10; Flutter wants at least 2.4.20 soon.

Also a separate, unrelated warning: `flutter_tts` applies its own Kotlin
Gradle Plugin directly, which Flutter says will eventually stop being
supported at all (apps are meant to move to Flutter's "Built-in Kotlin"
instead of individual plugins applying KGP themselves) -- this one isn't
something fixable from this app's side at all; it needs a
`flutter_tts` release that supports Built-in Kotlin, or a report filed
against the plugin if no such release exists yet.
**Why not fixed now:** these are forward-looking warnings, not current
failures, and bumping Gradle to 9.5.0+ cascades into needing a newer AGP
(9.5.x wants Gradle 9.6.0 per Google's own compatibility table) and
likely a newer Kotlin too -- a bigger, riskier chain than this sandbox
can verify (no real Android build toolchain here to test against).
Divakar asked to park this rather than fix proactively.
**When to revisit:** either when one of these warnings turns into an
actual build failure (same pattern as the AGP 9.1.0 one), or
deliberately as its own task with room to test the whole upgrade chain
properly rather than bundling it into an unrelated fix.
**Files likely involved:** android/settings.gradle.kts (AGP + Kotlin
plugin versions), android/gradle/wrapper/gradle-wrapper.properties
(Gradle version), and tracking flutter_tts's own releases for Built-in
Kotlin support.
**Status:** OPEN -- parked per Divakar's request, not blocking.


### 32. Answering one follow-up question silently replaced the others ("felt like I missed out")
**Type:** Bug
**Observed:** During birth-time calibration, the model suggests 2-3
follow-up questions at once. Answering just one of them immediately
re-ran rectification, which generates a BRAND NEW batch of suggested
questions -- so the other 1-2 originally-shown questions vanished,
replaced by a different set, before Divakar got a chance to answer them.
**Root cause:** `add_followup_and_rectify` (app/routers/rectify.py)
unconditionally called `run_rectification` immediately after storing
each answer. `run_rectification`'s non-locked path always generates a
fresh `suggested_followups` list from scratch, so the previous batch's
unanswered questions were discarded the moment any one of them was
answered -- not an intentional "skip," just the whole batch resetting
underneath the user.
**Fix:** `run_rectification`'s pending_review branch now also persists
`calibration.answered_followup_categories: []` (reset) alongside the
existing `calibration.pending_candidates`. `add_followup_and_rectify`
reconstructs the current batch from `pending_candidates` (no LLM call),
marks the just-answered category done, and:
- If questions from that batch are still unanswered, returns the SAME
  comparison result as before with `suggested_followups` trimmed to just
  what's left -- no recalibration call, so the remaining questions stay
  visible and answerable one at a time instead of disappearing.
- Once the last question in the batch is answered, falls through to a
  real `run_rectification` call, which naturally folds in every answer
  from the batch at once (also addresses FeatureBacklog #1's "batch
  before recalibrating" idea, for this flow specifically).
Mobile (rectify_screen.dart) updated to explain this ("recalibration
runs automatically once all N are answered") and added a "Recalibrate
now (skip the rest)" button (wired to the existing `runRectification()`
call already used elsewhere) for anyone who'd rather not answer every
question in the batch.

Follow-on fix (same defect, caught by Divakar asking "what if the user
taps Done before finishing the batch"): the screen's "Done" button
previously just closed the screen unconditionally, which meant a
partially-answered batch's progress -- answers already saved as events,
but not yet reflected in the shown confidence/rectified-time since no
rerun happened for them -- would sit stale until the user happened to
come back and either finish the batch or tap "Recalibrate now". Added a
`_hasUnrecalibratedAnswers` flag (set whenever an answer is recorded
without completing the batch, cleared by anything that DOES trigger a
real rerun) and made "Done" check it: if true, it runs recalibration
with whatever's been answered so far BEFORE closing the screen, instead
of leaving it stranded.
**Files:** app/routers/rectify.py, mobile_app/lib/screens/rectify_screen.dart.
**Status:** DONE -- verified with a fake-DB script simulating a
3-question batch: answering questions 1 and 2 triggered zero
rectification calls and correctly trimmed the remaining list; answering
question 3 (completing the batch) correctly fell through to a real
rectification call. The "Done" button fix is a straightforward state
check, consistent with the same pattern used elsewhere in this file, but
not separately testable from this sandbox (no Flutter toolchain here).
Not yet exercised against the real app/DB by Divakar.


### 33. Life events shown/reasoned about in insertion order, not actual chronological order
**Type:** Bug
**Observed:** Divakar asked how events are stored/ordered, specifically
flagging: if a user logs a child's birth on 2020-10-21 first, then later
adds an earlier-born sibling's birth (2017-08-23), the later-born child
should NOT be treated as the elder one just because it was logged first.
**Root cause:** Every place that reads a profile's life events sorted by
`createdAt` (when the event was LOGGED) or didn't sort at all (MongoDB's
natural/insertion order) -- never by the event's own actual date
(`exact_date`, or the year(s) mentioned in `time_frame_hint` for events
without one). This affected: the Life Events list screen
(app/routers/profiles.py `list_life_events`, sorted by `createdAt`), the
chat context sent to the LLM (app/routers/chat.py), and the
rectification prompt (app/routers/rectify.py `run_rectification`, feeding
both the full multi-provider prompt and the locked-profile recheck).
Each event line does carry its own explicit date text, so a careful LLM
reading closely could often still get sequencing right regardless of
list order -- but relying on that is fragile, and the human-facing
events list had no such excuse at all: a user glancing at it would see
events in logging order, not actual life order.
**Fix:** New shared helper, app/event_ordering.py
(`sort_events_chronologically`), used consistently everywhere events are
displayed or fed into a prompt. Sorts by: the event's `exact_date` when
present; otherwise the midpoint year of whatever year(s) appear in
`time_frame_hint` (e.g. "sometime between 2012 and 2015" -> ~2013), as a
rough ordering-only approximation -- never stored, consistent with the
earlier deliberate choice not to fabricate and persist a precise date for
this same data; otherwise sorted last (stable by `createdAt`) when no
date information exists at all. Wired into
`list_life_events`/`list_life_events`'s GET endpoint (profiles.py),
`send_message`'s context build (chat.py), and `run_rectification`'s
event fetch (rectify.py) -- the latter covers both the initial
multi-provider rectification prompt and the locked-profile recheck,
since both consume the same sorted list.
**Files:** app/event_ordering.py (new), app/routers/profiles.py,
app/routers/chat.py, app/routers/rectify.py.
**Status:** DONE -- verified with a direct unit test of the sort helper
covering exactly the reported scenario (2020 child logged before, 2017
child logged after -- confirmed the 2017 one sorts first), plus a
time_frame_hint-only event and a no-date-at-all event sorting correctly
relative to dated ones. Not yet exercised against the real app/DB by
Divakar.

### 34. No encryption at rest for sensitive stored data; debug prompt logging on by default
**Type:** Security hardening (not a reported malfunction -- raised by
Divakar asking how user details/life events are stored and whether
they're obfuscated/encrypted; answer at the time was: no application-
level encryption anywhere, and `LOG_LLM_PROMPTS` defaulted to on,
writing every exact prompt -- including full birth details and life
event text -- to a plaintext log file on disk).
**Observed:** Confirmed via direct grep across the whole app directory:
no encryption of any field in MongoDB, and `app/config.py`'s
`log_llm_prompts` setting defaulted to `"true"`.
**Fix, two parts:**
1. `LOG_LLM_PROMPTS` now defaults to `false` (app/config.py) -- it's a
   debug aid for copy-pasting a prompt into a provider's own UI while
   troubleshooting, not something that should be on by default, since
   it's a full unencrypted copy of birth details/life events/chat
   content written to plain disk outside the database entirely. Still
   available by setting the env var, for active debugging only.
2. New transparent field-level encryption layer: app/field_encryption.py
   (Fernet symmetric encryption, key from `FIELD_ENCRYPTION_KEY`) plus an
   `EncryptedFieldsCollection` wrapper applied at the collection-accessor
   boundary in app/db.py, so none of the dozens of existing call sites
   that read e.g. `profile["birth_time_24h"]` directly needed to change.
   Encrypted at rest: a birth profile's own and spouse's exact birth time
   (`birth_time_24h`/`spouse_birth_time_24h`) and geographic coordinates
   (`geographic_coordinates`/`spouse_geographic_coordinates`), a life
   event's free-text `notes` (can contain health/personal detail), and a
   chat message's `content`.
   **Explicitly NOT encrypted** (documented so this isn't overstated):
   category slugs, dates/time_frame_hint, confidence/rating scores,
   question_text, user email/name, and anything already sent to an LLM
   provider as part of a prompt -- encrypting data at rest in MongoDB
   does nothing about what's shared with Gemini/OpenAI by design, or
   about DB credentials, backups, server memory, or stolen session
   tokens (see the fuller threat-model list given to Divakar directly in
   conversation). A missing `FIELD_ENCRYPTION_KEY` raises an error at
   first use rather than silently storing plaintext.
   A legacy (pre-encryption) plaintext value decrypts back as-is on read
   instead of crashing, so existing live-testing data in MongoDB Atlas
   keeps working without a migration step -- it just won't be encrypted
   until it's next written.
**Files:** app/config.py, app/field_encryption.py (new), app/db.py,
requirements.txt, .env.example.
**Status:** DONE for the code. **Requires action before this actually
protects anything:** a `FIELD_ENCRYPTION_KEY` must be generated and set
in the real `.env` (see the comment added there) -- without it, every
read/write of these fields raises `FieldEncryptionNotConfigured` rather
than falling back to plaintext, so the backend will fail loudly on
startup use until the key is set. Verified in this sandbox with a
fake-Motor-collection test using REAL Fernet encryption: insert_one
stores ciphertext for tracked fields only, find_one/find().sort().to_list()
transparently decrypt, update_one's `$set` encrypts only tracked field
names (other `$set` keys like `calibration` pass through untouched),
delete_many passes through unmodified, and a legacy plaintext value
decrypts gracefully via the fallback path. NOT yet run against the real
MongoDB Atlas database or a real app restart -- existing data in Atlas is
currently still plaintext until a key is set and those documents are
next written.

### 35. Application logs could leak secrets and sensitive personal data
**Type:** Security hardening (raised by Divakar: "the logs being logged
should never have any sensitive data"). Separate from #34's data-at-rest
encryption -- that only covers what's written to MongoDB. These are
leaks into the application's own log output, which field encryption
does nothing about.
**Observed, via direct audit of every `logger.*` call site in `app/`:**
1. `app/timezone_lookup.py` logged the actual latitude/longitude and
   exact birth date/time, in plaintext, whenever timezone lookup failed
   or a timezone couldn't be resolved -- the exact same fields #34 just
   encrypted at rest, written straight to the log file instead.
2. `app/llm_gateway.py`'s `_raise_for_status_with_body` built its error
   message from the full request URL. Gemini's REST API takes its API
   key as a `?key=...` query parameter, so any logged exception for a
   failed Gemini call contained the live API key in plaintext.
3. That same exception's message also includes up to 2000 characters of
   the provider's raw response body. This was logged verbatim in two
   places -- `generate_structured_from_all`'s `logger.warning` and
   `chat.py`'s generic `except Exception` handler -- with no guarantee a
   provider's own error text never echoes back part of the request
   (e.g. a safety-filter or schema-validation message quoting the
   offending input).
4. `app/routers/rectify.py`'s `_keep_followup_or_log` logged the raw
   `time_frame_hint` text of a dropped follow-up question, which
   paraphrases the user's own life events.
**Fix:**
1. timezone_lookup.py: both log lines (missing timezone, and the
   `except Exception` catch-all) no longer include coordinates or
   birth date/time at all -- just that the lookup failed, which is
   enough to notice and debug the *code path*, never the person's data.
2. llm_gateway.py: new `_sanitize_url_for_log()` strips the query string
   before a provider URL goes into any exception message; applied in
   `_raise_for_status_with_body`, which is the single place these
   messages are built, so this closes the leak for every call site that
   logs an LLM exception, not just the one most recently hit.
3. `generate_structured_from_all` (llm_gateway.py) and the chat answer
   failure handler (chat.py) no longer log the raw exception for a
   provider failure. Both now log only `_summarize_provider_error()`'s
   short, already-sanitized reason (e.g. "rate-limited (429)", "HTTP 400
   error") -- the same text already considered safe enough to put in a
   user-facing HTTPException detail, now reused for the log line too
   instead of a second, unsanitized copy of the same failure.
4. rectify.py: the dropped-followup log line now reports only
   provider/category, not the hint text.
**Files:** app/timezone_lookup.py, app/llm_gateway.py,
app/routers/chat.py, app/routers/rectify.py.
**Status:** DONE. Verified directly: (a) a crafted HTTPStatusError whose
URL contains a fake API key and whose body echoes a birth
time/coordinate string confirms neither the key nor the echoed text
survives into `_raise_for_status_with_body`'s message or
`_summarize_provider_error`'s output; (b) re-grepped every `logger.*`
call site in `app/` after the fix to confirm no remaining call site logs
a raw exception, raw coordinates, raw birth date/time, or raw
model-generated hint/event text. LOG_LLM_PROMPTS (see #34) remains the
one deliberate, clearly-documented, off-by-default exception -- it's a
debug aid that explicitly trades a full plaintext prompt dump for
being able to reproduce a prompt issue against the provider's own UI,
never on unless someone turns it on to debug something.

### 36. "mobile_app isn't responding" (ANR) while a chat reply was displaying
**Type:** Bug, suspected cause (not yet confirmed by Divakar -- no adb/
logcat access from this sandbox to pull an actual ANR trace).
**Observed:** Android's "isn't responding" dialog appeared while a chat
answer was rendering, with the reply already partially visible behind
the dialog.
**Suspected root cause:** The voice-reply (TTS) feature added earlier
this session. `TtsService._applyPreferredVoice()` (tts_service.dart)
calls the native TTS engine's `getVoices()` the first time anything is
ever spoken, to find a specific installed en-IN voice. This is a known
weak point in flutter_tts on some devices/engines -- it can stall for a
long time, and because it's a platform-channel call, a stuck call there
can trip Android's main-thread responsiveness watchdog (ANR) even
though nothing in the Dart code itself loops or blocks.
**Fix:** Every native TTS setup call (`setLanguage`, `getVoices`,
`setVoice`, `stop`) is now wrapped in a 3-second `.timeout(...)` so a
stuck engine gets abandoned well before Android's own ~5s ANR threshold,
instead of being awaited indefinitely. Deliberately NOT applied to the
actual `speak(text)` call itself -- with `awaitSpeakCompletion(true)`,
that Future is SUPPOSED to take as long as the reply takes to read
aloud (tens of seconds for a full answer), so timing that out would cut
every normal reply off partway through, not just a genuinely stuck one.
**Files:** mobile_app/lib/services/tts_service.dart.
**Status:** NOT YET CONFIRMED as the actual cause -- this sandbox has no
way to pull an ANR trace from the device to verify. Asked Divakar to
test empirically: mute voice replies (speaker icon, top-right of chat
screen) and see if the freeze still happens on a typed (non-spoken)
question. If it doesn't reproduce with voice replies off, that confirms
TTS as the cause and this fix as the right area; if it still happens,
the real cause is elsewhere and this fix, while a reasonable robustness
improvement regardless, didn't address it.

### 37. No follow-up questions (or other-ayanamsa comparison) once a profile locked
**Type:** Bug / product decision (Divakar: "not seeing follow up
questions during calibration" + "also not seeing results of other
ayanamsas" -- both reported together).
**Observed:** Once a profile is locked, the Rectify screen never shows
follow-up question cards or the per-ayanamsa comparison dropdown again.
**Root cause:** Not a regression -- by design. Once locked,
`run_rectification` takes `_run_locked_recheck` (app/routers/rectify.py)
instead of the full multi-provider path, specifically to keep routine
rechecks (e.g. triggered by adding a new life event) cheap: single
provider, single ayanamsa. That path never asked the model for
`suggested_followups` at all, and `RectifyResult.candidates` is
deliberately left empty on a locked recheck (see its own docstring in
app/models.py) to keep the response small -- which is also what backs
the other-ayanamsa dropdown. So both features were correctly absent,
just confusingly so.
**Decision (asked Divakar directly -- three options: keep it cheap as-is;
always run the full comparison even once locked; or keep the recheck
cheap but still ask for follow-up questions):** chose the third --
follow-up questions should keep showing after lock, but the
multi-ayanamsa comparison should not (that one stays tied to actually
running all providers, which is the expensive part).
**Fix:**
- New shared `_followups_instruction(today)` helper, used by both the
  full prompt and the recheck prompt, so they can't quietly drift apart
  on the "suggest 2-3 more events, backward-looking only" wording.
- `_recheck_schema()` now also requires `suggested_followups` (same item
  shape as the full schema); `_build_recheck_prompt` appends the shared
  instruction.
- `_run_locked_recheck` now parses `suggested_followups` the same way
  `_candidate_from_raw` does (same future-dated filter via
  `_keep_followup_or_log`, same `MAX_SUGGESTED_FOLLOWUPS` cap) and
  includes them in the `RectifyResult` it returns while still locked.
  `candidates` stays empty on this path, by design -- no ayanamsa
  dropdown once locked, consistent with the chosen tradeoff.
- Each answer to one of these still falls straight through to a fresh
  `run_rectification` call (which re-enters the cheap recheck) rather
  than being batched like the pending-review flow's `pending_candidates`
  tracking (#32) -- intentional: that batching exists specifically to
  avoid repeated EXPENSIVE multi-provider reruns, and the locked recheck
  is cheap enough that just re-running it on every single answer is fine.
- Mobile (rectify_screen.dart): the "answer them one at a time, recalibrates
  once the whole batch is done" copy only applies to the pending-review
  batching flow -- now conditioned on `result.status`, so a locked
  profile's follow-up cards instead say each answer recalibrates right
  away (true for that path), and the "Recalibrate now (skip the rest)"
  button is relabeled "Check again now" when locked, since there's no
  batch being held back to skip.
**Files:** app/routers/rectify.py, mobile_app/lib/screens/rectify_screen.dart.
**Status:** DONE for the code (verified the recheck schema/prompt changes
directly). NOT yet exercised against a real locked profile and a real
LLM response -- next real-device test should confirm follow-up question
cards reappear once a profile shows "locked" status, and that the
ayanamsa dropdown correctly stays absent there (that part is intentional,
not a remaining bug).

### 38. Architecture-review fixes (cost controls, security hardening, tests, rectify.py split)
**Type:** Hardening / refactor, from the architecture review Divakar
asked for. The rectification engine itself stays LLM-reasoned (see
FeatureBacklog #11 for the recorded decision and the hybrid idea).
**Changes:**
- **Rectification is now metered.** `app/quota.py:consume_rectify_run`
  caps LLM-backed rectification runs per user per day
  (`RECTIFY_DAILY_LIMIT`, default now 100; returns 429 when used up). A run
  that fails before producing a result is given back. Applied to
  `POST /rectify` and to `/rectify/answer` when it actually triggers a
  rerun; `/rectify/choose-ayanamsa` makes no LLM call and is not
  counted. The automatic rechecks triggered from profiles.py (spouse
  details changed, enough new events on a locked profile) are not
  counted either.
- **Quota updates are atomic.** Every consume/refund is one conditional
  update (`$inc` guarded by the precondition in the filter) instead of
  read-modify-write, so concurrent requests can't spend the same credit.
- **Rate limiting.** New `app/rate_limit.py` (in-memory sliding window):
  chat 10/min per user, rectify 6/min per user, sign-in 10/min per IP.
  In-memory means per-process; move to a shared store if the API ever
  runs as several worker processes.
- **Length caps on client free text** (`app/models.py` MAX_* constants):
  chat message 4000, notes 2000, question text 1000, hint 500, category
  64. Request models only; response models stay uncapped so stored
  records always read back. The chat input field enforces the same 4000.
- **Indexes** created at startup (`app/db.py:ensure_indexes`, called from
  main.py's lifespan). A database that is unreachable at startup logs a
  warning and does not stop the app.
- **Startup config check** (`main.py:check_startup_config`): with
  `ENVIRONMENT` set to anything but `dev`, the app refuses to start on
  the placeholder/short `JWT_SECRET`, a missing `FIELD_ENCRYPTION_KEY`,
  `ALLOW_TEST_AD_BYPASS=true` or `LOG_LLM_PROMPTS=true`. Default is
  `dev`, so local testing is unchanged.
- **Session revocation.** Tokens now carry `iat`; `POST /auth/logout-all`
  sets `tokens_invalid_before` on the user and `get_current_user`
  refuses older tokens. Backend only -- there is no button for it in the
  app yet. Tokens issued before this change have no `iat` and keep
  working until they expire, unless logout-all is called.
- **Encryption key versioning.** New values are stored as `v1:<token>`.
  Bare tokens written before the prefix existed and pre-encryption
  plaintext both still read back. A `v1:` value that fails to decrypt
  now raises `FieldDecryptionError` (wrong/lost key) instead of
  returning ciphertext.
- **Encrypted collection wrapper generalised.** The cursor forwards any
  cursor method (limit, skip, ...) and decrypts under `async for` as
  well as `to_list`. The collection refuses methods it has no
  encrypting implementation for, apart from a short safe list.
- **API base URL** is a `--dart-define=API_BASE_URL=...` build setting
  with the emulator address as the default (`mobile_app/lib/config.dart`).
- **rectify.py split**: prompts and schemas moved to
  `app/rectify_prompts.py`, thresholds and result-parsing helpers to
  `app/rectify_scoring.py`; `app/routers/rectify.py` keeps the endpoints
  and the lock/recheck flow (948 -> 587 lines). Moved definitions were
  compared before and after and are identical; the router re-imports
  the names so existing references keep working.
- **Test suite**: `tests/` (43 tests, pytest) with an in-memory MongoDB
  stand-in and a stand-in for the LLM providers. `requirements-dev.txt`,
  `pytest.ini`, `.github/workflows/ci.yml` (pytest + `flutter analyze`),
  `Dockerfile`, `.dockerignore`, and README sections on what belongs in
  git, running outside your own machine, and running the tests.
**Verified:** all 43 tests pass (run on Python 3.10 in the sandbox; the
project runs 3.12). They cover the quota pools and refunds, the
concurrent-spend case, the rectify cap and refund, rate limiting,
token revocation, the encryption formats, follow-up batching, the
locked recheck, choose-ayanamsa, route ordering, ownership checks and
the startup check.
**Not verified:** nothing was run against the real MongoDB Atlas
database, a real LLM provider or a device. The GitHub Actions workflow
and the Dockerfile were written but never executed. The Dart changes
were only checked for balanced brackets.
**Not done:** HTTPS in front of the API (a reverse proxy is deployment
infrastructure, not code in this repo) and a "sign out everywhere"
button in the app.

## Architecture review fixes (Nov 2026)
Done: locked-profile auto-recheck now runs as a metered background task; push sends run off the event loop; calibration history/follow-up tracking use atomic $push/$addToSet (pending fields $unset on settle); request validation (real dates, lat/long/offset ranges, time format, weights) with lenient response models; truncation caps now log; userId stripped from raw responses; model names/timeouts in settings; shared httpx client; Gemini key sent via header; key rotation (FIELD_ENCRYPTION_OLD_KEYS + scripts/reencrypt_fields.py); lazy TimezoneFinder; mobile request timeouts, debug-only cleartext, session no longer cleared on network errors.
Not done: release signing config (needs your keystore), real ad-network SSV verification, HTTPS proxy, Redis-backed rate limiter, full response models for profile/conversation GETs, AppDataProvider split, Flutter tests, moving app/engines to experimental/, status-string enum.
