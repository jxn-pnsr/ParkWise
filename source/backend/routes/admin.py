"""Admin API for MySQL data, settings, outbox, stats, and transaction demos."""
from flask import Blueprint, current_app, request

from core.auth import current_user, login_required
from core.errors import ValidationError
from core.responses import created, get_pagination, ok, paginated
from core.settings import DEFAULTS, RULES, check_value
from core.timeutil import now_utc, parse_client_datetime
from core.validate import Input, like_contains, like_escape, query_int, request_json
from db.mysql import fetch_all, fetch_value
from services import admin, booking, demo, expiry, mongo_sync, payment, vehicles
from services.availability import lots_summary, resolve_window
from services.queries import RESERVATION_SELECT, serialize_reservation

bp = Blueprint("admin", __name__, url_prefix="/api/admin")
admin_only = login_required("Admin")


def me():
    return current_user()


def _bool_arg(name):
    v = request.args.get(name)
    return None if v in (None, "", "all") else v in ("1", "true", "yes")



@bp.get("/stats")
@admin_only
def stats():
    expiry.run_lazy()
    data = admin.stats()
    from db import mongo
    from jobs import scheduler
    data["mongo"] = "ok" if mongo.ping() else ("simulated_down" if mongo.is_simulated_down() else "down")
    data["scheduler"] = scheduler.status()
    start, end = resolve_window(None, 1)
    data["lots"] = lots_summary(start, end)
    data["recent"] = [serialize_reservation(r, include_customer=True) for r in
                      fetch_all(RESERVATION_SELECT + " ORDER BY r.created_at DESC LIMIT 6")]
    return ok(data)



@bp.get("/users")
@admin_only
def users():
    page, per_page, offset = get_pagination()
    where, args = ["1=1"], {}
    if request.args.get("role"):
        where.append("role = %(role)s"); args["role"] = request.args["role"]
    active = _bool_arg("active")
    if active is not None:
        where.append("is_active = %(active)s"); args["active"] = 1 if active else 0
    if request.args.get("q"):
        where.append("(email LIKE %(q)s OR full_name LIKE %(q)s OR phone LIKE %(q)s)"); args["q"] = like_contains(request.args["q"])
    cond = " AND ".join(where)
    total = fetch_value(f"SELECT COUNT(*) FROM Users WHERE {cond}", args)
    rows = fetch_all(f"SELECT {admin.USER_COLS} FROM Users WHERE {cond} ORDER BY user_id LIMIT %(l)s OFFSET %(o)s",
                     {**args, "l": per_page, "o": offset})
    return paginated([admin.user_out(u) for u in rows], page, per_page, total)


def _user_fields(v, creating):
    out = {}
    if creating or "full_name" in v.data:
        out["full_name"] = v.text("full_name", required=True, max_len=150, label="Name")
    if creating or "phone" in v.data:
        out["phone"] = v.phone() if v.data.get("phone") else None
    if creating or "role" in v.data:
        out["role"] = v.choice("role", ("Customer", "Staff", "Admin"), required=True, label="Role")
    if "is_active" in v.data:
        out["is_active"] = v.boolean("is_active")
    if creating:
        out["email"] = v.email()
        out["password"] = v.password()
    elif v.data.get("password"):
        out["password"] = v.password()
    return out


@bp.post("/users")
@admin_only
def create_user():
    v = Input(request_json())
    f = _user_fields(v, True)
    v.done()
    return created(admin.create_user(f["email"], f["password"], f["full_name"], f["phone"], f["role"]))


@bp.get("/users/<int:user_id>")
@admin_only
def get_user(user_id):
    return ok(admin.user_out(admin.get_user(user_id)))


@bp.patch("/users/<int:user_id>")
@admin_only
def update_user(user_id):
    v = Input(request_json())
    f = _user_fields(v, False)
    v.done()
    data = admin.update_user(me(), user_id, f)
    if user_id == me()["user_id"] and f.get("password"):
        # Keep this admin session active; other sessions are revoked.
        from core.security import restamp_session
        from db.mysql import fetch_value as _fv
        restamp_session(_fv("SELECT password_hash FROM Users WHERE user_id = %s", (user_id,)))
    return ok(data)


