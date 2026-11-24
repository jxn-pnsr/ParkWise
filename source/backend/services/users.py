import threading
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
    """Always creates a Customer. The role is never taken from the request (security rule 1)."""
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


# Emails with no account get the same lockout behaviour, kept in memory
_phantom = {}
_phantom_lock = threading.Lock()


def _phantom_failed(email, now):
    """Count a failed login for an unknown email. Returns locked_until or None."""
    with _phantom_lock:
        if len(_phantom) > 10_000:
            for k in [k for k, v in _phantom.items() if v["until"] and v["until"] <= now]:
                del _phantom[k]
        entry = _phantom.setdefault(email, {"count": 0, "until": None})
        if entry["until"] and entry["until"] > now:
            return entry["until"]
        entry["count"] += 1
        if entry["count"] >= cfg("LOGIN_MAX_FAILED"):
            entry["count"], entry["until"] = 0, now + minutes(cfg("LOGIN_LOCK_MINUTES"))
            return entry["until"]
        return None


def _phantom_locked(email, now):
    with _phantom_lock:
        entry = _phantom.get(email)
        return entry["until"] if entry and entry["until"] and entry["until"] > now else None


def authenticate(email, password):
    """Return the user row, or raise. Failed attempts are saved even though we raise.

    The password hash is checked BEFORE the row lock is taken, so a burst of logins
    for one email never holds a MySQL lock during the slow hash (M2).
    """
    now = now_utc()
    ip = _ip()
    u = fetch_one("SELECT user_id, password_hash, locked_until FROM Users WHERE email = %s", (email,))
    outcome, payload = None, None

    if not u:
        until = _phantom_locked(email, now)
        if until is None:
            verify_password(None, password)  # same work as a real wrong password
            until = _phantom_failed(email, now)
            outcome, payload = ("locked", until) if until else ("bad", None)
        else:
            outcome, payload = "locked", until
        with transaction() as cur:
            write_event(cur, "user.login_failed", "user", None,
                        metadata={"email": email, "reason": "locked" if outcome == "locked" else "invalid_credentials"},
                        who={"actor_user_id": None, "actor_role": "Anonymous", "ip_address": ip})
    else:
        locked_now = u["locked_until"] and u["locked_until"] > now
        password_ok = False if locked_now else verify_password(u["password_hash"], password)
        with transaction() as cur:
            cur.execute("SELECT user_id, email, password_hash, role, is_active, failed_login_count, locked_until "
                        "FROM Users WHERE user_id = %s FOR UPDATE", (u["user_id"],))
            u = cur.fetchone()
            anon = {"actor_user_id": u["user_id"], "actor_role": u["role"], "ip_address": ip}
            if u["locked_until"] and u["locked_until"] > now:
                write_event(cur, "user.login_failed", "user", u["user_id"],
                            metadata={"email": email, "reason": "locked"}, who=anon)
                outcome, payload = "locked", u["locked_until"]
            elif not password_ok:
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
                write_event(cur, "user.login_failed", "user", u["user_id"],
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

    # raise only after COMMIT, so the failed-attempt counter is saved
    if outcome == "locked":
        raise Locked("ACCOUNT_LOCKED",
                     f"Too many failed attempts. Try again after {cfg('LOGIN_LOCK_MINUTES')} minutes.",
                     extra={"locked_until": payload})
    if outcome == "bad":
        raise Unauthenticated("INVALID_CREDENTIALS", "The email or password is incorrect.")
    if outcome == "inactive":
        raise Forbidden("ACCOUNT_INACTIVE", "This account has been deactivated. Please contact an administrator.")
    return {**public_user(fetch_one(f"SELECT {PUBLIC_USER} FROM Users WHERE user_id = %s", (u["user_id"],))),
            "_password_hash": u["password_hash"]}


def reset_phantom_lockouts():
    with _phantom_lock:
        _phantom.clear()


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
    """Returns the new hash (the route re-stamps the caller's session with it; every other
    session of this user is logged out because its fingerprint no longer matches)."""
    row = fetch_one("SELECT password_hash FROM Users WHERE user_id = %s", (user_id,))
    if not row or not verify_password(row["password_hash"], current_password):   # slow hash, no lock held
        raise ValidationError(fields={"current_password": "Your current password is incorrect."})
    if current_password == new_password:
        raise ValidationError(fields={"new_password": "Choose a password different from the current one."})
    new_hash = hash_password(new_password)
    with transaction() as cur:
        cur.execute("UPDATE Users SET password_hash = %s, updated_at = %s WHERE user_id = %s AND password_hash = %s",
                    (new_hash, now_utc(), user_id, row["password_hash"]))
        if not cur.rowcount:      # changed by another request in the meantime
            raise Conflict("PASSWORD_CHANGED", "Your password was changed somewhere else. Log in again.")
        write_event(cur, "user.password_changed", "user", user_id, metadata={"other_sessions_logged_out": True})
    return new_hash
