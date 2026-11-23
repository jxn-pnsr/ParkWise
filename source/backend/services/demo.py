import threading

from datetime import timedelta
from core.errors import Conflict, NotFound
from core.timeutil import now_utc
from db import mongo
from db.mysql import fetch_all, fetch_one, fetch_value
from services import booking, checkio, mongo_sync

RES_COLS = ("reservation_id, status, slot_id, checked_in_at, checked_out_at, is_overstay, updated_at")


def _reservation_row(rid):
    return fetch_one(f"SELECT {RES_COLS} FROM Reservations WHERE reservation_id = %s", (rid,))


def _payment_row(rid):
    return fetch_one("SELECT payment_id, reservation_id, base_fee, overstay_fee, total_amount, status, method "
                     "FROM Payments WHERE reservation_id = %s", (rid,))


def candidates():
    """Parked cars the check-out scenarios can use, and free bays for the double-booking scenario."""
    parked = fetch_all("SELECT r.reservation_id, v.plate_number, s.slot_code, l.lot_name, r.end_time "
                       "FROM Reservations r JOIN Customer_Vehicles v ON v.vehicle_id = r.vehicle_id "
                       "JOIN Parking_Slots s ON s.slot_id = r.slot_id JOIN Parking_Lots l ON l.lot_id = s.lot_id "
                       "WHERE r.status = 'In Use' ORDER BY r.reservation_id")
    bays = fetch_all("SELECT s.slot_id, s.slot_code, l.lot_name FROM Parking_Slots s "
                     "JOIN Parking_Lots l ON l.lot_id = s.lot_id WHERE s.is_deleted = 0 AND s.slot_status = 'Active' "
                     "AND l.is_active = 1 ORDER BY s.lot_id, CHAR_LENGTH(s.slot_code), s.slot_code")
    return {"parked": parked, "bays": bays, "mongo_simulated_down": mongo.is_simulated_down()}


def checkout_success(staff, reservation_id):
    before = {"reservation": _reservation_row(reservation_id), "payment": _payment_row(reservation_id)}
    if not before["reservation"]:
        raise NotFound("RESERVATION_NOT_FOUND", "Reservation not found.")
    if before["reservation"]["status"] != "In Use":
        raise Conflict("INVALID_STATE", "Pick a reservation that is In Use (a parked car).")
    out = checkio.checkout(staff, reservation_id)
    after = {"reservation": _reservation_row(reservation_id), "payment": _payment_row(reservation_id)}
    return {"result": "COMMIT", "fee": out["fee"], "before": before, "after": after,
            "steps": ["lock bay + reservation (FOR UPDATE)", "reservation -> Completed, checked_out_at = now",
                      "calculate fee", "INSERT Payments (Pending)", "INSERT Integration_Outbox", "COMMIT"]}


def midway_rollback(staff, reservation_id):
    before = {"reservation": _reservation_row(reservation_id), "payment": _payment_row(reservation_id),
              "payments_total": fetch_value("SELECT COUNT(*) FROM Payments"),
              "outbox_total": fetch_value("SELECT COUNT(*) FROM Integration_Outbox")}
    if not before["reservation"]:
        raise NotFound("RESERVATION_NOT_FOUND", "Reservation not found.")
    if before["reservation"]["status"] != "In Use":
        raise Conflict("INVALID_STATE", "Pick a reservation that is In Use (a parked car).")
    error = None
    try:
        checkio.checkout(staff, reservation_id, fail_after="reservation")
    except checkio.SimulatedFailure as e:
        error = str(e)
    after = {"reservation": _reservation_row(reservation_id), "payment": _payment_row(reservation_id),
             "payments_total": fetch_value("SELECT COUNT(*) FROM Payments"),
             "outbox_total": fetch_value("SELECT COUNT(*) FROM Integration_Outbox")}
    return {"result": "ROLLBACK", "error": error, "before": before, "after": after, "unchanged": before == after,
            "steps": ["lock bay + reservation", "reservation -> Completed (done in the transaction)",
                      "!! simulated error before INSERT Payments", "ROLLBACK: step 2 is undone"]}


def _demo_customer_list(n):
    return [(f"demo.racer{i}@parkwise.local", f"Demo Racer {i}", f"DEMO {i:04d}") for i in range(1, n + 1)]


