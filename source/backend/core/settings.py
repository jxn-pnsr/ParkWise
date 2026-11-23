"""System business rules and configuration (spec section 5).

    cfg("HOLD_MINUTES")  # -> 5

    Values are cached for 5 seconds. Call invalidate() after admin updates.
"""
import threading
import time

from db.mysql import fetch_all

# Fallback values for missing settings.
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
    "BLOCK_BOOKING_WHEN_UNPAID": 1,
}

# Allowed values for admin updates (M6).
RULES = {
    "HOLD_MINUTES": (1, 60),
    "NOSHOW_GRACE_MINUTES": (5, 240),
    "EARLY_CHECKIN_MINUTES": (0, 120),
    "ADVANCE_CANCEL_HOURS": (0, 72),
    "INSTANT_CANCEL_MINUTES": (0, 120),
    "MIN_DURATION_HOURS": (1, 24),
    "MAX_DURATION_HOURS": (1, 24),
    "MAX_ADVANCE_DAYS": (1, 60),
    "MAX_ACTIVE_RESERVATIONS_PER_USER": (1, 20),
    "RATE_PER_HOUR": (1, 10000),
    "OVERSTAY_GRACE_MINUTES": (0, 240),
    "OVERSTAY_TIER1_RATE": (0, 10000),
    "OVERSTAY_TIER2_RATE": (0, 10000),
    "OVERSTAY_WARN_MINUTES": (1, 240),
    "LOGIN_MAX_FAILED": (1, 20),
    "LOGIN_LOCK_MINUTES": (1, 1440),
    "BLOCK_BOOKING_WHEN_UNPAID": (0, 1),
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


def check_value(key, value, current=None):
    """Validate a setting and related duration rules."""
    if key not in RULES:
        return "Unknown setting."
    lo, hi = RULES[key]
    if not lo <= value <= hi:
        return f"Must be between {lo} and {hi}."
    values = dict(current or all_config())
    values[key] = value
    if values["MIN_DURATION_HOURS"] > values["MAX_DURATION_HOURS"]:
        return (f"The minimum length of stay ({values['MIN_DURATION_HOURS']} h) cannot be longer than the "
                f"maximum ({values['MAX_DURATION_HOURS']} h).")
    return None


def invalidate():
    with _lock:
        _cache["values"] = None