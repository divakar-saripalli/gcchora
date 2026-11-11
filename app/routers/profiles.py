import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.db import birth_profiles_collection, conversations_collection, life_events_collection
from app.models import (
    BirthProfileCreate,
    BirthProfileOut,
    BirthProfileUpdate,
    LifeEventAddedResponse,
    LifeEventIn,
    LifeEventOut,
    LifeEventUpdate,
)
from app.security import get_current_user
from app.timezone_lookup import compute_timezone_offset_hours

router = APIRouter(prefix="/profiles", tags=["profiles"])


@router.post("", response_model=BirthProfileOut)
async def create_profile(payload: BirthProfileCreate, user: dict = Depends(get_current_user)):
    profile_id = str(uuid.uuid4())
    coordinates = payload.geographic_coordinates.model_dump()
    # Always derive the real, DST-aware UTC offset from the geocoded
    # location + birth date/time server-side -- never trust whatever the
    # client sent (a manually-typed field with no way to know historical
    # DST rules). Falls back to the client-supplied value only if lookup
    # genuinely fails (e.g. coordinates over open ocean), so profile
    # creation is never blocked by this. See app.timezone_lookup.
    computed_offset = compute_timezone_offset_hours(
        coordinates["latitude"], coordinates["longitude"], payload.birth_date, payload.birth_time_24h
    )
    if computed_offset is not None:
        coordinates["timezone_offset_hours"] = computed_offset
    doc = {
        "_id": profile_id,
        "userId": user["_id"],
        "label": payload.label,
        "birth_date": payload.birth_date,
        "birth_time_24h": payload.birth_time_24h,
        "geographic_coordinates": coordinates,
        "birth_time_uncertainty_minutes": payload.birth_time_uncertainty_minutes,
        "calibration": {"status": "uncalibrated"},
        "createdAt": datetime.now(timezone.utc),
    }
    await birth_profiles_collection().insert_one(doc)
    out_payload = payload.model_dump()
    out_payload["geographic_coordinates"] = coordinates
    return BirthProfileOut(id=profile_id, **out_payload, calibration_status="uncalibrated")


@router.get("/{profile_id}")
async def get_profile(profile_id: str, user: dict = Depends(get_current_user)):
    profile = await birth_profiles_collection().find_one({"_id": profile_id, "userId": user["_id"]})
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


@router.patch("/{profile_id}", response_model=BirthProfileOut)
async def update_profile(profile_id: str, payload: BirthProfileUpdate, user: dict = Depends(get_current_user)):
    """Updates an existing profile in place -- unlike POST /profiles, this
    does NOT create a new profile_id, so the profile's life_events and
    (unless chart-affecting fields changed) calibration stay attached.
    Only the fields present in the request body are changed."""
    profile = await birth_profiles_collection().find_one({"_id": profile_id, "userId": user["_id"]})
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    updates = payload.model_dump(exclude_unset=True, exclude_none=True)
    if "geographic_coordinates" in updates:
        updates["geographic_coordinates"] = payload.geographic_coordinates.model_dump()

    chart_affecting = {"birth_date", "birth_time_24h", "geographic_coordinates"}
    changed_chart_fields = any(field in updates and updates[field] != profile.get(field) for field in chart_affecting)

    # Any chart-affecting field changing means the UTC offset needs
    # recomputing against the *final* (post-merge) date/time/location --
    # not just whichever one field this particular PATCH happened to
    # touch. Same reasoning and fallback behavior as create_profile; see
    # app.timezone_lookup.
    if changed_chart_fields:
        merged_coords = updates.get("geographic_coordinates", profile.get("geographic_coordinates", {}))
        merged_date = updates.get("birth_date", profile.get("birth_date"))
        merged_time = updates.get("birth_time_24h", profile.get("birth_time_24h"))
        if merged_coords.get("latitude") is not None and merged_coords.get("longitude") is not None:
            computed_offset = compute_timezone_offset_hours(
                merged_coords["latitude"], merged_coords["longitude"], merged_date, merged_time
            )
            if computed_offset is not None:
                merged_coords = dict(merged_coords)
                merged_coords["timezone_offset_hours"] = computed_offset
                updates["geographic_coordinates"] = merged_coords

    if updates:
        await birth_profiles_collection().update_one({"_id": profile_id}, {"$set": updates})
        profile.update(updates)

    # Birth details changed -- any existing calibration was computed
    # against the old ones and is no longer trustworthy. Reset it (the
    # user can re-run /rectify), but leave life_events alone: those
    # describe real events in the person's life, unaffected by a
    # corrected birth time or location.
    if changed_chart_fields and profile.get("calibration", {}).get("status", "uncalibrated") != "uncalibrated":
        profile["calibration"] = {"status": "uncalibrated"}
        await birth_profiles_collection().update_one(
            {"_id": profile_id}, {"$set": {"calibration": profile["calibration"]}}
        )

    return BirthProfileOut(
        id=profile_id,
        label=profile["label"],
        birth_date=profile["birth_date"],
        birth_time_24h=profile["birth_time_24h"],
        geographic_coordinates=profile["geographic_coordinates"],
        birth_time_uncertainty_minutes=profile.get("birth_time_uncertainty_minutes", 30),
        calibration_status=profile.get("calibration", {}).get("status", "uncalibrated"),
    )


