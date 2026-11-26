import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from app.db import users_collection
from app.quota import default_usage
from app.models import GoogleLoginRequest, LoginResponse
from app.rate_limit import limit_per_ip
from app.security import create_access_token, get_current_user, verify_google_id_token

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/google",
    response_model=LoginResponse,
    dependencies=[Depends(limit_per_ip("login", max_calls=10, window_seconds=60))],
)
async def login_with_google(payload: GoogleLoginRequest):
    claims = verify_google_id_token(payload.id_token)
    google_id = claims["sub"]

    existing = await users_collection().find_one({"googleId": google_id})
    if existing:
        user_id = existing["_id"]
        is_new = False
        await users_collection().update_one(
            {"_id": user_id}, {"$set": {"lastLoginAt": datetime.now(timezone.utc)}}
        )
    else:
        user_id = str(uuid.uuid4())
        is_new = True
        await users_collection().insert_one({
            "_id": user_id,
            "googleId": google_id,
            "email": claims.get("email"),
            "name": claims.get("name"),
            "authProvider": "google",
            "createdAt": datetime.now(timezone.utc),
            "lastLoginAt": datetime.now(timezone.utc),
            "preferences": {},
            "usage": default_usage(),
        })

    token = create_access_token(user_id)
    return LoginResponse(access_token=token, user_id=user_id, is_new_user=is_new)


@router.post("/logout-all")
async def logout_everywhere(user: dict = Depends(get_current_user)):
    """Signs this account out on every device: every session token issued
    up to now stops working (see app.security.token_is_revoked), including
    the one used to make this call. The way to cut off a lost phone or a
    token that may have leaked, without waiting for it to expire."""
    # +1 so the token making this call (issued this same second at the
    # earliest) is covered too; a fresh sign-in a moment later gets a
    # later issued-at and works normally.
    cutoff = int(datetime.now(timezone.utc).timestamp()) + 1
    await users_collection().update_one({"_id": user["_id"]}, {"$set": {"tokens_invalid_before": cutoff}})
    return {"status": "signed_out_everywhere"}
