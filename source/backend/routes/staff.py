"""Staff endpoints: booking search, check-in, and check-out.

Staff cannot create bookings, handle walk-ins, edit logs, or manage users.
"""
import re
from datetime import datetime, timedelta

from flask import Blueprint, request

from core.auth import current_user, login_required
from core.errors import ValidationError
from core.responses import ok
from core.timeutil import BKK, MAX_YEAR, MIN_YEAR, now_utc, to_naive_utc
from core.validate import like_contains
from db.mysql import fetch_all
from services import availability, checkio, expiry, payment
from services.booking import get_reservation
from services.queries import RESERVATION_SELECT, effective_status, serialize_reservation

bp = Blueprint("staff", __name__, url_prefix="/api/staff")
staff_only = login_required("Staff", "Admin")


def staff_view(row, now):
    data = serialize_reservation(row, now, include_customer=True)
    opens, closes = checkio.checkin_window(row)
    status = data["status"]
    data["checkin_window"] = {"from": opens, "to": closes}
    data["can_checkin"] = status == "Confirmed" and opens <= now <= closes
    data["can_checkout"] = status == "In Use"
    if status == "Confirmed" and now < opens:
        data["checkin_note"] = "too_early"
    elif status == "Confirmed" and now > closes:
        data["checkin_note"] = "too_late"
    return data


def plate_key(text):
    """Normalize plate text and escape LIKE wildcards in the query."""
    return re.sub(r"[\s\-%_\\]", "", text or "").upper()


@bp.get("/search")
@staff_only
def search():
    q = plate_key(request.args.get("plate"))
    if len(q) < 2:
        raise ValidationError(fields={"plate": "Type at least 2 letters or digits of the plate."})
    expiry.run_lazy()
    now = now_utc()
    rows = fetch_all(RESERVATION_SELECT + " WHERE REPLACE(REPLACE(UPPER(v.plate_number), ' ', ''), '-', '') LIKE %s "
                     "ORDER BY (r.status = 'In Use') DESC, (r.status = 'Confirmed') DESC, "
                     "ABS(TIMESTAMPDIFF(MINUTE, r.start_time, %s)) LIMIT 30", (like_contains(q), now))
    active, other = [], []
    for r in rows:
        (active if effective_status(r, now) in ("Confirmed", "In Use") else other).append(staff_view(r, now))
    return ok({"query": q, "active": active, "recent": other[:5]})


@bp.get("/reservations/<int:reservation_id>")
@staff_only
def detail(reservation_id):
    row, _ = get_reservation(reservation_id)
    return ok(staff_view(row, now_utc()))


@bp.post("/reservations/<int:reservation_id>/checkin")
@staff_only
def checkin(reservation_id):
    return ok(checkio.checkin(current_user(), reservation_id))


@bp.post("/reservations/<int:reservation_id>/checkout")
@staff_only
def checkout(reservation_id):
    return ok(checkio.checkout(current_user(), reservation_id))


@bp.post("/payments/<int:payment_id>/confirm-cash")
@staff_only
def confirm_cash(payment_id):
    """Confirm cash paid at the counter."""
    return ok(payment.confirm_cash(current_user(), payment_id))


@bp.get("/dashboard")
@staff_only
def dashboard():
    expiry.run_lazy()
    now = now_utc()
    start, end = availability.resolve_window(None, 1, now)
    lots = availability.lots_summary(start, end, now)
    parked = fetch_all(RESERVATION_SELECT + " WHERE r.status = 'In Use' ORDER BY r.end_time")
    arriving = fetch_all(RESERVATION_SELECT + " WHERE r.status = 'Confirmed' AND r.start_time BETWEEN %s AND %s "
                         "ORDER BY r.start_time", (now - timedelta(minutes=30), now + timedelta(hours=2)))
    parked_v = [staff_view(r, now) for r in parked]
    arriving_v = [staff_view(r, now) for r in arriving if effective_status(r, now) == "Confirmed"]
    overstay = [r for r in parked_v if r["display_label"] == "overstay"]
    # Find arrivals blocked by overstaying cars in the same bay.
    blocked = []
    for o in overstay:
        for a in arriving_v:
            if a["slot"]["slot_id"] == o["slot"]["slot_id"]:
                blocked.append({"waiting": a["reservation_id"], "plate": a["vehicle"]["plate_number"],
                                "slot_code": a["slot"]["slot_code"], "blocked_by": o["vehicle"]["plate_number"],
                                "start_time": a["start_time"]})
    return ok({
        "open_bays": sum(l["available_slots"] for l in lots),
        "total_bays": sum(l["total_slots"] for l in lots),
        "parked_count": len(parked_v),
        "awaiting_checkin": sum(1 for a in arriving_v if a["start_time"] <= now + timedelta(minutes=30)),
        "overstay_count": len(overstay),
        "lots": lots,
        "arriving": arriving_v,
        "parked": parked_v,
        "overstay": overstay,
        "blocked": blocked,
        "server_time": now,
    })


@bp.get("/movements")
@staff_only
def movements():
    """List check-ins and check-outs for a Bangkok date (?date=YYYY-MM-DD)."""
    raw = request.args.get("date")
    try:
        day = datetime.strptime(raw, "%Y-%m-%d") if raw else (now_utc() + timedelta(hours=7)).replace(
            hour=0, minute=0, second=0)
    except ValueError:
        raise ValidationError(fields={"date": "Use YYYY-MM-DD."})
    if not MIN_YEAR <= day.year <= MAX_YEAR:
        raise ValidationError(fields={"date": f"Use a date between {MIN_YEAR} and {MAX_YEAR}."})
    start = to_naive_utc(day.replace(tzinfo=BKK))
    end = start + timedelta(days=1)
    rows = fetch_all(RESERVATION_SELECT + " WHERE (r.checked_in_at >= %(s)s AND r.checked_in_at < %(e)s) "
                     "OR (r.checked_out_at >= %(s)s AND r.checked_out_at < %(e)s)", {"s": start, "e": end})
    now = now_utc()
    events = []
    for r in rows:
        data = serialize_reservation(r, now, include_customer=True)
        if r["checked_in_at"] and start <= r["checked_in_at"] < end:
            events.append({"kind": "in", "at": r["checked_in_at"], "reservation": data})
        if r["checked_out_at"] and start <= r["checked_out_at"] < end:
            events.append({"kind": "out", "at": r["checked_out_at"], "reservation": data})
    events.sort(key=lambda e: e["at"], reverse=True)
    return ok({"date": day.strftime("%Y-%m-%d"), "items": events,
               "count_in": sum(e["kind"] == "in" for e in events),
               "count_out": sum(e["kind"] == "out" for e in events)})