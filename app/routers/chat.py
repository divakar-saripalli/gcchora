"""Conversation + message endpoints. Every question is quota-gated
(app.quota) and every turn, from both sides, is persisted so a returning
user's last conversation can be reloaded on login.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.db import birth_profiles_collection, conversations_collection, messages_collection
from app.llm_gateway import generate_answer
from app.models import ConversationCreate, MessageCreate
from app.quota import check_and_consume_quota
from app.security import get_current_user

router = APIRouter(tags=["chat"])


@router.post("/conversations")
async def create_conversation(payload: ConversationCreate, user: dict = Depends(get_current_user)):
    profile = await birth_profiles_collection().find_one({"_id": payload.profile_id, "userId": user["_id"]})
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    conv_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    await conversations_collection().insert_one({
        "_id": conv_id,
        "userId": user["_id"],
        "profileId": payload.profile_id,
        "title": payload.title,
        "startedAt": now,
        "lastMessageAt": now,
    })
    return {"id": conv_id}


@router.get("/conversations")
async def list_conversations(user: dict = Depends(get_current_user)):
    cursor = conversations_collection().find({"userId": user["_id"]}).sort("lastMessageAt", -1)
    return await cursor.to_list(length=100)


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, user: dict = Depends(get_current_user)):
    conv = await conversations_collection().find_one({"_id": conversation_id, "userId": user["_id"]})
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = await messages_collection().find({"conversationId": conversation_id}).sort("createdAt", 1).to_list(length=500)
    return {"conversation": conv, "messages": messages}


def _build_context(profile: dict) -> str:
    calibration = profile.get("calibration", {})
    if calibration.get("status") == "calibrated":
        return (
            f"You are an astrology assistant. The user's chart uses the "
            f"{calibration['chosen_ayanamsa']} ayanamsa with a rectified birth "
            f"time of {calibration['rectified_birth_time_24h']} on {profile['birth_date']} "
            f"(calibration confidence {calibration['confidence_score']}%). "
            f"Answer astrology questions grounded in this chart. This is guidance "
            f"and entertainment, not medical, legal, or financial advice."
        )
    return (
        "You are an astrology assistant. This user's birth chart has not been "
        "calibrated yet -- answer generally and suggest completing birth-time "
        "rectification for a personalized reading."
    )


@router.post("/conversations/{conversation_id}/messages")
async def send_message(conversation_id: str, payload: MessageCreate, user: dict = Depends(get_current_user)):
    conv = await conversations_collection().find_one({"_id": conversation_id, "userId": user["_id"]})
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    await check_and_consume_quota(user)  # raises HTTPException(402) if no quota left

    profile = await birth_profiles_collection().find_one({"_id": conv["profileId"]}) or {}
    context = _build_context(profile)

    now = datetime.now(timezone.utc)
    await messages_collection().insert_one({
        "_id": str(uuid.uuid4()), "conversationId": conversation_id,
        "role": "user", "content": payload.content, "createdAt": now,
    })

    answer = await generate_answer(context, payload.content)

    await messages_collection().insert_one({
        "_id": str(uuid.uuid4()), "conversationId": conversation_id,
        "role": "assistant", "content": answer, "createdAt": datetime.now(timezone.utc),
    })
    await conversations_collection().update_one(
        {"_id": conversation_id}, {"$set": {"lastMessageAt": datetime.now(timezone.utc)}}
    )
    return {"role": "assistant", "content": answer}
