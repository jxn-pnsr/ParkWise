from flask import Blueprint, g, session

from core.auth import current_user, login_required
from core.responses import created, ok
from core.security import csrf_token, new_csrf_token
from core.validate import Input, request_json
from db.mysql import fetch_value
from services import users
from services.mongo_read import preferences, unread_notifications
from services.queries import OCCUPYING_SQL, time_params

bp = Blueprint("auth", __name__, url_prefix="/api/auth")


def _counts(user):
    if user["role"] != "Customer":
        return {}
    active = fetch_value(f"SELECT COUNT(*) FROM Reservations r WHERE r.user_id = %(uid)s AND {OCCUPYING_SQL}",
                         {**time_params(), "uid": user["user_id"]})
    return {"active_reservations": active, "unread_notifications": unread_notifications(user["user_id"])}


def _me_payload(user):
    prefs = preferences(user["user_id"])
    return {**users.public_user(user), "csrf_token": csrf_token(),
            "preferences": {"theme": prefs["theme"], "language": prefs["language"]},
            "counts": _counts(user)}


@bp.post("/register")
def register():
    v = Input(request_json())
    full_name = v.text("full_name", required=True, max_len=150, label="Name")
    email = v.email()
    phone = v.phone()
    password = v.password()
    v.done()
    # Sign-up always creates a Customer.
    return created(users.register(email, password, full_name, phone))


@bp.post("/login")
def login():
    v = Input(request_json())
    email = v.email()
    password = v.password(min_len=1)
    v.done()
    user = users.authenticate(email, password)
    session.clear()                 # Start a new session after login.
    session.permanent = True        # Use the configured session timeout.
    session["uid"] = user["user_id"]
    new_csrf_token()
    g.pop("user", None)             # Clear the cached user.
    full = current_user()
    return ok(_me_payload(full))


@bp.post("/logout")
@login_required()
def logout():
    users.log_logout(current_user())
    session.clear()
    return ok(None)


@bp.get("/me")
@login_required()
def me():
    return ok(_me_payload(current_user()))