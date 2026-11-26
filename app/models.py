"""Pydantic schemas for API request/response bodies.

MongoDB documents themselves are handled as plain dicts (see app/db.py) --
these models validate what crosses the HTTP boundary, not what's stored.
"""
import re
from datetime import date as date_type
from datetime import datetime as datetime_type
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

# Length caps for free text that arrives from a client. Everything here
# ends up stored and most of it ends up inside an LLM prompt, so an
# unbounded string is both a storage and a cost problem. Applied to
# request models only -- response models (the *Out classes) re-declare
# these fields without a cap, so a record that is already stored can
# always be read back.
MAX_CATEGORY_LEN = 64
MAX_NOTES_LEN = 2000
MAX_QUESTION_LEN = 1000
MAX_HINT_LEN = 500
MAX_CHAT_MESSAGE_LEN = 4000


# ---- Auth ----

class GoogleLoginRequest(BaseModel):
    id_token: str = Field(max_length=8192)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    is_new_user: bool


# ---- Request validators ----
# Used on request models only. Response models override them as no-ops
# (or re-declare the field) so a record that is already stored can always
# be read back, even if it predates a rule.

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?$")


def _check_date(value: str | None, *, allow_future: bool) -> str | None:
    if value is None:
        return value
    try:
        parsed = datetime_type.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("must be a real date in YYYY-MM-DD format")
    if parsed.year < 1800:
        raise ValueError("date is implausibly old")
    if not allow_future and parsed > date_type.today():
        raise ValueError("date cannot be in the future")
    return value


def _check_time(value: str | None) -> str | None:
    if value is None:
        return value
    if not _TIME_RE.match(value):
        raise ValueError("must be a 24-hour time, HH:MM or HH:MM:SS")
    return value


# ---- Birth profiles ----

class GeoCoordinates(BaseModel):
    latitude: float
    longitude: float
    timezone_offset_hours: float


class GeoCoordinatesIn(GeoCoordinates):
    """Request-side coordinates with range checks."""
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    timezone_offset_hours: float = Field(ge=-14, le=14)


ProfileRelation = Literal["self", "spouse", "child", "parent", "other"]


class BirthProfileCreate(BaseModel):
    label: str = Field(default="Self", max_length=60)
    # How this person relates to the account holder. Profiles created
    # before this field existed have none stored and read back as "self".
    relation: ProfileRelation = "self"
    birth_date: str  # YYYY-MM-DD
    birth_time_24h: str  # HH:MM:SS
    geographic_coordinates: GeoCoordinatesIn
    # Assumed uncertainty in the stated birth time, used as the rectification
    # search window. Defaults to +/- 30 minutes when not given.
    birth_time_uncertainty_minutes: int = Field(default=30, ge=0, le=720)
    # Spouse birth details -- used for cross-chart ("synastry") reasoning:
    # marriage/children/parents-in-law-health life events and shared-life
    # questions (kids' health, in-laws' health, money/property/assets)
    # are correlated against BOTH charts, not just this profile's own
    # (see app.routers.rectify._build_prompt and
    # app.routers.chat._build_context). All three spouse fields are
    # optional independently of is_married -- a married profile with
    # some/none of them filled in is exactly the backward-compatibility
    # case app.routers.profiles.needs_spouse_details flags for the UI to
    # prompt for.
    is_married: bool = False
    spouse_birth_date: Optional[str] = None  # YYYY-MM-DD
    spouse_birth_time_24h: Optional[str] = None  # HH:MM:SS
    spouse_geographic_coordinates: Optional[GeoCoordinatesIn] = None

    @field_validator("birth_date", "spouse_birth_date")
    @classmethod
    def _validate_birth_dates(cls, v):
        return _check_date(v, allow_future=False)

    @field_validator("birth_time_24h", "spouse_birth_time_24h")
    @classmethod
    def _validate_birth_times(cls, v):
        return _check_time(v)


class BirthProfileOut(BirthProfileCreate):
    # Lenient on the way out: re-declare the constrained fields and turn
    # the request validators into no-ops (same names override them).
    geographic_coordinates: GeoCoordinates
    spouse_geographic_coordinates: Optional[GeoCoordinates] = None
    birth_time_uncertainty_minutes: int = 30

    @field_validator("birth_date", "spouse_birth_date")
    @classmethod
    def _validate_birth_dates(cls, v):
        return v

    @field_validator("birth_time_24h", "spouse_birth_time_24h")
    @classmethod
    def _validate_birth_times(cls, v):
        return v

    id: str
    label: str = "Self"  # uncapped on the way out, see MAX_* note above
    calibration_status: Literal[
        "uncalibrated", "pending_review", "calibrated", "locked"
    ] = "uncalibrated"
    # True when is_married but one or more of the three spouse_* fields
    # above is still missing -- the signal the mobile app uses to prompt
    # for them, including for profiles created before this feature
    # existed (see app.routers.profiles.needs_spouse_details).
    needs_spouse_details: bool = False


