"""Shared SQL for bookings, availability, and reservation lists.

A bay is blocked by an active hold, a Confirmed booking before its
no-show cutoff, or an In Use reservation. Expired holds and no-shows
are ignored even if the scheduler is late.
"""
from datetime import timedelta

from core.settings import cfg
from core.timeutil import now_utc

# Reservations that currently block a bay. Uses %(now)s and %(noshow_cut)s.
OCCUPYING_SQL = """(
      (r.status = 'Held' AND r.hold_expires_at > %(now)s)
   OR (r.status = 'Confirmed' AND r.start_time > %(noshow_cut)s)
   OR r.status = 'In Use')"""

# Overlap with [%(start)s, %(end)s). Overstays keep the bay until checkout.
OVERLAP_SQL = """(r.start_time < %(end)s
   AND (CASE WHEN r.status = 'In Use' THEN GREATEST(r.end_time, %(now)s + INTERVAL 1 MINUTE)
             ELSE r.end_time END) > %(start)s)"""


def time_params(start=None, end=None, now=None):
    now = now or now_utc()
    return {
        "now": now,
        "noshow_cut": now - timedelta(minutes=cfg("NOSHOW_GRACE_MINUTES")),
        "start": start,
        "end": end,
    }


RESERVATION_SELECT = """
SELECT r.*, v.plate_number, v.province, v.is_deleted AS vehicle_deleted,
       s.slot_code, s.slot_type, s.lot_id, l.lot_name, l.location_desc,
       u.full_name AS customer_name, u.email AS customer_email, u.phone AS customer_phone,
       p.payment_id, p.base_fee, p.overstay_fee, p.total_amount, p.status AS payment_status, p.method AS payment_method
FROM Reservations r
JOIN Customer_Vehicles v ON v.vehicle_id = r.vehicle_id
JOIN Parking_Slots s     ON s.slot_id = r.slot_id
JOIN Parking_Lots l      ON l.lot_id = s.lot_id
JOIN Users u             ON u.user_id = r.user_id
LEFT JOIN Payments p     ON p.reservation_id = r.reservation_id AND p.is_deleted = 0
"""


def effective_status(row, now=None):
    """Return the current status before the scheduler updates it."""
    now = now or now_utc()
    status = row["status"]
    if status == "Held" and row["hold_expires_at"] and row["hold_expires_at"] <= now:
        return "Expired"
    if status == "Confirmed" and row["start_time"] + timedelta(minutes=cfg("NOSHOW_GRACE_MINUTES")) <= now:
        return "No-show"
    return status


def display_label(row, now=None):
    """Return extra list labels not stored in the database."""
    now = now or now_utc()
    status = effective_status(row, now)
    if status == "Confirmed":
        early = row["start_time"] - timedelta(minutes=cfg("EARLY_CHECKIN_MINUTES"))
        if early <= now:
            return "awaiting_checkin"
    if status == "In Use" and (row["is_overstay"] or row["end_time"] < now):
        return "overstay"
    return None


def serialize_reservation(row, now=None, include_customer=False):
    now = now or now_utc()
    data = {
        "reservation_id": row["reservation_id"],
        "user_id": row["user_id"],
        "booking_type": row["booking_type"],
        "status": effective_status(row, now),
        "stored_status": row["status"],
        "display_label": display_label(row, now),
        "start_time": row["start_time"],
        "end_time": row["end_time"],
        "duration_hours": row["duration_hours"],
        "hold_expires_at": row["hold_expires_at"] if row["status"] == "Held" else None,
        "confirmed_at": row["confirmed_at"],
        "checked_in_at": row["checked_in_at"],
        "checked_out_at": row["checked_out_at"],
        "cancelled_at": row["cancelled_at"],
        "cancel_reason": row["cancel_reason"],
        "is_overstay": bool(row["is_overstay"]),
        "created_at": row["created_at"],
        "vehicle": {"vehicle_id": row["vehicle_id"], "plate_number": row["plate_number"],
                    "province": row["province"]},
        "slot": {"slot_id": row["slot_id"], "slot_code": row["slot_code"], "slot_type": row["slot_type"],
                 "lot_id": row["lot_id"], "lot_name": row["lot_name"]},
        "payment": None,
        "estimated_fee": row["duration_hours"] * cfg("RATE_PER_HOUR"),
        "server_time": now,
    }
    if row.get("payment_id"):
        data["payment"] = {"payment_id": row["payment_id"], "base_fee": row["base_fee"],
                           "overstay_fee": row["overstay_fee"], "total_amount": row["total_amount"],
                           "status": row["payment_status"], "method": row["payment_method"]}
    if include_customer:
        data["customer"] = {"user_id": row["user_id"], "full_name": row["customer_name"],
                            "email": row["customer_email"], "phone": row["customer_phone"]}
    return data