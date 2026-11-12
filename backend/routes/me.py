"""Customer data. Uses the session user, never a request user_id."""
from datetime import datetime, timedelta

from flask import Blueprint, request

from core.auth import current_user, ensure_owner, login_required
from core.responses import created, get_pagination, ok, paginated
from core.timeutil import BKK, now_utc, to_naive_utc
from core.validate import Input, request_json
from db.mysql import fetch_all, fetch_one, fetch_value
from services import availability, booking, expiry, users, vehicles
from services.queries import OCCUPYING_SQL, RESERVATION_SELECT, serialize_reservation, time_params

bp = Blueprint("me", __name__, url_prefix="/api/me")
customer = login_required("Customer")


# profile

@bp.get("/profile")
@customer
def profile():
    return ok(users.public_user(current_user()))


@bp.patch("/profile")
@customer
def update_profile():
    v = Input(request_json())
    full_name = v.text("full_name", max_len=150, label="Name") if "full_name" in v.data else None
    if "full_name" in v.data and not full_name:
        v.error("full_name", "Name is required.")
    phone = v.phone() if v.data.get("phone") else None
    v.done()
    clear_phone = "phone" in v.data and not v.data.get("phone")
    return ok(users.update_profile(current_user()["user_id"], full_name, phone, clear_phone))


@bp.post("/password")
@customer
def change_password():
    v = Input(request_json())
    current = v.password("current_password", min_len=1)
    new = v.password("new_password")
    v.done()
    users.change_password(current_user()["user_id"], current, new)
    return ok(None)


# vehicles

@bp.get("/vehicles")
@customer
def my_vehicles():
    return ok(vehicles.list_for_user(current_user()["user_id"]))


@bp.post("/vehicles")
@customer
def add_vehicle():
    v = Input(request_json())
    plate = v.text("plate_number", required=True, max_len=20, label="Plate")
    province = v.text("province", required=True, max_len=60, label="Province")
    make_default = v.boolean("is_default") or False
    v.done()
    return created(vehicles.add(current_user()["user_id"], plate, province, make_default))


@bp.patch("/vehicles/<int:vehicle_id>")
@customer
def edit_vehicle(vehicle_id):
    v = Input(request_json())
    plate = v.text("plate_number", max_len=20, label="Plate") if "plate_number" in v.data else None
    province = v.text("province", max_len=60, label="Province") if "province" in v.data else None
    v.done()
    return ok(vehicles.update(current_user()["user_id"], vehicle_id, plate, province))


@bp.post("/vehicles/<int:vehicle_id>/default")
@customer
def default_vehicle(vehicle_id):
    return ok(vehicles.set_default(current_user()["user_id"], vehicle_id))


@bp.delete("/vehicles/<int:vehicle_id>")
@customer
def delete_vehicle(vehicle_id):
    vehicles.delete(current_user()["user_id"], vehicle_id)
    return ok(None)


# reservations

@bp.get("/reservations")
@customer
def my_reservations():
    """Active, past, or all reservations with optional status and pagination."""
    expiry.run_all()
    uid = current_user()["user_id"]
    page, per_page, offset = get_pagination()
    scope = request.args.get("scope", "active")
    where, args = ["r.user_id = %(uid)s"], {**time_params(), "uid": uid}
    if scope == "active":
        where.append(OCCUPYING_SQL)
    elif scope == "past":
        where.append(f"NOT {OCCUPYING_SQL}")
    if request.args.get("status"):
        where.append("r.status = %(status)s")
        args["status"] = request.args["status"]
    cond = " AND ".join(where)
    total = fetch_value(f"SELECT COUNT(*) FROM Reservations r WHERE {cond}", args)
    order = "r.start_time ASC" if scope == "active" else "r.start_time DESC"
    rows = fetch_all(f"{RESERVATION_SELECT} WHERE {cond} ORDER BY {order} LIMIT %(lim)s OFFSET %(off)s",
                     {**args, "lim": per_page, "off": offset})
    now = now_utc()
    items = [{**serialize_reservation(r, now), **booking.customer_actions(r, now)} for r in rows]
    return paginated(items, page, per_page, total)


@bp.get("/reservations/<int:reservation_id>")
@customer
def my_reservation(reservation_id):
    row, data = booking.get_reservation(reservation_id)
    ensure_owner(row["user_id"])
    return ok({**data, **booking.customer_actions(row)})


@bp.get("/parking")
@customer
def my_parking():
    """Current parking and upcoming reservations."""
    expiry.run_all()
    uid = current_user()["user_id"]
    now = now_utc()
    parked = fetch_all(RESERVATION_SELECT + " WHERE r.user_id = %s AND r.status = 'In Use' ORDER BY r.checked_in_at",
                       (uid,))
    upcoming = fetch_all(f"{RESERVATION_SELECT} WHERE r.user_id = %(uid)s AND r.status IN ('Held','Confirmed') "
                         f"AND {OCCUPYING_SQL} ORDER BY r.start_time LIMIT 3", {**time_params(now), "uid": uid})
    return ok({"parked": [serialize_reservation(r, now) for r in parked],
               "upcoming": [{**serialize_reservation(r, now), **booking.customer_actions(r, now)} for r in upcoming]})


# dashboard

def _month_bounds_utc(now):
    """Current and previous month start times in Bangkok, as naive UTC."""
    local = now.replace(tzinfo=None) + timedelta(hours=7)
    this_start = datetime(local.year, local.month, 1, tzinfo=BKK)
    prev_local = this_start - timedelta(days=1)
    prev_start = datetime(prev_local.year, prev_local.month, 1, tzinfo=BKK)
    return to_naive_utc(prev_start), to_naive_utc(this_start)


@bp.get("/dashboard")
@customer
def dashboard():
    expiry.run_all()
    uid = current_user()["user_id"]
    now = now_utc()
    start, end = availability.resolve_window(None, 1, now)
    lots = availability.lots_summary(start, end, now)

    upcoming = fetch_value(f"SELECT COUNT(*) FROM Reservations r WHERE r.user_id = %(uid)s AND {OCCUPYING_SQL}",
                           {**time_params(now), "uid": uid})
    prev_start, this_start = _month_bounds_utc(now)
    spent = fetch_one(
        "SELECT COALESCE(SUM(CASE WHEN paid_at >= %(this)s THEN total_amount END), 0) AS this_month, "
        "       COALESCE(SUM(CASE WHEN paid_at >= %(prev)s AND paid_at < %(this)s THEN total_amount END), 0) AS last_month "
        "FROM Payments WHERE user_id = %(uid)s AND status = 'Paid' AND is_deleted = 0",
        {"this": this_start, "prev": prev_start, "uid": uid})
    this_month, last_month = float(spent["this_month"]), float(spent["last_month"])
    change = round((this_month - last_month) / last_month * 100) if last_month else None
    unpaid = fetch_value("SELECT COUNT(*) FROM Payments WHERE user_id = %s AND status = 'Pending' AND is_deleted = 0",
                         (uid,))
    default_vehicle = fetch_one("SELECT vehicle_id, plate_number, province FROM Customer_Vehicles "
                                "WHERE user_id = %s AND is_default = 1 AND is_deleted = 0", (uid,))
    return ok({
        "open_bays": sum(l["available_slots"] for l in lots),
        "total_bays": sum(l["total_slots"] for l in lots),
        "upcoming": upcoming,
        "spent_month": this_month,
        "spent_last_month": last_month,
        "spent_change_pct": change,
        "unpaid_payments": unpaid,
        "default_vehicle": default_vehicle,
        "lots": lots,
        "server_time": now,
    })