"""Server-side verification of rewarded-ad completions. This endpoint is
called by the ad network (server-to-server), never by the app directly --
the app cannot be trusted to self-report that an ad was watched.

The signature check below is a placeholder for whichever ad network is
chosen (e.g. AdMob rewarded ads SSV uses a signed query string verified
against Google's rotating public keys, not a shared secret) -- swap
_verify_signature for that network's actual verification method before
this goes live.
"""
import hashlib
import hmac
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.config import settings
from app.db import ad_reward_events_collection
from app.models import AdRewardCallback
from app.quota import credit_ad_unlock
from app.security import get_current_user

router = APIRouter(prefix="/ads", tags=["ads"])



def _verify_signature(payload: AdRewardCallback) -> bool:
    if not settings.ad_ssv_shared_secret:
        return False  # refuse to credit anything until real verification is configured
    expected = hmac.new(
        settings.ad_ssv_shared_secret.encode(),
        f"{payload.user_id}:{payload.ad_network}:{payload.reward_token}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, payload.signature)


@router.post("/reward-callback")
async def reward_callback(payload: AdRewardCallback):
    if not _verify_signature(payload):
        raise HTTPException(status_code=401, detail="Unverifiable ad reward callback")

    existing = await ad_reward_events_collection().find_one({"rewardToken": payload.reward_token})
    if existing:
        return {"status": "already_processed"}  # prevent replay

    await ad_reward_events_collection().insert_one({
        "userId": payload.user_id,
        "adNetwork": payload.ad_network,
        "rewardToken": payload.reward_token,
        "verifiedAt": datetime.now(timezone.utc),
    })
    await credit_ad_unlock(payload.user_id)
    return {"status": "credited"}


@router.post("/test-credit")
async def test_credit(user: dict = Depends(get_current_user)):
    """Dev/QA-only stand-in for the real rewarded-ad flow above, which the
    app doesn't have a real ad SDK wired up to call yet. Lets a signed-in
    tester grant themselves one ad-unlock credit directly, with no ad
    network or signature involved. Only reachable when
    ALLOW_TEST_AD_BYPASS=true is set server-side -- off by default, and
    entirely separate from /reward-callback's real verification, which
    this does not touch or weaken."""
    if not settings.allow_test_ad_bypass:
        raise HTTPException(
            status_code=403,
            detail="Ad bypass is disabled. Set ALLOW_TEST_AD_BYPASS=true in the backend's .env to enable it for testing.",
        )
    await credit_ad_unlock(user["_id"])
    return {"status": "credited", "note": "test bypass -- no real ad was watched"}
