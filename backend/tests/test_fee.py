"""Tests parking fees and the three spec examples."""
from datetime import datetime, timedelta

import pytest

from services.fee import calculate_fee, estimate_booking

RULES = {"RATE_PER_HOUR": 20, "OVERSTAY_GRACE_MINUTES": 15, "OVERSTAY_TIER1_RATE": 40, "OVERSTAY_TIER2_RATE": 60}
D = datetime(2026, 11, 20)


def t(hh, mm=0, day=0):
    return D + timedelta(days=day, hours=hh, minutes=mm)


def fee(start, end, hours, came, left):
    return calculate_fee(start, end, hours, came, left, RULES)


def test_spec_example_overstay_140():
    f = fee(t(10), t(12), 2, t(10), t(13, 30))
    assert (f["base_fee"], f["overstay_fee"], f["total_amount"]) == (40, 100, 140)
    assert f["overstay_hours"] == 2 and f["is_overstay"]


def test_spec_example_inside_grace():
    f = fee(t(10), t(12), 2, t(10), t(12, 10))
    assert (f["base_fee"], f["overstay_fee"], f["total_amount"]) == (40, 0, 40)
    assert f["is_overstay"]  # Late exit, but no extra fee.


def test_spec_example_leave_early_rounds_up():
    f = fee(t(10), t(14), 4, t(10, 5), t(11, 20))
    assert (f["base_fee"], f["total_amount"], f["billed_hours"]) == (40, 40, 2)
    assert not f["is_overstay"]


@pytest.mark.parametrize("left, expected_overstay", [
    (t(12, 15), 0),          # At the grace limit.
    (t(12, 16), 40),         # 1 minute over: 1st hour.
    (t(13, 15), 40),         # 60 minutes over: still 1 hour.
    (t(13, 16), 100),        # 2nd hour starts.
    (t(15, 16), 40 + 60 * 3),
])
def test_overstay_tiers(left, expected_overstay):
    assert fee(t(10), t(12), 2, t(10), left)["overstay_fee"] == expected_overstay


def test_minimum_one_hour():
    assert fee(t(10), t(12), 2, t(10), t(10, 5))["base_fee"] == 20


def test_early_checkin_never_bills_more_than_booked():
    # Early check-in does not add to the booked time.
    assert fee(t(10), t(12), 2, t(9, 45), t(12))["base_fee"] == 40


def test_overnight_booking():
    f = fee(t(22), t(2, day=1), 4, t(22), t(3, 30, day=1))
    assert (f["base_fee"], f["overstay_fee"]) == (80, 100)


def test_bad_times():
    with pytest.raises(ValueError):
        fee(t(10), t(12), 2, t(11), t(10))
    with pytest.raises(ValueError):
        fee(t(10), t(12), 2, None, t(10))


def test_estimate():
    assert estimate_booking(3, RULES) == 60