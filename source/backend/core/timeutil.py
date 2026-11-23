"""Centralized time handling (spec section 5).

    Database and app times use naive UTC.
    API times use ISO 8601 with Z; frontend displays Asia/Bangkok.
    Use Python for time calculations, not MySQL NOW().
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BKK = ZoneInfo("Asia/Bangkok")
MIN_YEAR, MAX_YEAR = 2000, 2100

_frozen = None  # Set by freeze() in tests and demos.


def now_utc():
    """Return naive UTC time to whole seconds."""
    if _frozen is not None:
        return _frozen
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


@contextmanager
def freeze(at):
    """Use a fixed time for tests and demos."""
    global _frozen
    old = _frozen
    _frozen = to_naive_utc(at).replace(microsecond=0)
    try:
        yield _frozen
    finally:
        _frozen = old


def to_naive_utc(dt):
    """Convert a datetime to naive UTC."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def to_iso(dt):
    """Convert naive UTC to ISO 8601 with Z."""
    if dt is None:
        return None
    return to_naive_utc(dt).replace(microsecond=0).isoformat() + "Z"


def parse_client_datetime(value):
    """Parse client time into naive UTC; assume Bangkok time if no offset."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("empty datetime")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if not MIN_YEAR <= dt.year <= MAX_YEAR:
        raise ValueError("year out of range")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BKK)
    try:
        return dt.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0)
    except OverflowError:
        raise ValueError("date out of range")


def to_bkk(dt):
    """Convert naive UTC to Bangkok time."""
    if dt is None:
        return None
    return to_naive_utc(dt).replace(tzinfo=timezone.utc).astimezone(BKK)


def fmt_bkk(dt, pattern="%d/%m/%Y %H:%M"):
    """Format a datetime as Bangkok time."""
    local = to_bkk(dt)
    return local.strftime(pattern) if local else ""


def minutes(n):
    return timedelta(minutes=n)