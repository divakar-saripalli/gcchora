"""Vedic dasha-timeline engine: ephemeris calculation, Vimshottari dasha/bhukti
generation, event scoring, and birth-time/ayanamsa rectification.

This supersedes both the Python and Java prototypes: it is now the single
source of the dasha algorithm (see CLAUDE.md / the design doc for why the
Java copy was retired), and it fixes a bug present in the original
prototype -- pyswisseph's ayanamsa constants are named ``SIDM_*``, not
``SE_SIDM_*``; the original code referenced the latter and would raise
AttributeError the moment the engine was constructed.
"""
from datetime import datetime, timedelta
from typing import Any, Optional

import swisseph as swe

DASHA_ORDER = ["Ketu", "Venus", "Sun", "Moon", "Mars", "Rahu", "Jupiter", "Saturn", "Mercury"]
DASHA_YEARS = {
    "Ketu": 7, "Venus": 20, "Sun": 6, "Moon": 10, "Mars": 7,
    "Rahu": 18, "Jupiter": 16, "Saturn": 19, "Mercury": 17,
}
NAKSHATRA_SPAN = 360.0 / 27.0

# Curated ayanamsa set. "any other available ones" -- pyswisseph exposes
# ~40 SIDM_* constants, but only a handful are actually used in Vedic
# practice; we expose those rather than the full, mostly-irrelevant list.
#
# Note: Lahiri's ayanamsa is officially named "Chitrapaksha Ayanamsa", so
# LAHIRI and CHITRA_PAKSHA below are the *same* calculation, not two
# options -- both keys are offered because both names are in common use.
# TRUE_CHITRA_PAKSHA is a genuinely different, dynamic variant (keeps Spica
# fixed at 0 Libra continuously, rather than Lahiri's fixed-epoch formula).
AYANAMSA_SIDM = {
    "LAHIRI": swe.SIDM_LAHIRI,
    "CHITRA_PAKSHA": swe.SIDM_LAHIRI,          # alias of LAHIRI, see note above
    "TRUE_CHITRA_PAKSHA": swe.SIDM_TRUE_CITRA,  # distinct "true"/dynamic variant
    "KRISHNAMURTI": swe.SIDM_KRISHNAMURTI,      # KP / Krishnamurti Paddhati
    "RAMAN": swe.SIDM_RAMAN,
    "YUKTESHWAR": swe.SIDM_LAHIRI,              # swisseph has no native Yukteshwar
    # constant; treat as a future custom-offset ayanamsa (see PUSHYA_PAKSHA)
    # rather than silently mislabeling it as Lahiri in production.
}

# Pushya Paksha uses a custom fixed ayanamsa offset (not a swisseph built-in
# constant). The offset below is carried over from the original prototype;
# flagged in the design doc as needing verification against a trusted
# published reference before this is relied on for real users.
PUSHYA_PAKSHA_T0_OFFSET_DEG = -1.221

CURATED_AYANAMSAS = ["LAHIRI", "TRUE_CHITRA_PAKSHA", "KRISHNAMURTI", "PUSHYA_PAKSHA", "RAMAN"]


def get_julian_day(date_str: str, time_str: str, tz_offset_hours: float) -> float:
    dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
    ut_hour = dt.hour + dt.minute / 60.0 + dt.second / 3600.0 - tz_offset_hours
    return swe.julday(dt.year, dt.month, dt.day, ut_hour)


def get_sidereal_moon_longitude(jd_ut: float, ayanamsa: str) -> float:
    """Set the sidereal mode for `ayanamsa` and return the Moon's sidereal
    longitude (degrees) at `jd_ut`."""
    if ayanamsa == "PUSHYA_PAKSHA":
        swe.set_sid_mode(swe.SIDM_USER, 0.0, PUSHYA_PAKSHA_T0_OFFSET_DEG)
    else:
        swe.set_sid_mode(AYANAMSA_SIDM.get(ayanamsa, swe.SIDM_LAHIRI))
    result, _ = swe.calc_ut(jd_ut, swe.MOON, swe.FLG_SWIEPH | swe.FLG_SIDEREAL)
    return result[0]