@bp.delete("/users/<int:user_id>")
@admin_only
def deactivate_user(user_id):
    """Deactivate the account without deleting its bookings or payments."""
    return ok(admin.update_user(me(), user_id, {"is_active": False}))


@bp.post("/users/<int:user_id>/unlock")
@admin_only
def unlock_user(user_id):
    return ok(admin.unlock_user(user_id))



@bp.get("/vehicles")
@admin_only
def all_vehicles():
    page, per_page, offset = get_pagination()
    where, args = ["1=1"], {}
    if not _bool_arg("include_deleted"):
        where.append("v.is_deleted = 0")
    if request.args.get("user_id"):
        where.append("v.user_id = %(uid)s"); args["uid"] = query_int("user_id")
    if request.args.get("q"):
        where.append("(v.plate_number LIKE %(q)s OR u.email LIKE %(q)s OR u.full_name LIKE %(q)s)")
        args["q"] = like_contains(request.args["q"])
    cond = " AND ".join(where)
    base = "FROM Customer_Vehicles v JOIN Users u ON u.user_id = v.user_id WHERE " + cond
    total = fetch_value("SELECT COUNT(*) " + base, args)
    rows = fetch_all("SELECT v.*, u.full_name, u.email " + base + " ORDER BY v.vehicle_id LIMIT %(l)s OFFSET %(o)s",
                     {**args, "l": per_page, "o": offset})
    return paginated([{**vehicles.serialize(r), "owner": {"full_name": r["full_name"], "email": r["email"]}} for r in rows],
                     page, per_page, total)


@bp.post("/vehicles")
@admin_only
def create_vehicle():
    v = Input(request_json())
    uid = v.integer("user_id", required=True, min_value=1, label="Owner")
    plate = v.text("plate_number", required=True, max_len=20, label="Plate")
    province = v.text("province", required=True, max_len=60, label="Province")
    v.done()
    owner = admin.get_user(uid)
    if owner["role"] != "Customer":
        raise ValidationError(fields={"user_id": "Vehicles belong to customers."})
    return created(vehicles.add(uid, plate, province, bool(v.data.get("is_default"))))


@bp.patch("/vehicles/<int:vehicle_id>")
@admin_only
def update_vehicle(vehicle_id):
    v = Input(request_json())
    plate = v.text("plate_number", max_len=20) if "plate_number" in v.data else None
    province = v.text("province", max_len=60) if "province" in v.data else None
    v.done()
    owner = fetch_value("SELECT user_id FROM Customer_Vehicles WHERE vehicle_id = %s", (vehicle_id,))
    if owner is None:
        from core.errors import NotFound
        raise NotFound("VEHICLE_NOT_FOUND", "Vehicle not found.")
    if v.data.get("is_default"):
        vehicles.set_default(owner, vehicle_id)
    return ok(vehicles.update(owner, vehicle_id, plate, province, by_admin=True))


@bp.delete("/vehicles/<int:vehicle_id>")
@admin_only
def delete_vehicle(vehicle_id):
    vehicles.delete(None, vehicle_id, admin=True)
    return ok(None)



@bp.get("/lots")
@admin_only
def lots():
    start, end = resolve_window(None, 1)
    return ok(lots_summary(start, end, include_inactive=True))


@bp.post("/lots")
@admin_only
def create_lot():
    v = Input(request_json())
    name = v.text("lot_name", required=True, max_len=100, label="Name")
    loc = v.text("location_desc", max_len=255)
    v.done()
    return created(admin.create_lot(name, loc))


@bp.patch("/lots/<int:lot_id>")
@admin_only
def update_lot(lot_id):
    v = Input(request_json())
    changes = {}
    if "lot_name" in v.data:
        changes["lot_name"] = v.text("lot_name", required=True, max_len=100, label="Name")
    if "location_desc" in v.data:
        changes["location_desc"] = v.text("location_desc", max_len=255)
    if "is_active" in v.data:
        changes["is_active"] = v.boolean("is_active")
    v.done()
    return ok(admin.update_lot(lot_id, changes))


@bp.delete("/lots/<int:lot_id>")
@admin_only
def delete_lot(lot_id):
    admin.delete_lot(lot_id)
    return ok(None)


