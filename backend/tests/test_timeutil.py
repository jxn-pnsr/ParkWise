from datetime import datetime, timezone

import pytest

from core import timeutil


def test_now_utc_is_naive_whole_seconds():
    now = timeutil.now_utc()
    assert now.tzinfo is None
    assert now.microsecond == 0


def test_freeze_pins_now_and_restores():
    with timeutil.freeze(datetime(2026, 11, 20, 3, 0, 0)):
        assert timeutil.now_utc() == datetime(2026, 11, 20, 3, 0, 0)
    assert timeutil.now_utc() != datetime(2026, 11, 20, 3, 0, 0)


def test_to_iso_adds_z():
    assert timeutil.to_iso(datetime(2026, 11, 20, 3, 0, 0)) == "2026-11-20T03:00:00Z"
    assert timeutil.to_iso(None) is None


@pytest.mark.parametrize("text", [
    "2026-11-20T10:00:00+07:00",   # Bangkok with offset
    "2026-11-20T03:00:00Z",        # UTC
    "2026-11-20T10:00:00",         # no offset -> treated as Bangkok
    "2026-11-20T10:00",
])
def test_parse_client_datetime_to_utc(text):
    assert timeutil.parse_client_datetime(text) == datetime(2026, 11, 20, 3, 0, 0)


@pytest.mark.parametrize("bad", ["", "   ", "tomorrow", None, 123])
def test_parse_client_datetime_rejects_garbage(bad):
    with pytest.raises((ValueError, TypeError)):
        timeutil.parse_client_datetime(bad)


def test_bangkok_display():
    assert timeutil.fmt_bkk(datetime(2026, 11, 20, 3, 0, 0)) == "20/11/2026 10:00"
    aware = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
    assert timeutil.to_naive_utc(aware) == datetime(2026, 11, 20, 10, 0)
