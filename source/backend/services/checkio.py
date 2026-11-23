"""Check-in and check-out by staff (spec 8.1, 8.2).

Check-in (one transaction):
    lock bay -> lock reservation -> must be Confirmed and inside the window
    (start - 15 min .. start + 30 min) -> the previous car must have left the
    bay -> In Use, checked_in_at = now -> outbox checkin.done -> COMMIT

Check-out (one transaction, all or nothing):
    lock bay -> lock reservation -> must be In Use -> checked_out_at = now,
    Completed -> work out the fee -> INSERT Payments (Pending) -> outbox
    checkout.done -> COMMIT. Any error rolls every step back.

`fail_after` lets the Transaction Demo page stop half-way on purpose
(spec 11, scenario 3) to prove the rollback.
"""
from datetime import timedelta

from core.errors import Conflict, NotFound
from core.settings import all_config, cfg
from core.timeutil import fmt_bkk, now_utc, to_iso
from db.mysql import fetch_one, transaction
from services.booking import get_reservation
from services.fee import calculate_fee
from services.outbox import notification, write_event
from services.queries import effective_status
from services.state_machine import assert_transition


class SimulatedFailure(Exception):
    """Raised on purpose by the Transaction Demo."""


def checkin_window(row):
    return (row["start_time"] - timedelta(minutes=cfg("EARLY_CHECKIN_MINUTES")),
            row["start_time"] + timedelta(minutes=cfg("NOSHOW_GRACE_MINUTES")))


def _lock(cur, reservation_id):
    """Lock in the fixed order: bay first, then the reservation."""
    row = fetch_one("SELECT slot_id FROM Reservations WHERE reservation_id = %s", (reservation_id,))
    if not row:
        raise NotFound("RESERVATION_NOT_FOUND", "Reservation not found.")
    cur.execute("SELECT slot_id FROM Parking_Slots WHERE slot_id = %s FOR UPDATE", (row["slot_id"],))
    cur.execute("SELECT r.*, s.slot_code, l.lot_name, v.plate_number, v.province FROM Reservations r "
                "JOIN Parking_Slots s ON s.slot_id = r.slot_id JOIN Parking_Lots l ON l.lot_id = s.lot_id "
                "JOIN Customer_Vehicles v ON v.vehicle_id = r.vehicle_id "
                "WHERE r.reservation_id = %s FOR UPDATE OF r", (reservation_id,))
    r = cur.fetchone()
    if r["slot_id"] != row["slot_id"]:      # an admin moved it between the two reads
        raise Conflict("RETRY", "The reservation changed while it was being processed. Please try again.")
    return r


def _session(r, checked_in_at, checked_out_at=None, fee=None):
    s = {"reservationId": r["reservation_id"], "userId": r["user_id"], "vehicleId": r["vehicle_id"],
         "plateNumber": r["plate_number"], "lotName": r["lot_name"], "slotCode": r["slot_code"],
         "checkInAt": to_iso(checked_in_at), "isOverstay": bool(fee and fee["is_overstay"])}
    if checked_out_at:
        s["checkOutAt"] = to_iso(checked_out_at)
        s["durationMinutes"] = fee["minutes_parked"]
        s["fees"] = {"base": fee["base_fee"], "overstay": fee["overstay_fee"], "total": fee["total_amount"]}
    return s


def checkin(staff, reservation_id):
    now = now_utc()
    with transaction() as cur:
        r = _lock(cur, reservation_id)
        status = effective_status(r, now)
        if status != "Confirmed":
            raise Conflict("INVALID_STATE", f"Only a Confirmed reservation can be checked in (this one is {status}).")
        opens, closes = checkin_window(r)
        if now < opens:
            raise Conflict("CHECKIN_WINDOW", f"Too early. Check-in opens at {fmt_bkk(opens, '%H:%M')} "
                                             f"({cfg('EARLY_CHECKIN_MINUTES')} minutes before the start).")
        if now > closes:
            raise Conflict("CHECKIN_WINDOW", "Too late: the check-in window has closed (no-show).")
        cur.execute("SELECT r.reservation_id, v.plate_number FROM Reservations r "
                    "JOIN Customer_Vehicles v ON v.vehicle_id = r.vehicle_id "
                    "WHERE r.slot_id = %s AND r.status = 'In Use' AND r.reservation_id <> %s LIMIT 1",
                    (r["slot_id"], reservation_id))
        blocker = cur.fetchone()
        if blocker:
            raise Conflict("SLOT_STILL_OCCUPIED",
                           f"Bay {r['slot_code']} is still occupied by {blocker['plate_number']} (overstay). "
                           "Ask an administrator to move this booking to another bay.",
                           extra={"blocking_reservation_id": blocker["reservation_id"]})
        assert_transition("Confirmed", "In Use")
        cur.execute("UPDATE Reservations SET status = 'In Use', checked_in_at = %s, checked_in_by = %s, "
                    "updated_at = %s WHERE reservation_id = %s", (now, staff["user_id"], now, reservation_id))
        write_event(cur, "checkin.done", "reservation", reservation_id,
                    metadata={"slot": r["slot_code"], "lot": r["lot_name"], "plate": r["plate_number"]},
                    notify=notification("checkin_done", "Check-in successful",
                                        f"{r['plate_number']} is parked in bay {r['slot_code']} ({r['lot_name']}) "
                                        f"until {fmt_bkk(r['end_time'], '%H:%M')}."),
                    target_user_id=r["user_id"], session=_session(r, now))
    return get_reservation(reservation_id, include_customer=True)[1]


def checkout(staff, reservation_id, fail_after=None):
    """fail_after: None, or 'reservation' to raise after step 2 (demo only)."""
    now = now_utc()
    with transaction() as cur:
        r = _lock(cur, reservation_id)                                   # step 1
        if r["status"] != "In Use":
            raise Conflict("INVALID_STATE", f"Only a parked car can be checked out (this one is {r['status']}).")
        fee = calculate_fee(r["start_time"], r["end_time"], r["duration_hours"],
                            r["checked_in_at"], now, all_config())
        assert_transition("In Use", "Completed")
        cur.execute("UPDATE Reservations SET status = 'Completed', checked_out_at = %s, checked_out_by = %s, "
                    "is_overstay = %s, updated_at = %s WHERE reservation_id = %s",
                    (now, staff["user_id"], 1 if fee["is_overstay"] else r["is_overstay"], now, reservation_id))  # step 2
        if fail_after == "reservation":
            raise SimulatedFailure("Simulated failure after updating the reservation, before creating the payment")
        cur.execute("INSERT INTO Payments (reservation_id, user_id, base_fee, overstay_fee, total_amount, status, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, 'Pending', %s)",
                    (reservation_id, r["user_id"], fee["base_fee"], fee["overstay_fee"], fee["total_amount"], now))  # step 3
        payment_id = cur.lastrowid
        extra = f" (includes THB {fee['overstay_fee']} overstay)" if fee["overstay_fee"] else ""
        write_event(cur, "checkout.done", "reservation", reservation_id,                                     # step 4
                    metadata={"slot": r["slot_code"], "lot": r["lot_name"], "plate": r["plate_number"],
                              "payment_id": payment_id, "total": fee["total_amount"]},
                    notify=notification("checkout_done", "Check-out complete",
                                        f"You parked {fee['minutes_parked']} minutes in bay {r['slot_code']}. "
                                        f"Amount due: THB {fee['total_amount']}{extra}. Please pay in Payments."),
                    target_user_id=r["user_id"], session=_session(r, r["checked_in_at"], now, fee))
    data = get_reservation(reservation_id, include_customer=True)[1]
    data["fee"] = fee
    return data
