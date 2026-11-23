"""Authentication and access control (security rules 2 and 3).

    @login_required("Customer")  # Empty roles = any logged-in user
    def my_vehicles():
        user = current_user()

    ensure_owner(row["user_id"])  # 403 if accessing another customer's data

    Users are checked against MySQL on every request, so deactivated
    accounts lose access immediately. Password changes also invalidate
    older sessions through a password-hash fingerprint.
"""
import hmac
from functools import wraps

from flask import g, request, session

from core.errors import Forbidden, Unauthenticated
from db.mysql import fetch_one

ROLES = ("Customer", "Staff", "Admin")

USER_COLUMNS = "user_id, email, full_name, phone, role, is_active, created_at"


def load_user(user_id):
    return fetch_one(f"SELECT {USER_COLUMNS} FROM Users WHERE user_id = %s", (user_id,))


def current_user():
    """Return the logged-in user or None."""
    if "user" not in g:
        from core.security import password_fingerprint
        uid = session.get("uid")
        user = None
        if uid:
            row = fetch_one(f"SELECT {USER_COLUMNS}, password_hash FROM Users WHERE user_id = %s", (uid,))
            fingerprint = session.get("pwf")
            if (not row or not row["is_active"] or not fingerprint
                    or not hmac.compare_digest(fingerprint, password_fingerprint(row["password_hash"]))):
                session.clear()
            else:
                row.pop("password_hash")
                user = row
        g.user = user
    return g.user


def login_required(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user:
                raise Unauthenticated()
            if roles and user["role"] not in roles:
                raise Forbidden()
            return fn(*args, **kwargs)
        return wrapper
    return deco


def is_staff_or_admin(user=None):
    user = user or current_user()
    return bool(user) and user["role"] in ("Staff", "Admin")


def ensure_owner(owner_user_id):
    """Customers may only access their own rows; staff and admins pass."""
    user = current_user()
    if not user:
        raise Unauthenticated()
    if user["role"] == "Customer" and int(owner_user_id) != int(user["user_id"]):
        raise Forbidden()


def actor():
    """Return actor details for logs and outbox events."""
    user = current_user() if _in_request() else None
    return {
        "actor_user_id": user["user_id"] if user else None,
        "actor_role": user["role"] if user else ("Anonymous" if _in_request() else "System"),
        "ip_address": client_ip() if _in_request() else None,
    }


def client_ip():
    return request.remote_addr


def _in_request():
    from flask import has_request_context
    return has_request_context()