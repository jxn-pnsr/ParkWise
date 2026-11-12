"""Hold -> Confirm -> Cancel (spec 7 and 10.1).

Double booking is prevented inside one transaction:
    lock order (always the same, avoids deadlocks):  user -> slot -> reservation
    1. lock the customer's row  (quota + same-car checks cannot race)
    2. lock the bay row          (SELECT ... FOR UPDATE)
    3. check overlap:  start_time < new_end AND end_time > new_start
       against bookings that still occupy the bay (Held not expired, Confirmed, In Use)
    4. INSERT the reservation + its outbox event, COMMIT
Two customers clicking the same bay at the same moment: the second waits for
the first lock, then sees the first hold and gets 409 SLOT_TAKEN.
"""
from datetime import timedelta

from core.errors import Conflict, Forbidden, NotFound, ValidationError
from core.settings import cfg
from core.timeutil import fmt_bkk, now_utc
from db.mysql import fetch_one, run_in_transaction, transaction
from services.availability import available_slot_ids
from services.outbox import notification, write_event
from services.queries import (OCCUPYING_SQL, OVERLAP_SQL, RESERVATION_SELECT, effective_status,
                              serialize_reservation, time_params)
from services.state_machine import assert_transition

SLOT_STEP_MINUTES = 30  # Advance bookings start on the hour or half hour


def get_reservation(reservation_id, include_customer=False):
    row = fetch_one(RESERVATION_SELECT + " WHERE r.reservation_id = %s", (reservation_id,))
    if not row:
        raise NotFound("RESERVATION_NOT_FOUND", "Reservation not found.")
    return row, serialize_reservation(row, include_customer=include_customer)


# ---- validation -------------------------------------------------------------

def validate_window(booking_type, start_time, duration_hours, now):
    """Return (start, end) or raise ValidationError (spec 7.2 rule 1-2)."""
    errors = {}
    lo, hi = cfg("MIN_DURATION_HOURS"), cfg("MAX_DURATION_HOURS")
    if duration_hours is None or not lo <= duration_hours <= hi:
        errors["duration_hours"] = f"Choose between {lo} and {hi} hours."

    if booking_type == "Instant":
        start = now
    else:
        start = start_time
        if start is None:
            errors["start_time"] = "Choose when you will arrive."
        elif start < now:
            errors["start_time"] = "The start time is in the past."
        elif start > now + timedelta(days=cfg("MAX_ADVANCE_DAYS")):
            errors["start_time"] = f"You can book at most {cfg('MAX_ADVANCE_DAYS')} days ahead."
        elif start.second or start.minute % SLOT_STEP_MINUTES:
            errors["start_time"] = "Start on the hour or half hour (e.g. 10:00 or 10:30)."
    if errors:
        raise ValidationError(fields=errors)
    return start, start + timedelta(hours=duration_hours)


# ---- create hold --------------------------------------------------------------

