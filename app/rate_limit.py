"""Small in-memory rate limiter for the endpoints that cost money (LLM
calls) or invite brute force (sign-in).

A sliding window per key: remember the timestamps of recent calls and
refuse a new one once `max_calls` of them fall inside the last
`window_seconds`. The quota system (app/quota.py) limits how much a user
can use per day; this limits how fast, so a retry loop in the app or a
script hammering an endpoint can't fire dozens of LLM calls in a few
seconds.

State lives in this process's memory. That is right for the current
single-process `uvicorn main:app` deployment; running several worker
processes would give each its own counters, and at that point this
should move to a shared store such as Redis.
"""
import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request

from app.security import get_current_user

_calls: dict[str, deque] = defaultdict(deque)


def _check(key: str, max_calls: int, window_seconds: float, now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    window = _calls[key]
    while window and now - window[0] >= window_seconds:
        window.popleft()
    if len(window) >= max_calls:
        retry_after = max(1, int(window_seconds - (now - window[0])) + 1)
        raise HTTPException(
            status_code=429,
            detail=f"Too many requests -- please wait about {retry_after} seconds and try again.",
            headers={"Retry-After": str(retry_after)},
        )
    window.append(now)


def limit_per_user(name: str, max_calls: int, window_seconds: float):
    """Dependency for authenticated routes: limits each signed-in user
    separately. Returns the user, so a route can use it in place of
    Depends(get_current_user) without authenticating twice."""

    async def dependency(user: dict = Depends(get_current_user)) -> dict:
        _check(f"{name}:{user['_id']}", max_calls, window_seconds)
        return user

    return dependency


def limit_per_ip(name: str, max_calls: int, window_seconds: float):
    """Dependency for routes with no signed-in user yet (sign-in itself)."""

    async def dependency(request: Request) -> None:
        host = request.client.host if request.client else "unknown"
        _check(f"{name}:{host}", max_calls, window_seconds)

    return dependency


def reset() -> None:
    """Clears all counters (used by tests)."""
    _calls.clear()
