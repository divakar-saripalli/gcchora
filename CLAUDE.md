# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository state

This is not yet a git repository, and there is no build tooling (no `pom.xml`/`build.gradle` for the Java file, no test suite) — an IntelliJ project (`.idea/`, `GoogleHora.iml`) plus a FastAPI service with a `requirements.txt` and `.venv`, and a separate Flutter app under `mobile_app/`.

## What this codebase is

- **Python FastAPI service** (`main.py` + `app/`): the actual product backend. Run with `uvicorn main:app --reload` after `pip install -r requirements.txt` (needs `MONGODB_URI` and friends in `.env` -- see `.env.example`).
- **Flutter mobile app** (`mobile_app/`): the client. `mobile_app/lib/models.dart` mirrors `app/models.py`'s JSON shapes field-for-field; keep the two in sync when either changes.
- **`DecoupledAstroEngine.java`**: a separate, standalone Java CLI prototype of an *earlier* deterministic version of the rectification algorithm (see "Legacy deterministic engine" below). It is not called by the FastAPI service or the mobile app, and is not being kept in sync with the Python side going forward -- treat it as a historical reference, not something to update alongside `app/`.

## Current design: LLM-reasoned rectification (`app/routers/rectify.py`)

Birth-time rectification does **not** compute anything locally anymore. The service collects the user's stated birth details (`app/routers/profiles.py`, `birth_profiles` collection) and their real life events (`life_events` collection, category + exact date + importance + notes), and hands both straight to the LLM via `app.llm_gateway`, asking it to do the Vedic dasha/bhukti reasoning itself and return a structured (JSON-schema-constrained) result. `app/engines/vedic.py`, `app/engines/significators.py`, and `astro_rules.json` -- the old Swiss-Ephemeris-based deterministic engine described under "Legacy deterministic engine" below -- are kept on disk but are **not imported** by `app/routers/rectify.py`. `app/ayanamsas.py` holds a standalone copy of the curated ayanamsa list (`CURATED_AYANAMSAS`) specifically so the LLM flow has no dependency on `app/engines/`.

Key pieces, all in `app/routers/rectify.py` unless noted:

- **Multi-provider, multi-ayanamsa fan-out**: `app.llm_gateway.generate_structured_from_all` calls every configured provider (`gemini`, `gpt`) concurrently; each is asked to evaluate every ayanamsa in `CURATED_AYANAMSAS` and return a rectified time + confidence score for each, plus reasoning and suggested follow-up event categories. Each provider's highest-confidence ayanamsa becomes that provider's `RectifyCandidate`.
- **Auto-lock at `LOCK_THRESHOLD` (95%)**: if any candidate's confidence reaches this on its own -- the LLM's self-reported score, not a user rating -- it's stored as the profile's calibration immediately (`calibration.status = "locked"`, `calibration.locked = True`). No user action needed.
- **User scoring when nothing auto-locks**: candidates are returned to the client as `status: "pending_review"`; the user can add one more life event first (`POST .../rectify/answer`, or any `POST .../events` call) to help judge between disagreeing candidates, then rate each 1-5 via `POST .../rectify/score` (stored in the `rectification_ratings` collection). The highest-rated candidate becomes the calibration.
- **Locked-mode rechecks stay cheap**: once locked, `POST .../rectify` (or the auto-trigger below) calls *only* the locked provider, for *only* the locked ayanamsa, rather than re-running the full fan-out. If the recheck's confidence holds above `UNLOCK_THRESHOLD` (75%), the lock just gets its confidence/time refreshed; below that, it unlocks and a full fan-out re-run happens automatically.
- **Bounded staleness while locked**: `app/routers/profiles.py`'s `add_life_event` doesn't call the LLM on every event add (that stays as cheap as ever). It only increments `calibration.events_since_lock_check` on a locked profile, and once that hits `EVENTS_SINCE_LOCK_CHECK_THRESHOLD` (== `MIN_EVENTS_REQUIRED`, currently 6), that request triggers the recheck automatically -- so a lock can't silently drift out of fit with events that don't actually match it, without needing the user to notice and revisit `/rectify` themselves.
- **Session-to-session context**: every rectification attempt (locked or not) appends a compact summary to `calibration.history`, and that history is included in the next prompt, so the LLM refines rather than starting cold each time.
- **Chat context** (`app/routers/chat.py` `_build_context`): includes the calibration summary *and* the full list of life events on every chat turn, since those events are now the primary signal behind the chart itself, not just an input to a separate computed timeline.

## Legacy deterministic engine (kept on disk, not imported by the current flow)

This describes `app/engines/vedic.py`, `app/engines/significators.py`, `astro_rules.json`, and the still-independent `DecoupledAstroEngine.java` -- the original approach, before rectification moved to LLM reasoning above. Useful if that approach is ever revisited, or for comparison.

Two **independent, parallel implementations** of the same Vedic astrology "dasha" (planetary period) calibration engine existed: `app/engines/vedic.py` (Python) and `DecoupledAstroEngine.java` (standalone Java CLI, package `com.astro.engine`). They were never connected to each other.

- `app/engines/vedic.py` used the Swiss Ephemeris (`swisseph`) to compute the Moon's sidereal longitude at birth, selecting an ayanamsa from `CURATED_AYANAMSAS`/`AYANAMSA_SIDM` (`PUSHYA_PAKSHA` uses a custom fixed offset via `SE_SIDM_USER`).
- `DecoupledAstroEngine.java` has a `main()` with a hardcoded example birth date/Moon longitude -- no ephemeris library is used; the caller must supply `moonLong` directly. It parses `astro_rules.json` with hand-rolled string splitting (`loadExternalRules()`), not a JSON library -- fragile, breaks on nested structures or values containing commas, so keep `astro_rules.json` flat if editing it. It prints results to stdout rather than returning structured data.

Core algorithm (shared logic, duplicated across both files):

1. **Nakshatra/dasha lookup**: divide the Moon's sidereal longitude by `360/27` to get the nakshatra index; `nakshatra_idx % 9` selects the starting Vimshottari dasha lord from a fixed 9-lord cycle (`DASHA_ORDER` / `dasha_order`, starting `Ketu`), each with a fixed year-length (`DASHA_YEARS` / `dasha_years`).
2. **Timeline generation**: walks all 9 mahadashas (major periods) starting from the birth-derived lord, each subdivided into 9 bhuktis (sub-periods, proportional to `mahadasha_years * bhukti_lord_years / 120`), using a 365.25-day year approximation.
3. **Event scoring**: for each historical life event, find the active mahadasha/bhukti lord at that date, then check membership against `astro_rules.json`'s significator list for that category (AND-matching: both the mahadasha and bhukti lord had to be a significator). A "confidence rating" is `earned_weight / total_weight * 100` across all events.

`astro_rules.json` is the shared significator matrix (event category → list of planet names expected to be active during that kind of event); `app/engines/significators.py` prefers a MongoDB `significator_rules` collection (seeded by `scripts/seed_rules.py`) over the local file, matching the original prototype's fallback behavior. Neither the Mongo collection nor this file is read by the current LLM-reasoned flow.
