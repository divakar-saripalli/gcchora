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


def _default_usage() -> dict:
    return {
        "free_questions_remaining": settings.free_questions_initial,
        "daily_question_used_on": None,
        "ad_unlock_credits": 0,
    }


async def check_and_consume_quota(user: dict) -> None:
    """Raises HTTPException(402, ...) if the user has no question credit
    left today; otherwise consumes one unit of whichever pool applies and
    persists the update."""
    usage = user.get("usage") or _default_usage()
    today = date.today().isoformat()

    if usage.get("free_questions_remaining", 0) > 0:
        usage["free_questions_remaining"] -= 1
    elif usage.get("daily_question_used_on") != today:
        usage["daily_question_used_on"] = today
    elif usage.get("ad_unlock_credits", 0) > 0:
        usage["ad_unlock_credits"] -= 1
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


async def credit_ad_unlock(user_id: str) -> None:
    user = await users_collection().find_one({"_id": user_id})
    usage = (user or {}).get("usage") or _default_usage()
    usage["ad_unlock_credits"] = usage.get("ad_unlock_credits", 0) + 1
    await users_collection().update_one(
        {"_id": user_id}, {"$set": {"usage": usage}}, upsert=True
    )
