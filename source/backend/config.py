"""App settings from environment variables or a .env file.

Business rules such as hold times and rates are stored in System_Config
so admins can change them from the web UI.
"""
import os
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
# Load from backend/.env first, then fallback to current working directory if different


def _bool(name, default):
    return os.getenv(name, str(int(default))).strip().lower() in ("1", "true", "yes", "on")

# The app refuses to start with one of these, because anybody who has read the
KNOWN_WEAK_SECRETS = {
    "", "dev-only-secret-change-me", "change-me-to-a-long-random-string", "change-me",
    "secret", "changeme", "dev", "test",
}
MIN_SECRET_LENGTH = 32

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "")
    DEBUG = _bool("FLASK_DEBUG", False)
    # "MongoDB down" switch. They change real rows, so they are OFF unless asked for.
    DEMO_MODE = _bool("DEMO_MODE", False)

    # MySQL
    MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
    MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
    MYSQL_USER = os.getenv("MYSQL_USER", "root")
    MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
    MYSQL_DB = os.getenv("MYSQL_DB", "parkwise")

    # MongoDB
    # Use 127.0.0.1 to avoid IPv6 connection delays on Windows.
    MONGO_URI = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017").replace("://localhost", "://127.0.0.1")
    MONGO_DB = os.getenv("MONGO_DB", "parkwise")
    MONGO_TIMEOUT_MS = int(os.getenv("MONGO_TIMEOUT_MS", "800"))  # local MongoDB answers in a few ms

    # Session cookie
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", True)
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=int(os.getenv("SESSION_TIMEOUT_MINUTES", "30")))
    SESSION_REFRESH_EACH_REQUEST = True  # Reset timeout after each request.

    # Background jobs
    SCHEDULER_ENABLED = _bool("SCHEDULER_ENABLED", True)

    # Request body limit
    MAX_CONTENT_LENGTH = 1 * 1024 * 1024
    
    # Idle MySQL connections kept for reuse between requests (0 = open a new one every time)
    MYSQL_POOL_SIZE = int(os.getenv("MYSQL_POOL_SIZE", "10"))

    # Rate limits (per process). Format: attempts per window in seconds.
    RATE_LIMIT_ENABLED = _bool("RATE_LIMIT_ENABLED", True)

class TestConfig(Config):
    TESTING = True
    SECRET_KEY = "test-only-secret-key-not-used-anywhere-else-0123456789"
    SESSION_COOKIE_SECURE = False   # the test client talks plain http
    SCHEDULER_ENABLED = False
    DEMO_MODE = True                # tests cover the Transaction Demo endpoints
    RATE_LIMIT_ENABLED = False      # tests log in many times; test_security turns it on itself
    MONGO_TIMEOUT_MS = 300


def check_secret_key(app_config):
    """Raise RuntimeError when SECRET_KEY is missing, too short or a known value (H1)."""
    if app_config.get("TESTING"):
        return
    key = app_config.get("SECRET_KEY") or ""
    if key.strip().lower() in KNOWN_WEAK_SECRETS or len(key) < MIN_SECRET_LENGTH:
        raise RuntimeError(
            "SECRET_KEY is missing, too short or a well-known value, so anybody could forge a login "
            "session. Put a random value of at least 32 characters in backend/.env, for example:\n"
            "    python -c \"import secrets; print(secrets.token_hex(32))\"\n"
            "and set SECRET_KEY=<that value>.")