@bp.get("/lots/<int:lot_id>/slots")
@admin_only
def slots(lot_id):
    admin.get_lot(lot_id)
    return ok(admin.list_slots(lot_id))


@bp.post("/slots")
@admin_only
def create_slot():
    v = Input(request_json())
    lot_id = v.integer("lot_id", required=True, min_value=1, label="Lot")
    code = v.text("slot_code", required=True, max_len=20, label="Bay code")
    stype = v.choice("slot_type", ("Standard", "EV")) or "Standard"
    v.done()
    return created(admin.create_slot(lot_id, code.upper(), stype))


@bp.patch("/slots/<int:slot_id>")
@admin_only
def update_slot(slot_id):
    v = Input(request_json())
    changes = {}
    if "slot_code" in v.data:
        changes["slot_code"] = (v.text("slot_code", required=True, max_len=20, label="Bay code") or "").upper()
    if "slot_type" in v.data:
        changes["slot_type"] = v.choice("slot_type", ("Standard", "EV"), required=True)
    if "slot_status" in v.data:
        changes["slot_status"] = v.choice("slot_status", ("Active", "Maintenance"), required=True)
    v.done()
    return ok(admin.update_slot(slot_id, changes))


@bp.delete("/slots/<int:slot_id>")
@admin_only
def delete_slot(slot_id):
    return ok({"delete": admin.delete_slot(slot_id)})



@bp.get("/reservations")
@admin_only
def reservations():
    expiry.run_lazy()
    page, per_page, offset = get_pagination()
    where, args = ["1=1"], {}
    for key, col in (("status", "r.status"), ("booking_type", "r.booking_type")):
        if request.args.get(key):
            where.append(f"{col} = %({key})s"); args[key] = request.args[key]
    for key, col in (("user_id", "r.user_id"), ("slot_id", "r.slot_id"), ("lot_id", "s.lot_id")):
        if request.args.get(key):
            where.append(f"{col} = %({key})s"); args[key] = query_int(key)
    if _bool_arg("overstay"):
        where.append("r.is_overstay = 1")
    for key, op in (("from", ">="), ("to", "<")):
        if request.args.get(key):
            try:
                args[key] = parse_client_datetime(request.args[key])
            except ValueError:
                raise ValidationError(fields={key: "Use a date like 2026-11-20 or 2026-11-20T10:00+07:00."})
            where.append(f"r.start_time {op} %({key})s")
    if request.args.get("q"):
        where.append("(v.plate_number LIKE %(q)s OR u.email LIKE %(q)s OR u.full_name LIKE %(q)s OR r.reservation_id = %(qid)s)")
        args["q"] = like_contains(request.args["q"])
        args["qid"] = int(request.args["q"]) if request.args["q"].isdigit() else -1
    cond = " AND ".join(where)
    joins = ("FROM Reservations r JOIN Customer_Vehicles v ON v.vehicle_id = r.vehicle_id "
             "JOIN Parking_Slots s ON s.slot_id = r.slot_id JOIN Users u ON u.user_id = r.user_id WHERE ")
    total = fetch_value("SELECT COUNT(*) " + joins + cond, args)
    rows = fetch_all(RESERVATION_SELECT + " WHERE " + cond + " ORDER BY r.start_time DESC LIMIT %(l)s OFFSET %(o)s",
                     {**args, "l": per_page, "o": offset})
    now = now_utc()
    return paginated([serialize_reservation(r, now, include_customer=True) for r in rows], page, per_page, total)


@bp.get("/reservations/<int:reservation_id>")
@admin_only
def reservation(reservation_id):
    return ok(booking.get_reservation(reservation_id, include_customer=True)[1])


@bp.patch("/reservations/<int:reservation_id>")
@admin_only
def edit_reservation(reservation_id):
    """Move the booking or override its status; both require admin_reason."""
    v = Input(request_json())
    reason = v.text("admin_reason", required=True, max_len=200, label="Reason")
    slot_id = v.integer("slot_id", min_value=1) if "slot_id" in v.data else None
    status = v.choice("status", admin.ADMIN_STATUSES) if "status" in v.data else None
    if not slot_id and not status:
        v.error("slot_id", "Send a new slot_id or a status.")
    v.done()
    data = None
    if slot_id:
        data = admin.move_reservation(me(), reservation_id, slot_id, reason)
    if status:
        data = admin.set_reservation_status(me(), reservation_id, status, reason)
    return ok(data)


