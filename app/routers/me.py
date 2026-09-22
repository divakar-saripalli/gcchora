"""GET /me -- the login-continuity endpoint: returns the user's profile(s)
plus their most recently active conversation (with recent messages
attached), so the app can resume "where you left off" instead of opening
to a blank chat screen."""
from fastapi import APIRouter, Depends

from app.db import birth_profiles_collection, conversations_collection, messages_collection
from app.security import get_current_user

router = APIRouter(tags=["me"])


@router.get("/me")
async def get_me(user: dict = Depends(get_current_user)):
    profiles = await birth_profiles_collection().find({"userId": user["_id"]}).to_list(length=20)
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
