"""Tests login, registration, lockout, CSRF, roles and access rules."""
from tests.conftest import PASSWORD, data, needs_mysql

pytestmark = needs_mysql


def test_login_me_logout(api):
    a = api()
    r = a.login("somchai@example.com")
    assert r.status_code == 200
    me = data(r)
    assert me["role"] == "Customer" and me["csrf_token"]
    assert "password_hash" not in me
    assert me["counts"]["active_reservations"] == 2  # Reservations 1 and 12 are in use.
    assert data(a.get("/api/auth/me"))["email"] == "somchai@example.com"
    assert a.post("/api/auth/logout").status_code == 200
    assert a.get("/api/auth/me").status_code == 401


def test_wrong_password_message_is_generic(api):
    a = api()
    r1 = a.login("somchai@example.com", "wrong-password")
    r2 = a.login("nobody@example.com", "wrong-password")
    assert r1.status_code == r2.status_code == 401
    assert data(r1)["message"] == data(r2)["message"]  # Does not reveal if an email exists.


def test_lockout_after_five_failures(api, fresh_db):
    a = api()
    for _ in range(4):
        assert a.login("suda@example.com", "nope").status_code == 401
    r = a.login("suda@example.com", "nope")  # 5th failure locks the account.
    assert r.status_code == 423 and data(r)["code"] == "ACCOUNT_LOCKED"
    assert data(r)["locked_until"].endswith("Z")
    r = a.login("suda@example.com", PASSWORD)  # Correct password is also refused.
    assert r.status_code == 423


def test_register_always_customer(api):
    a = api()
    r = a.c.post("/api/auth/register", json={
        "full_name": "New Person", "email": "New.Person@Example.com", "phone": "0812345678",
        "password": "longenough1", "role": "Admin"})  # Requested role is ignored.
    assert r.status_code == 201
    assert data(r)["role"] == "Customer" and data(r)["email"] == "new.person@example.com"
    r = a.c.post("/api/auth/register", json={
        "full_name": "Again", "email": "new.person@example.com", "password": "longenough1"})
    assert r.status_code == 409 and data(r)["code"] == "EMAIL_TAKEN"
    assert a.login("new.person@example.com", "longenough1").status_code == 200


def test_register_validation(client):
    r = client.post("/api/auth/register", json={"full_name": "", "email": "bad", "phone": "12", "password": "short"})
    assert r.status_code == 400
    assert set(data(r)["fields"]) == {"full_name", "email", "phone", "password"}


def test_csrf_required_for_changes(api):
    a = api()
    a.login("somchai@example.com")
    r = a.c.post("/api/me/vehicles", json={"plate_number": "9ZZ9999", "province": "Bangkok"})  # Missing token.
    assert r.status_code == 403 and data(r)["code"] == "CSRF_INVALID"
    r = a.c.post("/api/me/vehicles", json={"plate_number": "9ZZ9999", "province": "Bangkok"},
                 headers={"X-CSRF-Token": "forged"})
    assert r.status_code == 403


def test_not_logged_in_is_401(client):
    for url in ("/api/me/vehicles", "/api/me/reservations", "/api/lots", "/api/auth/me"):
        assert client.get(url).status_code == 401


def test_staff_cannot_use_customer_endpoints(api):
    a = api()
    a.login("staff1@parkwise.local")
    assert a.get("/api/me/vehicles").status_code == 403
    assert a.post("/api/reservations", {"booking_type": "Instant", "slot_id": 5, "vehicle_id": 1,
                                        "duration_hours": 1}).status_code == 403


def test_customer_cannot_open_someone_elses_reservation(api):
    a = api()
    a.login("somchai@example.com")  # User 4.
    assert a.get("/api/me/reservations/1").status_code == 200  # Own reservation.
    r = a.get("/api/me/reservations/2")  # Belongs to user 5.
    assert r.status_code == 403
    assert a.post("/api/reservations/4/confirm").status_code == 403  # User 7's hold.
    assert a.post("/api/reservations/2/cancel").status_code == 403


def test_deactivated_user_loses_access(api, app, fresh_db):
    a = api()
    a.login("anan@example.com")
    assert a.get("/api/auth/me").status_code == 200
    with app.app_context():
        from db.mysql import transaction
        with transaction() as cur:
            cur.execute("UPDATE Users SET is_active = 0 WHERE email = 'anan@example.com'")
    assert a.get("/api/auth/me").status_code == 401
    r = api().login("anan@example.com")
    assert r.status_code == 403 and data(r)["code"] == "ACCOUNT_INACTIVE"


def test_profile_and_password(api, fresh_db):
    a = api()
    a.login("malee@example.com")
    r = a.patch("/api/me/profile", {"full_name": "Malee S.", "phone": "081-234-5678"})
    assert data(r)["full_name"] == "Malee S." and data(r)["phone"] == "0812345678"
    r = a.post("/api/me/password", {"current_password": "wrong", "new_password": "NewPassw0rd!"})
    assert r.status_code == 400 and "current_password" in data(r)["fields"]
    assert a.post("/api/me/password", {"current_password": PASSWORD, "new_password": "NewPassw0rd!"}).status_code == 200
    assert api().login("malee@example.com", "NewPassw0rd!").status_code == 200


def test_login_events_go_to_outbox(api, app):
    a = api()
    a.login("preecha@example.com")
    a.login("preecha@example.com", "bad")
    with app.app_context():
        from db.mysql import fetch_all
        types = [r["event_type"] for r in fetch_all(
            "SELECT event_type FROM Integration_Outbox WHERE status='Pending' ORDER BY seq DESC LIMIT 2")]
    assert types == ["user.login_failed", "user.login"]