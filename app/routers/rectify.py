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
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.db_util import capped_list
from app.db import birth_profiles_collection, life_events_collection, rectification_ratings_collection
from app.event_ordering import sort_events_chronologically
from app.llm_gateway import generate_structured_answer, generate_structured_from_all
from app.quota import consume_rectify_run, refund_rectify_run
from app.rate_limit import limit_per_user
from app.models import (
    AyanamsaEstimate,
    DisambiguationQuestion,
    RectifyAnswer,
    RectifyAyanamsaChoice,
    RectifyCandidate,
    RectifyRequest,
    RectifyResult,
    RectifyScoreRequest,
)
from app.security import get_current_user
# Some of these names are not used in this module directly; they are
# imported so existing references such as
# `app.routers.rectify.EVENTS_SINCE_LOCK_CHECK_THRESHOLD` keep working.
from app.rectify_prompts import (  # noqa: F401
    _build_prompt,
    _build_recheck_prompt,
    _build_spouse_line,
    _followups_instruction,
    _format_events,
    _format_history,
    _full_schema,
    _recheck_schema,
)
from app.rectify_scoring import (  # noqa: F401
    EVENTS_SINCE_LOCK_CHECK_THRESHOLD,
    LOCK_THRESHOLD,
    MAX_SUGGESTED_FOLLOWUPS,
    MIN_EVENTS_REQUIRED,
    MODERATE_CONFIDENCE_THRESHOLD,
    UNLOCK_THRESHOLD,
    _agree,
    _candidate_from_raw,
    _history_entry,
    _is_future_only_hint,
    _keep_followup_or_log,
    _tier_text,
    _union_followups,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/profiles", tags=["rectification"])


# Fields only meaningful while a pending-review batch is open; cleared when
# a calibration is settled (the old whole-document replace did this
# implicitly).
_PENDING_FIELDS = {
    "calibration.pending_candidates": "",
    "calibration.answered_followup_categories": "",
    "calibration.locked_followups": "",
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
        "calibrated_at": datetime.now(timezone.utc),
        "locked_at": datetime.now(timezone.utc),
    }
    entry = _history_entry(candidate, events_used)
    # Replace the calibration fields individually and APPEND to history
    # with $push, so two concurrent calls can't overwrite each other's
    # history entries (read-modify-write of the whole list used to).
    await birth_profiles_collection().update_one(
        {"_id": profile_id},
        {
            "$set": {f"calibration.{k}": v for k, v in calibration.items()},
            "$push": {"calibration.history": entry},
            "$unset": _PENDING_FIELDS,
        },
    )
    return {**calibration, "history": history + [entry]}


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
    # Still asked for, and still filtered through the same future-dated
    # backstop as the full multi-provider path, even though this is the
    # cheap single-provider recheck -- see DefectBacklog: locking a chart
    # shouldn't silently stop follow-up questions from ever being
    # offered again, just keep them cheap (one provider, not a fan-out).
    followups = [
        DisambiguationQuestion(
            category=f["category"],
            question_text=f.get("question_text")
            or f"Can you tell me more about {f['category'].replace('_', ' ').lower()}?",
            time_frame_hint=f.get("time_frame_hint") or "",
        )
        for f in raw.get("suggested_followups", [])
        if _keep_followup_or_log(locked_provider, f)
    ][:MAX_SUGGESTED_FOLLOWUPS]
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
        suggested_followups=followups,
    )

    if new_confidence >= UNLOCK_THRESHOLD:
        await birth_profiles_collection().update_one(
            {"_id": profile_id},
            {
                "$set": {
                    "calibration.rectified_birth_time_24h": new_time,
                    "calibration.confidence_score": new_confidence,
                    "calibration.events_since_lock_check": 0,
                    # The batch of follow-ups just offered: kept so that
                    # answering them is batched (recorded one by one, ONE
                    # recheck once all are answered) instead of every
                    # answer triggering a recheck that replaces the rest.
                    "calibration.locked_followups": [f.model_dump() for f in followups],
                    "calibration.answered_followup_categories": [],
                },
                "$push": {"calibration.history": _history_entry(candidate, len(events))},
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
            suggested_followups=followups,
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
            },
            "$push": {"calibration.history": _history_entry(candidate, len(events))},
            "$unset": {"calibration.locked_followups": ""},
        },
    )
    return await run_rectification(profile_id, profile["userId"], RectifyRequest())