class BirthProfileUpdate(BaseModel):
    """All fields optional -- only the ones the client sends are changed.
    Editing birth_date/birth_time_24h/geographic_coordinates invalidates
    any existing calibration (it was computed against the old values), so
    app.routers.profiles.update_profile resets it; the profile's recorded
    life events are untouched either way, since they're facts about the
    person's life, not the chart calculation.

    Spouse fields deliberately do NOT invalidate calibration the same
    way -- adding/changing them instead nudges toward a RECHECK (see
    app.routers.profiles.update_profile's spouse_changed handling),
    since newly-available spouse data should refine, not discard, an
    existing rectification."""
    label: Optional[str] = Field(default=None, max_length=60)
    birth_date: Optional[str] = None
    birth_time_24h: Optional[str] = None
    geographic_coordinates: Optional[GeoCoordinatesIn] = None
    birth_time_uncertainty_minutes: Optional[int] = Field(default=None, ge=0, le=720)
    is_married: Optional[bool] = None
    spouse_birth_date: Optional[str] = None
    spouse_birth_time_24h: Optional[str] = None
    spouse_geographic_coordinates: Optional[GeoCoordinatesIn] = None

    @field_validator("birth_date", "spouse_birth_date")
    @classmethod
    def _validate_birth_dates(cls, v):
        return _check_date(v, allow_future=False)

    @field_validator("birth_time_24h", "spouse_birth_time_24h")
    @classmethod
    def _validate_birth_times(cls, v):
        return _check_time(v)


# ---- Life events ----

class LifeEventIn(BaseModel):
    category: str = Field(max_length=MAX_CATEGORY_LEN)
    exact_date: str  # YYYY-MM-DD
    impact_weight_score: float = Field(default=1.0, ge=0, le=10)

    @field_validator("exact_date")
    @classmethod
    def _validate_exact_date(cls, v):
        return _check_date(v, allow_future=False)

    # Free-text detail -- required in spirit (not enforced) for category
    # "OTHER", optional elsewhere, e.g. "Bought the Jubilee Hills flat".
    notes: Optional[str] = Field(default=None, max_length=MAX_NOTES_LEN)


class LifeEventOut(LifeEventIn):
    id: str
    # Uncapped on the way out, see MAX_* note at the top of this file.
    category: str
    notes: Optional[str] = None
    # Overridden as optional: events created from answering a rectify
    # suggested_followups question (see
    # app.routers.rectify.add_followup_and_rectify) deliberately don't
    # require an exact date -- only a 1-5 "did this happen around
    # <time_frame_hint>" rating. Keeping LifeEventOut.exact_date
    # required (inherited as `str`) made FastAPI's response_model
    # validation raise -- and therefore GET /profiles/{id}/events 500 --
    # for the WHOLE list the moment any one event like that existed.
    exact_date: str | None = None
    impact_weight_score: float = 1.0  # unconstrained on the way out

    @field_validator("exact_date")
    @classmethod
    def _validate_exact_date(cls, v):
        return v  # lenient on the way out (overrides the request check)

    # Echoed through so the life-events list has something to display
    # for an event with no exact_date, and so the UI can tell a
    # follow-up-question answer apart from a manually logged event.
    time_frame_hint: str | None = None
    source: str | None = None
    # Echoed through for the same reason as time_frame_hint -- lets the
    # life events list show the original follow-up question a given
    # answer responded to, not just its category slug.
    question_text: str | None = None


class LifeEventUpdate(BaseModel):
    category: Optional[str] = Field(default=None, max_length=MAX_CATEGORY_LEN)
    exact_date: Optional[str] = None
    impact_weight_score: Optional[float] = Field(default=None, ge=0, le=10)
    notes: Optional[str] = Field(default=None, max_length=MAX_NOTES_LEN)

    @field_validator("exact_date")
    @classmethod
    def _validate_exact_date(cls, v):
        return _check_date(v, allow_future=False)


class EventImportRequest(BaseModel):
    """Copy chosen events from another of the user's profiles (see
    app.routers.profiles.import_events)."""
    source_profile_id: str = Field(max_length=64)
    event_ids: list[str] = Field(min_length=1, max_length=50)


class EventImportResponse(BaseModel):
    imported: int
    skipped_duplicates: int
    skipped_not_shareable: int


class LifeEventAddedResponse(BaseModel):
    id: str
    # True when adding this event pushed a locked profile's
    # events-since-last-check counter over the threshold and triggered an
    # automatic re-evaluation as a side effect of this call. See
    # app.routers.rectify.EVENTS_SINCE_LOCK_CHECK_THRESHOLD.
    auto_rectify_triggered: bool = False


# ---- Rectification ----

class RectifyRequest(BaseModel):
    # Optionally override the profile's default search window for this run.
    time_window_minutes: Optional[int] = None


class DisambiguationQuestion(BaseModel):
    category: str
    question_text: str
    # The LLM's best plain-language guess at when an event like this would
    # plausibly fall, if one of the candidate birth times/ayanamsas is
    # correct (e.g. "sometime between 2012 and 2015") -- based on where
    # the candidates' implied dasha/bhukti periods actually differ. Lets
    # the user rate how much it resonates instead of having to recall an
    # exact date. Empty string if the model couldn't narrow it down.
    time_frame_hint: str = ""


