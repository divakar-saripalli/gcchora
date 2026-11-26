"""Prompt text and JSON schemas for LLM-reasoned birth-time rectification.

Everything here is a pure function of its arguments: it turns a profile,
its life events and prior attempts into the text sent to a provider, and
defines the shape of the answer expected back. No database access and
no HTTP -- the endpoints that use these live in app/routers/rectify.py,
and the parsing of what comes back lives in app/rectify_scoring.py.
"""
from datetime import datetime, timezone

from app.ayanamsas import CURATED_AYANAMSAS


def _format_events(events: list[dict]) -> str:
    """Renders one line per life event for the prompt. Most events are
    plain manually-logged ones (occurred_score absent -- unchanged
    formatting). Events that came from answering a suggested_followups
    question (see add_followup_and_rectify) carry an occurred_score --
    the user's 1-5 rating of how much it resonates -- and get phrased as
    either confirming or disconfirming evidence, since "no, that didn't
    happen around then" is just as useful for ruling a candidate out as a
    real event is for ruling one in. Those events also may have no
    exact_date (the user often doesn't know one, only a rough window), so
    fall back to the time_frame_hint text in that case.

    Events that carry BOTH `source == "suggested_followup_answer"` and a
    `question_text` (the exact follow-up question that was posed) are
    rendered as an explicit question/answer pair -- the user's own
    free-text `notes` is their actual answer to that specific question,
    not supplementary color tacked onto a generic rated-N/5 line. This
    matters because the user may use that free text to directly correct
    the originally-suggested time window (e.g. "actually this was more
    like 2018, not 2015") -- the model needs to read that as the
    authoritative answer to calibrate from, not an aside. Older events
    (pre-dating this field, or manually logged ones) fall back to the
    plain formatting below."""
    lines = []
    for e in events:
        occurred_score = e.get("occurred_score")
        when = e.get("exact_date") or e.get("time_frame_hint") or "an unknown date"
        note = f" -- {e['notes']}" if e.get("notes") else ""

        if e.get("source") == "suggested_followup_answer" and e.get("question_text"):
            answer_text = e.get("notes") or "(no written answer given -- only the resonance rating below)"
            score_text = f"{occurred_score}/5" if occurred_score is not None else "unrated"
            disconfirming = occurred_score is not None and occurred_score <= 2
            lines.append(
                f"- Follow-up question that was asked: \"{e['question_text']}\" -- User's answer: "
                f"{answer_text} (resonance rating {score_text}, time frame as confirmed or corrected by the user: {when}"
                + (
                    "; this is DISCONFIRMING evidence -- treat as evidence AGAINST candidates "
                    "whose dasha periods would imply this"
                    if disconfirming
                    else f"; importance {e.get('impact_weight_score', 1.0)}"
                )
                + ")"
            )
            continue

        if occurred_score is None:
            lines.append(
                f"- {e['category']} on {when} (importance {e.get('impact_weight_score', 1.0)}){note}"
            )
        elif occurred_score <= 2:
            lines.append(
                f"- User says {e['category']} did NOT notably happen around {when} "
                f"(rated {occurred_score}/5 -- treat as evidence AGAINST candidates whose dasha "
                f"periods would imply this){note}"
            )
        else:
            lines.append(
                f"- {e['category']} around {when}, user rates this {occurred_score}/5 as "
                f"matching their life (importance {e.get('impact_weight_score', 1.0)}){note}"
            )
    return "\n".join(lines) if lines else "(none)"


def _format_history(history: list[dict], limit: int = 3) -> str:
    if not history:
        return "(none -- this is the first rectification attempt for this profile)"
    lines = [
        f"- At {h['attempt_at']}, {h['provider']} concluded {h['best_ayanamsa']} / "
        f"{h['best_rectified_birth_time_24h']} at {h['best_confidence_score']:.0f}% confidence, "
        f"based on {h['events_used']} life events."
        for h in history[-limit:]
    ]
    return "\n".join(lines)


