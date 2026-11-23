"""Admin changes to MySQL data are saved in one transaction and recorded in Activity_Logs. 
Related data is soft deleted to keep database relationships safe."""
import pymysql

from datetime import timedelta
from core.errors import Conflict, NotFound, ValidationError
from core.security import hash_password
from core.settings import DEFAULTS, RULES, _number, check_value, invalidate
from core.timeutil import fmt_bkk, now_utc
from db.mysql import fetch_all, fetch_one, fetch_value, transaction
from services.availability import slot_states
from services.booking import get_reservation
from services.outbox import notification, write_event
from services.queries import OCCUPYING_SQL, OVERLAP_SQL, time_params
from datetime import datetime
from core.timeutil import BKK, to_naive_utc

USER_COLS = "user_id, email, full_name, phone, role, is_active, failed_login_count, locked_until, created_at, updated_at"


def _dup(e, field, message):
    if isinstance(e, pymysql.err.IntegrityError) and e.args and e.args[0] == 1062:
        raise Conflict("DUPLICATE", message, fields={field: message})
    raise e
# ---- users ----------------------------------------------------------------------

def user_out(u):
    return {**{k: u[k] for k in ("user_id", "email", "full_name", "phone", "role", "created_at", "updated_at")},
            "is_active": bool(u["is_active"]), "locked_until": u["locked_until"],
            "is_locked": bool(u["locked_until"] and u["locked_until"] > now_utc()),
            "failed_login_count": u["failed_login_count"]}


def get_user(user_id):
    u = fetch_one(f"SELECT {USER_COLS} FROM Users WHERE user_id = %s", (user_id,))
    if not u:
        raise NotFound("USER_NOT_FOUND", "User not found.")
    return u


def create_user(email, password, full_name, phone, role):
    now = now_utc()
    try:
        with transaction() as cur:
            cur.execute("INSERT INTO Users (email, password_hash, full_name, phone, role, created_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s)", (email, hash_password(password), full_name, phone, role, now))
            uid = cur.lastrowid
            write_event(cur, "admin.user_created", "user", uid, metadata={"email": email, "role": role})
    except pymysql.err.IntegrityError as e:
        _dup(e, "email", "An account with this email already exists.")
    return user_out(get_user(uid))


def update_user(admin, user_id, changes):
    if not changes:
        return user_out(get_user(user_id))
    if user_id == admin["user_id"] and (changes.get("is_active") is False or changes.get("role", "Admin") != "Admin"):
        raise Conflict("SELF_LOCKOUT", "You cannot remove your own administrator access.")
    now = now_utc()
    sets, args = [], []
    for key in ("full_name", "phone", "role"):
        if key in changes:
            sets.append(f"{key} = %s")
            args.append(changes[key])
    if "is_active" in changes:
        sets.append("is_active = %s")
        args.append(1 if changes["is_active"] else 0)
    if changes.get("password"):
        sets.append("password_hash = %s")
        args.append(hash_password(changes["password"]))
    with transaction() as cur:
        cur.execute(f"SELECT {USER_COLS} FROM Users WHERE user_id = %s FOR UPDATE", (user_id,))
        before = cur.fetchone()
        if not before:
            raise NotFound("USER_NOT_FOUND", "User not found.")
        cur.execute(f"UPDATE Users SET {', '.join(sets)}, updated_at = %s WHERE user_id = %s", (*args, now, user_id))
        released = []
        if changes.get("is_active") is False and before["is_active"]:
            released = _release_bookings(cur, user_id, admin, now)
        shown = {k: v for k, v in changes.items() if k != "password"}
        write_event(cur, "admin.user_updated", "user", user_id,
                    metadata={"changes": shown, "password_reset": bool(changes.get("password")),
                              "before": {k: before[k] for k in shown if k in before},
                              "cancelled_reservations": released})
    return user_out(get_user(user_id))


