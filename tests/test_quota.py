import asyncio
from datetime import date

import pytest
from fastapi import HTTPException

from app import quota
from tests.conftest import run

TODAY = date.today().isoformat()


def add_user(db, usage):
    doc = {"_id": "u1"}
    if usage is not None:
        doc["usage"] = usage
    db["users"].docs.append(doc)
    return dict(doc)


def stored_usage(db):
    return db["users"].docs[0]["usage"]


def test_pools_are_spent_in_order_then_402(db):
    user = add_user(db, {"free_questions_remaining": 1, "daily_question_used_on": None, "ad_unlock_credits": 1})
    assert run(quota.check_and_consume_quota(user))["pool"] == "free_questions_remaining"
    assert run(quota.check_and_consume_quota(user)) == {"pool": "daily_question_used_on", "previous_value": None}
    assert run(quota.check_and_consume_quota(user))["pool"] == "ad_unlock_credits"
    with pytest.raises(HTTPException) as exc:
        run(quota.check_and_consume_quota(user))
    assert exc.value.status_code == 402
    assert stored_usage(db) == {
        "free_questions_remaining": 0, "daily_question_used_on": TODAY, "ad_unlock_credits": 0,
    }


def test_refund_reverses_each_pool(db):
    user = add_user(db, {"free_questions_remaining": 1, "daily_question_used_on": "2020-01-01", "ad_unlock_credits": 1})
    pools = []
    for _ in range(3):
        before = dict(stored_usage(db))
        consumed = run(quota.check_and_consume_quota(user))
        pools.append(consumed["pool"])
        run(quota.refund_quota("u1", consumed))
        assert stored_usage(db) == before
        # Spend it for real so the next pass reaches the next pool.
        run(quota.check_and_consume_quota(user))
    assert pools == ["free_questions_remaining", "daily_question_used_on", "ad_unlock_credits"]


def test_concurrent_requests_cannot_spend_the_same_credit(db):
    user = add_user(db, {"free_questions_remaining": 1, "daily_question_used_on": TODAY, "ad_unlock_credits": 0})

    async def attempt():
        try:
            await quota.check_and_consume_quota(user)
            return True
        except HTTPException:
            return False

    async def burst():
        return await asyncio.gather(*(attempt() for _ in range(10)))

    assert sum(run(burst())) == 1
    assert stored_usage(db)["free_questions_remaining"] == 0


def test_missing_or_null_usage_is_healed(db):
    user = add_user(db, None)
    assert run(quota.check_and_consume_quota(user))["pool"] == "free_questions_remaining"
    assert stored_usage(db)["free_questions_remaining"] == 4

    db["users"].docs.clear()
    user = add_user(db, {"free_questions_remaining": None, "daily_question_used_on": None, "ad_unlock_credits": None})
    run(quota.check_and_consume_quota(user))
    assert stored_usage(db)["free_questions_remaining"] == 4
    assert stored_usage(db)["ad_unlock_credits"] == 0


def test_ad_credit_is_added(db):
    add_user(db, {"free_questions_remaining": 0, "daily_question_used_on": TODAY, "ad_unlock_credits": None})
    run(quota.credit_ad_unlock("u1"))
    run(quota.credit_ad_unlock("u1"))
    assert stored_usage(db)["ad_unlock_credits"] == 2


def test_rectify_runs_are_capped_per_day_and_refundable(db):
    add_user(db, {"free_questions_remaining": 5})
    for _ in range(3):  # RECTIFY_DAILY_LIMIT=3 in tests
        run(quota.consume_rectify_run("u1"))
    with pytest.raises(HTTPException) as exc:
        run(quota.consume_rectify_run("u1"))
    assert exc.value.status_code == 429

    run(quota.refund_rectify_run("u1"))
    run(quota.consume_rectify_run("u1"))  # the refunded run is usable again

    # A new day starts a fresh count.
    stored_usage(db)["rectify_runs_on"] = "2020-01-01"
    run(quota.consume_rectify_run("u1"))
    assert stored_usage(db)["rectify_runs_count"] == 1
    assert stored_usage(db)["free_questions_remaining"] == 5  # question pool untouched
