"""ParkWise backend entry point.

Run:  python app.py            (or: flask --app app run --debug)

All /api routes return JSON. Other paths serve frontend files from static/.
"""
import logging
import os
from datetime import date, datetime
from decimal import Decimal

from flask import Flask, send_from_directory
from flask.json.provider import DefaultJSONProvider

from config import Config
from core import security, timeutil
from core.errors import register_error_handlers
from core.responses import ok
from db import mongo, mysql

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Frontend folder (can be changed with FRONTEND_DIR)
STATIC_DIR = os.path.abspath(os.getenv(
    "FRONTEND_DIR", os.path.join(BASE_DIR, "..", "frontend", "static")))


class ApiJSONProvider(DefaultJSONProvider):
    """Convert datetime, Decimal, and ObjectId to JSON values."""
    ensure_ascii = False  # Keep Thai text readable.
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
    # Phase 1: authentication, customer data, lots, and bookings
    from routes import auth, lots, me, reservations
    for module in (auth, lots, me, reservations):
        app.register_blueprint(module.bp)
    # Phase 2: staff, payments, notifications, preferences, and sessions
    # Phase 3: admin


def register_system_routes(app):
    @app.get("/api/health")
    def health():
        mysql_ok = mysql.ping()
        mongo_ok = mongo.ping()
        outbox = None
        if mysql_ok:
            outbox = {row["status"]: row["n"] for row in mysql.fetch_all(
                "SELECT status, COUNT(*) AS n FROM Integration_Outbox GROUP BY status")}
        return ok({
            "mysql": "ok" if mysql_ok else "down",
            "mongo": "ok" if mongo_ok else ("simulated_down" if mongo.is_simulated_down() else "down"),
            "outbox": {"pending": outbox.get("Pending", 0), "failed": outbox.get("Failed", 0)} if outbox is not None else None,
            "server_time": timeutil.now_utc(),
        }, 200 if mysql_ok else 503)

    # Serve frontend pages.
    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/favicon.ico")
    def favicon():  # Browser favicon request
        return send_from_directory(STATIC_DIR, "favicon.svg", mimetype="image/svg+xml")


def create_app(config_class=Config):
    app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")
    app.json_provider_class = ApiJSONProvider
    app.json = ApiJSONProvider(app)
    app.config.from_object(config_class)

    logging.basicConfig(level=logging.DEBUG if app.config.get("DEBUG") else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    mysql.init_app(app)
    mongo.init_app(app)
    register_error_handlers(app)
    security.init_app(app)   # Check CSRF on data-changing requests.
    register_system_routes(app)
    register_blueprints(app)

    # Start APScheduler here in phase 3.
    return app


if __name__ == "__main__":
    application = create_app()
    application.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")),
                    debug=application.config.get("DEBUG", False))