"""Question-quota enforcement: first FREE_QUESTIONS_INITIAL questions ever
are free, then 1 free question per calendar day, then a verified ad-watch
is required per extra question. Rectification runs have their own daily
cap (RECTIFY_DAILY_LIMIT).

Never trust a client's own "I watched the ad" claim -- credits only come
from the server-verified callback in app/routers/ads.py.

Every consume/refund here is a single conditional MongoDB update, not a
read-modify-write: the filter states the precondition ("has a free
question left") and the update applies the change, so two concurrent
requests from the same user can't both spend the same credit. This used
to read the usage dict, change it in Python and $set the whole thing
back, which let a second request overwrite the first one's decrement.
"""
from datetime import date, datetime, timezone

from fastapi import HTTPException

from app.config import settings
from app.db import users_collection


def default_usage() -> dict:
    return {
        "free_questions_remaining": settings.free_questions_initial,
        "daily_question_used_on": None,
        "ad_unlock_credits": 0,
    }


async def _heal_usage(user: dict) -> None:
    """Fills in usage fields that are missing or stored as None (users
    created before these defaults were centralized), so the conditional
    updates below have real numbers to compare against. Only touches the
    database when the user document passed in shows something to fix."""
    usage = user.get("usage")
    users = users_collection()
    if not isinstance(usage, dict):
        await users.update_one({"_id": user["_id"], "usage": None}, {"$set": {"usage": default_usage()}})
        return
    if usage.get("free_questions_remaining") is None:
        await users.update_one(
            {"_id": user["_id"], "usage.free_questions_remaining": None},
            {"$set": {"usage.free_questions_remaining": settings.free_questions_initial}},
        )
    if usage.get("ad_unlock_credits") is None:
        await users.update_one(
            {"_id": user["_id"], "usage.ad_unlock_credits": None},
            {"$set": {"usage.ad_unlock_credits": 0}},
        )


async def check_and_consume_quota(user: dict) -> dict:
    """Raises HTTPException(402, ...) if the user has no question credit
    left today; otherwise consumes one unit of whichever pool applies and
    returns a small dict describing exactly what was consumed -- pass
    this to refund_quota if the question ends up never actually getting
    answered (e.g. every LLM provider is down), so a provider outage
    doesn't silently burn through the user's daily/free questions."""
    await _heal_usage(user)
    users = users_collection()
    user_id = user["_id"]
    today = date.today().isoformat()
    stamp = {"usage_updated_at": datetime.now(timezone.utc)}

    result = await users.update_one(
        {"_id": user_id, "usage.free_questions_remaining": {"$gt": 0}},
        {"$inc": {"usage.free_questions_remaining": -1}, "$set": stamp},
    )
    if result.modified_count:
        return {"pool": "free_questions_remaining"}

    # find_one_and_update returns the document as it was BEFORE the
    # update, which is where the previous date comes from for a refund.
    before = await users.find_one_and_update(
        {"_id": user_id, "usage.daily_question_used_on": {"$ne": today}},
        {"$set": {"usage.daily_question_used_on": today, **stamp}},
    )
    if before is not None:
        previous = (before.get("usage") or {}).get("daily_question_used_on")
        return {"pool": "daily_question_used_on", "previous_value": previous}

    result = await users.update_one(
        {"_id": user_id, "usage.ad_unlock_credits": {"$gt": 0}},
        {"$inc": {"usage.ad_unlock_credits": -1}, "$set": stamp},
    )
    if result.modified_count:
        return {"pool": "ad_unlock_credits"}

    raise HTTPException(
        status_code=402,
        detail={
            "status": "quota_exceeded",
            "reason": "daily_limit_reached",
            "message": "Watch a rewarded ad to unlock another question today.",
        },
    )


async def refund_quota(user_id: str, consumed: dict) -> None:
    """Reverses exactly what check_and_consume_quota took, keyed by the
    dict it returned."""
    pool = consumed.get("pool")
    if pool == "free_questions_remaining":
        await users_collection().update_one(
            {"_id": user_id}, {"$inc": {"usage.free_questions_remaining": 1}}
        )
    elif pool == "daily_question_used_on":
        await users_collection().update_one(
            {"_id": user_id},
            {"$set": {"usage.daily_question_used_on": consumed.get("previous_value")}},
        )
    elif pool == "ad_unlock_credits":
        await users_collection().update_one(
            {"_id": user_id}, {"$inc": {"usage.ad_unlock_credits": 1}}
        )


async def credit_ad_unlock(user_id: str) -> None:
    users = users_collection()
    # $inc can't add to a stored None, so clear that case first.
    await users.update_one(
        {"_id": user_id, "usage.ad_unlock_credits": None}, {"$set": {"usage.ad_unlock_credits": 0}}
    )
    await users.update_one({"_id": user_id}, {"$inc": {"usage.ad_unlock_credits": 1}})


async def consume_rectify_run(user_id: str) -> None:
    """Counts one LLM-backed rectification run against the user's daily
    cap, raising HTTPException(429) once it's used up. Kept separate from
    the question pools above: a rectification run fans out to every
    configured provider, so it's metered on its own counter rather than
    spending chat questions."""
    users = users_collection()
    today = date.today().isoformat()
    limit = settings.rectify_daily_limit

    result = await users.update_one(
        {"_id": user_id, "usage.rectify_runs_on": today, "usage.rectify_runs_count": {"$lt": limit}},
        {"$inc": {"usage.rectify_runs_count": 1}},
    )
    if result.modified_count:
        return
    if limit > 0:
        result = await users.update_one(
            {"_id": user_id, "usage.rectify_runs_on": {"$ne": today}},
            {"$set": {"usage.rectify_runs_on": today, "usage.rectify_runs_count": 1}},
        )
        if result.modified_count:
            return
    raise HTTPException(
        status_code=429,
        detail=(
            "Today's calibration quota has been reached. Anything you just entered has been "
            "saved -- calibration will be available again tomorrow."
        ),
    )


async def refund_rectify_run(user_id: str) -> None:
    """Gives back a run that never produced a result (provider outage)."""
    await users_collection().update_one(
        {"_id": user_id, "usage.rectify_runs_on": date.today().isoformat(), "usage.rectify_runs_count": {"$gt": 0}},
        {"$inc": {"usage.rectify_runs_count": -1}},
    )