def _release_bookings(cur, user_id, admin, now):
    """Deactivating an account cancels its Held and Confirmed bookings. 
    Cars already parked can still check out, and unpaid payments stay unchanged."""
    cur.execute("SELECT r.reservation_id, r.start_time, s.slot_code FROM Reservations r "
                "JOIN Parking_Slots s ON s.slot_id = r.slot_id "
                "WHERE r.user_id = %s AND r.status IN ('Held', 'Confirmed') FOR UPDATE OF r", (user_id,))
    rows = cur.fetchall()
    for r in rows:
        cur.execute("UPDATE Reservations SET status = 'Cancelled', cancelled_at = %s, cancelled_by = %s, "
                    "cancel_reason = 'Account deactivated', updated_at = %s WHERE reservation_id = %s",
                    (now, admin["user_id"], now, r["reservation_id"]))
        write_event(cur, "reservation.cancelled", "reservation", r["reservation_id"],
                    metadata={"slot": r["slot_code"], "reason": "Account deactivated", "by_admin": True})
    return [r["reservation_id"] for r in rows]


def unlock_user(user_id):
    with transaction() as cur:
        cur.execute("UPDATE Users SET failed_login_count = 0, locked_until = NULL WHERE user_id = %s", (user_id,))
        if not cur.rowcount and not fetch_one("SELECT 1 AS x FROM Users WHERE user_id = %s", (user_id,)):
            raise NotFound("USER_NOT_FOUND", "User not found.")
        write_event(cur, "admin.user_unlocked", "user", user_id, metadata={})
    return user_out(get_user(user_id))
# ---- lots ---------------------------------------------------------------------------

def get_lot(lot_id):
    lot = fetch_one("SELECT lot_id, lot_name, location_desc, is_active, created_at FROM Parking_Lots WHERE lot_id = %s",
                    (lot_id,))
    if not lot:
        raise NotFound("LOT_NOT_FOUND", "Parking lot not found.")
    return {**lot, "is_active": bool(lot["is_active"])}


def _active_bookings(cur, where, args):
    """Bookings that still need the bay: Held/Confirmed/In Use that have not ended."""
    params = time_params(now=now_utc())
    cur.execute(f"SELECT r.reservation_id, r.status, r.start_time, r.end_time, s.slot_code FROM Reservations r "
                f"JOIN Parking_Slots s ON s.slot_id = r.slot_id WHERE {where} AND {OCCUPYING_SQL} "
                f"AND (r.status = 'In Use' OR r.end_time > %(now)s) ORDER BY r.start_time", {**params, **args})
    return cur.fetchall()


def create_lot(name, location):
    try:
        with transaction() as cur:
            cur.execute("INSERT INTO Parking_Lots (lot_name, location_desc, created_at) VALUES (%s, %s, %s)",
                        (name, location, now_utc()))
            lot_id = cur.lastrowid
            write_event(cur, "admin.lot_created", "lot", lot_id, metadata={"lot": name})
    except pymysql.err.IntegrityError as e:
        _dup(e, "lot_name", "A lot with this name already exists.")
    return get_lot(lot_id)


def update_lot(lot_id, changes):
    try:
        with transaction() as cur:
            cur.execute("SELECT * FROM Parking_Lots WHERE lot_id = %s FOR UPDATE", (lot_id,))
            lot = cur.fetchone()
            if not lot:
                raise NotFound("LOT_NOT_FOUND", "Parking lot not found.")
            if changes.get("is_active") is False and lot["is_active"]:
                busy = _active_bookings(cur, "s.lot_id = %(lot)s", {"lot": lot_id})
                if busy:
                    raise Conflict("HAS_ACTIVE_RESERVATIONS",
                                   f"{len(busy)} active or upcoming booking(s) use this lot. Move or cancel them first.",
                                   extra={"reservations": busy})
            sets, args = [], []
            for key in ("lot_name", "location_desc"):
                if key in changes:
                    sets.append(f"{key} = %s"); args.append(changes[key])
            if "is_active" in changes:
                sets.append("is_active = %s"); args.append(1 if changes["is_active"] else 0)
            if sets:
                cur.execute(f"UPDATE Parking_Lots SET {', '.join(sets)}, updated_at = %s WHERE lot_id = %s",
                            (*args, now_utc(), lot_id))
                write_event(cur, "admin.lot_updated", "lot", lot_id, metadata={"changes": changes})
    except pymysql.err.IntegrityError as e:
        _dup(e, "lot_name", "A lot with this name already exists.")
    return get_lot(lot_id)


