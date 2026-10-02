"""Birth-time rectification.

Where this used to work -- generating a grid of (ayanamsa, candidate
birth-time) pairs with app.engines.vedic and scoring each against the
profile's life events via app.engines.significators -- it now hands the
same underlying inputs (stated birth details + all recorded life events)
straight to the LLM and asks it to do that reasoning itself, across every
curated ayanamsa (app.ayanamsas.CURATED_AYANAMSAS), for each of multiple
providers. app/engines/vedic.py and app/engines/significators.py are left
on disk but are no longer imported here.

Flow:
  1. Not yet locked: fan the prompt out to every configured provider
     (app.llm_gateway.generate_structured_from_all), each evaluating all
     curated ayanamsas. If any provider+ayanamsa combination reports
     confidence_score >= LOCK_THRESHOLD, it locks in automatically -- no
     user rating needed, this is the LLM's own self-reported confidence.
     If not, the providers' best picks are returned side by side for the
     user to compare, optionally add a tiebreaker life event
     (POST .../rectify/answer, or just POST .../events directly), and
     rate via POST .../rectify/score.
  2. Locked: a single, cheap recheck call to just the locked provider,
     confirming (or, if it no longer fits, un-locking and falling back to
     a full re-run of step 1) the existing ayanamsa + time against the
     full current event list. See app.routers.profiles.add_life_event for
     how this also gets triggered automatically after enough new events
     accumulate without the user calling this endpoint themselves.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.ayanamsas import CURATED_AYANAMSAS
from app.db import birth_profiles_collection, life_events_collection, rectification_ratings_collection
from app.llm_gateway import generate_structured_answer, generate_structured_from_all
from app.models import (
    AyanamsaEstimate,
    DisambiguationQuestion,
    RectifyAnswer,
    RectifyCandidate,
    RectifyRequest,
    RectifyResult,
    RectifyScoreRequest,
)
from app.security import get_current_user

router = APIRouter(prefix="/profiles", tags=["rectification"])

# A provider+ayanamsa combination this confident locks in automatically.
LOCK_THRESHOLD = 95.0

# Once locked, a recheck against new events that comes back below this is
# treated as "doesn't fit anymore" and triggers unlocking + a full re-run,
# rather than quietly staying locked with degraded confidence. Above this
# but below LOCK_THRESHOLD, it just stays locked with an updated score --
# a single new event nudging confidence down a little shouldn't force a
# full re-comparison every time.
UNLOCK_THRESHOLD = 75.0

MODERATE_CONFIDENCE_THRESHOLD = 50.0

# Too few events makes any rectification -- deterministic or LLM-reasoned
# -- under-constrained. Also reused as the events-since-last-check bar for
# auto-rechecking a locked profile (see app.routers.profiles), so the two
# don't drift apart as separate magic numbers.
MIN_EVENTS_REQUIRED = 6
EVENTS_SINCE_LOCK_CHECK_THRESHOLD = MIN_EVENTS_REQUIRED

MAX_SUGGESTED_FOLLOWUPS = 3


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
    fall back to the time_frame_hint text in that case."""
    lines = []
    for e in events:
        occurred_score = e.get("occurred_score")
        when = e.get("exact_date") or e.get("time_frame_hint") or "an unknown date"
        note = f" -- {e['notes']}" if e.get("notes") else ""
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


def _build_prompt(profile: dict, events: list[dict], window_minutes: int, history: list[dict]) -> str:
    coords = profile["geographic_coordinates"]
    return (
        "You are an expert Vedic astrologer performing birth-time rectification. "
        f"The user states they were born on {profile['birth_date']} at {profile['birth_time_24h']} "
        f"(local time), at latitude {coords['latitude']}, longitude {coords['longitude']}, "
        f"timezone offset {coords['timezone_offset_hours']} hours from UTC. They are unsure of the "
        f"exact time within +/- {window_minutes} minutes of that stated time.\n\n"
        f"Their real life events, which should align with Vimshottari dasha/bhukti lord "
        f"transitions if the birth time and ayanamsa are correct:\n{_format_events(events)}\n\n"
        f"Prior rectification attempts for this profile:\n{_format_history(history)}\n\n"
        f"For each of these ayanamsas -- {', '.join(CURATED_AYANAMSAS)} -- reason about which "
        "exact birth time within the stated window best explains the life events via dasha/bhukti "
        "lord matching, and give that ayanamsa's best rectified time and your confidence (0-100) "
        "that it's correct. Then explain your overall reasoning in at most 2-3 sentences -- keep it "
        "tight, not a full walkthrough. Finally, suggest 2-3 more life events that would "
        "most help sharpen the estimate further -- prefer ones where the different "
        "ayanamsas/candidate birth times above imply DIFFERENT dasha/bhukti periods, so the "
        "answer can help distinguish between them. For each, give a short UPPER_SNAKE_CASE "
        "category slug, a natural ready-to-show question (e.g. category \"CHRONIC_ILLNESS\", "
        "question \"Have you had any major health diagnoses or surgeries?\"), and a "
        "time_frame_hint -- your best plain-language guess at when such an event would fall "
        "if one of the candidates above is correct (e.g. \"sometime between 2012 and "
        "2015\"), so the user can rate how much it resonates rather than needing to recall "
        "an exact date. Leave time_frame_hint as an empty string only if you genuinely "
        "can't narrow it down."
    )


