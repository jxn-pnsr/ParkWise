from tests.conftest import data, needs_mysql

pytestmark = [needs_mysql]


def test_vehicle_crud(api, fresh_db):
    a = api()
    a.login("preecha@example.com")  # User 8, one default car (8QR1111)
    cars = data(a.get("/api/me/vehicles"))
    assert [c["plate_number"] for c in cars] == ["8QR1111"] and cars[0]["is_default"]

    r = a.post("/api/me/vehicles", {"plate_number": " 1ab   2345 ", "province": "Bangkok"})
    assert r.status_code == 201
    new = data(r)
    assert new["plate_number"] == "1AB 2345" and not new["is_default"]

    r = a.post("/api/me/vehicles", {"plate_number": "1AB 2345", "province": "Bangkok"})
    assert r.status_code == 409 and data(r)["code"] == "PLATE_TAKEN"

    cars = data(a.post(f"/api/me/vehicles/{new['vehicle_id']}/default"))
    assert [c["vehicle_id"] for c in cars if c["is_default"]] == [new["vehicle_id"]]

    r = a.patch(f"/api/me/vehicles/{new['vehicle_id']}", {"province": "Nonthaburi"})
    assert data(r)["province"] == "Nonthaburi"

    assert a.delete(f"/api/me/vehicles/{new['vehicle_id']}").status_code == 200
    cars = data(a.get("/api/me/vehicles"))
    assert [c["plate_number"] for c in cars] == ["8QR1111"] and cars[0]["is_default"]  # Default restored.

    # A deleted plate can be registered again.
    assert a.post("/api/me/vehicles", {"plate_number": "1AB 2345", "province": "Nonthaburi"}).status_code == 201


def test_cannot_delete_vehicle_with_active_booking(api):
    a = api()
    a.login("somchai@example.com")  # Vehicle 1 has an active reservation.
    r = a.delete("/api/me/vehicles/1")
    assert r.status_code == 409 and data(r)["code"] == "HAS_ACTIVE_RESERVATIONS"


def test_cannot_touch_other_customers_vehicle(api):
    a = api()
    a.login("somchai@example.com")
    assert a.patch("/api/me/vehicles/3", {"province": "X"}).status_code == 403  # User 5's vehicle.
    assert a.delete("/api/me/vehicles/3").status_code == 403
    assert a.post("/api/me/vehicles/3/default").status_code == 403