# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository state

This is not yet a git repository, and there is no build tooling (no `requirements.txt`, `pom.xml`, `build.gradle`, or test suite) — just an IntelliJ project (`.idea/`, `GoogleHora.iml`) around loose source files. Set up dependency/build files before assuming standard commands like `pip install -r requirements.txt` or `mvn build` will work.

## What this codebase is

Two **independent, parallel implementations** of the same Vedic astrology "dasha" (planetary period) calibration engine — a Python FastAPI service (`main.py`) and a standalone Java CLI (`DecoupledAstroEngine.java`, package `com.astro.engine`). They are not connected to each other; when changing the core algorithm (dasha timeline generation, event scoring), consider whether the change needs to be mirrored in both files.

### Python service (`main.py`)
- FastAPI app exposing `POST /api/v3/calibrate`. Depends on `pyswisseph` (`swisseph`), `fastapi`, `pydantic` — none are pinned in a manifest yet.
- Run with `uvicorn main:app --reload` (module name `main`, app object `app`) after installing dependencies manually.
- Uses the Swiss Ephemeris (`swisseph`) to compute the Moon's sidereal longitude at birth, selecting an ayanamsa via `CalculationSettings.ayanamsa_selection` (`LAHIRI`, `PUSHYA_PAKSHA`, or `KP_STELLAR`; `PUSHYA_PAKSHA` uses a custom fixed ayanamsa offset via `SE_SIDM_USER`).

### Java CLI (`DecoupledAstroEngine.java`)
- Has a `main()` with a hardcoded example birth date/Moon longitude — no ephemeris library is used; the caller must supply `moonLong` directly.
- Parses `astro_rules.json` with hand-rolled string splitting (`loadExternalRules()`), not a JSON library — it strips braces/quotes and splits on `,`/`:`. This is fragile: it breaks on nested structures or values containing commas, so keep `astro_rules.json` flat (`{"CATEGORY": ["Planet", ...]}`) if editing it.
- Prints results to stdout rather than returning structured data.

## Core algorithm (shared logic, duplicated across both files)

1. **Nakshatra/dasha lookup**: divide the Moon's sidereal longitude by `360/27` to get the nakshatra index; `nakshatra_idx % 9` selects the starting Vimshottari dasha lord from a fixed 9-lord cycle (`DASHA_ORDER` / `dasha_order`, starting `Ketu`), each with a fixed year-length (`DASHA_YEARS` / `dasha_years`).
2. **Timeline generation**: walks all 9 mahadashas (major periods) starting from the birth-derived lord, each subdivided into 9 bhuktis (sub-periods, proportional to `mahadasha_years * bhukti_lord_years / 120`), using a 365.25-day year approximation.
3. **Event scoring**: for each historical life event (`category`, `exact_date`, `impact_weight_score`), find the active mahadasha/bhukti lord at that date, then check membership against `astro_rules.json`'s significator list for that category. A "confidence rating" is `earned_weight / total_weight * 100` across all events — i.e., how well the dasha-lord theory retroactively explains the user's real life events.

`astro_rules.json` is the shared significator matrix (event category → list of planet names that are expected to be active during that kind of event) consumed by both implementations; if a category isn't present, both implementations fall back to an empty/default list (Python defaults to `{"MARRIAGE": [...]}` only if the whole file is missing, not per-category).
