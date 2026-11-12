"""Parking fee calculation (spec 8.3).

Base fee:
    Before end: actual hours rounded up, at least 1 hour
    After end: booked hours, no refund

Overstay:
    After 15-minute grace period
    First hour: TIER1, next hours: TIER2

Total = base + overstay
"""
import math
from datetime import timedelta


def _ceil_hours(delta):
    minutes = delta.total_seconds() / 60
    return max(0, math.ceil(minutes / 60 - 1e-9))


def calculate_fee(start_time, end_time, duration_hours, actual_in, actual_out, rules):
    """Calculate parking fees and usage details."""
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
    """Estimate the booking fee."""
    return duration_hours * rules["RATE_PER_HOUR"]