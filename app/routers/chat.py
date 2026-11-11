"""Conversation + message endpoints. Every question is quota-gated
(app.quota) and every turn, from both sides, is persisted so a returning
user's last conversation can be reloaded on login.
"""
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.db import birth_profiles_collection, conversations_collection, life_events_collection, messages_collection
from app.llm_gateway import ProviderNotConfigured, generate_answer
from app.models import ConversationCreate, MessageCreate
from app.quota import check_and_consume_quota, refund_quota
from app.security import get_current_user

router = APIRouter(tags=["chat"])
logger = logging.getLogger(__name__)

# Shown to the user whenever the LLM couldn't answer -- unconfigured
# provider, outage, exhausted quota, whatever. The real technical reason
# is logged server-side (see the except blocks below) so it's still
# diagnosable, but a raw "No AI provider is currently configured" or a
# bare exception string is confusing and off-brand for an astrology app.
# Keeping this generic on purpose: it should read the same whether the
# cause is a misconfigured key or a transient Gemini outage.
LLM_UNAVAILABLE_MESSAGE = (
    "Our expert astrologers are a little busy right now. Please post your "
    "question again in a few minutes and they'll get back to you."
)

# How many prior turns of this conversation to replay to the LLM on
# each new message, so it has actual continuity within a session
# instead of only ever seeing the static profile/events context plus
# the single latest question. Matches the length already used for
# app.routers.me's "resume where you left off" preview.
MAX_HISTORY_MESSAGES = 20


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


def _build_context(profile: dict, events: list[dict]) -> str:
    """Builds the system context sent to the LLM alongside every chat
    question. Since rectification itself is now LLM-reasoned from the
    life events (app.routers.rectify) rather than computed from a
    separate deterministic dasha timeline, those same life events are the
    grounding data available here too -- there's no richer computed chart
    to fall back on, so they're included directly rather than just a
    calibration summary.

    The stated birth details (date/time/location) are known from the
    moment the profile is created -- they must not be gated behind
    calibration status below, or the model has literally nothing to say
    when asked something as basic as "what birth location do you have
    for me", even though it was on file the whole time."""
    coords = profile.get("geographic_coordinates")
    if coords:
        location_line = (
            f"Their stated birth details: born {profile.get('birth_date', 'unknown date')} at "
            f"{profile.get('birth_time_24h', 'unknown time')} (local time), at latitude "
            f"{coords['latitude']}, longitude {coords['longitude']}, timezone offset "
            f"{coords['timezone_offset_hours']} hours from UTC. No city/country name is stored, only "
            "these coordinates -- if asked for the location, give the coordinates rather than saying "
            "none is on file."
        )
    else:
        location_line = "No birth details are on file for this user yet."

    calibration = profile.get("calibration", {})
    status = calibration.get("status", "uncalibrated")

    if status in ("calibrated", "locked"):
        confirmation = "locked in (95%+ confidence, auto-confirmed)" if status == "locked" else "user-confirmed"
        chart_line = (
            f" Birth-time rectification has additionally refined this to the "
            f"{calibration['chosen_ayanamsa']} ayanamsa with a rectified birth time of "
            f"{calibration['rectified_birth_time_24h']} (calibration confidence "
            f"{calibration['confidence_score']:.0f}%, {confirmation}, determined by "
            f"{calibration.get('source_provider', 'an LLM')})."
        )
    else:
        chart_line = (
            " Birth-time rectification has not been run yet -- answer using the stated time above, "
            "and suggest completing rectification for a more precise reading."
        )

    events_block = ""
    if events:
        event_lines = "\n".join(
            # .get(), not e["exact_date"]: an event created from answering a
            # rectify follow-up question has no exact date, only a
            # time_frame_hint (see app.routers.rectify.add_followup_and_rectify
            # and DefectBacklog #25) -- fall back to that so this reads as
            # "around 2013-2015" instead of the literal word "None".
            f"- {e['category']} on {e.get('exact_date') or e.get('time_frame_hint') or 'an unknown date'}"
            + (f" ({e['notes']})" if e.get("notes") else "")
            for e in events
        )
        events_block = f"\n\nTheir recorded life events, for grounding specific answers:\n{event_lines}"

    # Covers item 2 of Divakar's add-ons request (see FeatureBacklog.md):
    # the model should transparently handle whatever language, dialect, or
    # casual/slang phrasing the question arrives in -- including speech-
    # to-text transcripts from the chat screen's mic button, which are
    # never perfectly punctuated -- without the user having to ask in
    # English or in a formal register first.
    language_line = (
        " The user may write or speak in any language, regional dialect, or informal/slang "
        "phrasing (including text transcribed from speech, which may lack punctuation or have "
        "minor misheard words) -- understand it as best you can regardless, and reply in the "
        "same language and a similarly casual register the question was asked in, rather than "
        "defaulting to English or a more formal tone than the user used. If the question mixes "
        "languages (e.g. code-switching between English and a regional language in the same "
        "sentence), respond naturally in that same mixed style rather than forcing it into one "
        "language. Only ask the user to rephrase if the question is genuinely unintelligible, "
        "not merely informal or in a language other than English."
    )

    return (
        "You are an astrology assistant. "
        + location_line
        + chart_line
        + events_block
        + language_line
        + " Answer astrology questions grounded in this chart and these events. This is guidance "
        "and entertainment, not medical, legal, or financial advice. Keep answers short and to the "
        "point -- 3 to 5 sentences by default, no long lists or headers, and no restating the "
        "question back. Only go longer if the user explicitly asks for more detail."
    )


