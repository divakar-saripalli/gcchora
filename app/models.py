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
    calibration_status: Literal["uncalibrated", "pending_questions", "calibrated"] = "uncalibrated"


# ---- Life events ----

class LifeEventIn(BaseModel):
    category: str
    exact_date: str  # YYYY-MM-DD
    impact_weight_score: float = 1.0


# ---- Rectification ----

class RectifyRequest(BaseModel):
    # Optionally override the profile's default search window for this run.
    time_window_minutes: Optional[int] = None
    step_minutes: int = 2
    ayanamsas: Optional[list[str]] = None  # defaults to the full curated set


class DisambiguationQuestion(BaseModel):
    category: str
    question_text: str


class RectifyResult(BaseModel):
    status: Literal["calibrated", "pending_questions"]
    chosen_ayanamsa: Optional[str] = None
    rectified_birth_time_24h: Optional[str] = None
    confidence_score: Optional[float] = None
    candidates_considered: int
    pending_questions: list[DisambiguationQuestion] = Field(default_factory=list)


class RectifyAnswer(BaseModel):
    category: str
    exact_date: str
    impact_weight_score: float = 1.0


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