def delete_lot(lot_id):
    with transaction() as cur:
        cur.execute("SELECT lot_name FROM Parking_Lots WHERE lot_id = %s FOR UPDATE", (lot_id,))
        lot = cur.fetchone()
        if not lot:
            raise NotFound("LOT_NOT_FOUND", "Parking lot not found.")
        cur.execute("SELECT COUNT(*) AS n FROM Reservations r JOIN Parking_Slots s ON s.slot_id = r.slot_id "
                    "WHERE s.lot_id = %s", (lot_id,))
        if cur.fetchone()["n"]:
            raise Conflict("LOT_IN_USE", "This lot has booking history, so it cannot be deleted. Close it instead "
                                         "(set it to inactive): old bookings keep pointing at it.")
        cur.execute("DELETE FROM Parking_Slots WHERE lot_id = %s", (lot_id,))
        cur.execute("DELETE FROM Parking_Lots WHERE lot_id = %s", (lot_id,))
        write_event(cur, "admin.lot_deleted", "lot", lot_id, metadata={"lot": lot["lot_name"]})
# ---- slots ----------------------------------------------------------------------------

def get_slot(slot_id):
    s = fetch_one("SELECT s.*, l.lot_name FROM Parking_Slots s JOIN Parking_Lots l ON l.lot_id = s.lot_id "
                  "WHERE s.slot_id = %s", (slot_id,))
    if not s:
        raise NotFound("SLOT_NOT_FOUND", "Bay not found.")
    return {k: s[k] for k in ("slot_id", "lot_id", "lot_name", "slot_code", "slot_type", "slot_status", "created_at")} | {
        "is_deleted": bool(s["is_deleted"])}


def list_slots(lot_id):
    states = {s["slot_id"]: s for s in slot_states(lot_id, *_now_window(), with_details=True)}
    rows = fetch_all("SELECT s.slot_id, s.slot_code, s.slot_type, s.slot_status, s.lot_id, "
                     "(SELECT COUNT(*) FROM Reservations r WHERE r.slot_id = s.slot_id) AS bookings "
                     "FROM Parking_Slots s WHERE s.lot_id = %s AND s.is_deleted = 0 "
                     "ORDER BY CHAR_LENGTH(s.slot_code), s.slot_code", (lot_id,))
    return [{**r, **{k: states.get(r["slot_id"], {}).get(k) for k in ("display_status", "plate_number", "busy_until")}}
            for r in rows]


def _now_window():
    now = now_utc()
    return now, now + timedelta(hours=1)


def create_slot(lot_id, code, slot_type):
    get_lot(lot_id)
    try:
        with transaction() as cur:
            cur.execute("INSERT INTO Parking_Slots (lot_id, slot_code, slot_type, created_at) VALUES (%s, %s, %s, %s)",
                        (lot_id, code, slot_type, now_utc()))
            sid = cur.lastrowid
            write_event(cur, "admin.slot_created", "slot", sid, metadata={"slot": code, "lot_id": lot_id})
    except pymysql.err.IntegrityError as e:
        _dup(e, "slot_code", "This lot already has a bay with that code.")
    return get_slot(sid)


def update_slot(slot_id, changes):
    try:
        with transaction() as cur:
            cur.execute("SELECT * FROM Parking_Slots WHERE slot_id = %s FOR UPDATE", (slot_id,))
            s = cur.fetchone()
            if not s or s["is_deleted"]:
                raise NotFound("SLOT_NOT_FOUND", "Bay not found.")
            if changes.get("slot_status") == "Maintenance" and s["slot_status"] != "Maintenance":
                busy = _active_bookings(cur, "r.slot_id = %(sid)s", {"sid": slot_id})
                if busy:   
                    raise Conflict("HAS_ACTIVE_RESERVATIONS",
                                   f"Bay {s['slot_code']} has {len(busy)} active or upcoming booking(s). "
                                   "Move or cancel them before closing the bay.", extra={"reservations": busy})
            sets, args = [], []
            for key in ("slot_code", "slot_type", "slot_status"):
                if key in changes:
                    sets.append(f"{key} = %s"); args.append(changes[key])
            if sets:
                cur.execute(f"UPDATE Parking_Slots SET {', '.join(sets)}, updated_at = %s WHERE slot_id = %s",
                            (*args, now_utc(), slot_id))
                event = "slot.status_changed" if "slot_status" in changes and changes["slot_status"] != s["slot_status"] \
                    else "admin.slot_updated"
                write_event(cur, event, "slot", slot_id,
                            metadata={"slot": s["slot_code"], "from": s["slot_status"],
                                      "to": changes.get("slot_status", s["slot_status"]), "changes": changes})
    except pymysql.err.IntegrityError as e:
        _dup(e, "slot_code", "This lot already has a bay with that code.")
    return get_slot(slot_id)


