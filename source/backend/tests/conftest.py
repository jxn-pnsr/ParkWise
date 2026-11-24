
from datetime import datetime
from decimal import Decimal

from flask import Blueprint

from core.errors import Conflict, NotFound, ValidationError
from core.responses import get_pagination, ok, paginated
from tests.conftest import needs_mysql


def _add_test_routes(app):
    bp = Blueprint("t", __name__)

    @bp.get("/api/_t/conflict")
    def conflict():
        raise Conflict("SLOT_TAKEN", "This parking slot has already been reserved.",
                       extra={"alternatives": [5, 6]})

    @bp.get("/api/_t/validation")
    def validation():
        raise ValidationError(
            fields={"duration_hours": "Must be between 1 and 12."}
        )

    @bp.get("/api/_t/missing")
    def missing():
        raise NotFound()

    @bp.get("/api/_t/crash")
    def crash():
        raise RuntimeError("boom")

    @bp.get("/api/_t/types")
    def types():
        return ok({
            "at": datetime(2026, 11, 20, 3, 0, 0),
            "fee": Decimal("40.00"),
            "rate": Decimal("12.50"),
            "name": "Parking Lot A"
        })

    @bp.get("/api/_t/page")
    def page():
        p, per, off = get_pagination()
        return paginated([], p, per, 0)

    app.register_blueprint(bp)


def test_error_envelopes(app):
    _add_test_routes(app)
    c = app.test_client()

    r = c.get("/api/_t/conflict")
    assert r.status_code == 409
    assert r.get_json() == {
        "ok": False,
        "error": {
            "code": "SLOT_TAKEN",
            "message": "This parking slot has already been reserved.",
            "alternatives": [5, 6]
        }
    }

    r = c.get("/api/_t/validation")
    assert r.status_code == 400
    assert r.get_json()["error"]["fields"] == {
        "duration_hours": "Must be between 1 and 12."
    }

    assert c.get("/api/_t/missing").status_code == 404

    r = c.get("/api/_t/crash")
    assert r.status_code == 500
    assert r.get_json()["error"]["code"] == "INTERNAL_ERROR"
    assert "boom" not in r.get_data(as_text=True)  # Internal details must never be exposed.


def test_unknown_api_route_is_json(client):
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404
    assert r.get_json()["error"]["code"] == "NOT_FOUND"


def test_wrong_method_is_json(client):
    r = client.post("/api/health")
    assert r.status_code == 405
    assert r.get_json()["error"]["code"] == "METHOD_NOT_ALLOWED"


def test_json_types(app):
    _add_test_routes(app)
    data = app.test_client().get("/api/_t/types").get_json()["data"]
    assert data == {
        "at": "2026-11-20T03:00:00Z",
        "fee": 40,
        "rate": 12.5,
        "name": "Parking Lot A"
    }


def test_pagination_limits(app):
    _add_test_routes(app)
    c = app.test_client()

    assert c.get("/api/_t/page?page=0&per_page=999").get_json()["data"]["per_page"] == 100
    assert c.get("/api/_t/page?page=0").get_json()["data"]["page"] == 1
    assert c.get("/api/_t/page?page=abc").status_code == 400


def test_frontend_index_served(client):
    import os
    import pytest
    from app import STATIC_DIR

    if not os.path.exists(os.path.join(STATIC_DIR, "index.html")):
        pytest.skip("Frontend files have not been copied into static/ yet.")

    r = client.get("/")
    assert r.status_code == 200
    assert "ParkWise" in r.get_data(as_text=True)


@needs_mysql
def test_health_public_is_minimal(client):
    r = client.get("/api/health")
    assert r.status_code == 200

    data = r.get_json()["data"]
    assert data["status"] == "ok" and data["server_time"].endswith("Z")
    assert "mongo" not in data and "outbox" not in data  # Internal details are hidden from anonymous users.


@needs_mysql
def test_health_reports_both_databases_to_admin(api):
    c = api()
    assert c.login("admin@parkwise.local").status_code == 200

    data = c.get("/api/health").get_json()["data"]
    assert data["mysql"] == "ok"
    assert data["mongo"] in ("ok", "down")  # MySQL alone is sufficient for the application to run.
    assert data["server_time"].endswith("Z")
    assert set(data["outbox"]) == {"pending", "failed"}
