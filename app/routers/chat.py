"""Conversation + message endpoints. Every question is quota-gated
(app.quota) and every turn, from both sides, is persisted so a returning
user's last conversation can be reloaded on login.
"""
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.db_util import capped_list
from app.db import birth_profiles_collection, conversations_collection, life_events_collection, messages_collection
from app.event_ordering import sort_events_chronologically
from app.llm_gateway import ProviderNotConfigured, summarize_provider_error, generate_answer
from app.models import ConversationCreate, MessageCreate
from app.quota import check_and_consume_quota, refund_quota
from app.rate_limit import limit_per_user
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
# the single latest question.
#
# Trimmed down from 20 (which matched app.routers.me's "resume where you
# left off" preview length, but that's a display concern, not a cost
# one) -- every one of these turns gets resent, in full, on EVERY
# message for the rest of the conversation, so this number is the
# single biggest token-growth driver in a long chat, far more than the
# mostly-fixed-size birth/events context block. 10 still gives the
# model the last 5 question/answer exchanges, which is enough
# continuity for the short, one-topic-at-a-time questions this app
# actually gets; bump it back up if real conversations start losing
# useful thread.
MAX_HISTORY_MESSAGES = 10



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
    convs = await capped_list(cursor, 100, "conversations")
    for c in convs:
        c.pop("userId", None)
    return convs


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, user: dict = Depends(get_current_user)):
    conv = await conversations_collection().find_one({"_id": conversation_id, "userId": user["_id"]})
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = await capped_list(messages_collection().find({"conversationId": conversation_id}).sort("createdAt", 1), 500, "messages")
    conv.pop("userId", None)
    return {"conversation": conv, "messages": messages}


def _format_chat_event_line(e: dict) -> str:
    # .get(), not e["exact_date"]: an event created from answering a
    # rectify follow-up question has no exact date, only a
    # time_frame_hint (see app.routers.rectify.add_followup_and_rectify
    # and DefectBacklog #25) -- fall back to that so this reads as
    # "around 2013-2015" instead of the literal word "None".
    when = e.get("exact_date") or e.get("time_frame_hint") or "an unknown date"
    # Events that came from answering a specific calibration follow-up
    # question carry that question's exact text -- show it explicitly so
    # the user's own notes read as a direct answer to a named question,
    # not a vague aside, matching the same treatment
    # app.routers.rectify._format_events gives these during calibration.
    if e.get("source") == "suggested_followup_answer" and e.get("question_text"):
        answer_text = e.get("notes") or "(no written answer given)"
        return f"- Follow-up question asked: \"{e['question_text']}\" -- User's answer: {answer_text} (around {when})"
    return f"- {e['category']} on {when}" + (f" ({e['notes']})" if e.get("notes") else "")


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
    today = datetime.now(timezone.utc).date().isoformat()
    time_anchor_line = (
        f"Today's real-world date is {today}. When discussing timing -- favorable/unfavorable "
        "windows, transits, dasha/bhukti periods, \"best time to do X\" -- always reason relative "
        "to this actual current date, not some other date you might otherwise assume. If a window "
        "you'd otherwise mention has already ended before today, say so plainly (e.g. \"that "
        "window was April-May 2025, which has already passed\") and give the next upcoming one "
        "instead if there is one soon -- never present an already-past window as current or "
        "upcoming advice without flagging that it's in the past."
    )

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
        event_lines = "\n".join(_format_chat_event_line(e) for e in events)
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

    # Covers Divakar's spouse-correlation request: for any shared-life
    # question (not just marriage/kids events -- in-day-to-day chat terms
    # this is "our kids' health", "my/our parents' or in-laws' health",
    # "our money situation", "should we buy this property/asset"),
    # factor in BOTH charts, not just the user's own, whenever spouse
    # details are actually on file. See also _build_spouse_line in
    # app.routers.rectify for the rectification-side version of this.
    spouse_coords = profile.get("spouse_geographic_coordinates")
    spouse_date = profile.get("spouse_birth_date")
    spouse_time = profile.get("spouse_birth_time_24h")
    if profile.get("is_married") and spouse_coords and spouse_date and spouse_time:
        spouse_line = (
            f" The user is married. Their spouse's stated birth details: born {spouse_date} at "
            f"{spouse_time} (local time), at latitude {spouse_coords['latitude']}, longitude "
            f"{spouse_coords['longitude']}, timezone offset {spouse_coords['timezone_offset_hours']} "
            "hours from UTC. For any question about something that concerns both partners rather "
            "than the user alone -- their children and children's health, either side's parents' "
            "or in-laws' health, money/financial stability, buying property or other major assets, "
            "or anything else shared between the couple -- reason using BOTH the user's chart and "
            "the spouse's chart together, not just the user's alone, since these outcomes depend "
            "on both people's charts jointly. If the user has had more than one marriage, "
            "the spouse above is the current one; earlier marriages involved other people."
        )
    elif profile.get("is_married"):
        # Married but spouse details aren't on file (needs_spouse_details
        # -- see app.routers.profiles) -- say so ONCE rather than
        # repeating the caveat on every message, and still answer with
        # what's available rather than refusing.
        spouse_line = (
            " The user is married but hasn't provided their spouse's birth details yet, so "
            "questions about shared matters (kids' health, either side's parents'/in-laws' "
            "health, money, property/asset purchases) can only be answered from the user's own "
            "chart for now -- mention once, briefly, that adding spouse birth details would let "
            "you give a fuller answer, but don't repeat that caveat in every reply."
        )
    else:
        spouse_line = ""

    return (
        "You are an astrology assistant. "
        + time_anchor_line
        + " "
        + location_line
        + chart_line
        + events_block
        + language_line
        + spouse_line
        + " Answer astrology questions grounded in this chart and these events. This is guidance "
        "and entertainment, not medical, legal, or financial advice. Keep answers short and to the "
        "point -- 3 to 5 sentences by default, no long lists or headers, and no restating the "
        "question back. Only go longer if the user explicitly asks for more detail."
    )