def _build_recheck_prompt(
    profile: dict, events: list[dict], locked_ayanamsa: str, locked_time: str, history: list[dict]
) -> str:
    coords = profile["geographic_coordinates"]
    return (
        "You are an expert Vedic astrologer. A user's chart was previously rectified to "
        f"{locked_time} using the {locked_ayanamsa} ayanamsa (born {profile['birth_date']}, "
        f"stated time {profile['birth_time_24h']}, latitude {coords['latitude']}, "
        f"longitude {coords['longitude']}, timezone offset {coords['timezone_offset_hours']}).\n\n"
        f"Here is their full, current list of life events, including any added since that "
        f"rectification:\n{_format_events(events)}\n\n"
        f"Prior rectification attempts:\n{_format_history(history)}\n\n"
        f"Re-evaluate: using the {locked_ayanamsa} ayanamsa and a birth time close to "
        f"{locked_time}, how well do ALL of these events -- including the newest ones -- still "
        "fit via dasha/bhukti lord matching? Give your best refined birth time near the original, "
        "and an updated confidence score (0-100) reflecting the full event list. Explain your "
        "reasoning in at most 2-3 sentences -- keep it tight, not a full walkthrough."
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
                    },
                    "required": ["ayanamsa", "rectified_birth_time_24h", "confidence_score"],
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
        },
        "required": ["rectified_birth_time_24h", "confidence_score", "reasoning"],
        "additionalProperties": False,
    }


def _candidate_from_raw(provider: str, raw: dict) -> RectifyCandidate:
    ayanamsa_results = [
        AyanamsaEstimate(
            ayanamsa=r["ayanamsa"],
            rectified_birth_time_24h=r["rectified_birth_time_24h"],
            confidence_score=float(r["confidence_score"]),
        )
        for r in raw["ayanamsa_results"]
    ]
    best = max(ayanamsa_results, key=lambda r: r.confidence_score)
    return RectifyCandidate(
        provider=provider,
        ayanamsa_results=ayanamsa_results,
        best_ayanamsa=best.ayanamsa,
        best_rectified_birth_time_24h=best.rectified_birth_time_24h,
        best_confidence_score=best.confidence_score,
        reasoning=raw.get("reasoning", ""),
        # The model authors its own natural question text per category now
        # (see _build_prompt) instead of us title-casing whatever slug it
        # invents, which produced awkward results like "Can you add a
        # Exact Dates Of Vehicle Accidents Or Purchases event?". Falls
        # back to a templated question only if a provider (or an older
        # cached shape) omits question_text.
        suggested_followups=[
            DisambiguationQuestion(
                category=f["category"],
                question_text=f.get("question_text")
                or f"Can you tell me more about {f['category'].replace('_', ' ').lower()}?",
                time_frame_hint=f.get("time_frame_hint") or "",
            )
            for f in raw.get("suggested_followups", [])[:MAX_SUGGESTED_FOLLOWUPS]
        ],
    )


def _agree(a: RectifyCandidate, b: RectifyCandidate, tolerance_minutes: int = 15) -> bool:
    if a.best_ayanamsa != b.best_ayanamsa:
        return False
    fmt = "%H:%M:%S"
    try:
        ta = datetime.strptime(a.best_rectified_birth_time_24h, fmt)
        tb = datetime.strptime(b.best_rectified_birth_time_24h, fmt)
    except ValueError:
        return False
    return abs((ta - tb).total_seconds()) <= tolerance_minutes * 60


