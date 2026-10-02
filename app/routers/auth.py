import uuid
from datetime import datetime, timezone

from fastapi import APIRouter

from app.db import users_collection
from app.quota import default_usage
from app.models import GoogleLoginRequest, LoginResponse
from app.security import create_access_token, verify_google_id_token

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/google", response_model=LoginResponse)
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
