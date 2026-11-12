"""Register, login, profile and password management.

Lockout (security rule 7): Too many failed logins lock the account for
LOGIN_LOCK_MINUTES. Login events are saved through the outbox.
"""
import pymysql

from core.errors import AppError, Conflict, Forbidden, Locked, NotFound, Unauthenticated, ValidationError
from core.security import hash_password, verify_password
from core.settings import cfg
from core.timeutil import minutes, now_utc
from db.mysql import fetch_one, transaction
from services.outbox import write_event

PUBLIC_USER = "user_id, email, full_name, phone, role, is_active, created_at"


def public_user(row):
    return {k: row[k] for k in ("user_id", "email", "full_name", "phone", "role", "created_at")}


def register(email, password, full_name, phone):
    """Create a Customer account. The role is not taken from the request."""
    now = now_utc()
    try:
        with transaction() as cur:
            cur.execute("INSERT INTO Users (email, password_hash, full_name, phone, role, created_at) "
                        "VALUES (%s, %s, %s, %s, 'Customer', %s)",
                        (email, hash_password(password), full_name, phone, now))
            uid = cur.lastrowid
            write_event(cur, "user.registered", "user", uid, metadata={"email": email},
                        who={"actor_user_id": uid, "actor_role": "Customer", "ip_address": _ip()})
    except pymysql.err.IntegrityError as e:
        if e.args and e.args[0] == 1062:
            raise Conflict("EMAIL_TAKEN", "An account with this email already exists.",
                           fields={"email": "This email is already registered."})
        raise
    return public_user(fetch_one(f"SELECT {PUBLIC_USER} FROM Users WHERE user_id = %s", (uid,)))


def _ip():
    from flask import has_request_context, request
    return request.remote_addr if has_request_context() else None


def authenticate(email, password):
    """Return the user row or raise an error. Save failed attempts before raising."""
    now = now_utc()
    outcome, payload = None, None
    with transaction() as cur:
        cur.execute("SELECT user_id, email, password_hash, role, is_active, failed_login_count, locked_until "
                    "FROM Users WHERE email = %s FOR UPDATE", (email,))
        u = cur.fetchone()
        ip = _ip()
        anon = {"actor_user_id": u["user_id"] if u else None,
                "actor_role": u["role"] if u else "Anonymous", "ip_address": ip}

        if u and u["locked_until"] and u["locked_until"] > now:
            write_event(cur, "user.login_failed", "user", u["user_id"],
                        metadata={"email": email, "reason": "locked"}, who=anon)
            outcome, payload = "locked", u["locked_until"]
        elif not verify_password(u["password_hash"] if u else None, password):
            if u:
                count = u["failed_login_count"] + 1
                if count >= cfg("LOGIN_MAX_FAILED"):
                    until = now + minutes(cfg("LOGIN_LOCK_MINUTES"))
                    cur.execute("UPDATE Users SET failed_login_count = 0, locked_until = %s WHERE user_id = %s",
                                (until, u["user_id"]))
                    write_event(cur, "user.locked", "user", u["user_id"],
                                metadata={"email": email, "attempts": count, "locked_until": until.isoformat() + "Z"},
                                who=anon)
                    outcome, payload = "locked", until
                else:
                    cur.execute("UPDATE Users SET failed_login_count = %s WHERE user_id = %s", (count, u["user_id"]))
                    outcome, payload = "bad", count
            else:
                outcome = "bad"
            write_event(cur, "user.login_failed", "user", u["user_id"] if u else None,
                        metadata={"email": email, "reason": "invalid_credentials",
                                  "attempt": payload if outcome == "bad" else None}, who=anon)
        elif not u["is_active"]:
            write_event(cur, "user.login_failed", "user", u["user_id"],
                        metadata={"email": email, "reason": "inactive"}, who=anon)
            outcome = "inactive"
        else:
            cur.execute("UPDATE Users SET failed_login_count = 0, locked_until = NULL WHERE user_id = %s",
                        (u["user_id"],))
            write_event(cur, "user.login", "user", u["user_id"], metadata={}, who=anon)
            outcome = "ok"

    # Raise after commit so failed attempts are saved.
    if outcome == "locked":
        raise Locked("ACCOUNT_LOCKED",
                     f"Too many failed attempts. Try again after {cfg('LOGIN_LOCK_MINUTES')} minutes.",
                     extra={"locked_until": payload})
    if outcome == "bad":
        raise Unauthenticated("INVALID_CREDENTIALS", "The email or password is incorrect.")
    if outcome == "inactive":
        raise Forbidden("ACCOUNT_INACTIVE", "This account has been deactivated. Please contact an administrator.")
    return public_user(fetch_one(f"SELECT {PUBLIC_USER} FROM Users WHERE user_id = %s", (u["user_id"],)))


def log_logout(user):
    with transaction() as cur:
        write_event(cur, "user.logout", "user", user["user_id"], metadata={})


def update_profile(user_id, full_name=None, phone=None, clear_phone=False):
    now = now_utc()
    sets, args = [], []
    if full_name is not None:
        sets.append("full_name = %s"); args.append(full_name)
    if phone is not None or clear_phone:
        sets.append("phone = %s"); args.append(phone)
    if sets:
        with transaction() as cur:
            cur.execute(f"UPDATE Users SET {', '.join(sets)}, updated_at = %s WHERE user_id = %s",
                        (*args, now, user_id))
            write_event(cur, "user.profile_updated", "user", user_id,
                        metadata={"fields": [s.split(" =")[0] for s in sets]})
    row = fetch_one(f"SELECT {PUBLIC_USER} FROM Users WHERE user_id = %s", (user_id,))
    if not row:
        raise NotFound()
    return public_user(row)


def change_password(user_id, current_password, new_password):
    with transaction() as cur:
        cur.execute("SELECT password_hash FROM Users WHERE user_id = %s FOR UPDATE", (user_id,))
        row = cur.fetchone()
        if not row or not verify_password(row["password_hash"], current_password):
            raise ValidationError(fields={"current_password": "Your current password is incorrect."})
        if current_password == new_password:
            raise ValidationError(fields={"new_password": "Choose a password different from the current one."})
        cur.execute("UPDATE Users SET password_hash = %s, updated_at = %s WHERE user_id = %s",
                    (hash_password(new_password), now_utc(), user_id))
        write_event(cur, "user.password_changed", "user", user_id, metadata={})