@bp.post("/reservations/<int:reservation_id>/cancel")
@admin_only
def cancel_reservation(reservation_id):
    v = Input(request_json())
    reason = v.text("reason", required=True, max_len=255, label="Reason")
    v.done()
    return ok(booking.cancel(me(), reservation_id, reason, by_admin=True))


@bp.delete("/reservations/<int:reservation_id>")
@admin_only
def delete_reservation(reservation_id):
    admin.delete_reservation(reservation_id)
    return ok(None)



@bp.get("/payments")
@admin_only
def payments():
    page, per_page, offset = get_pagination()
    where, args = [], {}
    if not _bool_arg("include_deleted"):
        where.append("p.is_deleted = 0")
    if request.args.get("status"):
        where.append("p.status = %(status)s"); args["status"] = request.args["status"]
    if request.args.get("user_id"):
        where.append("p.user_id = %(uid)s"); args["uid"] = query_int("user_id")
    if request.args.get("q"):
        where.append("(v.plate_number LIKE %(q)s OR u.full_name LIKE %(q)s OR u.email LIKE %(q)s)")
        args["q"] = like_contains(request.args["q"])
    cond = " WHERE " + (" AND ".join(where) if where else "1=1")
    total = fetch_value("SELECT COUNT(*) FROM Payments p JOIN Reservations r ON r.reservation_id = p.reservation_id "
                        "JOIN Customer_Vehicles v ON v.vehicle_id = r.vehicle_id JOIN Users u ON u.user_id = p.user_id" + cond, args)
    rows = fetch_all(payment.PAYMENT_SELECT + cond + " ORDER BY p.created_at DESC LIMIT %(l)s OFFSET %(o)s",
                     {**args, "l": per_page, "o": offset})
    return paginated([payment.serialize(p) for p in rows], page, per_page, total)


@bp.post("/payments")
@admin_only
def create_payment():
    v = Input(request_json())
    rid = v.integer("reservation_id", required=True, min_value=1, label="Reservation")
    base = v.integer("base_fee", min_value=0) if v.has("base_fee") else None
    over = v.integer("overstay_fee", min_value=0) if v.has("overstay_fee") else None
    v.done()
    pid = admin.create_payment(rid, base, over)
    return created(payment.serialize(payment.get(pid)))


@bp.patch("/payments/<int:payment_id>")
@admin_only
def update_payment(payment_id):
    v = Input(request_json())
    status = v.choice("status", ("Pending", "Paid", "Failed"), required=True)
    method = v.choice("method", payment.METHODS) if v.has("method") else None
    v.done()
    admin.update_payment(payment_id, status, method)
    return ok(payment.serialize(payment.get(payment_id)))


@bp.delete("/payments/<int:payment_id>")
@admin_only
def delete_payment(payment_id):
    admin.delete_payment(payment_id)
    return ok(None)



@bp.get("/config")
@admin_only
def config():
    return ok(admin.list_config())


@bp.put("/config/<key>")
@admin_only
def set_config(key):
    if key not in DEFAULTS:
        from core.errors import NotFound
        raise NotFound("CONFIG_NOT_FOUND", "Unknown setting.")
    lo, hi = RULES[key]
    v = Input(request_json())
    value = v.integer("value", required=True, label="Value")
    v.done()
    problem = check_value(key, value)  # Check range and MIN <= MAX duration.
    if problem:
        raise ValidationError(fields={"value": problem}, extra={"min": lo, "max": hi})
    admin.set_config(me(), key, str(value))
    return ok(admin.list_config())