async def run_rectification(profile_id: str, user_id: str, req: RectifyRequest) -> RectifyResult:
    profile = await birth_profiles_collection().find_one({"_id": profile_id, "userId": user_id})
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    events = await capped_list(life_events_collection().find({"profileId": profile_id}), 1000, "life events")
    # Sorted by when each event actually happened, not insertion order --
    # otherwise e.g. an earlier-born child logged AFTER a younger
    # sibling's event would be presented to the model in the wrong
    # sequence, which matters for anything that depends on ordering
    # (which child is older, which job came first, etc). See
    # app.event_ordering. Covers both branches below: the full
    # multi-provider prompt and the locked-profile recheck.
    events = sort_events_chronologically(events)
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
                # Fresh batch of suggested_followups just got generated (see
                # _union_followups below) -- reset the answered-so-far
                # tracking used by add_followup_and_rectify so it doesn't
                # immediately re-run a full fan-out the moment just ONE of
                # them gets answered. See that function's docstring.
                "calibration.answered_followup_categories": [],
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


# At most this many rectification calls per user per minute, on top of
# the daily cap in app/quota.py -- stops a retry loop or a script from
# firing a burst of multi-provider LLM calls.
_rectify_rate_limit = limit_per_user("rectify", max_calls=6, window_seconds=60)


async def _metered_rectification(profile_id: str, user_id: str, req: RectifyRequest) -> RectifyResult:
    """run_rectification for a user-initiated call: counted against the
    user's daily cap (app.quota.consume_rectify_run), and given back if
    the run fails before producing a result (too few events, provider
    outage), so a failure doesn't use up the allowance."""
    await consume_rectify_run(user_id)
    try:
        return await run_rectification(profile_id, user_id, req)
    except Exception:
        await refund_rectify_run(user_id)
        raise


async def background_recheck(profile_id: str, user_id: str) -> None:
    """Best-effort recheck run after a response has already been sent
    (e.g. a life event that crossed the locked-profile staleness
    threshold). Metered like a user-initiated run; any failure -- cap
    reached, provider outage -- is logged WITHOUT detail and swallowed,
    since nobody is waiting on the result."""
    try:
        await _metered_rectification(profile_id, user_id, RectifyRequest())
    except Exception as exc:
        logger.warning("Background rectification recheck skipped (%s).", type(exc).__name__)


@router.post("/{profile_id}/rectify", response_model=RectifyResult)
async def start_rectification(
    profile_id: str, req: RectifyRequest = RectifyRequest(), user: dict = Depends(_rectify_rate_limit)
):
    return await _metered_rectification(profile_id, user["_id"], req)


