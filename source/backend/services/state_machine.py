"""Reservation status rules (spec 6.3).

    Held      -> Confirmed / Expired / Cancelled
    Confirmed -> In Use / Cancelled / No-show
    In Use    -> Completed
    Completed, Cancelled, Expired, No-show are final.

Anything else is refused, except an Admin edit (which is logged).
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

# Statuses that keep a bay busy (used for overlap checks)
OCCUPYING = ("Held", "Confirmed", "In Use")


def can_move(old, new):
    return new in ALLOWED.get(old, set())


def assert_transition(old, new, admin_override=False):
    if admin_override and new in ALLOWED:
        return
    if not can_move(old, new):
        raise Conflict("INVALID_STATE", f"A reservation that is {old} cannot become {new}.")
