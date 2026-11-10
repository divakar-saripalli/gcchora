"""GET /me -- the login-continuity endpoint: returns the user's profile(s)
plus their most recently active conversation (with recent messages
attached), so the app can resume "where you left off" instead of opening
to a blank chat screen. Also owns the small set of other per-user,
not-tied-to-a-profile endpoints: device-token registration, the
push-notification test endpoint, and the usage-status endpoint below."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException

from app import push
from app.config import settings
from app.db import birth_profiles_collection, conversations_collection, messages_collection
from app.models import DeviceTokenRegistration
from app.quota import default_usage
from app.security import get_current_user

router = APIRouter(tags=["me"])


@router.get("/me")
async def get_me(user: dict = Depends(get_current_user)):
    # Explicit sort so "the active profile" (mobile_app/lib/providers/
    # app_provider.dart AppDataProvider.loadFromMe takes profiles.first)
    # deterministically means "oldest profile", not whatever order an
    # unsorted find() happens to return -- which Mongo does not guarantee
    # is insertion order.
    profiles = await birth_profiles_collection().find({"userId": user["_id"]}).sort("createdAt", 1).to_list(length=20)
    last_conversation = await conversations_collection().find_one(
        {"userId": user["_id"]}, sort=[("lastMessageAt", -1)]
    )
    recent_messages = []
    if last_conversation:
        recent_messages = await messages_collection().find(
            {"conversationId": last_conversation["_id"]}
        ).sort("createdAt", -1).to_list(length=20)
        recent_messages.reverse()

    return {
        "user": {"id": user["_id"], "name": user.get("name"), "email": user.get("email")},
        "profiles": profiles,
        "last_conversation": last_conversation,
        "last_conversation_messages": recent_messages,
    }


@router.post("/me/device-token")
async def register_device_token(payload: DeviceTokenRegistration, user: dict = Depends(get_current_user)):
    """Called by the app once it has an FCM token (on launch, and again on
    firebase_messaging's onTokenRefresh) so the backend knows where to
    send this user's push notifications. Safe to call repeatedly with the
    same token -- see app.push.register_device_token."""
    await push.register_device_token(user["_id"], payload.token, payload.platform)
    return {"status": "registered"}


@router.post("/me/test-push")
async def test_push(user: dict = Depends(get_current_user)):
    """Dev/QA-only: sends a real push notification to every device token
    this user has registered, so the whole chain (FCM setup, token
    registration, device delivery) can be verified end-to-end before
    anything else depends on it. Same on/off switch as the rest of push:
    a no-op (not an error) when ENABLE_PUSH_NOTIFICATIONS isn't set, same
    pattern as /ads/test-credit and settings.allow_test_ad_bypass."""
    if not settings.enable_push_notifications:
        raise HTTPException(
            status_code=403,
            detail="Push notifications are disabled. Set ENABLE_PUSH_NOTIFICATIONS=true in the backend's .env to enable them.",
        )
    sent = await push.send_push(
        user["_id"],
        title="GoogleHora test notification",
        body="If you can see this, push notifications are working.",
        data={"type": "test"},
    )
    if sent == 0:
        return {
            "status": "no_devices_or_unconfigured",
            "note": "No push was sent -- either this account has no registered device tokens, "
                    "or the server's Firebase credentials aren't set up. Register a device token "
                    "via POST /me/device-token first, and check server logs for details.",
        }
    return {"status": "sent", "devices_notified": sent}


@router.get("/me/usage")
async def get_usage(user: dict = Depends(get_current_user)):
    """Surfaces the question-quota state that app.quota.check_and_consume_quota
    enforces but never otherwise reports back to the client -- added so
    "why did I just get a 402" can be answered by checking this instead
    of querying Mongo directly. daily_question_used_today is derived
    (rather than returning the raw daily_question_used_on date) so the
    caller doesn't need to know today's date is compared against it."""
    usage = user.get("usage") or default_usage()
    return {
        "free_questions_remaining": usage.get("free_questions_remaining", 0),
        "daily_question_used_today": usage.get("daily_question_used_on") == date.today().isoformat(),
        "ad_unlock_credits": usage.get("ad_unlock_credits", 0),
    }
