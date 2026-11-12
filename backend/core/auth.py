"""
Check user login, role, and data ownership.
Deactivated users cannot access the system.
"""
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
        uid = session.get("uid")
        user = load_user(uid) if uid else None
        if user and not user["is_active"]:
            session.clear()
            user = None
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
    """Check if the user can access the row."""
    user = current_user()
    if not user:
        raise Unauthenticated()
    if user["role"] == "Customer" and int(owner_user_id) != int(user["user_id"]):
        raise Forbidden()


def actor():
    """Return actor details for logs."""
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