def _build_spouse_line(profile: dict) -> str:
    """Covers Divakar's spouse-correlation request: when the profile is
    married and has spouse birth details on file (app.models.BirthProfileCreate's
    is_married/spouse_* fields), tell the rectification LLM to cross-check
    marriage/children/parents-and-in-laws-health events against the
    SPOUSE's chart too, not just the primary person's -- a marriage or a
    child's birth is a shared event that should show a plausible timing
    signature in both charts, and an in-law's health is more directly
    tied to the spouse's own family significators than the primary
    person's.

    Returns "" (not an error) when the profile isn't married, or is
    married but spouse details aren't on file yet -- the latter is
    exactly the needs_spouse_details backward-compatibility case (see
    app.routers.profiles), which is surfaced to the user as a UI prompt
    rather than something this prompt should ask the LLM to chase."""
    if not profile.get("is_married"):
        return ""
    spouse_coords = profile.get("spouse_geographic_coordinates")
    spouse_date = profile.get("spouse_birth_date")
    spouse_time = profile.get("spouse_birth_time_24h")
    if not (spouse_coords and spouse_date and spouse_time):
        return ""
    return (
        f"\n\nThe user is married. Their spouse's stated birth details: born {spouse_date} at "
        f"{spouse_time} (local time), at latitude {spouse_coords['latitude']}, longitude "
        f"{spouse_coords['longitude']}, timezone offset {spouse_coords['timezone_offset_hours']} "
        "hours from UTC. When an event is MARRIAGE, FIRST_CHILD/KIDS, or a health concern "
        "involving the user's parents or in-laws (categories like PARENT_HEALTH/INLAW_HEALTH), "
        "also check how well it aligns with the SPOUSE's own dasha/bhukti periods at that time, "
        "not just the primary person's -- these are shared or spouse-linked events, so a good "
        "fit should show up on both sides. If the events include more than one MARRIAGE (or a "
        "DIVORCE), only one marriage is to the spouse above -- most likely the latest -- so do "
        "not expect the others to align with that spouse's chart or count them against a candidate. "
        "Use this only as supporting evidence for calibrating "
        "the PRIMARY person's birth time -- do not attempt to rectify the spouse's own birth "
        "time here."
    )


def _followups_instruction(today: str) -> str:
    """Shared between _build_prompt (full multi-provider run) and
    _build_recheck_prompt (cheap single-provider recheck once locked) --
    both want the same "suggest a few more events worth asking about"
    behavior, just as an add-on to a cheaper single-ayanamsa prompt in
    the recheck case. Kept as one shared string so the two prompts can't
    quietly drift apart on wording/constraints over time."""
    return (
        "Finally, suggest 2-3 more life events that would "
        "most help sharpen the estimate further. For each, give a short UPPER_SNAKE_CASE "
        "category slug, a natural ready-to-show question (e.g. category \"CHRONIC_ILLNESS\", "
        "question \"Have you had any major health diagnoses or surgeries?\"), and a "
        "time_frame_hint -- your best plain-language guess at when such an event would fall "
        "if your current estimate is correct, so the user can rate how much it resonates "
        "rather than needing to recall an exact date. Keep it TIGHT: a month-to-month range "
        "of at most about 6 months (never wider than 12), tied to the specific dasha/bhukti "
        "sub-period or transit that would produce the event, written like \"October 2013 to "
        "March 2014\". Do NOT answer with a multi-year span such as \"2013 to 2015\" -- a "
        "wide window fits almost any life and gives the user no reason to trust the reading. "
        "If the event could only be placed within a wider span, pick a different, more "
        "pinpointable event to ask about instead. Leave time_frame_hint as an empty string "
        f"only if you genuinely can't narrow it down. IMPORTANT: this is strictly backward-looking -- only ask about "
        f"events that would have already happened on or before {today}. Never suggest a "
        "question about something that would only happen in the future relative to today."
    )