@router.post("/conversations/{conversation_id}/messages")
async def send_message(conversation_id: str, payload: MessageCreate, user: dict = Depends(get_current_user)):
    conv = await conversations_collection().find_one({"_id": conversation_id, "userId": user["_id"]})
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    consumed = await check_and_consume_quota(user)  # raises HTTPException(402) if no quota left

    profile = await birth_profiles_collection().find_one({"_id": conv["profileId"]}) or {}
    events = (
        await life_events_collection().find({"profileId": conv["profileId"]}).to_list(length=1000)
        if profile
        else []
    )
    context = _build_context(profile, events)

    # Prior turns in this conversation, oldest first -- without this, every
    # call was independent beyond the static context above, so the LLM had
    # no memory of anything said earlier in the same conversation.
    prior_messages = await messages_collection().find(
        {"conversationId": conversation_id}
    ).sort("createdAt", 1).to_list(length=MAX_HISTORY_MESSAGES)
    history = [{"role": m["role"], "content": m["content"]} for m in prior_messages]

    now = datetime.now(timezone.utc)
    await messages_collection().insert_one({
        "_id": str(uuid.uuid4()), "conversationId": conversation_id,
        "role": "user", "content": payload.content, "createdAt": now,
    })

    # Prefer whichever provider actually determined this chart, so chat
    # answers stay consistent with the calibration -- otherwise this always
    # fell back to the global default provider regardless of what locked
    # in the chart, which is confusing when they don't match.
    preferred_provider = profile.get("calibration", {}).get("source_provider")
    try:
        answer = await generate_answer(
            context, payload.content, provider=preferred_provider, history=history
        )
    except ProviderNotConfigured as exc:
        # Charged but never answered -- give the question back rather than
        # silently burning the user's daily/free quota on an outage.
        logger.error("Chat answer failed for conversation %s: provider not configured (%s)", conversation_id, exc)
        await refund_quota(user["_id"], consumed)
        raise HTTPException(status_code=503, detail=LLM_UNAVAILABLE_MESSAGE)
    except Exception as exc:
        logger.error("Chat answer failed for conversation %s: %s", conversation_id, exc)
        await refund_quota(user["_id"], consumed)
        raise HTTPException(status_code=503, detail=LLM_UNAVAILABLE_MESSAGE)

    await messages_collection().insert_one({
        "_id": str(uuid.uuid4()), "conversationId": conversation_id,
        "role": "assistant", "content": answer, "createdAt": datetime.now(timezone.utc),
    })
    await conversations_collection().update_one(
        {"_id": conversation_id}, {"$set": {"lastMessageAt": datetime.now(timezone.utc)}}
    )
    return {"role": "assistant", "content": answer}
