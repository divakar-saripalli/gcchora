"""End-to-end tests through the real FastAPI app, with the in-memory
database and a stand-in for the LLM providers (no network calls)."""
import copy
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

import main
from app.db import birth_profiles_collection, life_events_collection
from app.routers import rectify
from app.security import create_access_token, get_current_user
from tests.conftest import run

USER = {"_id": "u1", "usage": {"free_questions_remaining": 5, "daily_question_used_on": None, "ad_unlock_credits": 0}}


def llm_result(confidence=60.0):
    return {
        "ayanamsa_results": [
            {"ayanamsa": "LAHIRI", "rectified_birth_time_24h": "10:04:00", "confidence_score": confidence, "reasoning": "r1"},
            {"ayanamsa": "RAMAN", "rectified_birth_time_24h": "10:09:00", "confidence_score": 40.0, "reasoning": "r2"},
        ],
        "reasoning": "overall",
        "suggested_followups": [
            {"category": "JOB_CHANGE", "question_text": "Did you change jobs?", "time_frame_hint": "around 2012"},
            {"category": "RELOCATION", "question_text": "Did you move city?", "time_frame_hint": "around 2014"},
        ],
    }


@pytest.fixture
def api(db, monkeypatch):
    db["users"].docs.append(copy.deepcopy(USER))
    run(birth_profiles_collection().insert_one({
        "_id": "p1", "userId": "u1", "label": "Self", "birth_date": "1990-01-01", "birth_time_24h": "10:00:00",
        "geographic_coordinates": {"latitude": 12.9, "longitude": 77.6, "timezone_offset_hours": 5.5},
        "createdAt": datetime.now(timezone.utc),
    }))
    for i in range(6):
        run(life_events_collection().insert_one({
            "_id": f"e{i}", "profileId": "p1", "category": "OTHER", "exact_date": f"20{10 + i}-01-01",
            "impact_weight_score": 1.0, "notes": f"event {i}", "createdAt": datetime.now(timezone.utc),
        }))

    calls = {"llm": 0, "confidence": 60.0}

    async def fake_from_all(context, prompt, schema):
        calls["llm"] += 1
        return {"gemini": llm_result(calls["confidence"])}, {}

    monkeypatch.setattr(rectify, "generate_structured_from_all", fake_from_all)
    main.app.dependency_overrides[get_current_user] = lambda: copy.deepcopy(USER)
    with TestClient(main.app) as client:
        client.calls = calls
        client.db = db
        yield client
    main.app.dependency_overrides.clear()


def test_health_and_indexes_created_at_startup(api):
    assert api.get("/health").json() == {"status": "ok"}
    assert api.db["messages"].indexes == [[("conversationId", 1), ("createdAt", 1)]]
    assert api.db["life_events"].indexes == ["profileId"]


def test_rectify_returns_candidates_and_stores_sensitive_fields_encrypted(api):
    body = api.post("/profiles/p1/rectify").json()
    assert body["status"] == "pending_review"
    assert [f["category"] for f in body["suggested_followups"]] == ["JOB_CHANGE", "RELOCATION"]
    assert len(body["candidates"][0]["ayanamsa_results"]) == 2
    stored = api.db["birth_profiles"].docs[0]
    assert stored["birth_time_24h"].startswith("v1:")
    assert stored["calibration"]["status"] == "pending_review"
    assert api.db["life_events"].docs[0]["notes"].startswith("v1:")


def test_followup_answers_are_batched_into_one_llm_call(api):
    api.post("/profiles/p1/rectify")
    assert api.calls["llm"] == 1
    first = api.post("/profiles/p1/rectify/answer", json={
        "category": "JOB_CHANGE", "occurred_score": 4, "notes": "yes, 2013", "question_text": "Did you change jobs?",
    }).json()
    assert api.calls["llm"] == 1  # one question still open: no new LLM call
    assert [f["category"] for f in first["suggested_followups"]] == ["RELOCATION"]
    api.post("/profiles/p1/rectify/answer", json={"category": "RELOCATION", "occurred_score": 2})
    assert api.calls["llm"] == 2  # batch complete: one rerun covering both answers


def test_daily_rectify_cap_returns_429_and_failed_runs_are_not_counted(api, monkeypatch):
    for _ in range(3):  # RECTIFY_DAILY_LIMIT=3 in tests
        assert api.post("/profiles/p1/rectify").status_code == 200
    blocked = api.post("/profiles/p1/rectify")
    assert blocked.status_code == 429 and "quota has been reached" in blocked.json()["detail"]
    assert api.calls["llm"] == 3

    # A run that fails (no provider available) gives its slot back.
    api.db["users"].docs[0]["usage"]["rectify_runs_count"] = 2

    async def nothing_available(context, prompt, schema):
        return {}, {"gemini": "rate-limited (429) -- try again shortly"}

    monkeypatch.setattr(rectify, "generate_structured_from_all", nothing_available)
    assert api.post("/profiles/p1/rectify").status_code == 502
    assert api.db["users"].docs[0]["usage"]["rectify_runs_count"] == 2


