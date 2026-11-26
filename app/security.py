"""Google Sign-In verification and app-issued JWT session tokens.

Google's ID token is only ever verified once, at login (verify_google_id_token).
Every subsequent request authenticates against our own short-lived JWT, not
Google, via get_current_user.
"""
import requests as _requests
from cachecontrol import CacheControl
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from jose import JWTError, jwt

from app.config import settings
from app.db import users_collection

bearer_scheme = HTTPBearer(auto_error=True)

# google.oauth2.id_token.verify_oauth2_token fetches Google's public certs
# over the network EVERY SINGLE CALL with no caching of its own -- it's
# entirely on the caller to cache, and the obvious way to call it (a fresh
# google_requests.Request() per verification, as this used to do) caches
# nothing at all. That means every Google sign-in was paying for an extra
# live network round trip to Google's certs endpoint on top of everything
# else, which is very likely why sign-in "takes forever" -- this is a
# network hop to Google itself, not something slow in our own backend or
# the app.
#
# Fix: wrap a persistent requests.Session in CacheControl, which honors
# the Cache-Control headers Google's certs response already sends (valid
# for hours at a time -- these certs rotate rarely) via the standard
# HTTP caching contract, and reuse ONE Request object built on it across
# every call instead of a disposable one each time. After the very first
# verification in this process's lifetime, subsequent ones should resolve
# the certs from the in-memory cache with no network call at all.
_cached_session = CacheControl(_requests.Session())
_google_auth_request = google_requests.Request(session=_cached_session)


def verify_google_id_token(token: str) -> dict:
    """Verify a Google-issued ID token and return its claims.

    Raises HTTPException(401) if the token is invalid, expired, or not
    issued for this app's OAuth client.
    """
    try:
        claims = google_id_token.verify_oauth2_token(
            token, _google_auth_request, settings.google_client_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid Google ID token: {exc}")
    return claims


def create_access_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.jwt_expire_minutes)
    # "iat" (issued-at) is what lets a token be revoked before it
    # expires: see tokens_invalid_before in get_current_user.
    payload = {"sub": user_id, "exp": expire, "iat": int(now.timestamp())}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _decode_claims(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired session token")
    if not payload.get("sub"):
        raise HTTPException(status_code=401, detail="Invalid session token payload")
    return payload


def decode_access_token(token: str) -> str:
    return _decode_claims(token)["sub"]


def token_is_revoked(claims: dict, user: dict) -> bool:
    """True when the user has signed out everywhere (POST
    /auth/logout-all) after this token was issued. tokens_invalid_before
    is a Unix timestamp stored on the user; any token issued before it is
    refused even though its signature and expiry are still fine. A token
    with no "iat" at all pre-dates this check and is refused too once a
    cutoff exists."""
    cutoff = user.get("tokens_invalid_before")
    if cutoff is None:
        return False
    issued_at = claims.get("iat")
    return issued_at is None or issued_at < cutoff


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    claims = _decode_claims(credentials.credentials)
    user = await users_collection().find_one({"_id": claims["sub"]})
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if token_is_revoked(claims, user):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="This session was signed out. Please sign in again."
        )
    return user
