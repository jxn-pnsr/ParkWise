import hmac
import secrets

from flask import request, session
from werkzeug.security import check_password_hash, generate_password_hash

from core.errors import Forbidden

CSRF_HEADER = "X-CSRF-Token"
CSRF_EXEMPT = {"/api/auth/login", "/api/auth/register"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# Used when the email is not found to prevent email guessing.
_DUMMY_HASH = generate_password_hash("not-a-real-password")

MIN_PASSWORD_LENGTH = 8


def hash_password(password):
    return generate_password_hash(password)


def verify_password(password_hash, password):
    return check_password_hash(password_hash or _DUMMY_HASH, password or "")


def new_csrf_token():
    token = secrets.token_urlsafe(32)
    session["csrf"] = token
    return token


def csrf_token():
    return session.get("csrf") or new_csrf_token()


def check_csrf():
    """Check CSRF tokens for data-changing requests."""
    if request.method in SAFE_METHODS or not request.path.startswith("/api/"):
        return
    if request.path in CSRF_EXEMPT or "uid" not in session:
        return
    sent = request.headers.get(CSRF_HEADER, "")
    expected = session.get("csrf", "")
    if not sent or not expected or not hmac.compare_digest(sent, expected):
        raise Forbidden("CSRF_INVALID", "Your session token is missing or out of date. Reload the page and try again.")


def init_app(app):
    app.before_request(check_csrf)