def _tier_text(confidence_score: float) -> str:
    if confidence_score >= LOCK_THRESHOLD:
        return "This is a strong, well-supported match."
    if confidence_score >= MODERATE_CONFIDENCE_THRESHOLD:
        return "This is a reasonable match, though not an overwhelming one."
    return "This is only a rough estimate at this point -- treat it as a starting point, not a final answer."


def _history_entry(candidate: RectifyCandidate, events_used: int) -> dict:
    return {
        "attempt_at": datetime.now(timezone.utc).isoformat(),
        "provider": candidate.provider,
        "best_ayanamsa": candidate.best_ayanamsa,
        "best_rectified_birth_time_24h": candidate.best_rectified_birth_time_24h,
        "best_confidence_score": candidate.best_confidence_score,
        "events_used": events_used,
    }


async def _lock_in(profile_id: str, candidate: RectifyCandidate, events_used: int, history: list[dict]) -> dict:
    calibration = {
        "status": "locked",
        "locked": True,
        "source_provider": candidate.provider,
        "chosen_ayanamsa": candidate.best_ayanamsa,
        "rectified_birth_time_24h": candidate.best_rectified_birth_time_24h,
        "confidence_score": candidate.best_confidence_score,
        "events_since_lock_check": 0,
        "history": history + [_history_entry(candidate, events_used)],
        "calibrated_at": datetime.now(timezone.utc),
        "locked_at": datetime.now(timezone.utc),
    }
    await birth_profiles_collection().update_one({"_id": profile_id}, {"$set": {"calibration": calibration}})
    return calibration


def _union_followups(candidates: list[RectifyCandidate]) -> list[DisambiguationQuestion]:
    seen: set[str] = set()
    out: list[DisambiguationQuestion] = []
    for c in candidates:
        for q in c.suggested_followups:
            if q.category not in seen:
                seen.add(q.category)
                out.append(q)
    return out


async def _run_locked_recheck(
    profile: dict, events: list[dict], calibration: dict, history: list[dict]
) -> RectifyResult:
    profile_id = profile["_id"]
    locked_provider = calibration["source_provider"]
    locked_ayanamsa = calibration["chosen_ayanamsa"]
    locked_time = calibration["rectified_birth_time_24h"]

    prompt = _build_recheck_prompt(profile, events, locked_ayanamsa, locked_time, history)
    raw = await generate_structured_answer(
        "You are helping recheck a previously calibrated birth chart.",
        prompt,
        _recheck_schema(),
        provider=locked_provider,
    )
    new_confidence = float(raw["confidence_score"])
    new_time = raw.get("rectified_birth_time_24h", locked_time)
    candidate = RectifyCandidate(
        provider=locked_provider,
        ayanamsa_results=[
            AyanamsaEstimate(
                ayanamsa=locked_ayanamsa, rectified_birth_time_24h=new_time, confidence_score=new_confidence
            )
        ],
        best_ayanamsa=locked_ayanamsa,
        best_rectified_birth_time_24h=new_time,
        best_confidence_score=new_confidence,
        reasoning=raw.get("reasoning", ""),
    )

    if new_confidence >= UNLOCK_THRESHOLD:
        await birth_profiles_collection().update_one(
            {"_id": profile_id},
            {
                "$set": {
                    "calibration.rectified_birth_time_24h": new_time,
                    "calibration.confidence_score": new_confidence,
                    "calibration.events_since_lock_check": 0,
                    "calibration.history": history + [_history_entry(candidate, len(events))],
                }
            },
        )
        return RectifyResult(
            status="locked",
            chosen_provider=locked_provider,
            chosen_ayanamsa=locked_ayanamsa,
            rectified_birth_time_24h=new_time,
            confidence_score=new_confidence,
            events_considered=len(events),
            message=(
                f"Your new events still fit the locked {locked_ayanamsa} / {new_time} calibration "
                f"({new_confidence:.0f}% confidence). No change needed."
            ),
        )

    # Doesn't fit anymore -- unlock and fall back to a full re-run, which
    # will re-fetch the (now unlocked) profile and go through the normal
    # multi-provider comparison below.
    await birth_profiles_collection().update_one(
        {"_id": profile_id},
        {
            "$set": {
                "calibration.locked": False,
                "calibration.status": "pending_review",
                "calibration.events_since_lock_check": 0,
                "calibration.history": history + [_history_entry(candidate, len(events))],
            }
        },
    )
    return await run_rectification(profile_id, profile["userId"], RectifyRequest())


