from datetime import datetime, timezone

from app.routers import rectify

PROFILE = {
    "birth_date": "1990-01-01", "birth_time_24h": "10:00:00",
    "geographic_coordinates": {"latitude": 12.9, "longitude": 77.6, "timezone_offset_hours": 5.5},
}
NEXT_YEAR = datetime.now(timezone.utc).year + 1


def test_followup_answer_is_rendered_as_a_question_and_answer_pair():
    text = rectify._format_events([
        {"category": "MARRIAGE", "exact_date": "2015-05-01", "impact_weight_score": 1.0},
        {
            "category": "CAREER", "source": "suggested_followup_answer", "occurred_score": 4,
            "question_text": "Did you change jobs?", "notes": "Yes, but in 2018 not 2015",
            "time_frame_hint": "around 2015",
        },
        {
            "category": "ILLNESS", "source": "suggested_followup_answer", "occurred_score": 1,
            "question_text": "Any surgery?", "time_frame_hint": "2012",
        },
    ])
    lines = text.splitlines()
    assert lines[0].startswith("- MARRIAGE on 2015-05-01")
    assert 'Follow-up question that was asked: "Did you change jobs?"' in lines[1]
    assert "User's answer: Yes, but in 2018 not 2015" in lines[1]
    assert "DISCONFIRMING" in lines[2]
    assert rectify._format_events([]) == "(none)"


def test_future_only_hints_are_detected():
    assert rectify._is_future_only_hint(f"sometime in {NEXT_YEAR}")
    assert not rectify._is_future_only_hint(f"between 2020 and {NEXT_YEAR}")
    assert not rectify._is_future_only_hint("around when you started college")


def test_candidate_parsing_picks_best_and_drops_future_followups():
    raw = {
        "ayanamsa_results": [
            {"ayanamsa": "LAHIRI", "rectified_birth_time_24h": "10:04:00", "confidence_score": 70, "reasoning": "a"},
            {"ayanamsa": "RAMAN", "rectified_birth_time_24h": "10:10:00", "confidence_score": 82, "reasoning": "b"},
        ],
        "reasoning": "overall",
        "suggested_followups": [
            {"category": "FUTURE", "question_text": "q0", "time_frame_hint": f"in {NEXT_YEAR}"},
            {"category": "A", "question_text": "q1", "time_frame_hint": "2012"},
            {"category": "B", "question_text": "", "time_frame_hint": ""},
            {"category": "C", "question_text": "q3", "time_frame_hint": "2014"},
            {"category": "D", "question_text": "q4", "time_frame_hint": "2016"},
        ],
    }
    candidate = rectify._candidate_from_raw("gemini", raw)
    assert (candidate.best_ayanamsa, candidate.best_confidence_score) == ("RAMAN", 82.0)
    assert [a.reasoning for a in candidate.ayanamsa_results] == ["a", "b"]
    assert [f.category for f in candidate.suggested_followups] == ["A", "B", "C"]  # capped at 3, future one dropped
    assert candidate.suggested_followups[1].question_text  # fallback question text filled in


def test_recheck_prompt_and_schema_ask_for_followups():
    schema = rectify._recheck_schema()
    assert "suggested_followups" in schema["required"]
    prompt = rectify._build_recheck_prompt(PROFILE, [], "LAHIRI", "10:05:00", [])
    assert "suggest 2-3 more life events" in prompt
    full = rectify._build_prompt(PROFILE, [], 30, [])
    assert "suggest 2-3 more life events" in full and "LAHIRI" in full


def test_agreement_requires_same_ayanamsa_and_close_times():
    def cand(ayanamsa, t):
        return rectify.RectifyCandidate(
            provider="p", ayanamsa_results=[], best_ayanamsa=ayanamsa,
            best_rectified_birth_time_24h=t, best_confidence_score=50, reasoning="",
        )

    assert rectify._agree(cand("LAHIRI", "10:00:00"), cand("LAHIRI", "10:14:00"))
    assert not rectify._agree(cand("LAHIRI", "10:00:00"), cand("LAHIRI", "10:20:00"))
    assert not rectify._agree(cand("LAHIRI", "10:00:00"), cand("RAMAN", "10:00:00"))