def _create_hold(cur, user, booking_type, slot_id, vehicle_id, start, end, duration_hours, now):
    # 1. customer row (serialises this customer's own bookings)
    cur.execute("SELECT user_id FROM Users WHERE user_id = %s FOR UPDATE", (user["user_id"],))

    # 2. the bay
    cur.execute("SELECT s.slot_id, s.slot_code, s.slot_type, s.slot_status, s.is_deleted, s.lot_id, "
                "l.lot_name, l.is_active AS lot_active FROM Parking_Slots s "
                "JOIN Parking_Lots l ON l.lot_id = s.lot_id WHERE s.slot_id = %s FOR UPDATE OF s", (slot_id,))
    slot = cur.fetchone()
    if not slot or slot["is_deleted"] or not slot["lot_active"]:
        raise NotFound("SLOT_NOT_FOUND", "This bay does not exist.")
    if slot["slot_status"] != "Active":
        raise Conflict("SLOT_MAINTENANCE", f"Bay {slot['slot_code']} is closed for maintenance.")

    # the car must belong to this customer and not be deleted (rule 7)
    cur.execute("SELECT vehicle_id, user_id, plate_number, province, is_deleted FROM Customer_Vehicles "
                "WHERE vehicle_id = %s", (vehicle_id,))
    vehicle = cur.fetchone()
    if not vehicle or vehicle["user_id"] != user["user_id"] or vehicle["is_deleted"]:
        raise ValidationError(fields={"vehicle_id": "Choose one of your own vehicles."})

    params = time_params(start, end, now)

    # quota (rule 6)
    cur.execute(f"SELECT COUNT(*) AS n FROM Reservations r WHERE r.user_id = %(uid)s AND {OCCUPYING_SQL}",
                {**params, "uid": user["user_id"]})
    limit = cfg("MAX_ACTIVE_RESERVATIONS_PER_USER")
    if cur.fetchone()["n"] >= limit:
        raise Conflict("QUOTA_EXCEEDED", f"You already have {limit} active reservations. "
                                         "Finish or cancel one before booking again.")

    # same car twice at the same time (rule 5)
    cur.execute(f"SELECT r.reservation_id FROM Reservations r WHERE r.vehicle_id = %(vid)s "
                f"AND {OCCUPYING_SQL} AND {OVERLAP_SQL} LIMIT 1", {**params, "vid": vehicle_id})
    if cur.fetchone():
        raise Conflict("VEHICLE_OVERLAP", f"{vehicle['plate_number']} already has a booking at that time.")

    # 3. overlap on this bay (rule 4)
    cur.execute(f"SELECT r.reservation_id FROM Reservations r WHERE r.slot_id = %(sid)s "
                f"AND {OCCUPYING_SQL} AND {OVERLAP_SQL} LIMIT 1", {**params, "sid": slot_id})
    if cur.fetchone():
        alternatives = available_slot_ids(cur, slot["lot_id"], start, end, now, exclude_slot_id=slot_id)
        raise Conflict("SLOT_TAKEN", f"Bay {slot['slot_code']} was just taken for that time.",
                       extra={"alternatives": alternatives})

    # 4. insert + event
    hold_until = now + timedelta(minutes=cfg("HOLD_MINUTES"))
    cur.execute(
        "INSERT INTO Reservations (user_id, vehicle_id, slot_id, booking_type, start_time, end_time, "
        "duration_hours, status, hold_expires_at, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, 'Held', %s, %s)",
        (user["user_id"], vehicle_id, slot_id, booking_type, start, end, duration_hours, hold_until, now))
    rid = cur.lastrowid
    write_event(cur, "reservation.held", "reservation", rid,
                metadata={"slot": slot["slot_code"], "lot": slot["lot_name"], "booking_type": booking_type,
                          "hold_minutes": cfg("HOLD_MINUTES"), "plate": vehicle["plate_number"]})
    return rid


def create_hold(user, booking_type, slot_id, vehicle_id, duration_hours, start_time=None):
    now = now_utc()
    start, end = validate_window(booking_type, start_time, duration_hours, now)
    rid = run_in_transaction(_create_hold, user, booking_type, slot_id, vehicle_id, start, end, duration_hours, now)
    return get_reservation(rid)[1]


# ---- confirm --------------------------------------------------------------------

def _lock_reservation(cur, reservation_id):
    cur.execute("SELECT r.*, s.slot_code, l.lot_name FROM Reservations r "
                "JOIN Parking_Slots s ON s.slot_id = r.slot_id JOIN Parking_Lots l ON l.lot_id = s.lot_id "
                "WHERE r.reservation_id = %s FOR UPDATE OF r", (reservation_id,))
    row = cur.fetchone()
    if not row:
        raise NotFound("RESERVATION_NOT_FOUND", "Reservation not found.")
    return row


