from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BKK = ZoneInfo("Asia/Bangkok")

_frozen = None  # Used for fixed time in tests and demos


def now_utc():
    """Return the current UTC time."""
    if _frozen is not None:
        return _frozen
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


@contextmanager
def freeze(at):
    """Set a fixed time for testing."""
    global _frozen
    old = _frozen
    _frozen = to_naive_utc(at).replace(microsecond=0)
    try:
        yield _frozen
    finally:
        _frozen = old


def to_naive_utc(dt):
    """Convert a datetime to UTC without timezone info."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def to_iso(dt):
    """Convert UTC time to ISO format."""
    if dt is None:
        return None
    return to_naive_utc(dt).replace(microsecond=0).isoformat() + "Z"


def parse_client_datetime(value):
    """Convert a frontend datetime to UTC."""
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
    """Convert UTC time to Bangkok time."""
    if dt is None:
        return None
    return to_naive_utc(dt).replace(tzinfo=timezone.utc).astimezone(BKK)


def fmt_bkk(dt, pattern="%d/%m/%Y %H:%M"):
    """Format a datetime as Bangkok time."""
    local = to_bkk(dt)
    return local.strftime(pattern) if local else ""


def minutes(n):
    return timedelta(minutes=n)