def delete_slot(slot_id):
    """No booking history -> really deleted. History but nothing upcoming -> soft delete. Upcoming -> refused."""
    with transaction() as cur:
        cur.execute("SELECT * FROM Parking_Slots WHERE slot_id = %s FOR UPDATE", (slot_id,))
        s = cur.fetchone()
        if not s or s["is_deleted"]:
            raise NotFound("SLOT_NOT_FOUND", "Bay not found.")
        if _active_bookings(cur, "r.slot_id = %(sid)s", {"sid": slot_id}):
            raise Conflict("HAS_ACTIVE_RESERVATIONS", "This bay has active or upcoming bookings. Move or cancel them first.")
        cur.execute("SELECT COUNT(*) AS n FROM Reservations WHERE slot_id = %s", (slot_id,))
        if cur.fetchone()["n"]:
            cur.execute("UPDATE Parking_Slots SET is_deleted = 1, updated_at = %s WHERE slot_id = %s", (now_utc(), slot_id))
            how = "soft"
        else:
            cur.execute("DELETE FROM Parking_Slots WHERE slot_id = %s", (slot_id,))
            how = "hard"
        write_event(cur, "admin.slot_deleted", "slot", slot_id, metadata={"slot": s["slot_code"], "delete": how})
    return how
# ---- reservations ------------------------------------------------------------------------
ADMIN_STATUSES = ("Confirmed", "Cancelled", "Expired", "No-show")


def move_reservation(admin, reservation_id, new_slot_id, reason):
    """Move a booking to another free bay in the same window (spec 8.4: the next customer of an overstay)."""
    now = now_utc()
    r = fetch_one("SELECT * FROM Reservations WHERE reservation_id = %s", (reservation_id,))
    if not r:
        raise NotFound("RESERVATION_NOT_FOUND", "Reservation not found.")
    with transaction() as cur:
        for sid in sorted({r["slot_id"], new_slot_id}):          # bays first, in id order (no deadlock)
            cur.execute("SELECT slot_id FROM Parking_Slots WHERE slot_id = %s FOR UPDATE", (sid,))
        cur.execute("SELECT * FROM Reservations WHERE reservation_id = %s FOR UPDATE", (reservation_id,))
        locked = cur.fetchone()
        if not locked:                       # deleted while we were taking the locks
            raise NotFound("RESERVATION_NOT_FOUND", "Reservation not found.")
        if locked["slot_id"] != r["slot_id"]:  # moved by someone else meanwhile: our bay lock is the wrong one
            raise Conflict("CHANGED", "This reservation was just changed by someone else. Reload and try again.")
        r = locked
        if r["status"] not in ("Held", "Confirmed", "In Use"):
            raise Conflict("INVALID_STATE", f"A {r['status']} reservation cannot be moved.")
        cur.execute("SELECT s.*, l.lot_name, l.is_active AS lot_active FROM Parking_Slots s "
                    "JOIN Parking_Lots l ON l.lot_id = s.lot_id WHERE s.slot_id = %s", (new_slot_id,))
        target = cur.fetchone()
        if not target or target["is_deleted"] or not target["lot_active"] or target["slot_status"] != "Active":
            raise ValidationError(fields={"slot_id": "Choose an open bay."})
        start = max(r["start_time"], now) if r["status"] == "In Use" else r["start_time"]
        args = {**time_params(start, r["end_time"] if r["end_time"] > start else start + timedelta(minutes=1), now),
                "sid": new_slot_id, "rid": reservation_id}
        cur.execute(f"SELECT r.reservation_id FROM Reservations r WHERE r.slot_id = %(sid)s AND r.reservation_id <> %(rid)s "
                    f"AND {OCCUPYING_SQL} AND {OVERLAP_SQL} LIMIT 1", args)
        if cur.fetchone():
            raise Conflict("SLOT_TAKEN", f"Bay {target['slot_code']} is not free for this booking's time.")
        cur.execute("SELECT slot_code FROM Parking_Slots WHERE slot_id = %s", (r["slot_id"],))
        old_code = cur.fetchone()["slot_code"]
        cur.execute("UPDATE Reservations SET slot_id = %s, updated_at = %s WHERE reservation_id = %s",
                    (new_slot_id, now, reservation_id))
        write_event(cur, "admin.reservation_moved", "reservation", reservation_id,
                    metadata={"from": old_code, "to": target["slot_code"], "reason": reason},
                    notify=notification("reservation_moved", "Your bay has changed",
                                        f"Your booking on {fmt_bkk(r['start_time'])} moved from bay {old_code} to "
                                        f"bay {target['slot_code']} ({target['lot_name']}). Reason: {reason}"),
                    target_user_id=r["user_id"])
    return get_reservation(reservation_id, include_customer=True)[1]

