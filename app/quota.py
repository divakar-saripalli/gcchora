"""Question-quota enforcement: first FREE_QUESTIONS_INITIAL questions ever
are free, then 1 free question per calendar day, then a verified ad-watch
is required per extra question.

Never trust a client's own "I watched the ad" claim -- credits only come
from the server-verified callback in app/routers/ads.py.
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


async def check_and_consume_quota(user: dict) -> dict:
    """Raises HTTPException(402, ...) if the user has no question credit
    left today; otherwise consumes one unit of whichever pool applies,
    persists the update, and returns a small dict describing exactly what
    was consumed -- pass this to refund_quota if the question ends up
    never actually getting answered (e.g. every LLM provider is down),
    so a provider outage doesn't silently burn through the user's
    daily/free questions for nothing."""
    usage = user.get("usage") or default_usage()
    # Self-heal any stray Nones already persisted (e.g. from a user created
    # before this default was centralized) -- dict.get's fallback only
    # applies when the key is *absent*, not when it's present as None, so
    # a stored None would otherwise reach the comparisons below as-is.
    if usage.get("free_questions_remaining") is None:
        usage["free_questions_remaining"] = settings.free_questions_initial
    if usage.get("ad_unlock_credits") is None:
        usage["ad_unlock_credits"] = 0
    today = date.today().isoformat()

    if usage.get("free_questions_remaining", 0) > 0:
        usage["free_questions_remaining"] -= 1
        consumed = {"pool": "free_questions_remaining"}
    elif usage.get("daily_question_used_on") != today:
        consumed = {"pool": "daily_question_used_on", "previous_value": usage.get("daily_question_used_on")}
        usage["daily_question_used_on"] = today
    elif usage.get("ad_unlock_credits", 0) > 0:
        usage["ad_unlock_credits"] -= 1
        consumed = {"pool": "ad_unlock_credits"}
    else:
        raise HTTPException(
            status_code=402,
            detail={
                "status": "quota_exceeded",
                "reason": "daily_limit_reached",
                "message": "Watch a rewarded ad to unlock another question today.",
            },
        )

    await users_collection().update_one(
        {"_id": user["_id"]},
        {"$set": {"usage": usage, "usage_updated_at": datetime.now(timezone.utc)}},
    )
    return consumed


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
    user = await users_collection().find_one({"_id": user_id})
    usage = (user or {}).get("usage") or default_usage()
    usage["ad_unlock_credits"] = usage.get("ad_unlock_credits", 0) + 1
    await users_collection().update_one(
        {"_id": user_id}, {"$set": {"usage": usage}}, upsert=True
    )
