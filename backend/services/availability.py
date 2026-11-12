"""Bay colours based on reservations in a selected time window.

slot_status in MySQL only stores Active / Maintenance.
Display colours are determined by bookings in the selected window:

    Maintenance (grey)  slot_status = Maintenance
    Occupied (blue)     A car is parked during the window
    Reserved (red)      A Confirmed booking overlaps the window
    Held (yellow)       An unexpired Held booking overlaps the window
    Available (green)   No booking overlaps the window
"""
from datetime import timedelta

from core.errors import NotFound, ValidationError
from core.settings import cfg
from core.timeutil import now_utc
from db.mysql import fetch_all, fetch_one
from services.queries import OCCUPYING_SQL, OVERLAP_SQL, time_params

PRIORITY = ("Occupied", "Reserved", "Held")
STATUS_FOR = {"In Use": "Occupied", "Confirmed": "Reserved", "Held": "Held"}
COLOURS = ("Available", "Held", "Reserved", "Occupied", "Maintenance")


def resolve_window(start=None, duration_hours=None, now=None):
    """Return the lookup window, starting now if needed."""
    now = now or now_utc()
    hours = duration_hours or 1
    if hours < cfg("MIN_DURATION_HOURS") or hours > cfg("MAX_DURATION_HOURS"):
        raise ValidationError(fields={"duration_hours": f"Choose {cfg('MIN_DURATION_HOURS')}-{cfg('MAX_DURATION_HOURS')} hours."})
    if start is None or start < now:
        start = now
    return start, start + timedelta(hours=hours)


def slot_states(lot_id=None, start=None, end=None, now=None, with_details=False):
    """Get bay statuses for the window. Staff/admin can also see booking details."""
    now = now or now_utc()
    params = time_params(start, end, now)
    where, args = "s.is_deleted = 0", dict(params)
    if lot_id is not None:
        where += " AND s.lot_id = %(lot_id)s"
        args["lot_id"] = lot_id
    slots = fetch_all(
        f"SELECT s.slot_id, s.slot_code, s.slot_type, s.slot_status, s.lot_id, l.lot_name "
        f"FROM Parking_Slots s JOIN Parking_Lots l ON l.lot_id = s.lot_id "
        f"WHERE {where} ORDER BY s.lot_id, CHAR_LENGTH(s.slot_code), s.slot_code", args)
    busy = fetch_all(
        f"SELECT r.reservation_id, r.slot_id, r.status, r.start_time, r.end_time, r.user_id, "
        f"       v.plate_number, v.province "
        f"FROM Reservations r JOIN Customer_Vehicles v ON v.vehicle_id = r.vehicle_id "
        f"JOIN Parking_Slots s ON s.slot_id = r.slot_id "
        f"WHERE {where} AND {OCCUPYING_SQL} AND {OVERLAP_SQL} ORDER BY r.start_time", args)

    by_slot = {}
    for r in busy:
        by_slot.setdefault(r["slot_id"], []).append(r)

    out = []
    for s in slots:
        item = {"slot_id": s["slot_id"], "slot_code": s["slot_code"], "slot_type": s["slot_type"],
                "slot_status": s["slot_status"], "lot_id": s["lot_id"], "lot_name": s["lot_name"],
                "display_status": "Available", "busy_until": None}
        if s["slot_status"] == "Maintenance":
            item["display_status"] = "Maintenance"
        else:
            rows = by_slot.get(s["slot_id"], [])
            for colour in PRIORITY:
                hit = next((r for r in rows if STATUS_FOR[r["status"]] == colour), None)
                if hit:
                    item["display_status"] = colour
                    item["busy_until"] = max(hit["end_time"], now) if hit["status"] == "In Use" else hit["end_time"]
                    if with_details:
                        item["reservation_id"] = hit["reservation_id"]
                        item["plate_number"] = hit["plate_number"]
                        item["province"] = hit["province"]
                        item["overstay"] = hit["status"] == "In Use" and hit["end_time"] < now
                    break
        out.append(item)
    return out


def count(states):
    counts = {c: 0 for c in COLOURS}
    for s in states:
        counts[s["display_status"]] += 1
    return counts


def lots_summary(start=None, end=None, now=None, include_inactive=False):
    lots = fetch_all("SELECT lot_id, lot_name, location_desc, is_active FROM Parking_Lots "
                     + ("" if include_inactive else "WHERE is_active = 1 ") + "ORDER BY lot_id")
    states = slot_states(None, start, end, now)
    out = []
    for lot in lots:
        mine = [s for s in states if s["lot_id"] == lot["lot_id"]]
        c = count(mine)
        out.append({**lot, "is_active": bool(lot["is_active"]), "total_slots": len(mine),
                    "available_slots": c["Available"], "ev_available": sum(
                        1 for s in mine if s["display_status"] == "Available" and s["slot_type"] == "EV"),
                    "counts": c})
    return out


def lot_detail(lot_id, start=None, end=None, now=None, with_details=False):
    lot = fetch_one("SELECT lot_id, lot_name, location_desc, is_active FROM Parking_Lots WHERE lot_id = %s", (lot_id,))
    if not lot or not lot["is_active"]:
        raise NotFound("LOT_NOT_FOUND", "This parking lot does not exist or is closed.")
    states = slot_states(lot_id, start, end, now, with_details)
    return {"lot": {**lot, "is_active": bool(lot["is_active"])},
            "window": {"start": start, "end": end},
            "counts": count(states), "slots": states}


def available_slot_ids(cur, lot_id, start, end, now, exclude_slot_id=None, limit=5):
    """Find free bays in the same lot and time window."""
    args = {**time_params(start, end, now), "lot_id": lot_id, "ex": exclude_slot_id or 0, "lim": limit}
    cur.execute(
        f"SELECT s.slot_id, s.slot_code, s.slot_type FROM Parking_Slots s "
        f"WHERE s.lot_id = %(lot_id)s AND s.is_deleted = 0 AND s.slot_status = 'Active' AND s.slot_id <> %(ex)s "
        f"AND NOT EXISTS (SELECT 1 FROM Reservations r WHERE r.slot_id = s.slot_id AND {OCCUPYING_SQL} AND {OVERLAP_SQL}) "
        f"ORDER BY CHAR_LENGTH(s.slot_code), s.slot_code LIMIT %(lim)s", args)
    return cur.fetchall()