def confirm(user, reservation_id):
    now = now_utc()
    expired = False
    with transaction() as cur:
        r = _lock_reservation(cur, reservation_id)
        if r["user_id"] != user["user_id"]:
            raise Forbidden()
        if r["status"] != "Held":
            raise Conflict("INVALID_STATE", f"This reservation is {effective_status(r, now)}, not waiting for confirmation.")
        if r["hold_expires_at"] <= now:
            # the backend decides expiry, not the countdown on the page (rule 3)
            cur.execute("UPDATE Reservations SET status = 'Expired', updated_at = %s WHERE reservation_id = %s",
                        (now, reservation_id))
            write_event(cur, "reservation.expired", "reservation", reservation_id,
                        metadata={"slot": r["slot_code"], "lot": r["lot_name"], "detected": "on_confirm"},
                        notify=notification("hold_expired", "Reservation expired",
                                            f"Your hold on bay {r['slot_code']} ({r['lot_name']}) expired before you confirmed it."),
                        target_user_id=r["user_id"])
            expired = True
        else:
            assert_transition("Held", "Confirmed")
            cur.execute("UPDATE Reservations SET status = 'Confirmed', confirmed_at = %s, updated_at = %s "
                        "WHERE reservation_id = %s", (now, now, reservation_id))
            if r["booking_type"] == "Instant":
                msg = (f"Bay {r['slot_code']} at {r['lot_name']} is yours. Please check in within "
                       f"{cfg('NOSHOW_GRACE_MINUTES')} minutes.")
            else:
                msg = (f"Bay {r['slot_code']} at {r['lot_name']} on {fmt_bkk(r['start_time'])}. "
                       f"Check in from {cfg('EARLY_CHECKIN_MINUTES')} minutes before to "
                       f"{cfg('NOSHOW_GRACE_MINUTES')} minutes after the start time.")
            write_event(cur, "reservation.confirmed", "reservation", reservation_id,
                        metadata={"slot": r["slot_code"], "lot": r["lot_name"]},
                        notify=notification("reservation_confirmed", "Reservation confirmed", msg),
                        target_user_id=r["user_id"])
    if expired:  # raised after COMMIT so the Expired status is kept
        raise Conflict("HOLD_EXPIRED", "The 5-minute hold has ended. Please choose a bay again.")
    return get_reservation(reservation_id)[1]


# ---- cancel ---------------------------------------------------------------------

def cancel_deadline(row):
    """Last moment the customer can cancel by themselves (None = cannot)."""
    if row["status"] == "Held":
        return row["hold_expires_at"]
    if row["status"] != "Confirmed" or row["checked_in_at"]:
        return None
    if row["booking_type"] == "Advance":
        return row["start_time"] - timedelta(hours=cfg("ADVANCE_CANCEL_HOURS"))
    return row["confirmed_at"] + timedelta(minutes=cfg("INSTANT_CANCEL_MINUTES"))


def customer_actions(row, now=None):
    now = now or now_utc()
    status = effective_status(row, now)
    deadline = cancel_deadline(row) if status in ("Held", "Confirmed") else None
    return {
        "can_confirm": status == "Held",
        "can_cancel": bool(deadline and now <= deadline),
        "cancel_deadline": deadline,
    }


def cancel(user, reservation_id, reason=None, by_admin=False):
    now = now_utc()
    with transaction() as cur:
        r = _lock_reservation(cur, reservation_id)
        if not by_admin and r["user_id"] != user["user_id"]:
            raise Forbidden()
        status = effective_status(r, now)
        if status not in ("Held", "Confirmed"):
            raise Conflict("INVALID_STATE", f"A reservation that is {status} cannot be cancelled.")
        if not by_admin:
            deadline = cancel_deadline(r)
            if not deadline or now > deadline:
                if r["booking_type"] == "Advance":
                    why = f"Advance bookings can be cancelled up to {cfg('ADVANCE_CANCEL_HOURS')} hour(s) before the start time."
                else:
                    why = f"Instant bookings can be cancelled within {cfg('INSTANT_CANCEL_MINUTES')} minutes of confirming."
                raise Conflict("CANCEL_WINDOW_PASSED", why + " Please contact an administrator.")
        assert_transition(r["status"], "Cancelled")
        cur.execute("UPDATE Reservations SET status = 'Cancelled', cancelled_at = %s, cancelled_by = %s, "
                    "cancel_reason = %s, updated_at = %s WHERE reservation_id = %s",
                    (now, user["user_id"], reason, now, reservation_id))
        who = "an administrator" if by_admin else "you"
        write_event(cur, "reservation.cancelled", "reservation", reservation_id,
                    metadata={"slot": r["slot_code"], "lot": r["lot_name"], "reason": reason, "by_admin": by_admin},
                    notify=notification("reservation_cancelled", "Reservation cancelled",
                                        f"Your booking for bay {r['slot_code']} on {fmt_bkk(r['start_time'])} "
                                        f"was cancelled by {who}." + (f" Reason: {reason}" if reason else "")),
                    target_user_id=r["user_id"])
    return get_reservation(reservation_id)[1]
