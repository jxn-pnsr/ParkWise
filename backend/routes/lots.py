"""Lots and bay colours. Staff and admins can also see plates."""
from flask import Blueprint, request

from core.auth import current_user, is_staff_or_admin, login_required
from core.errors import ValidationError
from core.responses import ok
from core.settings import public_config
from core.timeutil import parse_client_datetime
from core.validate import query_int
from services import availability, expiry

bp = Blueprint("lots", __name__, url_prefix="/api")


def _window():
    raw = request.args.get("start")
    start = None
    if raw:
        try:
            start = parse_client_datetime(raw)
        except ValueError:
            raise ValidationError(fields={"start": "Use a date and time like 2026-11-20T10:00:00+07:00."})
    hours = query_int("duration_hours", default=1)
    return availability.resolve_window(start, hours)


@bp.get("/lots")
@login_required()
def lots():
    expiry.run_all()  # lazy check before showing free bays
    start, end = _window()
    return ok({"window": {"start": start, "end": end},
               "items": availability.lots_summary(start, end)})


@bp.get("/lots/<int:lot_id>/slots")
@login_required()
def lot_slots(lot_id):
    expiry.run_all()
    start, end = _window()
    return ok(availability.lot_detail(lot_id, start, end, with_details=is_staff_or_admin(current_user())))


@bp.get("/config/public")
def config_public():
    """Booking rules for the pages (hold minutes, rates, limits). No login needed."""
    return ok(public_config())
