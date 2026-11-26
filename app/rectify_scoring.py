"""Thresholds and helpers for interpreting rectification results: turning
a provider's raw structured answer into a RectifyCandidate, deciding
whether two candidates agree, and filtering the follow-up questions a
provider suggests.

Pure functions and constants only -- no database access and no LLM
calls. The endpoints and the lock/recheck flow that use these live in
app/routers/rectify.py; the prompts live in app/rectify_prompts.py.
"""
import logging
import re
from datetime import datetime, timezone

from app.models import AyanamsaEstimate, DisambiguationQuestion, RectifyCandidate

logger = logging.getLogger(__name__)

# A provider+ayanamsa combination this confident locks in automatically.
LOCK_THRESHOLD = 95.0

# Once locked, a recheck against new events that comes back below this is
# treated as "doesn't fit anymore" and triggers unlocking + a full re-run,
# rather than quietly staying locked with degraded confidence. Above this
# but below LOCK_THRESHOLD, it just stays locked with an updated score --
# a single new event nudging confidence down a little shouldn't force a
# full re-comparison every time.
UNLOCK_THRESHOLD = 75.0

MODERATE_CONFIDENCE_THRESHOLD = 50.0

# Too few events makes any rectification -- deterministic or LLM-reasoned
# -- under-constrained. Also reused as the events-since-last-check bar for
# auto-rechecking a locked profile (see app.routers.profiles), so the two
# don't drift apart as separate magic numbers.
MIN_EVENTS_REQUIRED = 6
EVENTS_SINCE_LOCK_CHECK_THRESHOLD = MIN_EVENTS_REQUIRED

MAX_SUGGESTED_FOLLOWUPS = 3


_YEAR_RE = re.compile(r"(?<!\d)(\d{4})(?!\d)")


def _is_future_only_hint(time_frame_hint: str) -> bool:
    """Defensive backstop for _build_prompt's "don't ask about the future"
    instruction, in case a provider ignores it anyway. Pulls every 4-digit
    year mentioned in the hint (e.g. "sometime between 2012 and 2015") and
    flags it only when EVERY year mentioned is still ahead of today -- a
    hint that straddles today (e.g. "between 2024 and 2027" when it's
    currently 2026) is left alone, since part of that window has already
    happened. A hint with no year at all (e.g. "around when you started
    college") can't be checked this way and is left alone too."""
    years = [int(y) for y in _YEAR_RE.findall(time_frame_hint)]
    if not years:
        return False
    current_year = datetime.now(timezone.utc).year
    return all(y > current_year for y in years)


def _keep_followup_or_log(provider: str, followup: dict) -> bool:
    hint = followup.get("time_frame_hint") or ""
    if _is_future_only_hint(hint):
        # Not logging the hint text itself -- it's model-generated, but it
        # paraphrases the user's own life events (e.g. "around when your
        # second child was born"), which doesn't belong in a plaintext
        # app log. category/provider are enough to spot this happening
        # and to debug _build_prompt's instruction not to do it.
        logger.warning(
            "Dropping future-dated rectify follow-up from provider=%s category=%s "
            "(_build_prompt asked it not to do this)",
            provider, followup.get("category"),
        )
        return False
    return True


def _candidate_from_raw(provider: str, raw: dict) -> RectifyCandidate:
    ayanamsa_results = [
        AyanamsaEstimate(
            ayanamsa=r["ayanamsa"],
            rectified_birth_time_24h=r["rectified_birth_time_24h"],
            confidence_score=float(r["confidence_score"]),
            reasoning=r.get("reasoning", ""),
        )
        for r in raw["ayanamsa_results"]
    ]
    best = max(ayanamsa_results, key=lambda r: r.confidence_score)
    return RectifyCandidate(
        provider=provider,
        ayanamsa_results=ayanamsa_results,
        best_ayanamsa=best.ayanamsa,
        best_rectified_birth_time_24h=best.rectified_birth_time_24h,
        best_confidence_score=best.confidence_score,
        reasoning=raw.get("reasoning", ""),
        # The model authors its own natural question text per category now
        # (see _build_prompt) instead of us title-casing whatever slug it
        # invents, which produced awkward results like "Can you add a
        # Exact Dates Of Vehicle Accidents Or Purchases event?". Falls
        # back to a templated question only if a provider (or an older
        # cached shape) omits question_text.
        suggested_followups=[
            DisambiguationQuestion(
                category=f["category"],
                question_text=f.get("question_text")
                or f"Can you tell me more about {f['category'].replace('_', ' ').lower()}?",
                time_frame_hint=f.get("time_frame_hint") or "",
            )
            # Backstop against the provider ignoring _build_prompt's
            # backward-looking-only instruction and suggesting a question
            # about an event that hasn't happened yet -- filtered BEFORE
            # the MAX_SUGGESTED_FOLLOWUPS slice so a dropped future-dated
            # one doesn't crowd out a good, usable suggestion behind it.
            for f in raw.get("suggested_followups", [])
            if _keep_followup_or_log(provider, f)
        ][:MAX_SUGGESTED_FOLLOWUPS],
    )


def _agree(a: RectifyCandidate, b: RectifyCandidate, tolerance_minutes: int = 15) -> bool:
    if a.best_ayanamsa != b.best_ayanamsa:
        return False
    fmt = "%H:%M:%S"
    try:
        ta = datetime.strptime(a.best_rectified_birth_time_24h, fmt)
        tb = datetime.strptime(b.best_rectified_birth_time_24h, fmt)
    except ValueError:
        return False
    return abs((ta - tb).total_seconds()) <= tolerance_minutes * 60


def _tier_text(confidence_score: float) -> str:
    if confidence_score >= LOCK_THRESHOLD:
        return "This is a strong, well-supported match."
    if confidence_score >= MODERATE_CONFIDENCE_THRESHOLD:
        return "This is a reasonable match, though not an overwhelming one."
    return "This is only a rough estimate at this point -- treat it as a starting point, not a final answer."


def _history_entry(candidate: RectifyCandidate, events_used: int) -> dict:
    return {
        "attempt_at": datetime.now(timezone.utc).isoformat(),
        "provider": candidate.provider,
        "best_ayanamsa": candidate.best_ayanamsa,
        "best_rectified_birth_time_24h": candidate.best_rectified_birth_time_24h,
        "best_confidence_score": candidate.best_confidence_score,
        "events_used": events_used,
    }


def _union_followups(candidates: list[RectifyCandidate]) -> list[DisambiguationQuestion]:
    seen: set[str] = set()
    out: list[DisambiguationQuestion] = []
    for c in candidates:
        for q in c.suggested_followups:
            if q.category not in seen:
                seen.add(q.category)
                out.append(q)
    return out