ADMIN_TRANSITIONS = {
    "Held": ("Confirmed", "Cancelled", "Expired"),
    "Confirmed": ("Cancelled", "No-show"),
    "Cancelled": ("Confirmed",),
    "Expired": ("Confirmed",),
    "No-show": ("Confirmed",),
}


def set_reservation_status(admin, reservation_id, status, reason):
    """Admin changes are logged. When confirming a booking again, the system checks for time conflicts with other bookings, 
    just like a new booking. It also uses the same locks to prevent overlapping bookings."""
    now = now_utc()
    first = fetch_one("SELECT user_id, slot_id FROM Reservations WHERE reservation_id = %s", (reservation_id,))
    if not first:
        raise NotFound("RESERVATION_NOT_FOUND", "Reservation not found.")
    with transaction() as cur:
        if status == "Confirmed":
            cur.execute("SELECT user_id FROM Users WHERE user_id = %s FOR UPDATE", (first["user_id"],))
            cur.execute("SELECT slot_id FROM Parking_Slots WHERE slot_id = %s FOR UPDATE", (first["slot_id"],))
        cur.execute("SELECT * FROM Reservations WHERE reservation_id = %s FOR UPDATE", (reservation_id,))
        r = cur.fetchone()
        if not r:
            raise NotFound("RESERVATION_NOT_FOUND", "Reservation not found.")
        if r["status"] == status:
            raise Conflict("INVALID_STATE", f"The reservation is already {status}.")
        if r["status"] in ("In Use", "Completed"):
            raise Conflict("INVALID_STATE", "Use check-out for a parked car; completed bookings cannot change status.")
        if status not in ADMIN_TRANSITIONS.get(r["status"], ()):
            raise Conflict("INVALID_TRANSITION", f"A {r['status']} reservation cannot be changed to {status} by hand.")
        if status == "Confirmed":
            _check_can_confirm(cur, r, now)
        extra = ""
        if status == "Confirmed":
            extra = (", confirmed_at = COALESCE(confirmed_at, %(now)s), hold_expires_at = COALESCE(hold_expires_at, %(now)s)"
                     ", cancelled_at = NULL, cancelled_by = NULL, cancel_reason = NULL")
        elif status == "Cancelled":
            extra = ", cancelled_at = %(now)s, cancelled_by = %(admin)s, cancel_reason = %(reason)s"
        elif status == "No-show":
            extra = ", confirmed_at = COALESCE(confirmed_at, %(now)s)"
        cur.execute(f"UPDATE Reservations SET status = %(status)s, updated_at = %(now)s{extra} WHERE reservation_id = %(rid)s",
                    {"status": status, "now": now, "admin": admin["user_id"], "reason": reason, "rid": reservation_id})
        write_event(cur, "admin.reservation_status_changed", "reservation", reservation_id,
                    metadata={"from": r["status"], "to": status, "reason": reason},
                    notify=notification("reservation_updated", "Your reservation was updated",
                                        f"An administrator changed your booking for {fmt_bkk(r['start_time'])} "
                                        f"from {r['status']} to {status}. Reason: {reason}"),
                    target_user_id=r["user_id"])
    return get_reservation(reservation_id, include_customer=True)[1]


