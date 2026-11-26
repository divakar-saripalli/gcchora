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

## 2. Minimize tokens sent/replied per chat message -- DONE (caching idea abandoned, replaced with what actually helps)
Originally raised as "session-scoped chat context... avoid resending
context every message," initially framed around per-user isolation
("so the model doesn't get confused one user with another"). Investigated
and clarified with Divakar: isolation was never actually at risk --
`send_message`/`_build_context` in app/routers/chat.py already scope
every DB read (conversation, profile, events, message history) strictly
by `userId`/`profileId`/`conversationId` on every call, with no shared
in-memory state anywhere in the path. Safe to run on multiple devices/
users concurrently today, no change needed for that.

The real goal, once clarified, was minimizing actual token spend (both
sent and replied). The originally-sketched plan -- Gemini **explicit
context caching** (`cachedContents`) -- was investigated and abandoned
before writing any code: explicit caching requires a minimum of roughly
32,768 input tokens per Google's own docs/pricing pages, and even
Gemini's automatic/implicit caching (free, no API calls needed) requires
4,096+ tokens for the `gemini-3.6-flash` model this app uses. This app's
actual chat context (birth details, one calibration line, a handful of
life events, tone/language instructions) is realistically a few hundred
tokens -- nowhere near either floor. Building explicit caching here would
have been real engineering effort (new API calls, hash-based cache
reuse, graceful-degradation handling) for zero benefit, so it was not
built.

