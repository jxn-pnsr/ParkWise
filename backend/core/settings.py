import threading
import time

from db.mysql import fetch_all

# Default values for missing settings.
DEFAULTS = {
    "HOLD_MINUTES": 5,
    "NOSHOW_GRACE_MINUTES": 30,
    "EARLY_CHECKIN_MINUTES": 15,
    "ADVANCE_CANCEL_HOURS": 1,
    "INSTANT_CANCEL_MINUTES": 10,
    "MIN_DURATION_HOURS": 1,
    "MAX_DURATION_HOURS": 12,
    "MAX_ADVANCE_DAYS": 7,
    "MAX_ACTIVE_RESERVATIONS_PER_USER": 3,
    "RATE_PER_HOUR": 20,
    "OVERSTAY_GRACE_MINUTES": 15,
    "OVERSTAY_TIER1_RATE": 40,
    "OVERSTAY_TIER2_RATE": 60,
    "OVERSTAY_WARN_MINUTES": 15,
    "LOGIN_MAX_FAILED": 5,
    "LOGIN_LOCK_MINUTES": 5,
}

# Settings available to the frontend.
PUBLIC_KEYS = (
    "HOLD_MINUTES", "NOSHOW_GRACE_MINUTES", "EARLY_CHECKIN_MINUTES", "ADVANCE_CANCEL_HOURS",
    "INSTANT_CANCEL_MINUTES", "MIN_DURATION_HOURS", "MAX_DURATION_HOURS", "MAX_ADVANCE_DAYS",
    "MAX_ACTIVE_RESERVATIONS_PER_USER", "RATE_PER_HOUR", "OVERSTAY_GRACE_MINUTES",
    "OVERSTAY_TIER1_RATE", "OVERSTAY_TIER2_RATE",
)

CACHE_SECONDS = 5
_cache = {"values": None, "at": 0.0}
_lock = threading.Lock()


def _number(text):
    try:
        f = float(text)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return text


def all_config():
    with _lock:
        if _cache["values"] is not None and time.monotonic() - _cache["at"] < CACHE_SECONDS:
            return _cache["values"]
    values = dict(DEFAULTS)
    for row in fetch_all("SELECT config_key, config_value FROM System_Config"):
        values[row["config_key"]] = _number(row["config_value"])
    with _lock:
        _cache["values"], _cache["at"] = values, time.monotonic()
    return values


def cfg(key):
    return all_config()[key]


def public_config():
    values = all_config()
    return {k: values[k] for k in PUBLIC_KEYS}


def invalidate():
    with _lock:
        _cache["values"] = None