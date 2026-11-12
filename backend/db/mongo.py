import logging
import threading

from pymongo import MongoClient
from pymongo.errors import PyMongoError

log = logging.getLogger(__name__)

COLLECTIONS = ("Parking_Sessions", "Activity_Logs", "Notifications", "User_Preferences")

_client = None
_db_name = None
_lock = threading.Lock()

# Used to test MongoDB connection failures.
_simulate_down = False


class MongoUnavailable(Exception):
    pass


def _make_client(uri, db_name, timeout_ms):
    global _client, _db_name
    with _lock:
        if _client is None:
            # MongoClient connects only when first used.
            # Return dates as naive UTC, like MySQL.
            _client = MongoClient(uri, serverSelectionTimeoutMS=timeout_ms, tz_aware=False)
            _db_name = db_name


def init_app(app):
    c = app.config
    _make_client(c["MONGO_URI"], c["MONGO_DB"], c["MONGO_TIMEOUT_MS"])


def _ensure_client():
    if _client is None:  # Used when running without create_app().
        from config import Config
        _make_client(Config.MONGO_URI, Config.MONGO_DB, Config.MONGO_TIMEOUT_MS)
    return _client


def get_mongo():
    """Return the database handle."""
    if _simulate_down:
        raise MongoUnavailable("MongoDB is down (simulated by Transaction Demo)")
    return _ensure_client()[_db_name]


def set_simulated_down(value):
    global _simulate_down
    _simulate_down = bool(value)
    log.warning("Simulated MongoDB outage: %s", "ON" if _simulate_down else "OFF")


def is_simulated_down():
    return _simulate_down


def ping():
    """Check if MongoDB is available."""
    try:
        get_mongo().command("ping")
        return True
    except (MongoUnavailable, PyMongoError) as e:
        log.warning("MongoDB ping failed: %s", e)
        return False