@router.post("/{profile_id}/rectify/answer", response_model=RectifyResult)
async def add_followup_and_rectify(profile_id: str, answer: RectifyAnswer, user: dict = Depends(_rectify_rate_limit)):
    """Adds one more life event (typically for one of the
    `suggested_followups` categories) and, once the WHOLE current batch of
    suggested follow-ups has been answered, re-runs rectification with all
    of them at once.

    This used to re-run rectification (a full multi-provider fan-out, if
    the profile isn't locked yet) immediately after every single answer.
    That was both wasteful -- one fan-out per answer instead of one for
    the whole batch -- and confusing: run_rectification generates a BRAND
    NEW set of suggested_followups each time it runs, so answering
    question 1 of a 3-question batch would immediately replace questions
    2 and 3 with a different batch, making it look like they'd been
    skipped/lost rather than simply not gotten to yet (this is what
    Divakar flagged as feeling like "missed out" on the other questions).

    Now: the event is always recorded immediately. Whether a recalibration
    actually runs depends on how many of the CURRENT batch (the
    `suggested_followups` from the last real rectification run, tracked
    via `calibration.pending_candidates`) are still unanswered:
      - If this answer completes the batch (or there's no tracked batch to
        begin with, e.g. the very first rectification, or the profile is
        locked and therefore never generates a batch at all), it falls
        through to a real `run_rectification` call below, same as before.
      - Otherwise, no LLM call happens at all -- just records progress and
        returns the SAME comparison as before, with the still-pending
        questions trimmed down to what's actually left, so the user can
        keep answering them one at a time without the list getting
        replaced out from under them.
    A "Recalibrate now" action (POST /{profile_id}/rectify, unconditional)
    remains available for anyone who'd rather not answer the rest of the
    batch and just wants to proceed with what they've given so far.
    """
    profile = await birth_profiles_collection().find_one({"_id": profile_id, "userId": user["_id"]})
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    await life_events_collection().insert_one({
        "_id": f"{profile_id}-{answer.category}-{datetime.now(timezone.utc).timestamp()}",
        "profileId": profile_id,
        "category": answer.category,
        "exact_date": answer.exact_date,
        "time_frame_hint": answer.time_frame_hint,
        "question_text": answer.question_text,
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

    calibration = profile.get("calibration", {})

    # Locked profile: same batching as below, over the follow-ups its last
    # (cheap) recheck offered -- otherwise each answer would trigger a
    # recheck that generates a different set of questions.
    locked_raw = calibration.get("locked_followups") or []
    if calibration.get("locked") and locked_raw:
        batch = [DisambiguationQuestion(**f) for f in locked_raw]
        in_batch = answer.category in {f.category for f in batch}
        answered = set(calibration.get("answered_followup_categories") or [])
        if in_batch:
            answered.add(answer.category)
        remaining = [f for f in batch if f.category not in answered]
        if remaining:
            if in_batch:
                await birth_profiles_collection().update_one(
                    {"_id": profile_id},
                    {"$addToSet": {"calibration.answered_followup_categories": answer.category}},
                )
            events_considered = len(
                await capped_list(life_events_collection().find({"profileId": profile_id}), 1000, "life events")
            )
            return RectifyResult(
                status="locked",
                chosen_provider=calibration.get("source_provider"),
                chosen_ayanamsa=calibration.get("chosen_ayanamsa"),
                rectified_birth_time_24h=calibration.get("rectified_birth_time_24h"),
                confidence_score=calibration.get("confidence_score"),
                events_considered=events_considered,
                message=(
                    f"Thanks -- recorded. {len(remaining)} more question"
                    f"{'s' if len(remaining) != 1 else ''} from this batch whenever you're ready; "
                    "the check runs once they're all answered, or tap \"Check again now\" to run it early."
                ),
                suggested_followups=remaining,
            )
        # Whole batch answered -- fall through to one recheck covering all of it.

    pending_candidates_raw = calibration.get("pending_candidates") or []
    if calibration.get("status") == "pending_review" and pending_candidates_raw:
        candidates = [RectifyCandidate(**c) for c in pending_candidates_raw]
        current_batch = _union_followups(candidates)
        batch_categories = {f.category for f in current_batch}
        answered = set(calibration.get("answered_followup_categories") or [])
        if answer.category in batch_categories:
            answered.add(answer.category)
        remaining = [f for f in current_batch if f.category not in answered]

        if remaining:
            if answer.category in batch_categories:
                await birth_profiles_collection().update_one(
                    {"_id": profile_id},
                    {"$addToSet": {"calibration.answered_followup_categories": answer.category}},
                )
            agreement = None
            if len(candidates) > 1:
                agreement = "agree" if _agree(candidates[0], candidates[1]) else "disagree"
            top = max(candidates, key=lambda c: c.best_confidence_score)
            events_considered = len(
                await capped_list(life_events_collection().find({"profileId": profile_id}), 1000, "life events")
            )
            return RectifyResult(
                status="pending_review",
                candidates=candidates,
                agreement=agreement,
                chosen_provider=top.provider,
                chosen_ayanamsa=top.best_ayanamsa,
                rectified_birth_time_24h=top.best_rectified_birth_time_24h,
                confidence_score=top.best_confidence_score,
                events_considered=events_considered,
                message=(
                    f"Thanks -- recorded. {len(remaining)} more question"
                    f"{'s' if len(remaining) != 1 else ''} from this batch whenever you're ready; "
                    "recalibration runs automatically once they're all answered, or you can "
                    "recalibrate now with what you've given so far."
                ),
                suggested_followups=remaining,
            )
        # Batch fully answered (or this category wasn't part of it, which
        # shouldn't normally happen) -- fall through to a real rerun,
        # which will fold in every event from the batch at once.

    return await _metered_rectification(profile_id, user["_id"], RectifyRequest())


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

    events = await capped_list(life_events_collection().find({"profileId": profile_id}), 1000, "life events")
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
            "calibrated_at": datetime.now(timezone.utc),
        }
        await birth_profiles_collection().update_one(
            {"_id": profile_id},
            {
                "$set": {f"calibration.{k}": v for k, v in new_calibration.items()},
                "$push": {"calibration.history": _history_entry(winner, len(events))},
                "$unset": _PENDING_FIELDS,
            },
        )

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


@router.post("/{profile_id}/rectify/choose-ayanamsa", response_model=RectifyResult)
async def choose_ayanamsa(profile_id: str, req: RectifyAyanamsaChoice, user: dict = Depends(get_current_user)):
    """Directly locks to ONE specific (provider, ayanamsa) combination
    from the last rectification run, instead of only ever being able to
    accept whichever ayanamsa a provider itself ranked best.

    Every curated ayanamsa was already scored during the last /rectify
    call (see _full_schema's ayanamsa_results, now with its own
    `reasoning` per ayanamsa) -- the mobile app shows all of them, with
    their scores and reasoning, so the user can expand a lower-ranked
    one and judge for themselves whether it actually fits their life
    events better than the provider's own top pick. This endpoint is how
    that choice gets applied.

    Unlike /rectify/score (which gates on LOCK_THRESHOLD and may leave
    the result at "calibrated" rather than "locked" for a middling
    score), an explicit choice here always locks immediately -- the user
    deliberately picked this one after reading why it fits, so there's
    no confidence threshold to second-guess them with."""
    profile = await birth_profiles_collection().find_one({"_id": profile_id, "userId": user["_id"]})
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    calibration = profile.get("calibration", {})
    pending = calibration.get("pending_candidates")
    if not pending:
        raise HTTPException(
            status_code=400, detail="No pending rectification candidates to choose from. Run /rectify first."
        )

    candidate_raw = next((c for c in pending if c["provider"] == req.provider), None)
    if candidate_raw is None:
        raise HTTPException(status_code=404, detail=f"No pending candidate from provider '{req.provider}'.")
    candidate = RectifyCandidate(**candidate_raw)
    chosen = next((r for r in candidate.ayanamsa_results if r.ayanamsa == req.ayanamsa), None)
    if chosen is None:
        raise HTTPException(
            status_code=404,
            detail=f"Provider '{req.provider}' has no ayanamsa result named '{req.ayanamsa}'.",
        )

    # Override just the "best" fields to the user's explicit pick --
    # keeps provider/ayanamsa_results/suggested_followups intact for
    # history/record-keeping, and prefers this ayanamsa's OWN reasoning
    # (why THIS one fits) over the candidate's overall "why my top pick
    # is best" reasoning, which wouldn't make sense here if the user
    # picked something other than that top pick.
    chosen_candidate = candidate.model_copy(update={
        "best_ayanamsa": chosen.ayanamsa,
        "best_rectified_birth_time_24h": chosen.rectified_birth_time_24h,
        "best_confidence_score": chosen.confidence_score,
        "reasoning": chosen.reasoning or candidate.reasoning,
    })

    await rectification_ratings_collection().insert_one({
        "_id": f"{profile_id}-{datetime.now(timezone.utc).timestamp()}",
        "profileId": profile_id,
        "userId": user["_id"],
        "ratings": {},
        "manual_ayanamsa_choice": {"provider": req.provider, "ayanamsa": req.ayanamsa},
        "createdAt": datetime.now(timezone.utc),
    })

    events = await capped_list(life_events_collection().find({"profileId": profile_id}), 1000, "life events")
    history = calibration.get("history", [])
    await _lock_in(profile_id, chosen_candidate, len(events), history)

    tier = _tier_text(chosen_candidate.best_confidence_score)
    return RectifyResult(
        status="locked",
        chosen_provider=chosen_candidate.provider,
        chosen_ayanamsa=chosen_candidate.best_ayanamsa,
        rectified_birth_time_24h=chosen_candidate.best_rectified_birth_time_24h,
        confidence_score=chosen_candidate.best_confidence_score,
        events_considered=len(events),
        message=(
            f"Locked to the {chosen_candidate.best_ayanamsa} ayanamsa at "
            f"{chosen_candidate.best_rectified_birth_time_24h} ({chosen_candidate.best_confidence_score:.0f}% "
            f"confidence per {chosen_candidate.provider}), as you chose. {tier}"
        ),
    )


@router.get("/{profile_id}/rectify/status")
async def rectification_status(profile_id: str, user: dict = Depends(get_current_user)):
    profile = await birth_profiles_collection().find_one({"_id": profile_id, "userId": user["_id"]})
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile.get("calibration", {"status": "uncalibrated"})