class AyanamsaEstimate(BaseModel):
    ayanamsa: str
    rectified_birth_time_24h: str
    confidence_score: float
    # Short, specific explanation of which life events support THIS
    # particular ayanamsa/time combination via dasha/bhukti matching --
    # distinct from RectifyCandidate.reasoning, which only ever covered
    # the provider's single overall best pick. Lets the user expand a
    # lower-ranked ayanamsa option and judge for themselves whether it
    # actually fits better than the "best" one the provider picked,
    # instead of only ever seeing the top pick's reasoning. Defaults to
    # "" for older cached/stored results from before this field existed.
    reasoning: str = ""


class RectifyCandidate(BaseModel):
    """One provider's full opinion: its estimate for every curated
    ayanamsa (see app.ayanamsas.CURATED_AYANAMSAS), plus whichever of
    those it considers its own best guess."""
    provider: str
    ayanamsa_results: list[AyanamsaEstimate]
    best_ayanamsa: str
    best_rectified_birth_time_24h: str
    best_confidence_score: float
    reasoning: str
    suggested_followups: list[DisambiguationQuestion] = Field(default_factory=list)


class RectifyResult(BaseModel):
    status: Literal["pending_review", "calibrated", "locked"]
    # Populated when status is "pending_review" (candidates to compare) or
    # right after a fresh "locked" result (the single winning candidate).
    # Left empty on a routine locked-mode recheck that just confirms an
    # existing lock, to keep that response small.
    candidates: list[RectifyCandidate] = Field(default_factory=list)
    agreement: Optional[Literal["agree", "disagree"]] = None
    chosen_provider: Optional[str] = None
    chosen_ayanamsa: Optional[str] = None
    rectified_birth_time_24h: Optional[str] = None
    confidence_score: Optional[float] = None
    events_considered: int
    # A short, honest, human-readable summary of the result -- meant to be
    # shown to the user more or less verbatim.
    message: str
    # Union of the candidates' suggested categories -- optional ways to
    # sharpen the estimate further, or to help break a tie between
    # disagreeing candidates. Never required.
    suggested_followups: list[DisambiguationQuestion] = Field(default_factory=list)


class RectifyCandidateRating(BaseModel):
    provider: str
    score: int = Field(ge=1, le=5)


class RectifyScoreRequest(BaseModel):
    ratings: list[RectifyCandidateRating]


class RectifyAyanamsaChoice(BaseModel):
    """Directly lock to one specific (provider, ayanamsa) combination out
    of everything the last rectification run evaluated -- e.g. the user
    reviewed a lower-ranked ayanamsa's own reasoning and decided it fits
    their life events better than whichever one that provider ranked
    best. See app.routers.rectify.choose_ayanamsa."""
    provider: str
    ayanamsa: str


class RectifyAnswer(BaseModel):
    """Answer to one suggested_followups question. Deliberately doesn't
    require an exact date -- the question is now framed as "did something
    like this happen around <time_frame_hint>?", rated 1 (not at all) to
    5 (very much), rather than asking the user to log a precisely-dated
    event. exact_date stays available for anyone who does know it."""
    category: str = Field(max_length=MAX_CATEGORY_LEN)
    occurred_score: int = Field(ge=1, le=5, default=3)
    exact_date: str | None = None
    # Echoed back from the DisambiguationQuestion that was answered, so
    # the life-event listing has something to show when exact_date is
    # left blank (see app.routers.rectify._format_events).
    time_frame_hint: str | None = Field(default=None, max_length=MAX_HINT_LEN)
    # The user's own free-text description -- NOT just supplementary
    # color. The user may use this to directly correct the suggested
    # time_frame_hint (e.g. "actually this was more like 2018, not
    # 2015") or give other detail the rating alone can't capture.
    # _format_events presents this as the actual answer to
    # `question_text` below, which the model should calibrate from
    # directly, rather than a minor aside tacked onto a generic
    # rated-N/5 line.
    notes: str | None = Field(default=None, max_length=MAX_NOTES_LEN)
    # Echoed back from the DisambiguationQuestion that was answered, so
    # the stored event -- and therefore every later prompt that reads it
    # back -- carries the actual question that was asked, not just a
    # category slug the model has to guess the original question from.
    question_text: str | None = Field(default=None, max_length=MAX_QUESTION_LEN)


# ---- Chat ----

class ConversationCreate(BaseModel):
    title: str = Field(default="New conversation", max_length=120)
    profile_id: str


class MessageCreate(BaseModel):
    content: str = Field(max_length=MAX_CHAT_MESSAGE_LEN)


class MessageOut(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    created_at: str


class QuotaExceeded(BaseModel):
    status: Literal["quota_exceeded"] = "quota_exceeded"
    reason: Literal["daily_limit_reached"] = "daily_limit_reached"
    message: str = "Watch a rewarded ad to unlock another question today."


# ---- Ads ----

class AdRewardCallback(BaseModel):
    user_id: str
    ad_network: str
    reward_token: str
    signature: str


# ---- Push notifications ----

class DeviceTokenRegistration(BaseModel):
    token: str = Field(max_length=4096)
    platform: Literal["android", "ios"]
