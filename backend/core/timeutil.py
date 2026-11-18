"""The ONLY place the backend gets the current time from.

Rules (spec section 5):
  * Every time in MySQL / MongoDB is UTC.
  * Python decides the time; never use MySQL NOW() in business queries.
  * Inside the app, datetimes are *naive UTC* (no tzinfo), which is what
    PyMySQL DATETIME columns and PyMongo dates give back.
  * The API sends ISO 8601 with a 'Z'. The frontend shows Asia/Bangkok.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BKK = ZoneInfo("Asia/Bangkok")

_frozen = None  # set by freeze() in tests / demo


def now_utc():
    """Current time as naive UTC, cut to whole seconds (DATETIME has no fraction)."""
    if _frozen is not None:
        return _frozen
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


@contextmanager
def freeze(at):
    """Pin now_utc() to a fixed moment. `at` is naive UTC or tz-aware."""
    global _frozen
    old = _frozen
    _frozen = to_naive_utc(at).replace(microsecond=0)
    try:
        yield _frozen
    finally:
        _frozen = old


def to_naive_utc(dt):
    """Any datetime -> naive UTC. A naive input is assumed to be UTC already."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def to_iso(dt):
    """naive UTC datetime -> '2026-11-20T03:00:00Z' (None stays None)."""
    if dt is None:
        return None
    return to_naive_utc(dt).replace(microsecond=0).isoformat() + "Z"


def parse_client_datetime(value):
    """Parse a time sent by the frontend into naive UTC.

    Accepts ISO 8601 with an offset ('2026-11-20T10:00:00+07:00') or 'Z'.
    A value without an offset is treated as Bangkok time, because that is
    what a user picks on the page. Raises ValueError if it cannot be parsed.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("empty datetime")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BKK)
    return dt.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0)


def to_bkk(dt):
    """naive UTC -> aware Asia/Bangkok (for text in notifications, reports)."""
    if dt is None:
        return None
    return to_naive_utc(dt).replace(tzinfo=timezone.utc).astimezone(BKK)


def fmt_bkk(dt, pattern="%d/%m/%Y %H:%M"):
    """Readable Bangkok time for messages, e.g. '20/11/2026 10:00'."""
    local = to_bkk(dt)
    return local.strftime(pattern) if local else ""


def minutes(n):
    return timedelta(minutes=n)
