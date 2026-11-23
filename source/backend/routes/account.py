"""Customer data that lives in MongoDB (spec 10.3: written directly by Flask,
not through the outbox): notifications, preferences, parking history.
Plus payments (MySQL)."""
from bson import ObjectId
from bson.errors import InvalidId
from flask import Blueprint, Response, request

from core.auth import current_user, login_required
from core.errors import NotFound, ValidationError
from core.responses import get_pagination, ok, paginated
from core.timeutil import now_utc
from core.validate import Input, request_json
from db.mysql import fetch_one
from services import payment, vehicles
from services.mongo_read import DEFAULT_PREFERENCES, preferences, require_mongo

bp = Blueprint("account", __name__, url_prefix="/api")
customer = login_required("Customer")


def _oid(value):
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise NotFound("NOTIFICATION_NOT_FOUND", "Notification not found.")


def _notification(d):
    return {"id": str(d["_id"]), "type": d.get("type"), "title": d.get("title"), "message": d.get("message"),
            "is_read": bool(d.get("isRead")), "reservation_id": d.get("reservationId"),
            "sent_at": d.get("sentAt"), "read_at": d.get("readAt")}


# ---- notifications ------------------------------------------------------------

@bp.get("/me/notifications")
@customer
def notifications():
    db = require_mongo()
    uid = current_user()["user_id"]
    page, per_page, offset = get_pagination()
    query = {"userId": uid}
    if request.args.get("unread_only") in ("1", "true"):
        query["isRead"] = False
    total = db.Notifications.count_documents(query)
    docs = db.Notifications.find(query).sort([("sentAt", -1), ("_id", -1)]).skip(offset).limit(per_page)
    resp = {"items": [_notification(d) for d in docs], "page": page, "per_page": per_page, "total": total,
            "unread_count": db.Notifications.count_documents({"userId": uid, "isRead": False})}
    return ok(resp)


@bp.post("/me/notifications/<nid>/read")
@customer
def read_one(nid):
    db = require_mongo()
    res = db.Notifications.update_one({"_id": _oid(nid), "userId": current_user()["user_id"]},
                                      {"$set": {"isRead": True, "readAt": now_utc()}})
    if not res.matched_count:
        raise NotFound("NOTIFICATION_NOT_FOUND", "Notification not found.")
    return ok(None)


@bp.post("/me/notifications/read-all")
@customer
def read_all():
    db = require_mongo()
    res = db.Notifications.update_many({"userId": current_user()["user_id"], "isRead": False},
                                       {"$set": {"isRead": True, "readAt": now_utc()}})
    return ok({"updated": res.modified_count})


@bp.delete("/me/notifications/<nid>")
@customer
def delete_one(nid):
    db = require_mongo()
    res = db.Notifications.delete_one({"_id": _oid(nid), "userId": current_user()["user_id"]})
    if not res.deleted_count:
        raise NotFound("NOTIFICATION_NOT_FOUND", "Notification not found.")
    return ok(None)


# ---- preferences --------------------------------------------------------------

def _prefs_out(uid):
    p = preferences(uid)
    main = fetch_one("SELECT vehicle_id FROM Customer_Vehicles WHERE user_id = %s AND is_default = 1 AND is_deleted = 0",
                     (uid,))
    p["defaultVehicleId"] = main["vehicle_id"] if main else None   # MySQL is the source of truth for the main car
    p.pop("_id", None)
    p.pop("updatedAt", None)
    return p


@bp.get("/me/preferences")
@customer
def get_preferences():
    return ok(_prefs_out(current_user()["user_id"]))