def test_high_confidence_locks_and_locked_recheck_still_offers_followups(api, monkeypatch):
    api.calls["confidence"] = 97.0
    assert api.post("/profiles/p1/rectify").json()["status"] == "locked"

    async def fake_recheck(context, prompt, schema, provider=None):
        return {
            "rectified_birth_time_24h": "10:04:00", "confidence_score": 90.0, "reasoning": "still fits",
            "suggested_followups": [{"category": "TRAVEL", "question_text": "Did you travel abroad?", "time_frame_hint": "2016"}],
        }

    monkeypatch.setattr(rectify, "generate_structured_answer", fake_recheck)
    body = api.post("/profiles/p1/rectify").json()
    assert body["status"] == "locked" and body["candidates"] == []
    assert [f["category"] for f in body["suggested_followups"]] == ["TRAVEL"]


def test_choose_ayanamsa_locks_to_the_picked_option_without_an_llm_call(api):
    api.post("/profiles/p1/rectify")
    body = api.post("/profiles/p1/rectify/choose-ayanamsa", json={"provider": "gemini", "ayanamsa": "RAMAN"}).json()
    assert api.calls["llm"] == 1
    assert (body["status"], body["chosen_ayanamsa"], body["rectified_birth_time_24h"]) == ("locked", "RAMAN", "10:09:00")


def test_reset_followup_answers_route_is_not_swallowed_by_the_event_id_route(api):
    run(life_events_collection().insert_one({
        "_id": "f1", "profileId": "p1", "category": "X", "source": "suggested_followup_answer",
        "occurred_score": 3, "createdAt": datetime.now(timezone.utc),
    }))
    response = api.delete("/profiles/p1/events/followup-answers")
    assert response.status_code == 200 and response.json()["deleted_count"] == 1
    assert len(api.db["life_events"].docs) == 6


def test_events_list_is_chronological_and_other_users_profiles_are_hidden(api):
    events = api.get("/profiles/p1/events").json()
    assert [e["exact_date"] for e in events] == sorted(e["exact_date"] for e in events)
    assert events[0]["notes"] == "event 0"  # decrypted on the way out
    main.app.dependency_overrides[get_current_user] = lambda: {"_id": "someone-else"}
    assert api.get("/profiles/p1/events").status_code == 404


def test_over_long_chat_message_is_rejected_before_any_work(api):
    response = api.post("/conversations/c1/messages", json={"content": "x" * 4001})
    assert response.status_code == 422


def test_logout_everywhere_revokes_existing_tokens(api):
    main.app.dependency_overrides.clear()  # use real token checking for this one
    token = create_access_token("u1")
    headers = {"Authorization": f"Bearer {token}"}
    assert api.get("/me/usage", headers=headers).status_code == 200
    assert api.post("/auth/logout-all", headers=headers).json() == {"status": "signed_out_everywhere"}
    assert api.get("/me/usage", headers=headers).status_code == 401


def test_rate_limit_on_rectify(api):
    api.db["users"].docs[0]["usage"]["rectify_runs_count"] = -100  # keep the daily cap out of the way
    api.db["users"].docs[0]["usage"]["rectify_runs_on"] = date.today().isoformat()
    codes = [api.post("/profiles/p1/rectify").status_code for _ in range(7)]
    assert codes == [200] * 6 + [429]


def test_chat_still_works_after_the_calibration_quota_is_used_up(api, monkeypatch):
    from app.routers import chat

    async def fake_answer(*args, **kwargs):
        return "Here is your answer."

    monkeypatch.setattr(chat, "generate_answer", fake_answer)
    api.db["conversations"].docs.append({
        "_id": "c1", "userId": api.db["users"].docs[0]["_id"], "profileId": "p1", "title": "t",
        "startedAt": datetime.now(timezone.utc), "lastMessageAt": datetime.now(timezone.utc),
    })
    for _ in range(3):
        api.post("/profiles/p1/rectify")
    assert api.post("/profiles/p1/rectify").status_code == 429
    reply = api.post("/conversations/c1/messages", json={"content": "How is my career?"})
    assert reply.status_code == 200 and reply.json()["content"] == "Here is your answer."


