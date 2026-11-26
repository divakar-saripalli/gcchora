"""Computes the historically-correct UTC offset for a birth date/time at a
given location, using the IANA timezone database -- replaces trusting a
client-entered timezone_offset_hours, which has no way to account for
daylight-saving rules that were actually in effect on a specific
historical date at a specific place. For example, a July 1990 New York
birth needs -4 (EDT), not -5 (EST) -- the "standard" offset most people
would type from memory ("New York is UTC-5") is wrong for half the year.

Fully offline, no network call and no cost: timezonefinder ships its own
lookup index, and zoneinfo (stdlib since Python 3.9) ships the full IANA
tzdata, including historical DST transition rules.
"""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from timezonefinder import TimezoneFinder

logger = logging.getLogger(__name__)

# TimezoneFinder loads a sizeable in-memory spatial index -- built once,
# lazily, then reused.
_tf: TimezoneFinder | None = None


def _get_tf() -> TimezoneFinder:
    """Built on first use, not at import -- the index is large, and
    importing this module (app startup, every test) shouldn't pay for it."""
    global _tf
    if _tf is None:
        _tf = TimezoneFinder()
    return _tf


def compute_timezone_offset_hours(
    latitude: float, longitude: float, birth_date: str, birth_time_24h: str
) -> float | None:
    """Returns the UTC offset in hours (e.g. 5.5, -4.0) actually in effect
    at this latitude/longitude on this specific birth date and time --
    correctly DST-aware, unlike a flat "this place is UTC+X" assumption.

    Returns None if no timezone could be resolved for the given
    coordinates (e.g. open ocean) or the inputs couldn't be parsed --
    callers should fall back to whatever value they already have in that
    case, never let this block profile creation/editing.
    """
    try:
        tz_name = _get_tf().timezone_at(lat=latitude, lng=longitude)
        if tz_name is None:
            # Deliberately not logging the actual coordinates here --
            # exact birth coordinates are sensitive personal data (see
            # app/field_encryption.py, which encrypts this same field at
            # rest) and have no business sitting in plaintext in an
            # application log just because a lookup happened to miss.
            logger.warning("No timezone found for the given coordinates -- leaving timezone_offset_hours unchanged.")
            return None
        local_dt = datetime.strptime(f"{birth_date} {birth_time_24h}", "%Y-%m-%d %H:%M:%S")
        local_dt = local_dt.replace(tzinfo=ZoneInfo(tz_name))
        offset = local_dt.utcoffset()
        return offset.total_seconds() / 3600 if offset is not None else None
    except Exception:
        # Same reasoning as above -- never log the actual coordinates or
        # exact birth date/time. logger.exception still captures the
        # stack trace and exception type/message, which is enough to
        # diagnose a parsing bug without writing someone's birth details
        # to disk in plaintext.
        logger.exception("Failed to compute timezone offset for the given coordinates/date/time -- leaving unchanged.")
        return None
