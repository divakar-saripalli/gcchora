import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.db import birth_profiles_collection, conversations_collection, life_events_collection
from app.models import BirthProfileCreate, BirthProfileOut, LifeEventIn
from app.security import get_current_user

router = APIRouter(prefix="/profiles", tags=["profiles"])


@router.post("", response_model=BirthProfileOut)
async def create_profile(payload: BirthProfileCreate, user: dict = Depends(get_current_user)):
    profile_id = str(uuid.uuid4())
    doc = {
        "_id": profile_id,
        "userId": user["_id"],
        "label": payload.label,
        "birth_date": payload.birth_date,
        "birth_time_24h": payload.birth_time_24h,
        "geographic_coordinates": payload.geographic_coordinates.model_dump(),
        "birth_time_uncertainty_minutes": payload.birth_time_uncertainty_minutes,
        "calibration": {"status": "uncalibrated"},
        "createdAt": datetime.now(timezone.utc),
    }
    await birth_profiles_collection().insert_one(doc)
    return BirthProfileOut(id=profile_id, **payload.model_dump(), calibration_status="uncalibrated")


@router.get("/{profile_id}")
async def get_profile(profile_id: str, user: dict = Depends(get_current_user)):
    profile = await birth_profiles_collection().find_one({"_id": profile_id, "userId": user["_id"]})
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


@router.post("/{profile_id}/events")
async def add_life_event(profile_id: str, payload: LifeEventIn, user: dict = Depends(get_current_user)):
    profile = await birth_profiles_collection().find_one({"_id": profile_id, "userId": user["_id"]})
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    event_id = str(uuid.uuid4())
    await life_events_collection().insert_one({
        "_id": event_id,
        "profileId": profile_id,
        "category": payload.category,
        "exact_date": payload.exact_date,
        "impact_weight_score": payload.impact_weight_score,
        "source": "user_provided",
        "createdAt": datetime.now(timezone.utc),
    })
    return {"id": event_id}