def _check_can_confirm(cur, r, now):
    """Rules for (re)confirming a booking. Locks on user + bay are already held."""
    params = time_params(r["start_time"], r["end_time"], now)
    if r["start_time"] <= params["noshow_cut"]:
        raise Conflict("TOO_LATE", "The check-in window of this booking has already closed, so it cannot be confirmed.")
    cur.execute("SELECT s.slot_code, s.slot_status, s.is_deleted, l.is_active AS lot_active FROM Parking_Slots s "
                "JOIN Parking_Lots l ON l.lot_id = s.lot_id WHERE s.slot_id = %s", (r["slot_id"],))
    slot = cur.fetchone()
    if not slot or slot["is_deleted"] or not slot["lot_active"] or slot["slot_status"] != "Active":
        raise Conflict("SLOT_MAINTENANCE", "The bay of this booking is closed. Move the booking to another bay first.")
    cur.execute("SELECT is_deleted FROM Customer_Vehicles WHERE vehicle_id = %s", (r["vehicle_id"],))
    car = cur.fetchone()
    if not car or car["is_deleted"]:
        raise Conflict("VEHICLE_DELETED", "The customer has deleted this vehicle.")
    args = {**params, "sid": r["slot_id"], "vid": r["vehicle_id"], "rid": r["reservation_id"]}
    cur.execute(f"SELECT r.reservation_id FROM Reservations r WHERE r.slot_id = %(sid)s AND r.reservation_id <> %(rid)s "
                f"AND {OCCUPYING_SQL} AND {OVERLAP_SQL} LIMIT 1", args)
    clash = cur.fetchone()
    if clash:
        raise Conflict("SLOT_TAKEN", f"Bay {slot['slot_code']} is already booked for that time "
                                     f"(reservation #{clash['reservation_id']}). Move this booking to another bay instead.")
    cur.execute(f"SELECT r.reservation_id FROM Reservations r WHERE r.vehicle_id = %(vid)s AND r.reservation_id <> %(rid)s "
                f"AND {OCCUPYING_SQL} AND {OVERLAP_SQL} LIMIT 1", args)
    if cur.fetchone():
        raise Conflict("VEHICLE_OVERLAP", "This car already has another booking at that time.")


def delete_reservation(reservation_id):
    """Only bookings that never used a bay (Expired / Cancelled) can be removed for good."""
    with transaction() as cur:
        cur.execute("SELECT status FROM Reservations WHERE reservation_id = %s FOR UPDATE", (reservation_id,))
        r = cur.fetchone()
        if not r:
            raise NotFound("RESERVATION_NOT_FOUND", "Reservation not found.")
        if r["status"] not in ("Expired", "Cancelled"):
            raise Conflict("INVALID_STATE", "Only Expired or Cancelled reservations can be deleted. Cancel it first.")
        cur.execute("SELECT COUNT(*) AS n FROM Payments WHERE reservation_id = %s", (reservation_id,))
        if cur.fetchone()["n"]:
            raise Conflict("HAS_PAYMENT", "This reservation has a payment and cannot be deleted.")
        cur.execute("DELETE FROM Reservations WHERE reservation_id = %s", (reservation_id,))
        write_event(cur, "admin.reservation_deleted", "reservation", reservation_id, metadata={"status": r["status"]})
# ---- payments --------------------------------------------------------------------------------

def create_payment(reservation_id, base_fee=None, overstay_fee=None):
    """Manual payment for a completed visit that has none (e.g. fixed by hand)."""
    from core.settings import all_config
    from services.fee import calculate_fee
    now = now_utc()
    with transaction() as cur:
        cur.execute("SELECT * FROM Reservations WHERE reservation_id = %s FOR UPDATE", (reservation_id,))
        r = cur.fetchone()
        if not r:
            raise NotFound("RESERVATION_NOT_FOUND", "Reservation not found.")
        if r["status"] != "Completed":
            raise Conflict("INVALID_STATE", "Payments are only for completed (checked-out) visits.")
        cur.execute("SELECT payment_id, is_deleted FROM Payments WHERE reservation_id = %s FOR UPDATE",
                    (reservation_id,))
        existing = cur.fetchone()
        if existing and not existing["is_deleted"]:
            raise Conflict("HAS_PAYMENT", "This visit already has a payment.")
        if base_fee is None or overstay_fee is None:
            fee = calculate_fee(r["start_time"], r["end_time"], r["duration_hours"], r["checked_in_at"],
                                r["checked_out_at"], all_config())
            base_fee = fee["base_fee"] if base_fee is None else base_fee
            overstay_fee = fee["overstay_fee"] if overstay_fee is None else overstay_fee
        if existing:
            pid = existing["payment_id"]
            cur.execute("UPDATE Payments SET base_fee = %s, overstay_fee = %s, total_amount = %s, status = 'Pending', "
                        "method = NULL, paid_at = NULL, is_deleted = 0, updated_at = %s WHERE payment_id = %s",
                        (base_fee, overstay_fee, base_fee + overstay_fee, now, pid))
        else:
            cur.execute("INSERT INTO Payments (reservation_id, user_id, base_fee, overstay_fee, total_amount, status, created_at) "
                        "VALUES (%s, %s, %s, %s, %s, 'Pending', %s)",
                        (reservation_id, r["user_id"], base_fee, overstay_fee, base_fee + overstay_fee, now))
            pid = cur.lastrowid
        write_event(cur, "admin.payment_created", "payment", pid,
                    metadata={"reservation_id": reservation_id, "total": base_fee + overstay_fee,
                              "restored_deleted_row": bool(existing)})
    return pid