async def run_rectification(profile_id: str, user_id: str, req: RectifyRequest) -> RectifyResult:
    profile = await birth_profiles_collection().find_one({"_id": profile_id, "userId": user_id})
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    events = await life_events_collection().find({"profileId": profile_id}).to_list(length=1000)
    if len(events) < MIN_EVENTS_REQUIRED:
        raise HTTPException(
            status_code=400,
            detail=(
                f"At least {MIN_EVENTS_REQUIRED} life events are needed before calibrating "
                f"(you have {len(events)}). Add a few more via POST /profiles/{{id}}/events, "
                "then try again."
            ),
        )

    calibration = profile.get("calibration", {})
    history = calibration.get("history", [])

    if calibration.get("locked"):
        return await _run_locked_recheck(profile, events, calibration, history)

    window = req.time_window_minutes or profile.get("birth_time_uncertainty_minutes", 30)
    prompt = _build_prompt(profile, events, window, history)
    raw_by_provider, provider_errors = await generate_structured_from_all(
        "You are helping calibrate a birth chart.", prompt, _full_schema()
    )
    if not raw_by_provider:
        detail = "No LLM provider is currently available to rectify this chart."
        if provider_errors:
            detail += " " + "; ".join(f"{p}: {reason}" for p, reason in provider_errors.items())
        raise HTTPException(status_code=502, detail=detail)

    candidates = [_candidate_from_raw(provider, raw) for provider, raw in raw_by_provider.items()]
    candidates.sort(key=lambda c: c.best_confidence_score, reverse=True)
    top = candidates[0]

    if top.best_confidence_score >= LOCK_THRESHOLD:
        await _lock_in(profile_id, top, len(events), history)
        return RectifyResult(
            status="locked",
            candidates=[top],
            chosen_provider=top.provider,
            chosen_ayanamsa=top.best_ayanamsa,
            rectified_birth_time_24h=top.best_rectified_birth_time_24h,
            confidence_score=top.best_confidence_score,
            events_considered=len(events),
            message=(
                f"{top.provider} calibrated your chart to {top.best_rectified_birth_time_24h} using the "
                f"{top.best_ayanamsa} ayanamsa at {top.best_confidence_score:.0f}% confidence -- high "
                "enough to lock in automatically. Future updates will only recheck this configuration "
                "rather than comparing providers again, unless new events stop fitting it."
            ),
        )

    agreement = None
    if len(candidates) > 1:
        agreement = "agree" if _agree(candidates[0], candidates[1]) else "disagree"

    await birth_profiles_collection().update_one(
        {"_id": profile_id},
        {
            "$set": {
                "calibration.status": "pending_review",
                "calibration.pending_candidates": [c.model_dump() for c in candidates],
            }
        },
    )

    tier = _tier_text(top.best_confidence_score)
    agreement_text = {"agree": "agree", "disagree": "disagree"}.get(agreement, "there's only one to show")
    return RectifyResult(
        status="pending_review",
        candidates=candidates,
        agreement=agreement,
        # Always carry the current best guess at the top level too, even
        # though the user hasn't rated/confirmed it yet -- not just when
        # multiple candidates exist. With only one provider configured
        # (no OPENAI_API_KEY), this fan-out only ever produces a single
        # candidate, so the mobile app's "needs comparison" check
        # (candidates.length > 1) is false and it renders the plain
        # result card instead of the rating UI. That card reads these
        # exact top-level fields -- leaving them null here (as before)
        # meant it silently showed "0% confidence" and "-" for
        # ayanamsa/time/source instead of the real numbers.
        chosen_provider=top.provider,
        chosen_ayanamsa=top.best_ayanamsa,
        rectified_birth_time_24h=top.best_rectified_birth_time_24h,
        confidence_score=top.best_confidence_score,
        events_considered=len(events),
        message=(
            f"{len(candidates)} provider(s) evaluated your chart; their top picks {agreement_text}. "
            f"{tier} None reached the {LOCK_THRESHOLD:.0f}% bar for an automatic lock -- review the "
            "candidates, optionally add one more life event to help you judge them, then rate each one."
        ),
        suggested_followups=_union_followups(candidates),
    )


@router.post("/{profile_id}/rectify", response_model=RectifyResult)
async def start_rectification(
    profile_id: str, req: RectifyRequest = RectifyRequest(), user: dict = Depends(get_current_user)
):
    return await run_rectification(profile_id, user["_id"], req)


