"""Shared helper for ordering a profile's life events chronologically by
when they actually happened -- not by insertion/creation order.

Every current place that reads a profile's life events (the life events
list screen, the rectification prompt, the chat context) fetches them
with no sort at all, or sorted by `createdAt` (when they were logged),
never by the event's own real-world date. That's indistinguishable from
correct most of the time, but breaks for events logged out of
chronological order -- e.g. a user adds "child born 2020-10-21" first,
then later adds an earlier "child born 2017-08-23" sibling: both the
life events list and the LLM prompt would show the 2020 birth before the
2017 one, the wrong way round for anything that depends on sequence
(e.g. figuring out which child is older).

Each event line already carries its own explicit date text, so a
capable LLM reading carefully can often still get this right regardless
of presentation order -- but relying on that is fragile, especially once
dates are vague time_frame_hints rather than exact dates, and the human-
facing events list has no such excuse: it should just show things in the
order they happened.
"""
import re
from datetime import date, datetime, timezone

# createdAt is always stored as a timezone-aware UTC datetime (see every
# insert site across app/routers/*.py) -- this fallback must match that
# awareness, or comparing it against a real createdAt raises
# "can't compare offset-naive and offset-aware datetimes".
_MIN_AWARE_DATETIME = datetime.min.replace(tzinfo=timezone.utc)

_YEAR_RE = re.compile(r"(?<!\d)(\d{4})(?!\d)")

# Sentinel for events with no determinable date at all (no exact_date,
# and no year mentioned anywhere in time_frame_hint) -- sorted after
# every dated event, so a totally undated event doesn't accidentally
# land in the middle of a real timeline for no reason.
_UNKNOWN_DATE = date.max


def _best_guess_date(event: dict) -> date:
    """Best real-world date to sort this event by. Prefers the exact
    date if one was given; otherwise looks for a year (or year range) in
    the free-text time_frame_hint and uses its midpoint, placed at that
    year's July 1st as a rough middle-of-year approximation. This is
    ONLY ever used to order events relative to each other -- never
    stored or shown to the user -- so an approximation here doesn't
    conflict with the deliberate choice (see DefectBacklog) not to
    fabricate and persist a precise date for data the user never gave
    one for."""
    exact = event.get("exact_date")
    if exact:
        try:
            return datetime.strptime(exact, "%Y-%m-%d").date()
        except ValueError:
            pass  # malformed exact_date -- fall through to the hint/unknown path
    years = [int(y) for y in _YEAR_RE.findall(event.get("time_frame_hint") or "")]
    if years:
        avg_year = round(sum(years) / len(years))
        try:
            return date(avg_year, 7, 1)
        except ValueError:
            return _UNKNOWN_DATE
    return _UNKNOWN_DATE


def sort_events_chronologically(events: list[dict]) -> list[dict]:
    """Returns a NEW list ordered oldest-first by each event's actual (or
    best-guess) real-world date. Events with no determinable date at all
    sort after every dated event, with a stable secondary order by
    `createdAt` so they don't shuffle unpredictably between calls."""
    return sorted(events, key=lambda e: (_best_guess_date(e), e.get("createdAt") or _MIN_AWARE_DATETIME))