def test_locked_profile_batches_followup_answers_into_one_recheck(api, monkeypatch):
    api.calls["confidence"] = 97.0
    assert api.post("/profiles/p1/rectify").json()["status"] == "locked"
    rechecks = {"n": 0}

    async def fake_recheck(context, prompt, schema, provider=None):
        rechecks["n"] += 1
        return {
            "rectified_birth_time_24h": "10:04:00", "confidence_score": 90.0, "reasoning": "fits",
            "suggested_followups": [
                {"category": f"Q{rechecks['n']}A", "question_text": "a?", "time_frame_hint": "March 2014 to June 2014"},
                {"category": f"Q{rechecks['n']}B", "question_text": "b?", "time_frame_hint": "May 2015 to July 2015"},
            ],
        }

    monkeypatch.setattr(rectify, "generate_structured_answer", fake_recheck)
    first = api.post("/profiles/p1/rectify").json()
    assert [f["category"] for f in first["suggested_followups"]] == ["Q1A", "Q1B"]

    one = api.post("/profiles/p1/rectify/answer", json={"category": "Q1A", "occurred_score": 4}).json()
    assert rechecks["n"] == 1  # recorded only: no recheck yet
    assert [f["category"] for f in one["suggested_followups"]] == ["Q1B"]  # same batch, not replaced

    done = api.post("/profiles/p1/rectify/answer", json={"category": "Q1B", "occurred_score": 3}).json()
    assert rechecks["n"] == 2  # batch complete: exactly one recheck
    assert [f["category"] for f in done["suggested_followups"]] == ["Q2A", "Q2B"]


def _seed_family_events(api):
    for _id, cat, d in (("m1", "MARRIAGE", "2005-01-10"), ("m2", "MARRIAGE", "2015-06-01"),
                        ("k1", "KIDS", "2008-03-03"), ("c1x", "CAREER_DISRUPTION", "2010-01-01")):
        run(life_events_collection().insert_one({
            "_id": _id, "profileId": "p1", "category": cat, "exact_date": d,
            "impact_weight_score": 1.0, "notes": f"note {_id}", "createdAt": datetime.now(timezone.utc),
        }))
    run(birth_profiles_collection().insert_one({
        "_id": "p2", "userId": api.db["users"].docs[0]["_id"], "label": "Wife", "relation": "spouse",
        "birth_date": "1988-05-05", "birth_time_24h": "06:00:00",
        "geographic_coordinates": {"latitude": 1, "longitude": 1, "timezone_offset_hours": 5.5},
        "calibration": {"status": "uncalibrated"}, "createdAt": datetime.now(timezone.utc),
    }))


def test_import_copies_only_chosen_shareable_events_and_skips_duplicates(api):
    _seed_family_events(api)
    body = {"source_profile_id": "p1", "event_ids": ["m2", "k1", "c1x", "nope"]}
    first = api.post("/profiles/p2/events/import", json=body).json()
    assert first == {"imported": 2, "skipped_duplicates": 0, "skipped_not_shareable": 2}
    copied = [e for e in api.db["life_events"].docs if e["profileId"] == "p2"]
    assert sorted(e["category"] for e in copied) == ["KIDS", "MARRIAGE"]
    assert all(e["source"] == "copied_from_profile" and e["notes"].startswith("v1:") for e in copied)
    assert all(e["exact_date"] != "2005-01-10" for e in copied)  # the first marriage was not chosen

    again = api.post("/profiles/p2/events/import", json=body).json()
    assert again["imported"] == 0 and again["skipped_duplicates"] == 2


def test_import_rejects_same_profile_and_other_users_profiles(api):
    _seed_family_events(api)
    assert api.post("/profiles/p1/events/import", json={"source_profile_id": "p1", "event_ids": ["m1"]}).status_code == 400
    api.db["birth_profiles"].docs[-1]["userId"] = "someone-else"
    assert api.post("/profiles/p2/events/import", json={"source_profile_id": "p1", "event_ids": ["m1"]}).status_code == 404


def test_new_profile_stores_relation_and_old_ones_default_to_self(api):
    body = {
        "label": "Wife", "relation": "spouse", "birth_date": "1988-05-05", "birth_time_24h": "06:00:00",
        "geographic_coordinates": {"latitude": 17.4, "longitude": 78.4, "timezone_offset_hours": 5.5},
    }
    created = api.post("/profiles", json=body).json()
    assert created["relation"] == "spouse"
    assert api.db["birth_profiles"].docs[-1]["relation"] == "spouse"
    patched = api.patch(f"/profiles/{created['id']}", json={"label": "Spouse"}).json()
    assert patched["relation"] == "spouse"  # editing must not reset it
    assert api.get("/profiles/p1").json().get("relation", "self") == "self"
