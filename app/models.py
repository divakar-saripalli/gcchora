"""Pydantic schemas for API request/response bodies.

MongoDB documents themselves are handled as plain dicts (see app/db.py) --
these models validate what crosses the HTTP boundary, not what's stored.
"""
from datetime import date as date_type
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ---- Auth ----

class GoogleLoginRequest(BaseModel):
    id_token: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    is_new_user: bool


# ---- Birth profiles ----

class GeoCoordinates(BaseModel):
    latitude: float
    longitude: float
    timezone_offset_hours: float


class BirthProfileCreate(BaseModel):
    label: str = "Self"
    birth_date: str  # YYYY-MM-DD
    birth_time_24h: str  # HH:MM:SS
    geographic_coordinates: GeoCoordinates
    # Assumed uncertainty in the stated birth time, used as the rectification
    # search window. Defaults to +/- 30 minutes when not given.
    birth_time_uncertainty_minutes: int = 30


class BirthProfileOut(BirthProfileCreate):
    id: str
    calibration_status: Literal[
        "uncalibrated", "pending_review", "calibrated", "locked"
    ] = "uncalibrated"


class BirthProfileUpdate(BaseModel):
    """All fields optional -- only the ones the client sends are changed.
    Editing birth_date/birth_time_24h/geographic_coordinates invalidates
    any existing calibration (it was computed against the old values), so
    app.routers.profiles.update_profile resets it; the profile's recorded
    life events are untouched either way, since they're facts about the
    person's life, not the chart calculation."""
    label: Optional[str] = None
    birth_date: Optional[str] = None
    birth_time_24h: Optional[str] = None
    geographic_coordinates: Optional[GeoCoordinates] = None
    birth_time_uncertainty_minutes: Optional[int] = None


# ---- Life events ----

class LifeEventIn(BaseModel):
    category: str
    exact_date: str  # YYYY-MM-DD
    impact_weight_score: float = 1.0
    # Free-text detail -- required in spirit (not enforced) for category
    # "OTHER", optional elsewhere, e.g. "Bought the Jubilee Hills flat".
    notes: Optional[str] = None


class LifeEventOut(LifeEventIn):
    id: str
    # Overridden as optional: events created from answering a rectify
    # suggested_followups question (see
    # app.routers.rectify.add_followup_and_rectify) deliberately don't
    # require an exact date -- only a 1-5 "did this happen around
    # <time_frame_hint>" rating. Keeping LifeEventOut.exact_date
    # required (inherited as `str`) made FastAPI's response_model
    # validation raise -- and therefore GET /profiles/{id}/events 500 --
    # for the WHOLE list the moment any one event like that existed.
    exact_date: str | None = None
    # Echoed through so the life-events list has something to display
    # for an event with no exact_date, and so the UI can tell a
    # follow-up-question answer apart from a manually logged event.
    time_frame_hint: str | None = None
    source: str | None = None


class LifeEventUpdate(BaseModel):
    category: Optional[str] = None
    exact_date: Optional[str] = None
    impact_weight_score: Optional[float] = None
    notes: Optional[str] = None


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


class RectifyAnswer(BaseModel):
    """Answer to one suggested_followups question. Deliberately doesn't
    require an exact date -- the question is now framed as "did something
    like this happen around <time_frame_hint>?", rated 1 (not at all) to
    5 (very much), rather than asking the user to log a precisely-dated
    event. exact_date stays available for anyone who does know it."""
    category: str
    occurred_score: int = Field(ge=1, le=5, default=3)
    exact_date: str | None = None
    # Echoed back from the DisambiguationQuestion that was answered, so
    # the life-event listing has something to show when exact_date is
    # left blank (see app.routers.rectify._format_events).
    time_frame_hint: str | None = None
    notes: str | None = None


# ---- Chat ----

class ConversationCreate(BaseModel):
    title: str = "New conversation"
    profile_id: str


class MessageCreate(BaseModel):
    content: str


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
    token: str
    platform: Literal["android", "ios"]