def update_payment(payment_id, status, method=None):
    now = now_utc()
    with transaction() as cur:
        cur.execute("SELECT * FROM Payments WHERE payment_id = %s AND is_deleted = 0 FOR UPDATE", (payment_id,))
        p = cur.fetchone()
        if not p:
            raise NotFound("PAYMENT_NOT_FOUND", "Payment not found.")
        if status == "Paid":
            cur.execute("UPDATE Payments SET status = 'Paid', method = %s, paid_at = COALESCE(paid_at, %s), updated_at = %s "
                        "WHERE payment_id = %s", (method or p["method"] or "Cash", now, now, payment_id))
        else:
            cur.execute("UPDATE Payments SET status = %s, paid_at = NULL, updated_at = %s WHERE payment_id = %s",
                        (status, now, payment_id))
        notify = None
        if status == "Failed":
            notify = notification("payment_failed", "Payment failed",
                                  f"Your payment of THB {p['total_amount']} did not go through. Please try again in Payments.")
        write_event(cur, "admin.payment_updated", "payment", payment_id,
                    metadata={"reservation_id": p["reservation_id"], "from": p["status"], "to": status},
                    notify=notify, target_user_id=p["user_id"] if notify else None)


def delete_payment(payment_id):
    with transaction() as cur:
        cur.execute("UPDATE Payments SET is_deleted = 1, updated_at = %s WHERE payment_id = %s AND is_deleted = 0",
                    (now_utc(), payment_id))
        if not cur.rowcount:
            raise NotFound("PAYMENT_NOT_FOUND", "Payment not found.")
        write_event(cur, "admin.payment_deleted", "payment", payment_id, metadata={"soft_delete": True})
# ---- config --------------------------------------------------------------------------------------

def list_config():
    rows = fetch_all("SELECT c.config_key, c.config_value, c.description, c.updated_at, u.full_name AS updated_by_name "
                     "FROM System_Config c LEFT JOIN Users u ON u.user_id = c.updated_by ORDER BY c.config_key")
    for row in rows:   # allowed range, so the page can show it and set min/max on the input
        lo, hi = RULES.get(row["config_key"], (None, None))
        row["min"], row["max"] = lo, hi
    return rows


def set_config(admin, key, value):
    if key not in DEFAULTS:
        raise NotFound("CONFIG_NOT_FOUND", "Unknown setting.")
    with transaction() as cur:
        # all rows locked: two admins cannot save MIN and MAX at the same moment and break MIN <= MAX
        cur.execute("SELECT config_key, config_value FROM System_Config FOR UPDATE")
        current = dict(DEFAULTS)
        rows = cur.fetchall()
        for row in rows:
            current[row["config_key"]] = _number(row["config_value"])
        old = next((row for row in rows if row["config_key"] == key), None)
        problem = check_value(key, int(value), current)
        if problem:
            raise ValidationError(fields={"value": problem})
        cur.execute("INSERT INTO System_Config (config_key, config_value, updated_at, updated_by) VALUES (%s, %s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE config_value = VALUES(config_value), updated_at = VALUES(updated_at), "
                    "updated_by = VALUES(updated_by)", (key, value, now_utc(), admin["user_id"]))
        write_event(cur, "admin.config_updated", "system_config", key,
                    metadata={"old": old["config_value"] if old else None, "new": value})
    invalidate()
