"""Create, confirm, and cancel customer reservations."""
from flask import Blueprint

from core.auth import current_user, login_required
from core.responses import created, ok
from core.validate import Input, request_json
from services import booking

bp = Blueprint("reservations", __name__, url_prefix="/api/reservations")


@bp.post("")
@login_required("Customer")
def create():
    v = Input(request_json())
    booking_type = v.choice("booking_type", ("Advance", "Instant"), required=True)
    slot_id = v.integer("slot_id", required=True, min_value=1, label="Bay")
    vehicle_id = v.integer("vehicle_id", required=True, min_value=1, label="Vehicle")
    hours = v.integer("duration_hours", required=True, min_value=1, max_value=24, label="Length of stay")
    start = v.datetime("start_time", required=booking_type == "Advance", label="Start time") \
        if booking_type == "Advance" else None
    if v.has("end_time"):
        v.error("end_time", "Do not send end_time; it is worked out from start_time and duration_hours.")
    v.done()
    data = booking.create_hold(current_user(), booking_type, slot_id, vehicle_id, hours, start)
    return created(data)


@bp.post("/<int:reservation_id>/confirm")
@login_required("Customer")
def confirm(reservation_id):
    return ok(booking.confirm(current_user(), reservation_id))


@bp.post("/<int:reservation_id>/cancel")
@login_required("Customer")
def cancel(reservation_id):
    v = Input(request_json())
    reason = v.text("reason", max_len=255)
    v.done()
    return ok(booking.cancel(current_user(), reservation_id, reason))