def _demo_customers(n=2):
    """customers that exist only for this scenario (random password nobody knows)."""
    import secrets

    from core.security import hash_password
    from db.mysql import transaction
    out = []
    with transaction() as cur:
        for email, name, plate in _demo_customer_list(n):
            cur.execute("SELECT user_id FROM Users WHERE email = %s FOR UPDATE", (email,))
            row = cur.fetchone()
            if row:
                uid = row["user_id"]
                cur.execute("UPDATE Users SET is_active = 1 WHERE user_id = %s", (uid,))
            else:
                cur.execute("INSERT INTO Users (email, password_hash, full_name, role, created_at) "
                            "VALUES (%s, %s, %s, 'Customer', %s)",
                            (email, hash_password(secrets.token_urlsafe(32)), name, now_utc()))
                uid = cur.lastrowid
            cur.execute("SELECT vehicle_id FROM Customer_Vehicles WHERE user_id = %s AND is_deleted = 0 "
                        "ORDER BY vehicle_id LIMIT 1", (uid,))
            car = cur.fetchone()
            if car:
                vid = car["vehicle_id"]
            else:
                cur.execute("INSERT INTO Customer_Vehicles (user_id, plate_number, province, is_default, created_at) "
                            "VALUES (%s, %s, 'Demo', 1, %s)", (uid, plate, now_utc()))
                vid = cur.lastrowid
            # leftovers from an earlier run that stopped half way
            cur.execute("UPDATE Reservations SET status = 'Cancelled', cancelled_at = %s, cancel_reason = %s, "
                        "updated_at = %s WHERE user_id = %s AND status IN ('Held','Confirmed')",
                        (now_utc(), "Transaction demo clean-up", now_utc(), uid))
            out.append({"user_id": uid, "full_name": name, "vehicle_id": vid, "plate_number": plate})
    return out


def double_booking(admin, slot_id, attempts=2):
    """`attempts` customers try to hold one bay for the same window at the same instant (threads)."""
    customers = _demo_customers(attempts)  
    start = (now_utc() + timedelta(days=6)).replace(hour=20, minute=0, second=0, microsecond=0)
    rows_before = fetch_value("SELECT COUNT(*) FROM Reservations WHERE slot_id = %s", (slot_id,))
    barrier = threading.Barrier(attempts)
    results = []

    def attempt(c):
        user = {"user_id": c["user_id"], "role": "Customer"}
        barrier.wait()
        try:
            r = booking.create_hold(user, "Advance", slot_id, c["vehicle_id"], 1, start)
            results.append({"customer": c["full_name"], "plate": c["plate_number"], "outcome": "HELD",
                            "reservation_id": r["reservation_id"]})
        except Exception as e:  
            results.append({"customer": c["full_name"], "plate": c["plate_number"], "outcome": "REJECTED",
                            "code": getattr(e, "code", type(e).__name__), "message": str(e)})

    threads = [threading.Thread(target=attempt, args=(customers[i % len(customers)],)) for i in range(attempts)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rows_after = fetch_value("SELECT COUNT(*) FROM Reservations WHERE slot_id = %s", (slot_id,))
    # tidy up so the demo can be repeated: release the winning hold
    for r in results:
        if r["outcome"] == "HELD":
            booking.cancel(admin, r["reservation_id"], "Transaction demo clean-up", by_admin=True, notify=False)
    return {"window_start": start, "slot_id": slot_id, "attempts": results,
            "rows_before": rows_before, "rows_after": rows_after, "new_rows": rows_after - rows_before}


def mongo_state():
    counts = {r["status"]: r["n"] for r in fetch_all("SELECT status, COUNT(*) AS n FROM Integration_Outbox GROUP BY status")}
    latest = fetch_all("SELECT event_id, event_type, status, retry_count, last_error, created_at, sent_at "
                       "FROM Integration_Outbox ORDER BY seq DESC LIMIT 8")
    return {"simulated_down": mongo.is_simulated_down(), "mongo_ok": mongo.ping(),
            "outbox": {"Pending": counts.get("Pending", 0), "Sent": counts.get("Sent", 0), "Failed": counts.get("Failed", 0)},
            "latest": latest}


def set_mongo_down(enabled):
    mongo.set_simulated_down(enabled)
    return mongo_state()


def retry_and_send():
    retried = mongo_sync.retry()
    sent = mongo_sync.process_pending()
    return {"retried": retried, **sent, **mongo_state()}
