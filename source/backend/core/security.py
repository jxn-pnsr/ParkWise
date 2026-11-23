"""Password security, CSRF and HTTP security headers (rules 5 and 6).

    Passwords use scrypt with a dummy hash for unknown emails.
    Password-hash fingerprints invalidate old sessions after password changes.
    CSRF tokens protect API write requests; login and register are exempt.
    Security headers protect responses, with HSTS over HTTPS.
"""
import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
import threading

from flask import current_app, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from core.errors import Forbidden

log = logging.getLogger(__name__)

CSRF_HEADER = "X-CSRF-Token"
CSRF_EXEMPT = {"/api/auth/login", "/api/auth/register"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# Use the same hashing method for all passwords.
HASH_METHOD = "scrypt"

# Match the cost of checking a real password.
_DUMMY_HASH = generate_password_hash("not-a-real-password", method=HASH_METHOD)

MIN_PASSWORD_LENGTH = 8


def hash_password(password):
    return generate_password_hash(password, method=HASH_METHOD)


def verify_password(password_hash, password):
    return check_password_hash(password_hash or _DUMMY_HASH, password or "")


# Session fingerprint (M3)
def password_fingerprint(password_hash):
    """Keyed digest used to invalidate old sessions."""
    key = (current_app.config.get("SECRET_KEY") or "").encode()
    return hmac.new(key, (password_hash or "").encode(), hashlib.sha256).hexdigest()[:24]


def start_session(user_id, password_hash):
    """Start a fresh session with a new CSRF token."""
    session.clear()
    session.permanent = True
    session["uid"] = user_id
    session["pwf"] = password_fingerprint(password_hash)
    return new_csrf_token()


def restamp_session(password_hash):
    """Keep the current session valid after a password change."""
    session["pwf"] = password_fingerprint(password_hash)


# CSRF protection
def new_csrf_token():
    token = secrets.token_urlsafe(32)
    session["csrf"] = token
    return token


def csrf_token():
    return session.get("csrf") or new_csrf_token()


def check_csrf():
    """Validate CSRF tokens on API write requests."""
    if request.method in SAFE_METHODS or not request.path.startswith("/api/"):
        return
    if request.path in CSRF_EXEMPT or "uid" not in session:
        return
    sent = request.headers.get(CSRF_HEADER, "")
    expected = session.get("csrf", "")
    if not sent or not expected or not hmac.compare_digest(sent, expected):
        raise Forbidden("CSRF_INVALID", "Your session token is missing or out of date. Reload the page and try again.")


# Security headers (M9)
_SCRIPT_RE = re.compile(rb"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)


def inline_script_hashes(static_dir):
    """Hash inline scripts for the CSP."""
    found = set()
    for root, _dirs, files in os.walk(static_dir):
        for name in files:
            if not name.endswith(".html"):
                continue
            try:
                with open(os.path.join(root, name), "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            for body in _SCRIPT_RE.findall(data):
                digest = base64.b64encode(hashlib.sha256(body).digest()).decode()
                found.add(f"'sha256-{digest}'")
    return sorted(found)


def build_csp(script_hashes):
    return "; ".join([
        "default-src 'self'",
        "script-src 'self' " + " ".join(script_hashes),
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ])


class _CspCache:
    """Rebuild CSP when frontend HTML files change."""

    def __init__(self, static_dir):
        self.static_dir = static_dir
        self.signature = None
        self.value = build_csp([])
        self.lock = threading.Lock()

    def _signature(self):
        sig = []
        for root, _dirs, files in os.walk(self.static_dir):
            for name in files:
                if name.endswith(".html"):
                    try:
                        st = os.stat(os.path.join(root, name))
                    except OSError:
                        continue
                    sig.append((root, name, st.st_mtime_ns, st.st_size))
        return tuple(sorted(sig))

    def get(self):
        if not self.static_dir:
            return self.value
        sig = self._signature()
        if sig != self.signature:
            with self.lock:
                if sig != self.signature:
                    self.value = build_csp(inline_script_hashes(self.static_dir))
                    self.signature = sig
        return self.value


def init_app(app, static_dir=None):
    app.before_request(check_csrf)
    csp = _CspCache(static_dir)
    csp.get()

    @app.after_request
    def _security_headers(resp):
        h = resp.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "same-origin")
        h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if resp.mimetype == "text/html":
            h.setdefault("Content-Security-Policy", csp.get())
        if request.path.startswith("/api/"):
            h.setdefault("Cache-Control", "no-store")
        if request.is_secure or app.config.get("SESSION_COOKIE_SECURE"):
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return resp