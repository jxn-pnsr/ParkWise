"""Admin endpoints for MongoDB collections, updated directly by Flask."""
import math
import re

from bson import ObjectId
from bson.errors import InvalidId
from flask import Blueprint, request

from core.auth import client_ip, current_user, login_required
from core.errors import NotFound, ValidationError
from core.responses import created, get_pagination, ok, paginated
from core.timeutil import now_utc, parse_client_datetime
from core.validate import Input, query_int, request_json
from db.mysql import fetch_all, fetch_one, transaction
from services import vehicles
from services.mongo_read import DEFAULT_PREFERENCES, require_mongo
from services.outbox import write_event

bp = Blueprint("admin_mongo", __name__, url_prefix="/api/admin")
admin_only = login_required("Admin")


def _oid(value, what="Document"):
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise NotFound("NOT_FOUND", f"{what} not found.")


def _out(d):
    d = dict(d)
    d["id"] = str(d.pop("_id"))
    return d


def _page(collection, query, sort):
    page, per_page, offset = get_pagination()
    total = collection.count_documents(query)
    docs = collection.find(query).sort(sort).skip(offset).limit(per_page)
    return paginated([_out(d) for d in docs], page, per_page, total)


def _log_admin(action, entity, entity_id, metadata):
    """Record admin MongoDB changes through the MySQL outbox."""
    with transaction() as cur:
        write_event(cur, action, entity, entity_id, metadata=metadata)


def _copy(doc):
    """Convert ObjectIds for audit logging."""
    if doc is None:
        return None
    return {k: (str(v) if isinstance(v, ObjectId) else v) for k, v in doc.items()}


def _time(v, name, required=False):
    if not v.has(name):
        if required:
            v.error(name, "Required.")
        return None
    return v.datetime(name)


@bp.get("/sessions")
@admin_only
def sessions():
    db = require_mongo()
    q = {}
    if request.args.get("user_id"):
        q["userId"] = query_int("user_id")
    if request.args.get("q"):
        q["plateNumber"] = {"$regex": re.escape(request.args["q"]), "$options": "i"}
    if request.args.get("open") == "1":
        q["checkOutAt"] = None
    return _page(db.Parking_Sessions, q, [("checkInAt", -1)])


