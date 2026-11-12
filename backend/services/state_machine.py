"""Reservation status rules (spec 6.3).

    Held      -> Confirmed / Expired / Cancelled
    Confirmed -> In Use / Cancelled / No-show
    In Use    -> Completed
    Final: Completed, Cancelled, Expired, No-show.

Invalid changes are rejected unless overridden by an Admin.
"""
from core.errors import Conflict

ALLOWED = {
    "Held": {"Confirmed", "Expired", "Cancelled"},
    "Confirmed": {"In Use", "Cancelled", "No-show"},
    "In Use": {"Completed"},
    "Completed": set(),
    "Cancelled": set(),
    "Expired": set(),
    "No-show": set(),
}
STATUSES = tuple(ALLOWED)
FINAL = {s for s, nxt in ALLOWED.items() if not nxt}

# Statuses that occupy a bay.
OCCUPYING = ("Held", "Confirmed", "In Use")


def can_move(old, new):
    return new in ALLOWED.get(old, set())


def assert_transition(old, new, admin_override=False):
    if admin_override and new in ALLOWED:
        return
    if not can_move(old, new):
        raise Conflict("INVALID_STATE", f"A reservation that is {old} cannot become {new}.")