@bp.put("/me/preferences")
@customer
def put_preferences():
    """Change any of: theme, language, notifications{...}, defaultVehicleId. Missing keys stay as they are."""
    uid = current_user()["user_id"]
    v = Input(request_json())
    theme = v.choice("theme", ("light", "dark")) if "theme" in v.data else None
    language = v.choice("language", ("en", "th")) if "language" in v.data else None
    switches = v.data.get("notifications")
    if switches is not None and (not isinstance(switches, dict) or any(
            k not in DEFAULT_PREFERENCES["notifications"] or not isinstance(x, bool) for k, x in switches.items())):
        v.error("notifications", "Use true/false for: " + ", ".join(DEFAULT_PREFERENCES["notifications"]))
    vehicle_id = v.integer("defaultVehicleId", min_value=1) if v.data.get("defaultVehicleId") else None
    v.done()

    db = require_mongo()
    if vehicle_id:
        vehicles.set_default(uid, vehicle_id)          # MySQL first: it checks the car is this customer's
    doc = preferences(uid)                             # current values (or defaults)
    doc.pop("_id", None)
    if theme:
        doc["theme"] = theme
    if language:
        doc["language"] = language
    if switches:
        doc["notifications"] = {**doc["notifications"], **switches}
    if vehicle_id:
        doc["defaultVehicleId"] = vehicle_id
    doc.update({"userId": uid, "updatedAt": now_utc()})
    db.User_Preferences.replace_one({"userId": uid}, doc, upsert=True)
    return ok(_prefs_out(uid))


@bp.delete("/me/preferences")
@customer
def reset_preferences():
    uid = current_user()["user_id"]
    db = require_mongo()
    db.User_Preferences.replace_one({"userId": uid}, {"userId": uid, **{k: v for k, v in DEFAULT_PREFERENCES.items()
                                                                         if k != "defaultVehicleId"},
                                                     "defaultVehicleId": None, "updatedAt": now_utc()}, upsert=True)
    return ok(_prefs_out(uid))


# ---- parking history (MongoDB Parking_Sessions) -------------------------------

def _session(d):
    fees = d.get("fees") or {}
    return {"reservation_id": d.get("reservationId"), "plate_number": d.get("plateNumber"),
            "lot_name": d.get("lotName"), "slot_code": d.get("slotCode"),
            "check_in_at": d.get("checkInAt"), "check_out_at": d.get("checkOutAt"),
            "duration_minutes": d.get("durationMinutes"), "is_overstay": bool(d.get("isOverstay")),
            "fees": {"base": fees.get("base"), "overstay": fees.get("overstay"), "total": fees.get("total")} if fees else None}


@bp.get("/me/sessions")
@customer
def sessions():
    db = require_mongo()
    uid = current_user()["user_id"]
    page, per_page, offset = get_pagination()
    total = db.Parking_Sessions.count_documents({"userId": uid})
    docs = db.Parking_Sessions.find({"userId": uid}).sort("checkInAt", -1).skip(offset).limit(per_page)
    return paginated([_session(d) for d in docs], page, per_page, total)


# ---- payments (MySQL) -----------------------------------------------------------

@bp.get("/me/payments")
@customer
def my_payments():
    status = request.args.get("status")
    if status and status not in ("Pending", "Paid", "Failed"):
        raise ValidationError(fields={"status": "Use Pending, Paid or Failed."})
    items = payment.list_for_user(current_user()["user_id"], status)
    return ok({"items": items,
               "unpaid_total": sum(float(p["total_amount"]) for p in items if p["status"] != "Paid")})


@bp.post("/payments/<int:payment_id>/pay")
@customer
def pay(payment_id):
    v = Input(request_json())
    method = v.choice("method", payment.METHODS, required=True, label="Payment method")
    v.done()
    return ok(payment.pay(current_user(), payment_id, method))


@bp.get("/payments/<int:payment_id>/qr")
@customer
def qr(payment_id):
    p = payment.get(payment_id)
    if p["user_id"] != current_user()["user_id"]:
        from core.errors import Forbidden
        raise Forbidden()
    return Response(payment.qr_svg(p), mimetype="image/svg+xml", headers={"Cache-Control": "no-store"})