@bp.get("/outbox")
@admin_only
def outbox():
    page, per_page, offset = get_pagination()
    where, args = ["1=1"], {}
    if request.args.get("status"):
        where.append("status = %(status)s"); args["status"] = request.args["status"]
    if request.args.get("event_type"):
        where.append("event_type LIKE %(t)s"); args["t"] = like_escape(request.args["event_type"]) + "%"
    cond = " AND ".join(where)
    total = fetch_value(f"SELECT COUNT(*) FROM Integration_Outbox WHERE {cond}", args)
    rows = fetch_all(f"SELECT event_id, seq, event_type, aggregate_id, payload, status, retry_count, last_error, "
                     f"created_at, sent_at FROM Integration_Outbox WHERE {cond} ORDER BY seq DESC LIMIT %(l)s OFFSET %(o)s",
                     {**args, "l": per_page, "o": offset})
    import json
    for r in rows:
        r["payload"] = json.loads(r["payload"]) if isinstance(r["payload"], (str, bytes)) else r["payload"]
    counts = {r["status"]: r["n"] for r in fetch_all("SELECT status, COUNT(*) AS n FROM Integration_Outbox GROUP BY status")}
    return ok({"items": rows, "page": page, "per_page": per_page, "total": total, "counts": counts})


@bp.post("/outbox/<event_id>/retry")
@admin_only
def retry_one(event_id):
    n = mongo_sync.retry([event_id])
    return ok({"retried": n, **mongo_sync.process_pending()})


@bp.post("/outbox/retry-failed")
@admin_only
def retry_all():
    n = mongo_sync.retry()
    return ok({"retried": n, **mongo_sync.process_pending()})


@bp.post("/outbox/process")
@admin_only
def process_now():
    return ok(mongo_sync.process_pending())


@bp.delete("/outbox/<event_id>")
@admin_only
def delete_event(event_id):
    from db.mysql import transaction
    with transaction() as cur:
        cur.execute("DELETE FROM Integration_Outbox WHERE event_id = %s AND status = 'Sent'", (event_id,))
        if not cur.rowcount:
            from core.errors import Conflict
            raise Conflict("NOT_SENT", "Only events that were sent can be deleted.")
    return ok(None)


@bp.delete("/outbox")
@admin_only
def purge_sent():
    """Delete Sent events older than older_than_days (default 7)."""
    days = query_int("older_than_days", default=7, min_value=0, max_value=3650)
    from datetime import timedelta

    from db.mysql import transaction
    with transaction() as cur:
        cur.execute("DELETE FROM Integration_Outbox WHERE status = 'Sent' AND sent_at < %s",
                    (now_utc() - timedelta(days=days),))
        return ok({"deleted": cur.rowcount})


# Demo endpoints modify real data and are available only when DEMO_MODE=1.

@bp.before_request
def _demo_gate():
    if request.path.startswith("/api/admin/demo") and not current_app.config.get("DEMO_MODE"):
        from core.errors import NotFound
        raise NotFound("DEMO_MODE_OFF", "The Transaction Demo is turned off on this server (set DEMO_MODE=1).")


@bp.get("/demo")
@admin_only
def demo_candidates():
    return ok({**demo.candidates(), **demo.mongo_state()})


@bp.post("/demo/checkout-success")
@admin_only
def demo_checkout():
    v = Input(request_json())
    rid = v.integer("reservation_id", required=True, min_value=1)
    v.done()
    return ok(demo.checkout_success(me(), rid))


@bp.post("/demo/midway-rollback")
@admin_only
def demo_rollback():
    v = Input(request_json())
    rid = v.integer("reservation_id", required=True, min_value=1)
    v.done()
    return ok(demo.midway_rollback(me(), rid))


@bp.post("/demo/double-booking")
@admin_only
def demo_double():
    v = Input(request_json())
    sid = v.integer("slot_id", required=True, min_value=1)
    attempts = v.integer("attempts", min_value=2, max_value=10) or 2
    v.done()
    return ok(demo.double_booking(me(), sid, attempts))


@bp.post("/demo/mongo-down")
@admin_only
def demo_mongo_down():
    v = Input(request_json())
    enabled = v.boolean("enabled", required=True)
    v.done()
    return ok(demo.set_mongo_down(enabled))


@bp.post("/demo/mongo-retry")
@admin_only
def demo_mongo_retry():
    return ok(demo.retry_and_send())


@bp.post("/demo/run-jobs")
@admin_only
def demo_run_jobs():
    from jobs import scheduler
    return ok(scheduler.run_all_now())
