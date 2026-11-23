"""Auth endpoints: register, login, logout, and current user."""
from flask import Blueprint, current_app, g, session

from core.auth import client_ip, current_user, login_required
from core.errors import NotFound
from core.ratelimit import hit
from core.responses import created, ok
from core.security import csrf_token, start_session
from core.validate import Input, request_json
from db.mysql import fetch_value
from services import users
from services.mongo_read import unread_notifications
from services.queries import OCCUPYING_SQL, time_params

bp = Blueprint("auth", __name__, url_prefix="/api/auth")


def _counts(user):
    if user["role"] != "Customer":
        return {}
    active = fetch_value(f"SELECT COUNT(*) FROM Reservations r WHERE r.user_id = %(uid)s AND {OCCUPYING_SQL}",
                         {**time_params(), "uid": user["user_id"]})
    return {"active_reservations": active, "unread_notifications": unread_notifications(user["user_id"])}


def _me_payload(user):
    return {**users.public_user(user), "csrf_token": csrf_token(), "counts": _counts(user)}


# Login limits: per IP and per email.
LOGIN_PER_IP = (30, 300)          # 30 tries / 5 minutes per IP
LOGIN_PER_EMAIL = (10, 300)       # 10 tries / 5 minutes per email
REGISTER_PER_IP = (5, 3600)       # 5 accounts / hour per IP

# Demo accounts shown only when DEMO_MODE=1.
DEMO_ACCOUNTS = [
    {"label": "Customer", "email": "somchai@example.com"},
    {"label": "Staff", "email": "staff1@parkwise.local"},
    {"label": "Administrator", "email": "admin@parkwise.local"},
]
DEMO_PASSWORD = "Passw0rd!"


@bp.get("/demo-accounts")
def demo_accounts():
    """Return seed accounts for demos; 404 when DEMO_MODE is off."""
    if not current_app.config.get("DEMO_MODE"):
        raise NotFound()
    return ok({"password": DEMO_PASSWORD, "accounts": DEMO_ACCOUNTS})


@bp.post("/register")
def register():
    hit("register-ip", client_ip(), *REGISTER_PER_IP)
    v = Input(request_json())
    full_name = v.text("full_name", required=True, max_len=150, label="Name")
    email = v.email()
    phone = v.phone()
    password = v.password()
    v.done()
    # Ignore the submitted role; sign-up always creates a Customer.
    return created(users.register(email, password, full_name, phone))


@bp.post("/login")
def login():
    hit("login-ip", client_ip(), *LOGIN_PER_IP)
    v = Input(request_json())
    email = v.email()
    password = v.password(min_len=1)
    v.done()
    hit("login-email", email, *LOGIN_PER_EMAIL)
    user = users.authenticate(email, password)
    start_session(user["user_id"], user.pop("_password_hash"))  # Prevent session fixation.
    g.pop("user", None)  # Clear any cached user from this request.
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