@router.post("/{profile_id}/rectify/answer", response_model=RectifyResult)
async def add_followup_and_rectify(profile_id: str, answer: RectifyAnswer, user: dict = Depends(get_current_user)):
    """Convenience endpoint: add one more life event (typically for one of
    the `suggested_followups` categories) and immediately re-run
    rectification in a single call. Purely a shortcut -- POST an event via
    /profiles/{id}/events and POST /profiles/{id}/rectify separately
    achieves the same thing."""
    profile = await birth_profiles_collection().find_one({"_id": profile_id, "userId": user["_id"]})
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    await life_events_collection().insert_one({
        "_id": f"{profile_id}-{answer.category}-{datetime.now(timezone.utc).timestamp()}",
        "profileId": profile_id,
        "category": answer.category,
        "exact_date": answer.exact_date,
        "time_frame_hint": answer.time_frame_hint,
        "occurred_score": answer.occurred_score,
        # Scale importance by how strongly the user says this resonates,
        # rather than always weighting it the same as a normal manually-
        # logged event -- a confident "yes, exactly" (5/5) counts fully;
        # a shaky "maybe, a little" (3/5) counts for less; a "no" (1-2/5)
        # is kept as negative evidence in the prompt itself (see
        # _format_events) rather than a real event, so it's weighted
        # near zero here.
        "impact_weight_score": round(answer.occurred_score / 5.0, 2),
        "notes": answer.notes,
        "source": "suggested_followup_answer",
        "createdAt": datetime.now(timezone.utc),
    })
    return await run_rectification(profile_id, user["_id"], RectifyRequest())


@router.post("/{profile_id}/rectify/score", response_model=RectifyResult)
async def score_candidates(profile_id: str, req: RectifyScoreRequest, user: dict = Depends(get_current_user)):
    """User rates each pending candidate 1-5; the highest-rated one becomes
    the stored calibration. Only meaningful while calibration.status ==
    "pending_review" (i.e. no candidate crossed LOCK_THRESHOLD on its own
    during the last /rectify call)."""
    profile = await birth_profiles_collection().find_one({"_id": profile_id, "userId": user["_id"]})
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    calibration = profile.get("calibration", {})
    pending = calibration.get("pending_candidates")
    if not pending:
        raise HTTPException(
            status_code=400, detail="No pending rectification candidates to score. Run /rectify first."
        )

    ratings_by_provider = {r.provider: r.score for r in req.ratings}
    await rectification_ratings_collection().insert_one({
        "_id": f"{profile_id}-{datetime.now(timezone.utc).timestamp()}",
        "profileId": profile_id,
        "userId": user["_id"],
        "ratings": ratings_by_provider,
        "createdAt": datetime.now(timezone.utc),
    })

    scored = [c for c in pending if c["provider"] in ratings_by_provider]
    if not scored:
        raise HTTPException(status_code=400, detail="None of the rated providers match the pending candidates.")
    winner = RectifyCandidate(**max(scored, key=lambda c: ratings_by_provider[c["provider"]]))

    events = await life_events_collection().find({"profileId": profile_id}).to_list(length=1000)
    history = calibration.get("history", [])

    if winner.best_confidence_score >= LOCK_THRESHOLD:
        new_calibration = await _lock_in(profile_id, winner, len(events), history)
    else:
        new_calibration = {
            "status": "calibrated",
            "locked": False,
            "source_provider": winner.provider,
            "chosen_ayanamsa": winner.best_ayanamsa,
            "rectified_birth_time_24h": winner.best_rectified_birth_time_24h,
            "confidence_score": winner.best_confidence_score,
            "history": history + [_history_entry(winner, len(events))],
            "calibrated_at": datetime.now(timezone.utc),
        }
        await birth_profiles_collection().update_one({"_id": profile_id}, {"$set": {"calibration": new_calibration}})

    tier = _tier_text(winner.best_confidence_score)
    return RectifyResult(
        status=new_calibration["status"],
        chosen_provider=winner.provider,
        chosen_ayanamsa=winner.best_ayanamsa,
        rectified_birth_time_24h=winner.best_rectified_birth_time_24h,
        confidence_score=winner.best_confidence_score,
        events_considered=len(events),
        message=(
            f"You rated {winner.provider}'s reading highest. Calibrated to "
            f"{winner.best_rectified_birth_time_24h} using the {winner.best_ayanamsa} ayanamsa at "
            f"{winner.best_confidence_score:.0f}% confidence. {tier}"
        ),
    )


@router.get("/{profile_id}/rectify/status")
async def rectification_status(profile_id: str, user: dict = Depends(get_current_user)):
    profile = await birth_profiles_collection().find_one({"_id": profile_id, "userId": user["_id"]})
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile.get("calibration", {"status": "uncalibrated"})
