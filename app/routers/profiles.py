import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from app.db_util import capped_list
from app.db import birth_profiles_collection, conversations_collection, life_events_collection
from app.event_ordering import sort_events_chronologically
from app.models import (
    EventImportRequest,
    EventImportResponse,
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
logger = logging.getLogger(__name__)


def _needs_spouse_details(profile: dict) -> bool:
    """True when the profile is married but is missing one or more of the
    spouse birth-detail fields needed for the cross-chart ("synastry")
    reasoning in app.routers.rectify and app.routers.chat. Used both for
    a freshly-created/updated profile and, for backward compatibility,
    for every profile fetched that predates this feature entirely (such
    a profile simply has no spouse_* keys at all, which this treats the
    same as them being explicitly unset)."""
    if not profile.get("is_married"):
        return False
    return not (
        profile.get("spouse_birth_date")
        and profile.get("spouse_birth_time_24h")
        and profile.get("spouse_geographic_coordinates")
    )


def _compute_spouse_offset_if_possible(doc: dict) -> None:
    """Mirrors create_profile's own DST-aware timezone_offset_hours
    override (see app.timezone_lookup), but for the spouse's coordinates
    -- mutates doc["spouse_geographic_coordinates"] in place if all three
    spouse fields needed to compute it are present. A no-op (not an
    error) if any of them is still missing, which is the normal
    not-fully-filled-in-yet state while needs_spouse_details is true."""
    coords = doc.get("spouse_geographic_coordinates")
    date_ = doc.get("spouse_birth_date")
    time_ = doc.get("spouse_birth_time_24h")
    if not (coords and date_ and time_):
        return
    computed_offset = compute_timezone_offset_hours(coords["latitude"], coords["longitude"], date_, time_)
    if computed_offset is not None:
        coords["timezone_offset_hours"] = computed_offset


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

    spouse_coordinates = (
        payload.spouse_geographic_coordinates.model_dump() if payload.spouse_geographic_coordinates else None
    )
    doc = {
        "_id": profile_id,
        "userId": user["_id"],
        "label": payload.label,
        "relation": payload.relation,
        "birth_date": payload.birth_date,
        "birth_time_24h": payload.birth_time_24h,
        "geographic_coordinates": coordinates,
        "birth_time_uncertainty_minutes": payload.birth_time_uncertainty_minutes,
        "is_married": payload.is_married,
        "spouse_birth_date": payload.spouse_birth_date,
        "spouse_birth_time_24h": payload.spouse_birth_time_24h,
        "spouse_geographic_coordinates": spouse_coordinates,
        "calibration": {"status": "uncalibrated"},
        "createdAt": datetime.now(timezone.utc),
    }
    # Same DST-aware override as the person's own coordinates above, but
    # for the spouse's -- see _compute_spouse_offset_if_possible's doc
    # comment for when this is a no-op (spouse details not fully given
    # yet).
    _compute_spouse_offset_if_possible(doc)

    await birth_profiles_collection().insert_one(doc)
    out_payload = payload.model_dump()
    out_payload["geographic_coordinates"] = coordinates
    out_payload["spouse_geographic_coordinates"] = doc["spouse_geographic_coordinates"]
    return BirthProfileOut(
        id=profile_id,
        **out_payload,
        calibration_status="uncalibrated",
        needs_spouse_details=_needs_spouse_details(doc),
    )


@router.get("/{profile_id}")
async def get_profile(profile_id: str, user: dict = Depends(get_current_user)):
    profile = await birth_profiles_collection().find_one({"_id": profile_id, "userId": user["_id"]})
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    # No response_model on this endpoint -- the raw document is returned
    # as-is, so needs_spouse_details (computed, not a stored field) has
    # to be added here explicitly rather than relying on a Pydantic model
    # to fill it in. This also covers profiles created before spouse
    # fields existed at all: _needs_spouse_details treats a profile with
    # no spouse_* keys the same as one with them explicitly unset.
    profile["needs_spouse_details"] = _needs_spouse_details(profile)
    # Don't hand internal bookkeeping back to the client. (A full response
    # model would be stricter, but the mobile app reads the raw calibration
    # sub-document, so this is a projection rather than a schema.)
    profile.pop("userId", None)
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
    if "spouse_geographic_coordinates" in updates:
        updates["spouse_geographic_coordinates"] = payload.spouse_geographic_coordinates.model_dump()

    chart_affecting = {"birth_date", "birth_time_24h", "geographic_coordinates"}
    changed_chart_fields = any(field in updates and updates[field] != profile.get(field) for field in chart_affecting)

    spouse_fields = {"is_married", "spouse_birth_date", "spouse_birth_time_24h", "spouse_geographic_coordinates"}
    changed_spouse_fields = any(field in updates and updates[field] != profile.get(field) for field in spouse_fields)

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

    # Same idea, but for the spouse's coordinates -- recompute against
    # the final post-merge spouse date/time/location, using whichever of
    # this PATCH's updates or the profile's existing stored values apply
    # to each field (same merge pattern as the self-offset above).
    if changed_spouse_fields:
        merged_spouse_coords = updates.get(
            "spouse_geographic_coordinates", profile.get("spouse_geographic_coordinates")
        )
        merged_spouse_date = updates.get("spouse_birth_date", profile.get("spouse_birth_date"))
        merged_spouse_time = updates.get("spouse_birth_time_24h", profile.get("spouse_birth_time_24h"))
        if merged_spouse_coords and merged_spouse_date and merged_spouse_time:
            computed_offset = compute_timezone_offset_hours(
                merged_spouse_coords["latitude"], merged_spouse_coords["longitude"],
                merged_spouse_date, merged_spouse_time,
            )
            if computed_offset is not None:
                merged_spouse_coords = dict(merged_spouse_coords)
                merged_spouse_coords["timezone_offset_hours"] = computed_offset
                updates["spouse_geographic_coordinates"] = merged_spouse_coords

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
    elif changed_spouse_fields and profile.get("calibration", {}).get("locked"):
        # Unlike the person's own birth details changing (which discards
        # the calibration outright, above), newly available or changed
        # spouse details should REFINE an existing locked calibration
        # via the same recheck path a new life event triggers (see
        # add_life_event's EVENTS_SINCE_LOCK_CHECK_THRESHOLD handling),
        # not discard it -- the chart itself hasn't changed, only the
        # cross-chart evidence available to evaluate it against (see
        # app.routers.rectify._build_recheck_prompt's spouse_line).
        # run_rectification persists whatever it decides directly to the
        # DB and returns a RectifyResult, not a calibration dict -- this
        # PATCH's own response needs the stored calibration shape, so
        # re-fetch rather than trying to adapt its return value. A
        # transient failure here (e.g. an LLM outage) shouldn't fail the
        # spouse-detail update itself, so it's caught and logged rather
        # than propagated.
        from app.models import RectifyRequest
        from app.routers.rectify import run_rectification

        try:
            await run_rectification(profile_id, user["_id"], RectifyRequest())
        except Exception as exc:
            logger.warning(
                "Spouse-detail-triggered rectification recheck failed (%s).", type(exc).__name__
            )
        refreshed = await birth_profiles_collection().find_one({"_id": profile_id})
        if refreshed:
            profile["calibration"] = refreshed.get("calibration", profile["calibration"])

    return BirthProfileOut(
        id=profile_id,
        label=profile["label"],
        relation=profile.get("relation", "self"),
        birth_date=profile["birth_date"],
        birth_time_24h=profile["birth_time_24h"],
        geographic_coordinates=profile["geographic_coordinates"],
        birth_time_uncertainty_minutes=profile.get("birth_time_uncertainty_minutes", 30),
        is_married=profile.get("is_married", False),
        spouse_birth_date=profile.get("spouse_birth_date"),
        spouse_birth_time_24h=profile.get("spouse_birth_time_24h"),
        spouse_geographic_coordinates=profile.get("spouse_geographic_coordinates"),
        calibration_status=profile.get("calibration", {}).get("status", "uncalibrated"),
        needs_spouse_details=_needs_spouse_details(profile),
    )


@router.post("/{profile_id}/events", response_model=LifeEventAddedResponse)
async def add_life_event(
    profile_id: str,
    payload: LifeEventIn,
    background: BackgroundTasks,
    user: dict = Depends(get_current_user),
):
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
        from app.routers.rectify import EVENTS_SINCE_LOCK_CHECK_THRESHOLD, background_recheck

        events_since = calibration.get("events_since_lock_check", 0) + 1
        if events_since >= EVENTS_SINCE_LOCK_CHECK_THRESHOLD:
            # Runs AFTER the response is sent, metered against the user's
            # daily cap, and never fails the (already saved) event.
            background.add_task(background_recheck, profile_id, user["_id"])
            auto_rectify_triggered = True
        else:
            await birth_profiles_collection().update_one(
                {"_id": profile_id},
                {"$set": {"calibration.events_since_lock_check": events_since}},
            )

    return LifeEventAddedResponse(id=event_id, auto_rectify_triggered=auto_rectify_triggered)


# Only kinds of events that are facts about a FAMILY rather than one person
# can be copied between profiles. Follow-up answers and personal events
# (career, education, health...) are never copied.
SHAREABLE_EVENT_CATEGORIES = {"MARRIAGE", "FIRST_CHILD", "KIDS", "DIVORCE"}


@router.post("/{profile_id}/events/import", response_model=EventImportResponse)
async def import_events(profile_id: str, payload: EventImportRequest, user: dict = Depends(get_current_user)):
    """Copies the chosen shareable events (marriage, children's births,
    divorce) from another of the user's profiles onto this one. The user
    picks the events -- nothing is guessed, because only they know which
    wedding or which children belong to which relationship. Copies are
    independent (editing one doesn't change the other) and an event with
    the same category and date already on this profile is skipped."""
    if payload.source_profile_id == profile_id:
        raise HTTPException(status_code=400, detail="Choose a different profile to copy events from.")
    target = await birth_profiles_collection().find_one({"_id": profile_id, "userId": user["_id"]})
    source = await birth_profiles_collection().find_one(
        {"_id": payload.source_profile_id, "userId": user["_id"]}
    )
    if not target or not source:
        raise HTTPException(status_code=404, detail="Profile not found")

    existing = await capped_list(life_events_collection().find({"profileId": profile_id}), 1000, "life events")
    seen = {(e.get("category"), e.get("exact_date")) for e in existing}

    imported = duplicates = not_shareable = 0
    for event_id in dict.fromkeys(payload.event_ids):  # de-duplicate, keep order
        event = await life_events_collection().find_one({"_id": event_id, "profileId": payload.source_profile_id})
        if (
            event is None
            or event.get("category") not in SHAREABLE_EVENT_CATEGORIES
            or not event.get("exact_date")
            or event.get("source") == "suggested_followup_answer"
        ):
            not_shareable += 1
            continue
        key = (event["category"], event["exact_date"])
        if key in seen:
            duplicates += 1
            continue
        await life_events_collection().insert_one({
            "_id": str(uuid.uuid4()),
            "profileId": profile_id,
            "category": event["category"],
            "exact_date": event["exact_date"],
            "impact_weight_score": event.get("impact_weight_score", 1.0),
            "notes": event.get("notes"),
            "source": "copied_from_profile",
            "copied_from_event_id": event_id,
            "createdAt": datetime.now(timezone.utc),
        })
        seen.add(key)
        imported += 1
    return EventImportResponse(
        imported=imported, skipped_duplicates=duplicates, skipped_not_shareable=not_shareable
    )


@router.get("/{profile_id}/events", response_model=list[LifeEventOut])
async def list_life_events(profile_id: str, user: dict = Depends(get_current_user)):
    profile = await birth_profiles_collection().find_one({"_id": profile_id, "userId": user["_id"]})
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    events = await capped_list(life_events_collection().find({"profileId": profile_id}), 1000, "life events")
    # Sorted by when the event actually happened, not by createdAt (when
    # it was logged) -- a user adding events out of chronological order
    # (e.g. a 2020 child birth logged before an earlier 2017 sibling's)
    # would otherwise show up in the wrong sequence here. See
    # app.event_ordering for the full rationale.
    events = sort_events_chronologically(events)
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
            question_text=e.get("question_text"),
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
        question_text=existing.get("question_text"),
    )


