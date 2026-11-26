from datetime import datetime, timezone

from app.event_ordering import sort_events_chronologically


def test_events_sort_by_when_they_happened_not_when_logged():
    logged_first = {"category": "KIDS", "exact_date": "2020-10-21", "createdAt": datetime(2026, 1, 1, tzinfo=timezone.utc)}
    logged_second = {"category": "KIDS", "exact_date": "2017-08-23", "createdAt": datetime(2026, 1, 2, tzinfo=timezone.utc)}
    assert sort_events_chronologically([logged_first, logged_second]) == [logged_second, logged_first]


def test_hint_only_and_undated_events():
    exact = {"exact_date": "2016-01-01"}
    hinted = {"time_frame_hint": "sometime between 2012 and 2014"}
    undated = {"time_frame_hint": "around when you started college"}
    malformed = {"exact_date": "not-a-date", "time_frame_hint": "in 2010"}
    ordered = sort_events_chronologically([undated, exact, hinted, malformed])
    assert ordered == [malformed, hinted, exact, undated]
