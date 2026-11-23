"""ParkWise backend entry point.

Run with `python app.py` or `flask --app app run --debug`.
API routes return JSON. Other paths serve frontend files from static/.
"""
import logging
import os
from datetime import date, datetime
from decimal import Decimal

from flask import Flask, send_from_directory
from flask.json.provider import DefaultJSONProvider

from config import Config, check_secret_key
from core import security, timeutil
from core.auth import current_user
from core.errors import register_error_handlers
from core.responses import ok
from db import mongo, mysql

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Frontend static files (can be overridden with FRONTEND_DIR).
STATIC_DIR = os.path.abspath(os.getenv(
    "FRONTEND_DIR", os.path.join(BASE_DIR, "..", "frontend", "static")))


class ApiJSONProvider(DefaultJSONProvider):
    """Convert dates, decimals, and MongoDB ObjectIds to JSON values."""
    ensure_ascii = False  # Keep Thai text readable in JSON responses.
    sort_keys = False

    @staticmethod
    def default(o):
        if isinstance(o, datetime):
            return timeutil.to_iso(o)
        if isinstance(o, date):
            return o.isoformat()
        if isinstance(o, Decimal):
            return int(o) if o == o.to_integral_value() else float(o)
        if type(o).__name__ == "ObjectId":
            return str(o)
        return DefaultJSONProvider.default(o)


def register_blueprints(app):
    # Core routes.
    from routes import auth, lots, me, reservations
    for module in (auth, lots, me, reservations):
        app.register_blueprint(module.bp)

    # Account and staff routes.
    from routes import account, staff
    for module in (account, staff):
        app.register_blueprint(module.bp)
    # Admin routes.
    from routes import admin, admin_mongo
    for module in (admin, admin_mongo):
        app.register_blueprint(module.bp)

def register_system_routes(app):
    @app.get("/api/health")
    def health():
        mysql_ok = mysql.ping()
        body = {"status": "ok" if mysql_ok else "down", "server_time": timeutil.now_utc()}
        user = current_user() if mysql_ok else None
        if user and user["role"] == "Admin":
            mongo_ok = mongo.ping()
            outbox = {row["status"]: row["n"] for row in mysql.fetch_all(
                "SELECT status, COUNT(*) AS n FROM Integration_Outbox GROUP BY status")}
            body.update({
                "mysql": "ok",
                "mongo": "ok" if mongo_ok else ("simulated_down" if mongo.is_simulated_down() else "down"),
                "outbox": {"pending": outbox.get("Pending", 0), "failed": outbox.get("Failed", 0)},
            })
        return ok(body, 200 if mysql_ok else 503)

    # Serve frontend pages.
    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/favicon.ico")
    def favicon():  # Serve the favicon requested by browsers.
        return send_from_directory(STATIC_DIR, "favicon.svg", mimetype="image/svg+xml")

# Password hash of the public seed accounts (database/seed.sql, password Passw0rd!).
SEED_HASH_PREFIXES = ("scrypt:32768:8:1$UHqtFbjFBWLNB09I$", "pbkdf2:sha256:1000000$w1zQVoOofunQ36rp$")


def check_seed_accounts(app):
    """H2: outside DEMO_MODE, refuse to run while accounts still use the public seed password."""
    if app.config.get("TESTING") or app.config.get("DEMO_MODE"):
        return
    try:
        with app.app_context():
            rows = mysql.fetch_all("SELECT email FROM Users WHERE is_active = 1 AND ("
                                   + " OR ".join(["password_hash LIKE %s"] * len(SEED_HASH_PREFIXES)) + ")",
                                   tuple(p + "%" for p in SEED_HASH_PREFIXES))
    except Exception as e:  # MySQL not up yet: /api/health will say so
        logging.getLogger(__name__).warning("could not check for seed accounts: %s", e)
        return
    if rows:
        raise RuntimeError(
            f"{len(rows)} active account(s) still use the public demo password from seed.sql "
            f"(e.g. {rows[0]['email']}). Either set DEMO_MODE=1 (presentation / development only) or "
            "build the database without seed.sql and create the admin with database/create_admin.py.")


def create_app(config_class=Config):
    app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")
    app.json_provider_class = ApiJSONProvider
    app.json = ApiJSONProvider(app)
    app.config.from_object(config_class)
    check_secret_key(app.config)    

    logging.basicConfig(level=logging.DEBUG if app.config.get("DEBUG") else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Silence noisy driver debug logs (heartbeats, connection pools)
    logging.getLogger("pymongo").setLevel(logging.WARNING)

    mysql.init_app(app)
    mongo.init_app(app)
    register_error_handlers(app)
    security.init_app(app, STATIC_DIR)   # Check CSRF tokens on data-changing requests.
    register_system_routes(app)
    register_blueprints(app)
    check_seed_accounts(app)
    if not app.config.get("DEMO_MODE") and not app.config.get("TESTING"):
        if app.config["MYSQL_USER"] == "root" or not app.config["MYSQL_PASSWORD"]:
            logging.getLogger(__name__).warning(
                "MySQL is used as root and/or without a password. Use a ParkWise account with a password.")
        if app.config.get("DEBUG"):
            logging.getLogger(__name__).warning("FLASK_DEBUG is on: never use it on a server other people reach.")
    if app.config.get("DEMO_MODE") and not app.config.get("TESTING"):
        logging.getLogger(__name__).warning(
            "DEMO_MODE is ON: Transaction Demo endpoints and demo login buttons are enabled. "
            "Never use DEMO_MODE on a server with real users.")

    # Start background jobs.
    from jobs import scheduler
    scheduler.start(app)
    return app


if __name__ == "__main__":
    application = create_app()
    application.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")),
                    debug=application.config.get("DEBUG", False))