**What was built instead** (the things that actually reduce tokens at
this app's real context size):
- **Reply length hard cap -- shipped, then reverted.** Added
  `CHAT_MAX_OUTPUT_TOKENS = 400` (app/llm_gateway.py), wired into both
  `GeminiProvider.generate` (`generationConfig.maxOutputTokens`) and
  `GPTProvider.generate` (`max_tokens`), as a backstop behind the
  existing soft "3-5 sentences" prompt instruction. Divakar reported real
  replies became "very short... really annoying" shortly after. Likely
  cause: astrology-specific terminology (ayanamsa/dasha/bhukti names,
  Sanskrit terms) appears to tokenize less efficiently than plain
  English, so 400 tokens bought noticeably less actual content than a
  plain-English estimate suggested, visibly clipping normal answers
  rather than only stopping genuine runaways. Reverted entirely (not
  just raised) -- this sandbox can't verify a safe number against the
  real provider, so re-guessing a different threshold risks repeating
  the same problem; relying on the soft prompt instruction alone (as
  before this session's changes) is the safer default until there's a
  way to actually observe real reply-length behavior.
- **History replay window halved, and a real bug fixed along the way**
  (app/routers/chat.py): `MAX_HISTORY_MESSAGES` dropped from 20 to 10 --
  every prior turn gets resent in full on every later message, so this
  count is the single biggest token-growth driver in a long conversation,
  more than the roughly-fixed-size context block. While reducing it,
  found and fixed a real pre-existing bug: the query sorted
  `createdAt` ascending THEN limited to N, which grabs the OLDEST N
  messages, not the most recent N -- a conversation that outgrew the cap
  would silently get stuck replaying its earliest turns forever and lose
  all recent context. (20 mostly hid this; dropping to 10 would have made
  it bite almost immediately.) Fixed by sorting descending, limiting to N,
  then reversing back to chronological order before sending.
- **Life-events block: considered capping, reverted.** Initially capped
  to the most-recent 30 events, but Divakar correctly flagged that life
  events are the actual evidentiary basis the model reasons from for
  every astrology answer -- unlike chat history (just back-and-forth
  text) or reply length, trimming this by recency risks silently
  dropping an old-but-significant event (an early marriage, a childhood
  health issue, a career change years back) and degrading answer
  correctness. Reverted back to the original unbounded
  `to_list(length=1000)` fetch -- full event history is sent on every
  chat message, same as before this feature. Left out of the token
  savings here on purpose.

**Verified** with fake-DB/fake-HTTP functional tests (not run against the
real Gemini/GPT APIs, which this sandbox can't reach): confirmed history
replay keeps exactly the most-recent `MAX_HISTORY_MESSAGES` turns in
correct chronological order (not the oldest ones); confirmed both
providers' actual HTTP payloads include the output-token cap.

**Not done / caveats:**
- No live measurement of actual token savings against real Gemini/GPT
  usage -- the numbers above (400 output tokens, 10 history turns, 30
  events) are reasoned defaults, not tuned against real billing data.
  Worth revisiting once there's real usage to look at.
- (superseded by the revert above -- see that entry for what happened
  and why the hard cap was removed rather than just retuned.)
- Gemini's automatic implicit caching still applies for free with zero
  code on our side, for whatever edge case (e.g. a profile with many
  events) happens to push a single request over its 4,096-token floor --
  nothing to build for that, it already just works when it can.

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

## 5. Spoken chat replies (text-to-speech) -- DONE
Once mic input (item 4) was working, Divakar asked for responses to also
come back as voice, so the chat reads like a real voice conversation
rather than speak-to-type-then-read.

**What shipped:**
- `flutter_tts` package added; new `lib/services/tts_service.dart` wraps
  speak/stop with completion/cancel/error handlers collapsed into one
  `onDone` callback.
- Every assistant reply is now read aloud automatically right after it's
  sent (see `_maybeSpeak` in chat_screen.dart), on by default.
- A speaker icon in the chat AppBar mutes/unmutes this per session
  without needing to touch any settings screen.
- To avoid an obvious feedback loop (the mic picking up the assistant's
  own voice) and the confusion of talking over a reply still playing,
  starting to listen now stops any in-progress speech first.
- Android manifest got the extra `<queries>` entry flutter_tts's own
  README calls for (TTS_SERVICE, same Android 11+ visibility requirement
  as speech_to_text's RecognitionService entry).
**Not done:** voice/accent selection, speech rate control, or switching
the TTS voice's language to match whatever language the reply happens to
be in (left as device TTS default, documented in tts_service.dart's doc
comment, for the same reason speech_to_text's locale was left as device
default).

## 6. Indian-accented TTS voice -- DONE (best-effort)
Divakar asked for spoken replies to sound like Indian English rather
than the generic (US-sounding) voice flutter_tts was defaulting to.

**What shipped:** `tts_service.dart` now explicitly requests `en-IN` via
setLanguage, then tries to find and pin a specific installed en-IN voice
via getVoices/setVoice (some engines offer more than one voice per
locale). Hard-coded rather than derived from the profile's location,
since every profile tested so far is India-based -- flagged in the code
as something to revisit if that changes.

**Caveat:** this only sets accent for the TTS voice itself, not which
language a reply is spoken in -- see the language_line in
_build_context (app/routers/chat.py), which lets the assistant answer in
whatever language the question was asked in. If a reply comes back in,
say, Telugu, it'll still be read by whatever voice setLanguage('en-IN')
resolved to, which won't sound right for non-English text. Also,
whether an actual Indian-accented voice is available at all depends on
the phone's installed TTS voice data -- if none is installed, this
degrades gracefully to whatever the engine's own default is.

## 7. Spouse birth details + cross-chart ("synastry") correlation -- DONE
Divakar asked to: (1) collect spouse birth date/time/place when a profile
is married; (2) correlate marriage/kids/parents'-and-in-laws' health
events against the spouse's chart too during rectification, recalibrating
if needed; (3) factor in the spouse's chart when answering chat questions
about shared matters (kids' health, either side's parents'/in-laws'
health, money, property/asset purchases); (4) backward-compatibility --
prompt for spouse details on any already-married profile that doesn't
have them yet.

**What shipped:**
- Backend model (app/models.py): `is_married`, `spouse_birth_date`,
  `spouse_birth_time_24h`, `spouse_geographic_coordinates` added to
  BirthProfileCreate/Update/Out, all independent of each other so a
  married profile can have none/some/all filled in.
- app/routers/profiles.py: `_needs_spouse_details()` computes the
  backward-compat flag (true whenever married but missing any spouse
  field -- including a profile with no spouse_* keys at all, i.e. one
  that predates this feature); added to both create/update's
  BirthProfileOut response AND the raw-dict GET /profiles/{id} path
  (which has no response_model, so it needed adding explicitly).
  Spouse coordinates get the same DST-aware timezone_offset_hours
  override as the user's own (via app/timezone_lookup.py). Changing
  spouse details on an already-LOCKED profile triggers a rectification
  recheck (same path as add_life_event's auto-recheck) rather than
  discarding the calibration outright, since the chart itself hasn't
  changed -- only the cross-chart evidence available to evaluate it.
- app/routers/rectify.py: new `_build_spouse_line()` helper, wired into
  both `_build_prompt` (initial/unlocked rectification) and
  `_build_recheck_prompt` (locked recheck) -- when spouse details are on
  file, tells the LLM to cross-check MARRIAGE/FIRST_CHILD/KIDS/
  PARENT_HEALTH/INLAW_HEALTH events against the spouse's own dasha/bhukti
  periods too, as supporting evidence for the PRIMARY person's
  calibration (explicitly NOT asked to rectify the spouse's own time).
- app/routers/chat.py: `_build_context` now includes a spouse_line when
  details are on file, naming the specific shared topics Divakar asked
  about (kids' health, parents'/in-laws' health, money/financial
  stability, property/asset purchases) and instructing the model to
  reason using both charts jointly for those. If married but spouse
  details are missing, a different line tells it to answer from the
  user's own chart only and mention once (not every message) that spouse
  details would help.
- Two new life-event categories, PARENT_HEALTH and INLAW_HEALTH
  (mobile_app/lib/screens/life_events_screen.dart), so these events can
  actually be logged -- previously there was no category covering either
  parents' or in-laws' health at all.
- Mobile: profile_setup_screen.dart got a "Married" switch and (when on)
  spouse birth date/time/place-with-lookup/manual-coordinates fields,
  mirroring the user's own birth-detail UI. home_screen.dart shows a
  standing (non-dismissable-by-ignoring, always visible until resolved)
  card right under the profile card whenever
  `profile.needsSpouseDetails` is true -- covers both a profile just
  marked married without filling in spouse details yet, and the
  backward-compatibility case of a profile that predates this feature
  entirely.
- Full plumbing added through api_client.dart, app_provider.dart, and
  models.dart's BirthProfile (isMarried/spouseBirthDate/
  spouseBirthTime24h/spouseCoordinates/needsSpouseDetails).

**Verified:** all backend logic (needs_spouse_details computation across
create/update/get/legacy-doc cases, spouse DST-aware offset computation,
and both rectify/chat prompts correctly including/omitting the spouse
block across married-with-details / married-without-details / unmarried)
with a fake in-memory DB and direct prompt-string assertions -- see this
session's verification run. Mobile-side changes checked for brace/paren
balance and against the actual Flutter/Dart APIs used elsewhere in the
file, but not run on a real device yet (no `flutter analyze` available
in this sandbox).

**Not done / caveats:**
- No synastry-specific scoring formula -- the correlation is entirely
  LLM-reasoned (consistent with how rectification already works post the
  vedic.py/significators.py deprecation), same as the rest of this app's
  rectification logic. There's no programmatic check of whether the
  LLM actually did this well.
- The spouse's OWN birth time is never itself rectified/calibrated by
  this feature -- it's used only as supporting evidence for the primary
  profile. If the spouse's stated time is itself very uncertain, that
  uncertainty isn't modeled, just passed through as given.
- A married profile is not required to have spouse details to be used
  normally -- the backward-compat prompt is a nudge (a standing card on
  the home screen), not a block.


## 8. Show all ayanamsa systems with scores, not just the top pick -- DONE
Divakar asked: during calibration, only the single highest-matching
ayanamsa is shown per provider. He wanted all of them listed with their
confidence scores (e.g. as a dropdown), and the ability to expand one to
see which life events support it, then choose a different one than the
model's own top pick to lock in.

**What shipped:**
- Every curated ayanamsa was already being scored internally
  (`ayanamsa_results` in the rectification response) -- only the
  provider's own best one ever reached the UI. No backend change needed
  to surface the rest; this part was already free.
- New per-ayanamsa `reasoning` field (app/models.py `AyanamsaEstimate`,
  `_full_schema()` in app/routers/rectify.py): the model is now asked to
  explain, for EACH ayanamsa individually (not just its overall
  favorite), which specific life events support that ayanamsa/time
  combination via dasha/bhukti matching -- e.g. naming which event fell
  in which planetary period under that ayanamsa. This is a real added
  cost (a short paragraph per ayanamsa instead of just three numbers,
  during calibration only -- not on every chat message, so it doesn't
  fight the chat token-minimization work done earlier this session).
- New endpoint `POST /{profile_id}/rectify/choose-ayanamsa`
  (`RectifyAyanamsaChoice` in app/models.py, `choose_ayanamsa` in
  app/routers/rectify.py): locks directly to an explicit (provider,
  ayanamsa) pair from the last rectification run, overriding that
  provider's own "best" pick. Unlike the existing 1-5 star rating flow
  (`/rectify/score`, which gates on LOCK_THRESHOLD and may only reach
  "calibrated" rather than "locked"), an explicit choice here always
  locks immediately -- the user deliberately picked it after reading
  why it fits, so there's no confidence threshold to second-guess them
  with.
- Mobile (rectify_screen.dart, models.dart, api_client.dart,
  app_provider.dart): each candidate card now shows an expandable "See
  all N ayanamsa systems" list, sorted by confidence score, each row
  showing its score, rectified time, its own reasoning text, and a "Use
  this" button (with a confirming dialog, since it locks immediately) --
  wired to the new endpoint. The provider's own top pick is labeled
  "(top pick)" for clarity but is otherwise just one more row in the
  list, not specially gated.

**Verified:** backend with a fake-DB script -- confirmed choosing a
NON-top-ranked ayanamsa (60% confidence) correctly overrides the
provider's own top pick (78%) in the stored calibration, and that
choosing an unknown ayanamsa name is rejected with a clear 404 rather
than silently falling back to something else. Mobile changes checked
for brace/paren/quote balance and against the Flutter APIs already used
elsewhere in this file, but not run on a real device (no Flutter
toolchain in this sandbox).

**Not done / caveats:**
- No live LLM call to confirm the model actually produces good,
  specific per-ayanamsa reasoning text in practice (vs. generic
  filler) -- this sandbox can't reach Gemini/GPT. Worth a close look on
  the first real calibration run after this ships.
- Choosing a non-top ayanamsa doesn't get logged into
  `calibration.history`'s normal attempt trail any differently than an
  auto-picked one -- it's recorded in `rectification_ratings_collection`
  with a `manual_ayanamsa_choice` marker for an audit trail, but nothing
  in the UI currently surfaces "you overrode the model's pick here" after
  the fact if you look back at history later.


## 9. Follow-up answer's free-text description treated as the actual answer to the specific question -- DONE
Divakar asked: while answering a calibration follow-up question, the
user can already write a free-text description (the existing "Any
detail?" notes field). He wanted that description -- which might
directly correct the suggested date/time window, or give some other
input -- passed to the model explicitly as "the response to THIS
question is this, calibrate accordingly," rather than just tacked on as
a minor aside.

**What was found:** the notes field's text WAS already being included in
the prompt (via `_format_events`'s trailing `-- {notes}` clause), but
two things were missing: (1) the original question text itself was
never stored at all -- the life event only kept the category slug, so
the model had no way to know exactly what had been asked, only a rough
category name to guess from; (2) the notes were rendered as a dash-
appended afterthought on a generic "rated N/5" line, not foregrounded as
the actual answer to a specific question -- easy for a correction like
"actually this was 2018, not 2012-2015" to read as a minor comment
rather than the authoritative answer to calibrate from.

**What shipped:**
- `question_text` is now echoed back from the question the user
  answered and stored on the life event (`RectifyAnswer.question_text`,
  app/models.py; threaded through app/routers/rectify.py's
  `add_followup_and_rectify` insert; `DisambiguationAnswer.questionText`
  in mobile_app/lib/models.dart; `rectify_screen.dart`'s `_addFollowup`
  now sends `q.questionText` with the answer).
- `_format_events` (app/routers/rectify.py, used for every rectification
  prompt) now renders these events as an explicit question/answer pair:
  `Follow-up question that was asked: "<question>" -- User's answer:
  <notes>`, with the resonance rating and originally-suggested window
  kept as secondary context rather than the main framing. The existing
  disconfirming-evidence phrasing for a low (1-2) rating is preserved.
  Falls back to the old generic formatting for events with no
  `question_text` (manually-logged events, or older records from before
  this field existed).
- app/routers/chat.py got the same treatment (new `_format_chat_event_line`
  helper) for consistency, since chat reads the same stored events for
  grounding -- a question/answer pair shouldn't look like a generic
  category+date line there just because it's a different endpoint.
- `LifeEventOut.question_text` (app/models.py) and the corresponding
  mobile `LifeEvent.questionText` (models.dart) pass this through to the
  Life Events list screen display too (life_events_screen.dart now shows
  "Q: <question>" alongside the user's notes), so a human looking at the
  list also sees the original question, not just a category slug.

**Verified** with a fake-DB script: confirmed the rendered prompt line
for both a correcting answer (user's notes override the originally-
suggested window) and a disconfirming low-rated answer, in both
`_format_events` and chat.py's `_format_chat_event_line`. Not yet
exercised against a real LLM call or a real device by Divakar.
**Files:** app/models.py, app/routers/rectify.py, app/routers/chat.py,
app/routers/profiles.py, mobile_app/lib/models.dart,
mobile_app/lib/screens/rectify_screen.dart,
mobile_app/lib/screens/life_events_screen.dart.


## 10. Reset/re-answer follow-up question answers -- DONE
Divakar asked to remove all his previously-given answers to calibration
follow-up questions so he could re-answer them fresh.

**What shipped:**
- New endpoint `DELETE /profiles/{profile_id}/events/followup-answers`
  (app/routers/profiles.py) -- deletes every life event with
  `source == "suggested_followup_answer"` for that profile, leaving
  manually-logged events untouched. Also resets
  `calibration.answered_followup_categories` (the DefectBacklog #32
  batching-progress tracker), since whatever was answered before is now
  gone. Registered BEFORE the existing `/{event_id}` DELETE route in the
  file on purpose -- FastAPI/Starlette matches routes in declaration
  order, and the dynamic `/{event_id}` segment would otherwise swallow
  this literal path too, treating "followup-answers" as an event id.
  Confirmed correct with Starlette's own route-matching check, not just
  by inspection.
- Deliberately does NOT touch calibration status/lock or trigger a
  recalibration automatically -- that would mean an unrequested extra
  LLM fan-out call. The existing "Run calibration" button is how a fresh
  batch of follow-up questions gets generated once the user is ready.
- Mobile: `api.deleteFollowupAnswers` / `AppDataProvider.deleteFollowupAnswers`
  (clears the matching events locally and drops `lastRectifyResult`,
  since it may reference now-deleted answers) and a "Reset follow-up
  answers" button on the Life Events screen (life_events_screen.dart),
  shown only when at least one such answer exists, with a confirming
  dialog stating the count and that it doesn't by itself undo the
  current calibration result.

**Verified:** backend with a fake-DB script (deletes exactly the
follow-up-answer events, leaves a manually-logged one untouched, resets
the tracker, leaves calibration status alone) and Starlette's route
matcher directly (confirmed the new literal route wins over the dynamic
`/{event_id}` route for the exact collision path). Mobile changes
checked for brace/paren balance; not run on a real device.
**Files:** app/routers/profiles.py, mobile_app/lib/services/api_client.dart,
mobile_app/lib/providers/app_provider.dart,
mobile_app/lib/screens/life_events_screen.dart.

## 11. Feed computed chart facts into the rectification prompt (hybrid) -- OPEN

**Idea:** Today the rectification prompt gives the LLM only the birth
date/time/coordinates and the life events, so the model has to work out
planetary positions, the ascendant and decades of dasha/bhukti dates in
its head before it can reason about them. Models estimate that
arithmetic unreliably, which is a likely cause of run-to-run and
provider-to-provider disagreement.
**Proposal:** Have the backend compute the facts with Swiss Ephemeris
(already a dependency) for each candidate birth time and ayanamsa --
ascendant, planetary positions, the exact Vimshottari dasha/bhukti
timeline -- and include them in the prompt. The LLM keeps doing the
interpretation (which timeline fits the events); it stops doing the
astronomy. No astrology rule table of our own is needed.
**Reuses:** `app/engines/vedic.py`'s `get_julian_day`,
`get_sidereal_moon_longitude` and `generate_dasha_timeline` (ephemeris
and date arithmetic only). It would need an ascendant/house calculation
added, since the engine currently computes only the Moon.
**Does NOT reuse:** `score_timeline_against_events` or
`astro_rules.json`. That fixed planet-per-event table is unsourced and
structurally wrong (real significators depend on the individual chart's
house lordships), so it should not be wired back in as-is.
**Cost:** longer prompts, so each rectification costs somewhat more.
Needs testing against the real providers.
**Decision recorded (Divakar):** rectification stays LLM-reasoned
("Option A") for now. `app/engines/` and `astro_rules.json` are kept on
disk, unwired, because this item depends on the ephemeris functions.

## Profile relations and shared family events (Nov 2026)
Done: `relation` on profiles (self/spouse/child/parent/other; older profiles read as self); "Add another profile" in the profile switcher; after adding a SPOUSE, a checklist copies chosen MARRIAGE / FIRST_CHILD / KIDS / DIVORCE events from the user's own profile (nothing pre-ticked unless there is exactly one candidate; copies are independent and de-duplicated by category+date); MARRIAGE is now repeatable and DIVORCE added; calibration/chat prompts note that with several marriages only one is to the current spouse.
Not done: copying in other directions (child/parent), keeping copies in sync, tagging which marriage belongs to which partner.