# ---- stats -----------------------------------------------------------------------------------------

def stats():
    now = now_utc()
    local = now + timedelta(hours=7)
    month = to_naive_utc(datetime(local.year, local.month, 1, tzinfo=BKK))
    prev = to_naive_utc(datetime((local.replace(day=1) - timedelta(days=1)).year,
                                 (local.replace(day=1) - timedelta(days=1)).month, 1, tzinfo=BKK))
    today = to_naive_utc(datetime(local.year, local.month, local.day, tzinfo=BKK))

    def pct(a, b):
        return round((a - b) / b * 100, 1) if b else None

    res_this = fetch_value("SELECT COUNT(*) FROM Reservations WHERE created_at >= %s", (month,))
    res_prev = fetch_value("SELECT COUNT(*) FROM Reservations WHERE created_at >= %s AND created_at < %s", (prev, month))
    rev = fetch_one("SELECT COALESCE(SUM(CASE WHEN paid_at >= %(m)s THEN total_amount END),0) AS this_m, "
                    "COALESCE(SUM(CASE WHEN paid_at >= %(p)s AND paid_at < %(m)s THEN total_amount END),0) AS prev_m, "
                    "COALESCE(SUM(CASE WHEN paid_at >= %(t)s THEN total_amount END),0) AS today "
                    "FROM Payments WHERE status = 'Paid' AND is_deleted = 0", {"m": month, "p": prev, "t": today})
    users = fetch_one("SELECT COUNT(*) AS total, SUM(role = 'Customer') AS customers, SUM(role = 'Staff') AS staff, "
                      "SUM(role = 'Admin') AS admins, SUM(created_at >= %s) AS new_this_month FROM Users WHERE is_active = 1",
                      (month,))
    by_status = {r["status"]: r["n"] for r in fetch_all("SELECT status, COUNT(*) AS n FROM Reservations GROUP BY status")}
    outbox = {r["status"]: r["n"] for r in fetch_all("SELECT status, COUNT(*) AS n FROM Integration_Outbox GROUP BY status")}
    unpaid = fetch_one("SELECT COUNT(*) AS n, COALESCE(SUM(total_amount),0) AS amount FROM Payments "
                       "WHERE status <> 'Paid' AND is_deleted = 0")

    # occupancy per Bangkok day for the last 7 days: booked bay-minutes / available bay-minutes
    active_bays = fetch_value("SELECT COUNT(*) FROM Parking_Slots s JOIN Parking_Lots l ON l.lot_id = s.lot_id "
                              "WHERE s.is_deleted = 0 AND s.slot_status = 'Active' AND l.is_active = 1") or 1
    days = []
    for i in range(6, -1, -1):
        d0 = today - timedelta(days=i)
        d1 = d0 + timedelta(days=1)
        used = fetch_value(
            "SELECT COALESCE(SUM(TIMESTAMPDIFF(MINUTE, GREATEST(start_time, %(a)s), LEAST(COALESCE(checked_out_at, "
            "GREATEST(end_time, LEAST(%(now)s, %(b)s))), %(b)s))), 0) FROM Reservations "
            "WHERE status IN ('Confirmed','In Use','Completed') AND start_time < %(b)s "
            "AND COALESCE(checked_out_at, GREATEST(end_time, %(now)s)) > %(a)s", {"a": d0, "b": d1, "now": now})
        days.append({"date": (d0 + timedelta(hours=7)).strftime("%Y-%m-%d"),
                     "occupancy_pct": round(max(0, float(used)) / (active_bays * 1440) * 100, 1)})
    return {
        "reservations_this_month": res_this, "reservations_change_pct": pct(res_this, res_prev),
        "revenue_this_month": float(rev["this_m"]), "revenue_change_pct": pct(float(rev["this_m"]), float(rev["prev_m"])),
        "revenue_today": float(rev["today"]),
        "users": {k: int(v or 0) for k, v in users.items()},
        "reservations_by_status": by_status,
        "outbox": {"Pending": outbox.get("Pending", 0), "Sent": outbox.get("Sent", 0), "Failed": outbox.get("Failed", 0)},
        "unpaid": {"count": unpaid["n"], "amount": float(unpaid["amount"])},
        "daily_occupancy": days,
        "active_bays": active_bays,
        "server_time": now,
    }
