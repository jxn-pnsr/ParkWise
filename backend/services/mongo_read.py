"""MongoDB reads for page settings and unread notifications.

Returns defaults if MongoDB is unavailable.
"""
import logging
import os
import time

from pymongo.errors import PyMongoError

from db.mongo import MongoUnavailable, get_mongo

log = logging.getLogger(__name__)

# Skip MongoDB briefly after a real failure to avoid delays on every page.
# Simulated outages are not cached.
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


DEFAULT_PREFERENCES = {
    "theme": "light",
    "language": "en",
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
    return out


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