# Per-minute brake on top of the daily question quota (app/quota.py).
_chat_rate_limit = limit_per_user("chat", max_calls=10, window_seconds=60)


@router.post("/conversations/{conversation_id}/messages")
async def send_message(conversation_id: str, payload: MessageCreate, user: dict = Depends(_chat_rate_limit)):
    conv = await conversations_collection().find_one({"_id": conversation_id, "userId": user["_id"]})
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    consumed = await check_and_consume_quota(user)  # raises HTTPException(402) if no quota left

    profile = await birth_profiles_collection().find_one({"_id": conv["profileId"]}) or {}
    # Deliberately NOT capped/trimmed -- unlike chat history (just the
    # back-and-forth text, safe to shorten) and reply length, life events
    # are the actual evidentiary basis the model reasons from for every
    # astrology answer. An old-but-significant event (an early marriage,
    # a childhood health issue, a career change years back) is exactly
    # the kind of thing a recency-based cap would drop, and losing it
    # silently degrades answer quality/correctness -- not an acceptable
    # trade for a token saving that's usually small anyway (most profiles
    # are nowhere near 1000 events).
    events = (
        await capped_list(life_events_collection().find({"profileId": conv["profileId"]}), 1000, "life events")
        if profile
        else []
    )
    # Sorted by when each event actually happened, not insertion order --
    # otherwise e.g. an earlier-born child logged AFTER a younger sibling
    # would be presented to the model in the wrong sequence. See
    # app.event_ordering.
    events = sort_events_chronologically(events)
    context = _build_context(profile, events)

    # Prior turns in this conversation, most-recent-first from the query
    # (so the length cap keeps the LATEST MAX_HISTORY_MESSAGES turns, not
    # the earliest -- sorting ascending-then-limiting would otherwise grab
    # the oldest N messages and never advance, silently losing all recent
    # context once a conversation outgrows the cap), then reversed back to
    # chronological order before replaying to the LLM, which is the order
    # it needs to actually read as a coherent conversation.
    prior_messages = await messages_collection().find(
        {"conversationId": conversation_id}
    ).sort("createdAt", -1).to_list(length=MAX_HISTORY_MESSAGES)
    prior_messages.reverse()
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
        # Sanitized reason only -- never the raw exception. For an LLM
        # provider failure this can be an httpx.HTTPStatusError whose
        # message embeds the provider's raw response body, which could
        # echo back request content depending on the provider's own
        # error format (see llm_gateway._raise_for_status_with_body).
        # The user's actual question (payload.content) is deliberately
        # never logged here either.
        logger.error(
            "Chat answer failed for conversation %s: %s", conversation_id, summarize_provider_error(exc)
        )
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
