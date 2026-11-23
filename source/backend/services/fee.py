"""Parking fee (spec 8.3). Pure functions: no database, easy to test.

base fee
    leave before end_time : ceil(actual_out - actual_in) hours, at least 1, x RATE
    leave after end_time  : the booked hours x RATE (no refund for unused time)
overstay fee
    late = actual_out - end_time - GRACE (15 min). If late <= 0 -> 0.
    late hours = ceil(late / 60): 1st hour TIER1 (40), every next hour TIER2 (60)
total = base + overstay

Examples (spec):
    10:00-12:00, out 13:30            -> base 40, overstay 100, total 140
    10:00-12:00, out 12:10            -> base 40, overstay 0,   total 40
    10:00-14:00, in 10:05, out 11:20  -> base 40 (2 h used),     total 40
"""
import math
from datetime import timedelta


def _ceil_hours(delta):
    minutes = delta.total_seconds() / 60
    return max(0, math.ceil(minutes / 60 - 1e-9))


def calculate_fee(start_time, end_time, duration_hours, actual_in, actual_out, rules):
    """Return {base_fee, overstay_fee, total_amount, billed_hours, overstay_hours, is_overstay, minutes_parked}.

    rules: dict with RATE_PER_HOUR, OVERSTAY_GRACE_MINUTES, OVERSTAY_TIER1_RATE, OVERSTAY_TIER2_RATE
    """
    if actual_in is None or actual_out is None:
        raise ValueError("check-in and check-out times are required")
    if actual_out < actual_in:
        raise ValueError("check-out is before check-in")

    rate = rules["RATE_PER_HOUR"]
    if actual_out <= end_time:
        billed_hours = max(1, _ceil_hours(actual_out - actual_in))
        billed_hours = min(billed_hours, duration_hours)
    else:
        billed_hours = duration_hours
    base_fee = billed_hours * rate

    late = actual_out - end_time - timedelta(minutes=rules["OVERSTAY_GRACE_MINUTES"])
    overstay_hours = _ceil_hours(late) if late > timedelta(0) else 0
    overstay_fee = 0
    if overstay_hours:
        overstay_fee = rules["OVERSTAY_TIER1_RATE"] + (overstay_hours - 1) * rules["OVERSTAY_TIER2_RATE"]

    return {
        "base_fee": base_fee,
        "overstay_fee": overstay_fee,
        "total_amount": base_fee + overstay_fee,
        "billed_hours": billed_hours,
        "overstay_hours": overstay_hours,
        "is_overstay": overstay_fee > 0 or actual_out > end_time,
        "minutes_parked": int((actual_out - actual_in).total_seconds() // 60),
    }


def estimate_booking(duration_hours, rules):
    """Price shown before booking: booked hours x rate."""
    return duration_hours * rules["RATE_PER_HOUR"]