@router.delete("/{profile_id}/events/followup-answers")
async def delete_followup_answers(profile_id: str, user: dict = Depends(get_current_user)):
    """Deletes every life event that came from answering a calibration
    follow-up question (source == "suggested_followup_answer"), so the
    user can re-answer a fresh batch from scratch instead of being stuck
    with earlier answers they want to redo.

    NOTE: registered BEFORE delete_life_event's `/{event_id}` route on
    purpose -- FastAPI/Starlette matches routes in declaration order, and
    `/{event_id}` is a dynamic segment that would otherwise swallow this
    literal path too (treating "followup-answers" as an event id).

    Does NOT touch calibration status/lock, or trigger a recalibration
    automatically -- that would mean an unrequested extra LLM fan-out
    call. The user can tap "Run calibration" (the existing action)
    afterward to get a fresh batch of follow-up questions once they're
    ready; whether that's a fresh multi-provider comparison or a
    locked-profile recheck depends on the profile's current calibration
    state, same as any other rectification trigger. Does reset
    `calibration.answered_followup_categories` (the DefectBacklog #32
    batching-progress tracker), since whatever was answered before no
    longer exists."""
    profile = await birth_profiles_collection().find_one({"_id": profile_id, "userId": user["_id"]})
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    result = await life_events_collection().delete_many(
        {"profileId": profile_id, "source": "suggested_followup_answer"}
    )
    await birth_profiles_collection().update_one(
        {"_id": profile_id},
        {"$set": {"calibration.answered_followup_categories": []}},
    )
    return {"status": "deleted", "deleted_count": result.deleted_count}


@router.delete("/{profile_id}/events/{event_id}")
async def delete_life_event(profile_id: str, event_id: str, user: dict = Depends(get_current_user)):
    profile = await birth_profiles_collection().find_one({"_id": profile_id, "userId": user["_id"]})
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    result = await life_events_collection().delete_one({"_id": event_id, "profileId": profile_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Life event not found")
    return {"status": "deleted", "id": event_id}
