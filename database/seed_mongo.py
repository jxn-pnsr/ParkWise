"""
seed_mongo.py - fills MongoDB with sample data that matches seed.sql

Order to run:
    1. mysql ... < database/schema.sql
    2. mysql ... parkwise < database/seed.sql
    3. mongosh ... database/mongo_init.js
    4. python database/seed_mongo.py

It reads the rows in Integration_Outbox with status 'Sent' and writes them to
MongoDB the same way the real outbox worker will. apply_event() can be reused
in the backend. Writes are idempotent (upsert on source_event_id), so running
an event twice does not create duplicates.

The Pending and Failed rows are skipped on purpose, they are used to demo the
Retry button on the admin outbox page.

Environment variables (defaults are for a local dev machine):
    MYSQL_HOST=127.0.0.1  MYSQL_PORT=3306  MYSQL_USER=root  MYSQL_PASSWORD=  MYSQL_DB=parkwise
    MONGO_URI=mongodb://localhost:27017  MONGO_DB=parkwise
"""
import json
import os
from datetime import datetime, timedelta

import pymysql
from pymongo import MongoClient

COLLECTIONS = ["Parking_Sessions", "Activity_Logs", "Notifications", "User_Preferences"]


def parse_iso(value):
    # '2026-11-06T09:00:00Z' -> naive datetime (pymongo stores it as UTC)
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")


def apply_event(db, event_id, event_type, payload, occurred_at):
    """Write one outbox event to MongoDB. Raises if MongoDB fails, so the
    worker can catch it and increase retry_count."""
    if isinstance(payload, (str, bytes)):
        payload = json.loads(payload)

    # 1) every event creates one activity log
    log_doc = {
        "userId": payload.get("actor_user_id"),
        "role": payload.get("actor_role") or "System",
        "action_type": event_type,
        "entity_type": payload.get("entity_type"),
        "entity_id": payload.get("entity_id"),
        "ip_address": payload.get("ip_address"),
        "metadata": payload.get("metadata") or {},
        "timestamp": occurred_at,
        "admin_note": None,
        "is_archived": False,
        "source_event_id": event_id,
    }
    db.Activity_Logs.update_one({"source_event_id": event_id},
                                {"$setOnInsert": log_doc}, upsert=True)

    # 2) create a notification if the event has one
    notify = payload.get("notify")
    target = payload.get("target_user_id")
    if notify and target is not None:
        if payload.get("entity_type") == "reservation":
            reservation_id = payload.get("entity_id")
        else:
            reservation_id = (payload.get("metadata") or {}).get("reservation_id")
        notif_doc = {
            "userId": target,
            "type": notify["type"],
            "title": notify["title"],
            "message": notify["message"],
            "isRead": False,
            "reservationId": reservation_id,
            "sentAt": occurred_at,
            "readAt": None,
            "source_event_id": event_id,
        }
        db.Notifications.update_one({"source_event_id": event_id},
                                    {"$setOnInsert": notif_doc}, upsert=True)

    # 3) check-in / check-out events create or update the parking session
    session = payload.get("session")
    if session and session.get("reservationId") is not None:
        fields = {}
        for key in ("userId", "vehicleId", "plateNumber", "lotName", "slotCode", "isOverstay"):
            if key in session:
                fields[key] = session[key]
        fields["checkInAt"] = parse_iso(session.get("checkInAt"))
        fields["checkOutAt"] = parse_iso(session.get("checkOutAt"))
        fields["durationMinutes"] = session.get("durationMinutes")
        if session.get("fees"):
            fields["fees"] = session["fees"]
        fields["updatedAt"] = occurred_at
        db.Parking_Sessions.update_one(
            {"reservationId": session["reservationId"]},
            {"$set": fields,
             "$setOnInsert": {"reservationId": session["reservationId"], "createdAt": occurred_at},
             "$addToSet": {"source_event_ids": event_id}},
            upsert=True)


def mysql_conn():
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=os.getenv("MYSQL_DB", "parkwise"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor)