def _build_prompt(profile: dict, events: list[dict], window_minutes: int, history: list[dict]) -> str:
    coords = profile["geographic_coordinates"]
    today = datetime.now(timezone.utc).date().isoformat()
    return (
        "You are an expert Vedic astrologer performing birth-time rectification. "
        f"Today's real-world date is {today} -- the user is answering these questions now, "
        "so every event you ask about must be something that could already have happened by "
        f"then.\n\n"
        f"The user states they were born on {profile['birth_date']} at {profile['birth_time_24h']} "
        f"(local time), at latitude {coords['latitude']}, longitude {coords['longitude']}, "
        f"timezone offset {coords['timezone_offset_hours']} hours from UTC. They are unsure of the "
        f"exact time within +/- {window_minutes} minutes of that stated time.\n\n"
        f"Their real life events, which should align with Vimshottari dasha/bhukti lord "
        f"transitions if the birth time and ayanamsa are correct:\n{_format_events(events)}"
        + _build_spouse_line(profile)
        + "\n\n"
        f"Prior rectification attempts for this profile:\n{_format_history(history)}\n\n"
        f"For each of these ayanamsas -- {', '.join(CURATED_AYANAMSAS)} -- reason about which "
        "exact birth time within the stated window best explains the life events via dasha/bhukti "
        "lord matching, and give that ayanamsa's best rectified time and your confidence (0-100) "
        "that it's correct. ALSO, for EACH ayanamsa individually (not just your overall favorite), "
        "give a short 1-2 sentence reasoning naming the SPECIFIC life events (by category and date/"
        "time_frame) that support that particular ayanamsa/time combination via dasha/bhukti "
        "matching -- e.g. 'Under this ayanamsa, MARRIAGE (2015) falls in a Venus bhukti and "
        "CAREER_CHANGE (2018) in a Mercury bhukti, both consistent with this time.' The user will "
        "be shown every ayanamsa's score and reasoning side by side, including ones you don't "
        "personally rank highest, so each one's reasoning needs to stand on its own rather than "
        "only explaining why it's worse than your top pick. Then explain your OVERALL reasoning "
        "(why your top pick is the best of the set) in at most 2-3 sentences -- keep it tight, not "
        "a full walkthrough. " + _followups_instruction(today) + " Prefer follow-up suggestions "
        "where the different ayanamsas/candidate birth times above imply DIFFERENT dasha/bhukti "
        "periods, so the answer can help distinguish between them; if every plausible event for "
        "a dasha/bhukti period you'd otherwise ask about falls after today, skip that period and "
        "pick a different one that's already in the past instead."
    )


def _build_recheck_prompt(
    profile: dict, events: list[dict], locked_ayanamsa: str, locked_time: str, history: list[dict]
) -> str:
    coords = profile["geographic_coordinates"]
    today = datetime.now(timezone.utc).date().isoformat()
    return (
        "You are an expert Vedic astrologer. A user's chart was previously rectified to "
        f"{locked_time} using the {locked_ayanamsa} ayanamsa (born {profile['birth_date']}, "
        f"stated time {profile['birth_time_24h']}, latitude {coords['latitude']}, "
        f"longitude {coords['longitude']}, timezone offset {coords['timezone_offset_hours']}).\n\n"
        f"Here is their full, current list of life events, including any added since that "
        f"rectification:\n{_format_events(events)}"
        + _build_spouse_line(profile)
        + "\n\n"
        f"Prior rectification attempts:\n{_format_history(history)}\n\n"
        f"Re-evaluate: using the {locked_ayanamsa} ayanamsa and a birth time close to "
        f"{locked_time}, how well do ALL of these events -- including the newest ones -- still "
        "fit via dasha/bhukti lord matching? Give your best refined birth time near the original, "
        "and an updated confidence score (0-100) reflecting the full event list. Explain your "
        "reasoning in at most 2-3 sentences -- keep it tight, not a full walkthrough. "
        + _followups_instruction(today)
    )


def _full_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "ayanamsa_results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "ayanamsa": {"type": "string", "enum": CURATED_AYANAMSAS},
                        "rectified_birth_time_24h": {"type": "string"},
                        "confidence_score": {"type": "number"},
                        "reasoning": {"type": "string"},
                    },
                    "required": ["ayanamsa", "rectified_birth_time_24h", "confidence_score", "reasoning"],
                    "additionalProperties": False,
                },
            },
            "reasoning": {"type": "string"},
            "suggested_followups": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string"},
                        "question_text": {"type": "string"},
                        "time_frame_hint": {"type": "string"},
                    },
                    "required": ["category", "question_text", "time_frame_hint"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["ayanamsa_results", "reasoning", "suggested_followups"],
        "additionalProperties": False,
    }


def _recheck_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "rectified_birth_time_24h": {"type": "string"},
            "confidence_score": {"type": "number"},
            "reasoning": {"type": "string"},
            # Still asked for even on the cheap, single-provider recheck
            # path (Divakar: locking shouldn't mean follow-up questions
            # stop being offered -- just that they stay cheap to get,
            # one provider instead of fanning out to all of them). See
            # DefectBacklog for the full writeup of this decision.
            "suggested_followups": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string"},
                        "question_text": {"type": "string"},
                        "time_frame_hint": {"type": "string"},
                    },
                    "required": ["category", "question_text", "time_frame_hint"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["rectified_birth_time_24h", "confidence_score", "reasoning", "suggested_followups"],
        "additionalProperties": False,
    }
