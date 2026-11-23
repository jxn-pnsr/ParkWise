"""Small reads from MongoDB that pages need every time (theme, unread badge).

MongoDB may be down (it is the follower database), so these never raise:
they fall back to defaults and the page still works.
"""
import logging
import os
import time

from pymongo.errors import PyMongoError

from db.mongo import MongoUnavailable, get_mongo

log = logging.getLogger(__name__)

# When MongoDB is off, every try waits MONGO_TIMEOUT_MS. These reads run on every
# page (/api/auth/me), so after one real failure we skip MongoDB for a short time
# instead of making every page wait. Simulated outages (Transaction Demo) raise
# MongoUnavailable instantly and are not cached.
SKIP_SECONDS = float(os.getenv("MONGO_SKIP_SECONDS", "30"))
_skip_until = 0.0


def _skipping():
    return time.monotonic() < _skip_until


def _mark_down(e):
    global _skip_until
    _skip_until = time.monotonic() + SKIP_SECONDS
    log.warning("MongoDB unreachable, page reads skip it for %ss: %s", SKIP_SECONDS, e)


def reset_skip():
    global _skip_until
    _skip_until = 0.0

LEGACY_KEYS = ("theme", "language")     

DEFAULT_PREFERENCES = {
    "notifications": {"holdExpired": True, "noShow": True, "checkIn": True, "checkOut": True, "overstay": True},
    "defaultVehicleId": None,
}


def preferences(user_id):
    doc = None
    if not _skipping():
        try:
            doc = get_mongo().User_Preferences.find_one({"userId": user_id}, {"_id": 0})
        except MongoUnavailable as e:
            log.info("preferences fallback: %s", e)
        except PyMongoError as e:
            _mark_down(e)
    out = {**DEFAULT_PREFERENCES, **(doc or {})}
    out["notifications"] = {**DEFAULT_PREFERENCES["notifications"], **(out.get("notifications") or {})}
    out.pop("userId", None)
    for key in LEGACY_KEYS:
        out.pop(key, None)
    return out


def require_mongo():
    """MongoDB handle for pages that only live in MongoDB (history, notifications).
    Raises 503 with a clear message instead of crashing when MongoDB is down."""
    from core.errors import ServiceUnavailable
    try:
        if _skipping():
            raise MongoUnavailable("skipped: MongoDB was unreachable a moment ago")
        db = get_mongo()
        db.command("ping")
        return db
    except PyMongoError as e:
        _mark_down(e)
        raise ServiceUnavailable("MONGO_DOWN", "History and notifications are temporarily unavailable. "
                                               "Bookings still work; please try again in a moment.")
    except MongoUnavailable:
        raise ServiceUnavailable("MONGO_DOWN", "History and notifications are temporarily unavailable. "
                                               "Bookings still work; please try again in a moment.")


def unread_notifications(user_id):
    if _skipping():
        return 0
    try:
        return get_mongo().Notifications.count_documents({"userId": user_id, "isRead": False})
    except MongoUnavailable:
        return 0
    except PyMongoError as e:
        _mark_down(e)
        return 0
