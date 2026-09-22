"""Birth-time rectification: search ayanamsas x candidate birth times
against the profile's reported life events, ask disambiguating questions
when the leading candidates are too close to call, and finalize a
calibrated (ayanamsa, birth time) once one is decisively ahead.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.db import birth_profiles_collection, life_events_collection
from app.engines.significators import load_significator_matrix
from app.engines.vedic import generate_disambiguation_questions, rectify_birth_time
from app.models import RectifyAnswer, RectifyRequest, RectifyResult
from app.security import get_current_user

router = APIRouter(prefix="/profiles", tags=["rectification"])

# Candidates within this many points of the leader are considered "too
# close to call" and trigger disambiguation questions instead of an
# automatic finalize.
CONFIDENCE_MARGIN = 15.0


async def _run_rectification(profile_id: str, user_id: str, req: RectifyRequest) -> RectifyResult:
    profile = await birth_profiles_collection().find_one({"_id": profile_id, "userId": user_id})
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    events = await life_events_collection().find({"profileId": profile_id}).to_list(length=1000)
    if not events:
        raise HTTPException(
            status_code=400,
            detail="No life events recorded yet -- add at least one via POST /profiles/{id}/events before rectifying.",
        )

    matrix = await load_significator_matrix("vedic")
    window = req.time_window_minutes or profile.get("birth_time_uncertainty_minutes", 30)

    candidates = rectify_birth_time(
        birth_date=profile["birth_date"],
        stated_time_24h=profile["birth_time_24h"],
        tz_offset_hours=profile["geographic_coordinates"]["timezone_offset_hours"],
        events=events,
        significator_matrix=matrix,
        window_minutes=window,
        step_minutes=req.step_minutes,
        ayanamsas=req.ayanamsas,
    )

    best = candidates[0]
    runner_up_score = candidates[1]["confidence_score"] if len(candidates) > 1 else 0.0
    decisive = (best["confidence_score"] - runner_up_score) >= CONFIDENCE_MARGIN or best["confidence_score"] >= 95.0

    already_asked = {e["category"] for e in events}

    if decisive:
        calibration = {
            "status": "calibrated",
            "chosen_ayanamsa": best["ayanamsa"],
            "rectified_birth_time_24h": best["birth_time_24h"],
            "confidence_score": best["confidence_score"],
            "events_used": [e["_id"] for e in events],
            "calibrated_at": datetime.now(timezone.utc),
        }
        await birth_profiles_collection().update_one(
            {"_id": profile_id}, {"$set": {"calibration": calibration}}
        )
        return RectifyResult(
            status="calibrated",
            chosen_ayanamsa=best["ayanamsa"],
            rectified_birth_time_24h=best["birth_time_24h"],
            confidence_score=best["confidence_score"],
            candidates_considered=len(candidates),
        )

    questions = generate_disambiguation_questions(candidates, already_asked)
    await birth_profiles_collection().update_one(
        {"_id": profile_id},
        {"$set": {"calibration": {"status": "pending_questions", "pending_questions": questions}}},
    )
    return RectifyResult(
        status="pending_questions",
        candidates_considered=len(candidates),
        pending_questions=[{"category": q["category"], "question_text": q["question_text"]} for q in questions],
    )


@router.post("/{profile_id}/rectify", response_model=RectifyResult)
async def start_rectification(profile_id: str, req: RectifyRequest = RectifyRequest(), user: dict = Depends(get_current_user)):
    return await _run_rectification(profile_id, user["_id"], req)


@router.post("/{profile_id}/rectify/answer", response_model=RectifyResult)
async def answer_disambiguation(profile_id: str, answer: RectifyAnswer, user: dict = Depends(get_current_user)):
    profile = await birth_profiles_collection().find_one({"_id": profile_id, "userId": user["_id"]})
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    await life_events_collection().insert_one({
        "_id": f"{profile_id}-{answer.category}-{datetime.now(timezone.utc).timestamp()}",
        "profileId": profile_id,
        "category": answer.category,
        "exact_date": answer.exact_date,
        "impact_weight_score": answer.impact_weight_score,
        "source": "disambiguation_answer",
        "createdAt": datetime.now(timezone.utc),
    })
    return await _run_rectification(profile_id, user["_id"], RectifyRequest())


@router.get("/{profile_id}/rectify/status")
async def rectification_status(profile_id: str, user: dict = Depends(get_current_user)):
    profile = await birth_profiles_collection().find_one({"_id": profile_id, "userId": user["_id"]})
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile.get("calibration", {"status": "uncalibrated"})
