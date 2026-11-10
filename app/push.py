"""Push notifications via Firebase Cloud Messaging (FCM).

Generic on purpose -- send_push() takes a title/body/data payload and
doesn't know anything about chat or rectification, so it can be reused
as-is by future features (e.g. a daily dasha-summary notification)
without new plumbing, not just the delayed-chat-answer use case it was
built for.

Degrades gracefully when unconfigured: a fresh checkout without
ENABLE_PUSH_NOTIFICATIONS=true and a real service-account key file just
logs and no-ops instead of failing, same pattern as
app.config.allow_test_ad_bypass. This matters because nothing else in
the app should break just because push hasn't been set up yet.
"""
import logging

from app.config import settings
from app.db import device_tokens_collection

logger = logging.getLogger(__name__)

_firebase_app = None
_init_attempted = False


def _get_firebase_app():
    """Lazily initializes the Firebase Admin SDK on first real use, not at
    import time -- so importing this module never fails just because
    firebase-admin isn't installed yet or the credentials file is
    missing, which would otherwise take down the whole backend on
    startup for a feature that's allowed to be unconfigured."""
    global _firebase_app, _init_attempted
    if _firebase_app is not None:
        return _firebase_app
    if _init_attempted:
        return None  # already tried and failed this process -- don't retry every call
    _init_attempted = True

    if not settings.enable_push_notifications:
        logger.info("Push notifications disabled (ENABLE_PUSH_NOTIFICATIONS is not set) -- send_push() will no-op.")
        return None

    try:
        import firebase_admin
        from firebase_admin import credentials
    except ImportError:
        logger.warning("firebase-admin not installed -- run `pip install -r requirements.txt`. send_push() will no-op.")
        return None

    try:
        cred = credentials.Certificate(settings.firebase_credentials_path)
        _firebase_app = firebase_admin.initialize_app(cred)
        logger.info("Firebase Admin SDK initialized from %s.", settings.firebase_credentials_path)
        return _firebase_app
    except FileNotFoundError:
        logger.warning(
            "FIREBASE_CREDENTIALS_PATH (%s) not found -- push notifications are enabled but unconfigured. "
            "send_push() will no-op.", settings.firebase_credentials_path,
        )
        return None
    except Exception:
        logger.exception("Failed to initialize Firebase Admin SDK -- send_push() will no-op.")
        return None


async def register_device_token(user_id: str, token: str, platform: str) -> None:
    """Upserts a device token for a user. A user can have several (one per
    device); the same token re-registering (e.g. on every app launch)
    just refreshes lastSeenAt rather than creating duplicates."""
    from datetime import datetime, timezone

    await device_tokens_collection().update_one(
        {"_id": token},
        {
            "$set": {
                "userId": user_id,
                "platform": platform,
                "lastSeenAt": datetime.now(timezone.utc),
            },
            "$setOnInsert": {"createdAt": datetime.now(timezone.utc)},
        },
        upsert=True,
    )


async def send_push(user_id: str, title: str, body: str, data: dict[str, str] | None = None) -> int:
    """Sends a push notification to every device token registered for this
    user. Returns how many of their tokens it actually sent to (0 if push
    isn't configured, the user has no registered devices, or every send
    failed) -- callers should treat this as best-effort and never let a
    push failure block the thing that triggered it (e.g. a chat answer
    being ready is still saved and shown in-app even if the push to
    announce it fails)."""
    app = _get_firebase_app()
    if app is None:
        return 0

    tokens = [t["_id"] async for t in device_tokens_collection().find({"userId": user_id})]
    if not tokens:
        return 0

    from firebase_admin import messaging

    sent = 0
    stale_tokens = []
    for token in tokens:
        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data=data or {},
            token=token,
        )
        try:
            messaging.send(message, app=app)
            sent += 1
        except messaging.UnregisteredError:
            # The app was uninstalled, or this token was replaced by a
            # newer one from the same device -- FCM tells us it's dead,
            # so stop storing and trying it.
            stale_tokens.append(token)
        except Exception:
            logger.exception("Failed to send push to a device token for user %s (token kept for retry).", user_id)

    if stale_tokens:
        await device_tokens_collection().delete_many({"_id": {"$in": stale_tokens}})

    return sent