def _session_fields(v, creating):
    f = {}
    if creating or v.has("userId"):
        f["userId"] = v.integer("userId", required=True, min_value=1, label="Customer")
    if creating or v.has("plateNumber"):
        f["plateNumber"] = v.text("plateNumber", required=True, min_len=2, max_len=20, label="Plate")
    for k in ("lotName", "slotCode"):
        if v.has(k):
            f[k] = v.text(k, max_len=60)
    if creating or v.has("checkInAt"):
        f["checkInAt"] = _time(v, "checkInAt", required=True)
    if v.has("checkOutAt"):
        f["checkOutAt"] = _time(v, "checkOutAt")
    if v.has("reservationId"):
        f["reservationId"] = v.integer("reservationId", min_value=1)
    if v.has("isOverstay"):
        f["isOverstay"] = v.boolean("isOverstay")
    fees = v.data.get("fees")
    if fees is not None:
        def bad(x):
            return (isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
                    or x < 0 or x > 1_000_000)
        if not isinstance(fees, dict) or any(bad(fees.get(k, 0)) for k in ("base", "overstay")):
            v.error("fees", "Use numbers for base and overstay.")
        else:
            f["fees"] = {"base": fees.get("base", 0), "overstay": fees.get("overstay", 0),
                         "total": fees.get("base", 0) + fees.get("overstay", 0)}
    if f.get("checkInAt") and f.get("checkOutAt"):
        if f["checkOutAt"] < f["checkInAt"]:
            v.error("checkOutAt", "Check-out must be after check-in.")
        else:
            f["durationMinutes"] = int((f["checkOutAt"] - f["checkInAt"]).total_seconds() // 60)
    return f


@bp.post("/sessions")
@admin_only
def create_session():
    db = require_mongo()
    v = Input(request_json())
    f = _session_fields(v, True)
    v.done()
    now = now_utc()
    doc = {"isOverstay": False, "checkOutAt": None, "durationMinutes": None, **f,
           "createdAt": now, "updatedAt": now, "source": "admin"}
    res = db.Parking_Sessions.insert_one(doc)
    _log_admin("admin.session_created", "parking_session", str(res.inserted_id), {"plate": f["plateNumber"]})
    return created(_out(db.Parking_Sessions.find_one({"_id": res.inserted_id})))


@bp.patch("/sessions/<sid>")
@admin_only
def update_session(sid):
    db = require_mongo()
    v = Input(request_json())
    f = _session_fields(v, False)
    v.done()
    f["updatedAt"] = now_utc()
    before = db.Parking_Sessions.find_one({"_id": _oid(sid, "Session")})
    if not before:
        raise NotFound("NOT_FOUND", "Session not found.")
    db.Parking_Sessions.update_one({"_id": before["_id"]}, {"$set": f})
    _log_admin("admin.session_updated", "parking_session", sid,
               {"fields": [k for k in f if k != "updatedAt"],
                "before": _copy({k: before.get(k) for k in f if k != "updatedAt"}),
                "note": "MongoDB copy only. MySQL (Reservations / Payments) is the source of truth and is not changed."})
    return ok(_out(db.Parking_Sessions.find_one({"_id": _oid(sid)})))


@bp.delete("/sessions/<sid>")
@admin_only
def delete_session(sid):
    db = require_mongo()
    doc = db.Parking_Sessions.find_one({"_id": _oid(sid, "Session")})
    if not doc or not db.Parking_Sessions.delete_one({"_id": doc["_id"]}).deleted_count:
        raise NotFound("NOT_FOUND", "Session not found.")
    _log_admin("admin.session_deleted", "parking_session", sid, {"deleted_document": _copy(doc)})
    return ok(None)


@bp.get("/notifications")
@admin_only
def notifications():
    db = require_mongo()
    q = {}
    if request.args.get("user_id"):
        q["userId"] = query_int("user_id")
    if request.args.get("type"):
        q["type"] = request.args["type"]
    if request.args.get("q"):
        q["$or"] = [{f: {"$regex": re.escape(request.args["q"]), "$options": "i"}} for f in ("title", "message")]
    return _page(db.Notifications, q, [("sentAt", -1), ("_id", -1)])


@bp.post("/notifications")
@admin_only
def send_announcement():
    """Send to one active customer or all active customers."""
    db = require_mongo()
    v = Input(request_json())
    title = v.text("title", required=True, max_len=150, label="Title")
    message = v.text("message", required=True, max_len=1000, label="Message")
    uid = v.integer("user_id", min_value=1) if v.has("user_id") else None
    v.done()
    if uid:
        targets = fetch_all("SELECT user_id FROM Users WHERE user_id = %s AND is_active = 1", (uid,))
        if not targets:
            raise ValidationError(fields={"user_id": "No active user with this id."})
    else:
        targets = fetch_all("SELECT user_id FROM Users WHERE role = 'Customer' AND is_active = 1")
    now = now_utc()
    docs = [{"userId": t["user_id"], "type": "announcement", "title": title, "message": message, "isRead": False,
             "reservationId": None, "sentAt": now, "readAt": None} for t in targets]
    if docs:
        db.Notifications.insert_many(docs)
    _log_admin("admin.announcement_sent", "notification", None,
               {"title": title, "recipients": len(docs), "user_id": uid})
    return created({"sent": len(docs)})


@bp.patch("/notifications/<nid>")
@admin_only
def update_notification(nid):
    """Edit notification content or read status; log the previous values."""
    db = require_mongo()
    v = Input(request_json())
    changes = {}
    if "title" in v.data:
        changes["title"] = v.text("title", required=True, max_len=150, label="Title")
    if "message" in v.data:
        changes["message"] = v.text("message", required=True, max_len=1000, label="Message")
    if "isRead" in v.data:
        changes["isRead"] = v.boolean("isRead", required=True)
    extra = set(v.data) - {"title", "message", "isRead"}
    if extra:
        v.error(sorted(extra)[0], "Only title, message and isRead can be changed.")
    if not v.errors and not changes:
        v.error("title", "Send at least one field to change.")
    v.done()
    before = db.Notifications.find_one({"_id": _oid(nid, "Notification")})
    if not before:
        raise NotFound("NOT_FOUND", "Notification not found.")
    if "isRead" in changes:
        changes["readAt"] = now_utc() if changes["isRead"] else None
    db.Notifications.update_one({"_id": before["_id"]}, {"$set": changes})
    _log_admin("admin.notification_updated", "notification", nid,
               {"fields": [k for k in changes if k != "readAt"],
                "before": _copy({k: before.get(k) for k in changes}), "user_id": before.get("userId")})
    return ok(_out(db.Notifications.find_one({"_id": before["_id"]})))


@bp.delete("/notifications/<nid>")
@admin_only
def delete_notification(nid):
    db = require_mongo()
    if not db.Notifications.delete_one({"_id": _oid(nid, "Notification")}).deleted_count:
        raise NotFound("NOT_FOUND", "Notification not found.")
    return ok(None)


@bp.get("/preferences")
@admin_only
def all_preferences():
    db = require_mongo()
    users = {u["user_id"]: u for u in fetch_all("SELECT user_id, full_name, email, role FROM Users")}
    docs = {d["userId"]: d for d in db.User_Preferences.find({})}
    out = []
    for uid, u in users.items():
        d = docs.get(uid)
        out.append({"user_id": uid, "full_name": u["full_name"], "email": u["email"], "role": u["role"],
                    "has_document": bool(d),
                    "notifications": {**DEFAULT_PREFERENCES["notifications"], **((d or {}).get("notifications") or {})},
                    "defaultVehicleId": (d or {}).get("defaultVehicleId"),
                    "updatedAt": (d or {}).get("updatedAt")})
    return ok(out)


@bp.put("/preferences/<int:user_id>")
@admin_only
def set_preferences(user_id):
    db = require_mongo()
    if not fetch_one("SELECT user_id FROM Users WHERE user_id = %s", (user_id,)):
        raise NotFound("USER_NOT_FOUND", "User not found.")
    v = Input(request_json())
    switches = v.data.get("notifications")
    if switches is not None and (not isinstance(switches, dict) or any(
            k not in DEFAULT_PREFERENCES["notifications"] or not isinstance(x, bool) for k, x in switches.items())):
        v.error("notifications", "Use true/false switches.")
    v.done()
    doc = db.User_Preferences.find_one({"userId": user_id}) or {"userId": user_id, **DEFAULT_PREFERENCES,
                                                               "notifications": dict(DEFAULT_PREFERENCES["notifications"])}
    doc.pop("_id", None)
    for key in ("theme", "language"):  # Remove obsolete fields.
        doc.pop(key, None)
    if switches:
        doc["notifications"] = {**DEFAULT_PREFERENCES["notifications"], **(doc.get("notifications") or {}), **switches}
    doc["updatedAt"] = now_utc()
    db.User_Preferences.replace_one({"userId": user_id}, doc, upsert=True)
    _log_admin("admin.preferences_updated", "user", user_id, {"switches": switches})
    return ok({k: v for k, v in doc.items() if k != "_id"})


@bp.delete("/preferences/<int:user_id>")
@admin_only
def reset_preferences(user_id):
    db = require_mongo()
    db.User_Preferences.delete_one({"userId": user_id})
    _log_admin("admin.preferences_reset", "user", user_id, {})
    return ok(None)


@bp.get("/logs")
@admin_only
def logs():
    db = require_mongo()
    q = {}
    if request.args.get("user_id"):
        q["userId"] = query_int("user_id")
    if request.args.get("action_type"):
        q["action_type"] = {"$regex": "^" + re.escape(request.args["action_type"])}
    if request.args.get("entity_type"):
        q["entity_type"] = request.args["entity_type"]
    if request.args.get("role"):
        q["role"] = request.args["role"]
    archived = request.args.get("archived", "0")
    if archived in ("0", "1"):
        q["is_archived"] = archived == "1"
    rng = {}
    for key, op in (("from", "$gte"), ("to", "$lt")):
        if request.args.get(key):
            try:
                rng[op] = parse_client_datetime(request.args[key])
            except ValueError:
                raise ValidationError(fields={key: "Use a date like 2026-11-20."})
    if rng:
        q["timestamp"] = rng
    resp = _page(db.Activity_Logs, q, [("timestamp", -1), ("_id", -1)])
    return resp


@bp.get("/logs/action-types")
@admin_only
def log_types():
    db = require_mongo()
    return ok(sorted(t for t in db.Activity_Logs.distinct("action_type") if t))


@bp.post("/logs")
@admin_only
def create_log():
    """Create a manual Admin log with a 'manual.' action prefix."""
    db = require_mongo()
    v = Input(request_json())
    action = v.text("action_type", required=True, max_len=80, label="Action",
                    pattern=re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$"),
                    pattern_msg="Use letters, numbers, dot, dash or underscore.")
    message = v.text("message", required=True, max_len=500, label="Message")
    entity_type = v.text("entity_type", max_len=40) if v.has("entity_type") else None
    entity_id = None
    if v.has("entity_id"):
        raw = v.data["entity_id"]
        if isinstance(raw, str):
            entity_id = v.text("entity_id", max_len=60)
            if entity_id and entity_id.isdigit() and len(entity_id) <= 15:
                entity_id = int(entity_id)
        else:
            entity_id = v.integer("entity_id", min_value=0)
    v.done()
    if not action.startswith("manual."):
        action = "manual." + action
    me = current_user()
    doc = {"userId": me["user_id"], "role": "Admin", "action_type": action,
           "ip_address": client_ip(), "metadata": {"source": "manual", "message": message},
           "timestamp": now_utc(), "admin_note": None, "is_archived": False}
    if entity_type:
        doc["entity_type"] = entity_type
    if entity_id is not None and entity_id != "":
        doc["entity_id"] = entity_id
    res = db.Activity_Logs.insert_one(doc)
    _log_admin("admin.log_created", "activity_log", str(res.inserted_id), {"action_type": action})
    return created(_out(db.Activity_Logs.find_one({"_id": res.inserted_id})))


@bp.patch("/logs/<lid>")
@admin_only
def update_log(lid):
    """Only admin_note and is_archived can be changed."""
    db = require_mongo()
    v = Input(request_json())
    changes = {}
    if "admin_note" in v.data:
        changes["admin_note"] = v.text("admin_note", max_len=500)
    if "is_archived" in v.data:
        changes["is_archived"] = v.boolean("is_archived")
    extra = set(v.data) - {"admin_note", "is_archived"}
    if extra:
        v.error(sorted(extra)[0], "Only admin_note and is_archived can be changed.")
    v.done()
    before = db.Activity_Logs.find_one({"_id": _oid(lid, "Log")})
    if not before:
        raise NotFound("NOT_FOUND", "Log not found.")
    db.Activity_Logs.update_one({"_id": before["_id"]}, {"$set": changes})
    if changes:
        _log_admin("admin.log_annotated", "activity_log", lid,
                   {"changes": changes, "before": {k: before.get(k) for k in changes}})
    return ok(_out(db.Activity_Logs.find_one({"_id": _oid(lid)})))


@bp.delete("/logs/<lid>")
@admin_only
def delete_log(lid):
    """Archive the log instead of deleting it; keep the archive action in the audit trail."""
    db = require_mongo()
    res = db.Activity_Logs.update_one({"_id": _oid(lid, "Log")},
                                      {"$set": {"is_archived": True, "archived_at": now_utc(),
                                                "archived_by": current_user()["user_id"]}})
    if not res.matched_count:
        raise NotFound("NOT_FOUND", "Log not found.")
    _log_admin("admin.log_archived", "activity_log", lid, {})
    return ok(_out(db.Activity_Logs.find_one({"_id": _oid(lid)})))