@router.post("/{profile_id}/events", response_model=LifeEventAddedResponse)
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
        "notes": payload.notes,
        "source": "user_provided",
        "createdAt": datetime.now(timezone.utc),
    })

    # If this profile's calibration is locked, count new events toward the
    # bounded-staleness check: once enough have piled up since the last
    # check, automatically re-run rectification right here (rather than
    # waiting for the user to notice and call /rectify themselves) so a
    # lock can't silently drift out of fit with events that don't
    # actually match it. See app.routers.rectify.run_rectification /
    # EVENTS_SINCE_LOCK_CHECK_THRESHOLD.
    auto_rectify_triggered = False
    calibration = profile.get("calibration", {})
    if calibration.get("locked"):
        # Local import to avoid a circular import at module load time
        # (rectify.py doesn't import profiles.py, so this is safe, just
        # deferred).
        from app.models import RectifyRequest
        from app.routers.rectify import EVENTS_SINCE_LOCK_CHECK_THRESHOLD, run_rectification

        events_since = calibration.get("events_since_lock_check", 0) + 1
        if events_since >= EVENTS_SINCE_LOCK_CHECK_THRESHOLD:
            await run_rectification(profile_id, user["_id"], RectifyRequest())
            auto_rectify_triggered = True
        else:
            await birth_profiles_collection().update_one(
                {"_id": profile_id},
                {"$set": {"calibration.events_since_lock_check": events_since}},
            )

    return LifeEventAddedResponse(id=event_id, auto_rectify_triggered=auto_rectify_triggered)


@router.get("/{profile_id}/events", response_model=list[LifeEventOut])
async def list_life_events(profile_id: str, user: dict = Depends(get_current_user)):
    profile = await birth_profiles_collection().find_one({"_id": profile_id, "userId": user["_id"]})
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    events = await life_events_collection().find({"profileId": profile_id}).sort("createdAt", 1).to_list(length=1000)
    return [
        LifeEventOut(
            id=e["_id"],
            category=e["category"],
            # .get(), not e["exact_date"]: events created from answering a
            # rectify suggested_followups question are stored with
            # exact_date=None (see app.routers.rectify.add_followup_and_rectify).
            exact_date=e.get("exact_date"),
            impact_weight_score=e.get("impact_weight_score", 1.0),
            notes=e.get("notes"),
            time_frame_hint=e.get("time_frame_hint"),
            source=e.get("source"),
        )
        for e in events
    ]


@router.patch("/{profile_id}/events/{event_id}", response_model=LifeEventOut)
async def update_life_event(
    profile_id: str, event_id: str, payload: LifeEventUpdate, user: dict = Depends(get_current_user)
):
    profile = await birth_profiles_collection().find_one({"_id": profile_id, "userId": user["_id"]})
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    existing = await life_events_collection().find_one({"_id": event_id, "profileId": profile_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Life event not found")

    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    if updates:
        await life_events_collection().update_one({"_id": event_id}, {"$set": updates})
        existing.update(updates)

    return LifeEventOut(
        id=existing["_id"],
        category=existing["category"],
        exact_date=existing.get("exact_date"),
        impact_weight_score=existing.get("impact_weight_score", 1.0),
        notes=existing.get("notes"),
        time_frame_hint=existing.get("time_frame_hint"),
        source=existing.get("source"),
    )


@router.delete("/{profile_id}/events/{event_id}")
async def delete_life_event(profile_id: str, event_id: str, user: dict = Depends(get_current_user)):
    profile = await birth_profiles_collection().find_one({"_id": profile_id, "userId": user["_id"]})
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    result = await life_events_collection().delete_one({"_id": event_id, "profileId": profile_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Life event not found")
    return {"status": "deleted", "id": event_id}
