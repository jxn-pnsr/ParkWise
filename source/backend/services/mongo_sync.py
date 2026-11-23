"""Outbox worker: Integration_Outbox (MySQL) -> MongoDB (spec 10.2).

    process_pending()   called by the scheduler every few seconds

For each Pending event, oldest first:
    write it to MongoDB (idempotent: upsert on source_event_id, so sending
    twice never makes duplicates) -> status Sent, sent_at = now.
On error: retry_count + 1 and last_error; after MAX_RETRIES -> Failed, and an
admin can press Retry (phase 3). MySQL stays the source of truth: if MongoDB
is down, booking and check-in keep working and events simply wait.

apply_event() follows database/seed_mongo.py (same payload format), plus:
  * user.registered  -> default User_Preferences document
  * a notification is skipped if the user switched that kind off in Preferences
"""
import json
import logging
from datetime import datetime

from pymongo.errors import ConnectionFailure, PyMongoError, ServerSelectionTimeoutError

from core.timeutil import now_utc
from db.mongo import MongoUnavailable, get_mongo
from db.mysql import fetch_all, transaction
from services.mongo_read import DEFAULT_PREFERENCES

log = logging.getLogger(__name__)

MAX_RETRIES = 5
BATCH = 100

# "MongoDB is not reachable" (do not count against the event)
UNREACHABLE = (MongoUnavailable, ConnectionFailure, ServerSelectionTimeoutError)

def _is_unreachable(err):
    if isinstance(err, UNREACHABLE):
        return True
    # NotPrimaryError / network errors carry this label in PyMongo 4
    has_label = getattr(err, "has_error_label", None)
    return bool(has_label and (has_label("RetryableWriteError") or has_label("NetworkError")))


# notification type -> switch in User_Preferences.notifications
PREF_FOR_TYPE = {"hold_expired": "holdExpired", "no_show": "noShow", "checkin_done": "checkIn",
                 "checkout_done": "checkOut", "overstay": "overstay", "overstay_warning": "overstay"}


def _parse_iso(value):
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ") if value else None


def _wants(db, user_id, kind):
    key = PREF_FOR_TYPE.get(kind)
    if not key:
        return True
    prefs = db.User_Preferences.find_one({"userId": user_id}, {"notifications": 1}) or {}
    return (prefs.get("notifications") or {}).get(key, True)


def apply_event(db, event_id, event_type, payload, occurred_at):
    if isinstance(payload, (str, bytes)):
        payload = json.loads(payload)

    # 1) every event -> one activity log
    db.Activity_Logs.update_one({"source_event_id": event_id}, {"$setOnInsert": {
        "userId": payload.get("actor_user_id"), "role": payload.get("actor_role") or "System",
        "action_type": event_type, "entity_type": payload.get("entity_type"),
        "entity_id": payload.get("entity_id"), "ip_address": payload.get("ip_address"),
        "metadata": payload.get("metadata") or {}, "timestamp": occurred_at,
        "admin_note": None, "is_archived": False, "source_event_id": event_id}}, upsert=True)

    # 2) notification for the customer (unless they turned that kind off)
    notify, target = payload.get("notify"), payload.get("target_user_id")
    if notify and target is not None and _wants(db, target, notify["type"]):
        rid = payload.get("entity_id") if payload.get("entity_type") == "reservation" \
            else (payload.get("metadata") or {}).get("reservation_id")
        db.Notifications.update_one({"source_event_id": event_id}, {"$setOnInsert": {
            "userId": target, "type": notify["type"], "title": notify["title"], "message": notify["message"],
            "isRead": False, "reservationId": rid, "sentAt": occurred_at, "readAt": None,
            "source_event_id": event_id}}, upsert=True)

    # 3) parking session (check-in / check-out). Order-safe: never overwrites with missing values.
    session = payload.get("session")
    if session and session.get("reservationId") is not None:
        fields = {k: session[k] for k in ("userId", "vehicleId", "plateNumber", "lotName", "slotCode")
                  if session.get(k) is not None}
        if session.get("isOverstay"):
            fields["isOverstay"] = True
        for k in ("checkInAt", "checkOutAt"):
            if session.get(k):
                fields[k] = _parse_iso(session[k])
        if session.get("durationMinutes") is not None:
            fields["durationMinutes"] = session["durationMinutes"]
        if session.get("fees"):
            fields["fees"] = session["fees"]
        fields["updatedAt"] = occurred_at
        on_insert = {"reservationId": session["reservationId"], "createdAt": occurred_at}
        if "isOverstay" not in fields:
            on_insert["isOverstay"] = False
        db.Parking_Sessions.update_one({"reservationId": session["reservationId"]},
                                       {"$set": fields, "$setOnInsert": on_insert,
                                        "$addToSet": {"source_event_ids": event_id}}, upsert=True)

    # 4) new account -> default preferences
    if event_type == "user.registered" and payload.get("entity_id") is not None:
        db.User_Preferences.update_one({"userId": payload["entity_id"]}, {"$setOnInsert": {
            "userId": payload["entity_id"], "notifications": dict(DEFAULT_PREFERENCES["notifications"]),
            "defaultVehicleId": None, "updatedAt": occurred_at}}, upsert=True)


def process_pending(limit=BATCH):
    """Send Pending events to MongoDB. Returns {'sent': n, 'failed': n}."""
    result = {"sent": 0, "failed": 0}
    ids = [r["event_id"] for r in fetch_all(
        "SELECT event_id FROM Integration_Outbox WHERE status = 'Pending' ORDER BY seq LIMIT %s", (limit,))]
    for event_id in ids:
        with transaction() as cur:
            cur.execute("SELECT event_id, event_type, payload, created_at, retry_count FROM Integration_Outbox "
                        "WHERE event_id = %s AND status = 'Pending' FOR UPDATE SKIP LOCKED", (event_id,))
            e = cur.fetchone()
            if not e:
                continue  # another worker took it
            try:
                apply_event(get_mongo(), e["event_id"], e["event_type"], e["payload"], e["created_at"])
            except Exception as err:
                if _is_unreachable(err):
                    cur.execute("UPDATE Integration_Outbox SET last_error = %s WHERE event_id = %s",
                                (("MongoDB unreachable: " + str(err))[:500], event_id))
                    result["waiting"] = result.get("waiting", 0) + 1
                    log.info("outbox paused: %s", err)
                    break
                retries = e["retry_count"] + 1
                cur.execute("UPDATE Integration_Outbox SET retry_count = %s, last_error = %s, status = %s "
                            "WHERE event_id = %s",
                            (retries, f"{type(err).__name__}: {err}"[:500],
                             "Failed" if retries >= MAX_RETRIES else "Pending", event_id))
                result["failed"] += 1
                log.warning("outbox event %s failed (%s/%s): %s", event_id, retries, MAX_RETRIES, err)
                continue
            cur.execute("UPDATE Integration_Outbox SET status = 'Sent', sent_at = %s, last_error = NULL "
                        "WHERE event_id = %s", (now_utc(), event_id))
            result["sent"] += 1
    return result


def retry(event_ids=None):
    """Put Failed events back in the queue (admin Retry button). None = all Failed."""
    with transaction() as cur:
        if event_ids:
            marks = ", ".join(["%s"] * len(event_ids))
            cur.execute(f"UPDATE Integration_Outbox SET status = 'Pending', retry_count = 0 "
                        f"WHERE status = 'Failed' AND event_id IN ({marks})", tuple(event_ids))
        else:
            cur.execute("UPDATE Integration_Outbox SET status = 'Pending', retry_count = 0 WHERE status = 'Failed'")
        return cur.rowcount