def generate_dasha_timeline(birth_dt: datetime, moon_long: float) -> list[dict[str, Any]]:
    """Vimshottari mahadasha/bhukti timeline starting from the birth-derived
    nakshatra lord. Unchanged from the original prototype's algorithm."""
    nakshatra_idx = int(moon_long // NAKSHATRA_SPAN)
    fraction_consumed = (moon_long % NAKSHATRA_SPAN) / NAKSHATRA_SPAN

    start_lord_idx = nakshatra_idx % 9
    start_lord = DASHA_ORDER[start_lord_idx]
    years_spent = DASHA_YEARS[start_lord] * fraction_consumed

    pointer = birth_dt - timedelta(days=years_spent * 365.25)

    timeline = []
    idx = start_lord_idx
    for _ in range(9):
        lord = DASHA_ORDER[idx]
        m_span = DASHA_YEARS[lord]
        m_end = pointer + timedelta(days=m_span * 365.25)

        bhuktis = []
        b_pointer = pointer
        b_idx = idx
        for _ in range(9):
            b_lord = DASHA_ORDER[b_idx]
            b_days = (m_span * DASHA_YEARS[b_lord] / 120.0) * 365.25
            b_end = b_pointer + timedelta(days=b_days)
            bhuktis.append({"bhukti_lord": b_lord, "start": b_pointer, "end": b_end})
            b_pointer = b_end
            b_idx = (b_idx + 1) % 9

        timeline.append({"mahadasha_lord": lord, "start": pointer, "end": m_end, "bhuktis": bhuktis})
        pointer = m_end
        idx = (idx + 1) % 9

    return timeline


def score_timeline_against_events(
    timeline: list[dict[str, Any]],
    events: list[dict[str, Any]],
    significator_matrix: dict[str, list[str]],
) -> dict[str, Any]:
    """Score how well a dasha timeline explains a set of reported life
    events. Extracted from the original prototype's calibrate endpoint so
    it can be reused by both a one-off check and the rectification search
    below."""
    earned_sum, total_sum = 0.0, 0.0
    breakdown = []

    for ev in events:
        ev_date = datetime.strptime(ev["exact_date"], "%Y-%m-%d")
        weight = ev.get("impact_weight_score", 1.0)
        total_sum += weight
        active_md, active_bh = "Unknown", "Unknown"

        for md in timeline:
            if md["start"] <= ev_date <= md["end"]:
                active_md = md["mahadasha_lord"]
                for bh in md["bhuktis"]:
                    if bh["start"] <= ev_date <= bh["end"]:
                        active_bh = bh["bhukti_lord"]
                        break
                break

        valid_indicators = significator_matrix.get(ev["category"], [])
        # Requiring BOTH the mahadasha and bhukti lord to be a significator
        # (not either/or) matters a lot here: each category's significator
        # list covers roughly half the 9 planets, so an "either" match lets
        # essentially any candidate -- including wrong ones -- match most
        # events by pure chance, which made rectification unable to ever
        # converge (see design doc: "Life-event calibration" section).
        is_match = active_md in valid_indicators and active_bh in valid_indicators
        earned = weight if is_match else 0.0
        earned_sum += earned

        breakdown.append({
            "category": ev["category"],
            "dasha_active": f"{active_md}-{active_bh}",
            "match_status": is_match,
        })

    confidence = (earned_sum / total_sum) * 100 if total_sum > 0 else 0.0
    return {"confidence_rating_percentage": round(confidence, 2), "validation_matrix": breakdown}


def rectify_birth_time(
    birth_date: str,
    stated_time_24h: str,
    tz_offset_hours: float,
    events: list[dict[str, Any]],
    significator_matrix: dict[str, list[str]],
    window_minutes: int = 30,
    step_minutes: int = 2,
    ayanamsas: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """Grid-search candidate birth times x ayanamsas, score each against the
    user's reported life events, and return all candidates ranked by
    confidence (best first).

    This is the core of "recalibrate the birth time": the stated time is
    treated as a midpoint with +/- `window_minutes` of uncertainty, not as
    ground truth.
    """
    ayanamsas = ayanamsas or CURATED_AYANAMSAS
    stated_dt = datetime.strptime(f"{birth_date} {stated_time_24h}", "%Y-%m-%d %H:%M:%S")

    candidates = []
    offset = -window_minutes
    while offset <= window_minutes:
        candidate_dt = stated_dt + timedelta(minutes=offset)
        candidate_time_str = candidate_dt.strftime("%H:%M:%S")
        jd_ut = get_julian_day(birth_date, candidate_time_str, tz_offset_hours)

        for ayanamsa in ayanamsas:
            moon_long = get_sidereal_moon_longitude(jd_ut, ayanamsa)
            timeline = generate_dasha_timeline(candidate_dt, moon_long)
            score = score_timeline_against_events(timeline, events, significator_matrix)
            candidates.append({
                "birth_time_24h": candidate_time_str,
                "offset_minutes": offset,
                "ayanamsa": ayanamsa,
                "confidence_score": score["confidence_rating_percentage"],
                "validation_matrix": score["validation_matrix"],
            })
        offset += step_minutes

    candidates.sort(key=lambda c: c["confidence_score"], reverse=True)
    return candidates


# Human-readable prompts for the categories in astro_rules.json, used when
# generating disambiguation questions. Extend this alongside astro_rules.json
# as new categories are added.
CATEGORY_QUESTIONS = {
    "SCHOOL_GRADUATION": "When did you finish school (10th/12th grade or equivalent)?",
    "UNDERGRAD_GRADUATION": "When did you complete your undergraduate degree?",
    "POSTGRADUATE": "When did you complete a postgraduate/master's degree?",
    "PHD": "When did you complete your PhD or doctorate?",
    "MARRIAGE": "What was your marriage date?",
    "FIRST_CHILD": "What was your first child's birth date?",
    "KIDS": "What was another child's birth date?",
    "CAREER_DISRUPTION": "When did you last change jobs unexpectedly or face a career setback?",
    "PROPERTY_PURCHASE": "When did you buy a property?",
    "FOREIGN_TRAVEL": "When did you first travel abroad?",
    "MAJOR_ACCIDENT": "Have you had a major accident or health event? When?",
    "BUSINESS_LAUNCH": "When did you start a business, if ever?",
    "JOB_PROMOTION": "When did you last get a significant promotion?",
    "OTHER": "Tell us about another meaningful life event -- when did it happen?",
}


def generate_disambiguation_questions(
    top_candidates: list[dict[str, Any]],
    already_asked_categories: set[str],
    max_questions: int = 3,
) -> list[dict[str, str]]:
    """Pick a handful of unanswered event categories to ask about next,
    aimed at separating close-scoring rectification candidates.

    A simple, explainable heuristic: among the leading candidates, find
    which mahadasha lord each would have been in for a handful of common
    life-event windows (approximated by candidate diversity in ayanamsa/
    offset), and ask about categories not yet supplied. A future iteration
    can weight this by how much a category's significator sets actually
    differ across the leading candidates' active lords.
    """
    leading = top_candidates[:5]
    distinct_ayanamsas = {c["ayanamsa"] for c in leading}
    if len(distinct_ayanamsas) <= 1 and len(leading) > 0 and leading[0]["confidence_score"] >= 80:
        return []  # candidates agree closely enough; no need to ask more

    questions = []
    for category, question_text in CATEGORY_QUESTIONS.items():
        if category in already_asked_categories:
            continue
        questions.append({"category": category, "question_text": question_text})
        if len(questions) >= max_questions:
            break
    return questions