def seed(db, conn):
    # start from empty collections
    for name in COLLECTIONS:
        db[name].delete_many({})

    cur = conn.cursor()
    cur.execute("SET time_zone = '+00:00'")

    # replay the Sent events in order
    cur.execute("SELECT event_id, event_type, payload, created_at FROM Integration_Outbox "
                "WHERE status = 'Sent' ORDER BY seq")
    events = cur.fetchall()
    for e in events:
        apply_event(db, e["event_id"], e["event_type"], e["payload"], e["created_at"])

    now = datetime.utcnow().replace(second=0, microsecond=0)

    # mark a few notifications as read so we have both read and unread ones
    read_ids = ["00000000-0000-4000-8000-000000000003",
                "00000000-0000-4000-8000-000000000010",
                "00000000-0000-4000-8000-000000000011",
                "00000000-0000-4000-8000-000000000014"]
    db.Notifications.update_many({"source_event_id": {"$in": read_ids}},
                                 {"$set": {"isRead": True, "readAt": now - timedelta(minutes=30)}})

    # data that does not come from the outbox (written directly by Flask in the real app)
    # admin announcement for every customer
    cur.execute("SELECT user_id FROM Users WHERE role = 'Customer' AND is_active = 1")
    for u in cur.fetchall():
        db.Notifications.insert_one({
            "userId": u["user_id"],
            "type": "announcement",
            "title": "Notice: Lot B maintenance",
            "message": "Slot B10 in Lot B is closed for maintenance. Sorry for the inconvenience.",
            "isRead": False,
            "reservationId": None,
            "sentAt": now - timedelta(hours=6),
            "readAt": None})

    # a few extra logs: staff/admin logins, a failed login, admin changes (with admin_note)
    # (user_id, role, action, entity_type, entity_id, ip, metadata, minutes_ago, admin_note)
    extra_logs = [
        (1, "Admin", "user.login", "user", 1, "192.168.1.2", {"user_agent": "Firefox/Linux"}, 200, None),
        (2, "Staff", "user.login", "user", 2, "192.168.1.5", {"user_agent": "Chrome/Windows"}, 130, None),
        (3, "Staff", "user.login", "user", 3, "192.168.1.6", {"user_agent": "Chrome/Windows"}, 250, None),
        (None, "Anonymous", "user.login_failed", "user", None, "203.0.113.50",
         {"email": "unknown@example.com", "reason": "invalid_credentials", "attempt": 3}, 95,
         "Checked: user typed the wrong password"),
        (1, "Admin", "admin.config_updated", "system_config", "RATE_PER_HOUR", "192.168.1.2",
         {"old": "15", "new": "20"}, 1000, "Rate changed after team decision"),
        (1, "Admin", "admin.slot_status_changed", "slot", 20, "192.168.1.2",
         {"slot": "B10", "from": "Active", "to": "Maintenance"}, 600, None),
    ]
    for uid, role, action, etype, eid, ip, meta, mins_ago, note in extra_logs:
        db.Activity_Logs.insert_one({
            "userId": uid, "role": role, "action_type": action,
            "entity_type": etype, "entity_id": eid, "ip_address": ip,
            "metadata": meta, "timestamp": now - timedelta(minutes=mins_ago),
            "admin_note": note, "is_archived": False})

    # one preferences document per user, default vehicle taken from MySQL
    cur.execute("SELECT u.user_id, v.vehicle_id AS default_vehicle "
                "FROM Users u LEFT JOIN Customer_Vehicles v "
                "  ON v.user_id = u.user_id AND v.is_default = 1 AND v.is_deleted = 0 "
                "ORDER BY u.user_id")
    dark_users = {1, 4, 6}
    for u in cur.fetchall():
        db.User_Preferences.insert_one({
            "userId": u["user_id"],
            "theme": "dark" if u["user_id"] in dark_users else "light",
            "language": "en",
            "notifications": {"holdExpired": True, "noShow": True, "checkIn": True,
                              "checkOut": True, "overstay": u["user_id"] != 7},
            "defaultVehicleId": u["default_vehicle"],
            "updatedAt": now})

    counts = {name: db[name].count_documents({}) for name in COLLECTIONS}
    return counts, len(events)


if __name__ == "__main__":
    client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"),
                         serverSelectionTimeoutMS=5000)
    mongo_db = client[os.getenv("MONGO_DB", "parkwise")]
    counts, n_events = seed(mongo_db, mysql_conn())
    print("Replayed Sent outbox events:", n_events)
    for name, n in counts.items():
        print("  %-18s %d documents" % (name, n))
