"""Customer vehicles: add, update, set default, and delete.

* Active plate and province combinations must be unique.
* Each customer can have one default vehicle.
* The first vehicle becomes the default automatically.
* Vehicles with active or upcoming reservations cannot be deleted.
"""
import re

import pymysql

from core.errors import Conflict, Forbidden, NotFound, ValidationError
from core.timeutil import now_utc
from db.mysql import fetch_all, fetch_one, transaction
from services.outbox import write_event
from services.queries import OCCUPYING_SQL, time_params

COLUMNS = "vehicle_id, user_id, plate_number, province, is_default, is_deleted, created_at, updated_at"


def normalise_plate(text):
    """Clean the plate and keep Thai letters unchanged."""
    return re.sub(r"\s+", " ", (text or "").strip()).upper()


def serialize(v):
    return {"vehicle_id": v["vehicle_id"], "user_id": v["user_id"], "plate_number": v["plate_number"],
            "province": v["province"], "is_default": bool(v["is_default"]),
            "is_deleted": bool(v["is_deleted"]), "created_at": v["created_at"]}


def list_for_user(user_id):
    rows = fetch_all(f"SELECT {COLUMNS} FROM Customer_Vehicles WHERE user_id = %s AND is_deleted = 0 "
                     "ORDER BY is_default DESC, vehicle_id", (user_id,))
    return [serialize(r) for r in rows]


def get_owned(cur, user_id, vehicle_id, for_update=True):
    cur.execute(f"SELECT {COLUMNS} FROM Customer_Vehicles WHERE vehicle_id = %s" + (" FOR UPDATE" if for_update else ""),
                (vehicle_id,))
    v = cur.fetchone()
    if not v or v["is_deleted"]:
        raise NotFound("VEHICLE_NOT_FOUND", "Vehicle not found.")
    if v["user_id"] != user_id:
        raise Forbidden()
    return v


def _plate_taken(e):
    if isinstance(e, pymysql.err.IntegrityError) and e.args and e.args[0] == 1062:
        raise Conflict("PLATE_TAKEN", "This plate is already registered.",
                       fields={"plate_number": "This plate and province are already registered."})
    raise e


def add(user_id, plate_number, province, make_default=False):
    plate = normalise_plate(plate_number)
    if len(plate.replace(" ", "")) < 2:
        raise ValidationError(fields={"plate_number": "Enter the licence plate."})
    now = now_utc()
    try:
        with transaction() as cur:
            cur.execute("SELECT user_id FROM Users WHERE user_id = %s FOR UPDATE", (user_id,))
            cur.execute("SELECT COUNT(*) AS n FROM Customer_Vehicles WHERE user_id = %s AND is_deleted = 0", (user_id,))
            first = cur.fetchone()["n"] == 0
            default = first or make_default
            if default:
                cur.execute("UPDATE Customer_Vehicles SET is_default = 0, updated_at = %s "
                            "WHERE user_id = %s AND is_default = 1", (now, user_id))
            cur.execute("INSERT INTO Customer_Vehicles (user_id, plate_number, province, is_default, created_at) "
                        "VALUES (%s, %s, %s, %s, %s)", (user_id, plate, province, 1 if default else 0, now))
            vid = cur.lastrowid
            write_event(cur, "vehicle.added", "vehicle", vid, metadata={"plate": plate, "province": province})
    except pymysql.err.IntegrityError as e:
        _plate_taken(e)
    return serialize(fetch_one(f"SELECT {COLUMNS} FROM Customer_Vehicles WHERE vehicle_id = %s", (vid,)))


def update(user_id, vehicle_id, plate_number=None, province=None):
    now = now_utc()
    try:
        with transaction() as cur:
            v = get_owned(cur, user_id, vehicle_id)
            plate = normalise_plate(plate_number) if plate_number is not None else v["plate_number"]
            prov = province if province is not None else v["province"]
            if len(plate.replace(" ", "")) < 2:
                raise ValidationError(fields={"plate_number": "Enter the licence plate."})
            cur.execute("UPDATE Customer_Vehicles SET plate_number = %s, province = %s, updated_at = %s "
                        "WHERE vehicle_id = %s", (plate, prov, now, vehicle_id))
            write_event(cur, "vehicle.updated", "vehicle", vehicle_id,
                        metadata={"from": f"{v['plate_number']} {v['province']}", "to": f"{plate} {prov}"})
    except pymysql.err.IntegrityError as e:
        _plate_taken(e)
    return serialize(fetch_one(f"SELECT {COLUMNS} FROM Customer_Vehicles WHERE vehicle_id = %s", (vehicle_id,)))


def set_default(user_id, vehicle_id):
    now = now_utc()
    with transaction() as cur:
        cur.execute("SELECT user_id FROM Users WHERE user_id = %s FOR UPDATE", (user_id,))
        get_owned(cur, user_id, vehicle_id)
        cur.execute("UPDATE Customer_Vehicles SET is_default = 0, updated_at = %s "
                    "WHERE user_id = %s AND is_default = 1 AND vehicle_id <> %s", (now, user_id, vehicle_id))
        cur.execute("UPDATE Customer_Vehicles SET is_default = 1, updated_at = %s WHERE vehicle_id = %s",
                    (now, vehicle_id))
        write_event(cur, "vehicle.default_changed", "vehicle", vehicle_id, metadata={})
    return list_for_user(user_id)


def delete(user_id, vehicle_id, admin=False):
    """Soft delete the vehicle and keep its reservation history."""
    now = now_utc()
    with transaction() as cur:
        if admin:
            cur.execute(f"SELECT {COLUMNS} FROM Customer_Vehicles WHERE vehicle_id = %s FOR UPDATE", (vehicle_id,))
            v = cur.fetchone()
            if not v or v["is_deleted"]:
                raise NotFound("VEHICLE_NOT_FOUND", "Vehicle not found.")
        else:
            v = get_owned(cur, user_id, vehicle_id)
        params = time_params(now=now)
        cur.execute(f"SELECT COUNT(*) AS n FROM Reservations r WHERE r.vehicle_id = %(vid)s AND {OCCUPYING_SQL}",
                    {**params, "vid": vehicle_id})
        if cur.fetchone()["n"]:
            raise Conflict("HAS_ACTIVE_RESERVATIONS",
                           "This vehicle has an active or upcoming reservation. Cancel or finish it first.")
        cur.execute("UPDATE Customer_Vehicles SET is_deleted = 1, is_default = 0, updated_at = %s "
                    "WHERE vehicle_id = %s", (now, vehicle_id))
        if v["is_default"]:
            # Make the oldest remaining vehicle the default.
            cur.execute("SELECT vehicle_id FROM Customer_Vehicles WHERE user_id = %s AND is_deleted = 0 "
                        "ORDER BY vehicle_id LIMIT 1", (v["user_id"],))
            nxt = cur.fetchone()
            if nxt:
                cur.execute("UPDATE Customer_Vehicles SET is_default = 1, updated_at = %s WHERE vehicle_id = %s",
                            (now, nxt["vehicle_id"]))
        write_event(cur, "vehicle.deleted", "vehicle", vehicle_id,
                    metadata={"plate": v["plate_number"], "soft_delete": True})