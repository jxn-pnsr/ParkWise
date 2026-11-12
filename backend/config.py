"""App settings from environment variables or a .env file.

Business rules such as hold times and rates are stored in System_Config
so admins can change them from the web UI.
"""
import os
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()


def _bool(name, default):
    return os.getenv(name, str(int(default))).strip().lower() in ("1", "true", "yes", "on")


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-secret-change-me")
    DEBUG = _bool("FLASK_DEBUG", False)

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
    MONGO_TIMEOUT_MS = int(os.getenv("MONGO_TIMEOUT_MS", "800"))  # Local MongoDB usually responds quickly.

    # Session cookie
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", False)  # Enable only when using HTTPS.
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=int(os.getenv("SESSION_TIMEOUT_MINUTES", "30")))
    SESSION_REFRESH_EACH_REQUEST = True  # Reset timeout after each request.

    # Background jobs
    SCHEDULER_ENABLED = _bool("SCHEDULER_ENABLED", True)

    # Request body limit
    MAX_CONTENT_LENGTH = 1 * 1024 * 1024


class TestConfig(Config):
    TESTING = True
    SCHEDULER_ENABLED = False
    MONGO_TIMEOUT_MS = 300