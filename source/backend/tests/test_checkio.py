"""Check-in / check-out by staff, payments, and the check-out rollback (spec 8, 10.1)."""
from datetime import timedelta

from core import timeutil
from tests.conftest import data, needs_mysql

pytestmark = needs_mysql


def staff(api):
    s = api()
    s.login("staff1@parkwise.local")
    return s


def row(app, sql, args=()):
    with app.app_context():
        from db.mysql import fetch_one
        return fetch_one(sql, args)


def test_checkin_then_checkout_creates_payment(api, app, fresh_db):
    s = staff(api)
    r = s.post("/api/staff/reservations/3/checkin")          # Confirmed, started 10 min ago
    assert r.status_code == 200, data(r)
    assert data(r)["status"] == "In Use" and data(r)["customer"]["full_name"] == "Anan Mankhong"
    assert s.post("/api/staff/reservations/3/checkin").status_code == 409     # not Confirmed any more

    r = s.post("/api/staff/reservations/3/checkout")
    assert r.status_code == 200, data(r)
    out = data(r)
    assert out["status"] == "Completed" and out["fee"]["base_fee"] == 20 and out["fee"]["overstay_fee"] == 0
    pay = row(app, "SELECT status, total_amount FROM Payments WHERE reservation_id = 3")
    assert pay["status"] == "Pending" and float(pay["total_amount"]) == 20
    events = row(app, "SELECT GROUP_CONCAT(event_type ORDER BY seq) AS e FROM Integration_Outbox WHERE aggregate_id = '3' "
                      "AND status = 'Pending'")["e"]
    assert events == "checkin.done,checkout.done"


def test_overstay_checkout_fee(api, app, fresh_db):
    """Reservation 12: booked -240..-60 min, still parked -> 1h overstay after 15 min grace."""
    s = staff(api)
    out = data(s.post("/api/staff/reservations/12/checkout"))
    assert out["fee"]["base_fee"] == 3 * 20                      # booked 3 hours
    assert out["fee"]["overstay_fee"] == 40                      # 60 min late - 15 grace = 45 min -> 1 hour
    assert out["is_overstay"] is True


def test_checkin_window(api, fresh_db):
    s = staff(api)
    r = s.post("/api/staff/reservations/2/checkin")             # starts in 3 hours
    assert r.status_code == 409 and data(r)["code"] == "CHECKIN_WINDOW"
    with timeutil.freeze(timeutil.now_utc() + timedelta(minutes=170)):
        assert s.post("/api/staff/reservations/2/checkin").status_code == 200   # 10 min before start


def test_cannot_checkin_while_previous_car_overstays(api, fresh_db):
    """Bay B5: reservation 12 is overstaying, reservation 13 starts in 30 min (spec 8.1 rule 4)."""
    s = staff(api)
    with timeutil.freeze(timeutil.now_utc() + timedelta(minutes=20)):
        r = s.post("/api/staff/reservations/13/checkin")
    assert r.status_code == 409 and data(r)["code"] == "SLOT_STILL_OCCUPIED"
    assert data(r)["blocking_reservation_id"] == 12


def test_checkout_rollback_when_step_3_fails(app, fresh_db):
    """Transaction demo scenario 3: error after updating the reservation -> nothing is saved."""
    import pytest
    from services import checkio
    before = row(app, "SELECT status, checked_out_at FROM Reservations WHERE reservation_id = 1")
    with app.app_context():
        with pytest.raises(checkio.SimulatedFailure):
            checkio.checkout({"user_id": 2}, 1, fail_after="reservation")
    after = row(app, "SELECT status, checked_out_at FROM Reservations WHERE reservation_id = 1")
    assert before == after == {"status": "In Use", "checked_out_at": None}
    assert row(app, "SELECT COUNT(*) AS n FROM Payments WHERE reservation_id = 1")["n"] == 0


def test_customers_cannot_use_staff_api(api):
    c = api()
    c.login("somchai@example.com")
    assert c.post("/api/staff/reservations/3/checkin").status_code == 403
    assert c.get("/api/staff/search", query_string={"plate": "1AB"}).status_code == 403
    assert c.get("/api/staff/dashboard").status_code == 403


def test_search_by_plate(api, fresh_db):
    s = staff(api)
    found = data(s.get("/api/staff/search", query_string={"plate": "1ab 12"}))
    assert [r["reservation_id"] for r in found["active"]] == [1]
    assert found["active"][0]["can_checkout"] and not found["active"][0]["can_checkin"]
    anan = data(s.get("/api/staff/search", query_string={"plate": "5jk"}))
    assert anan["active"][0]["reservation_id"] == 3 and anan["active"][0]["can_checkin"]
    assert s.get("/api/staff/search", query_string={"plate": "x"}).status_code == 400


def test_staff_dashboard_and_movements(api, fresh_db):
    s = staff(api)
    d = data(s.get("/api/staff/dashboard"))
    assert d["parked_count"] == 2 and d["overstay_count"] == 1
    assert d["blocked"] and d["blocked"][0]["slot_code"] == "B5"      # reservation 13 waits behind the overstay
    s.post("/api/staff/reservations/3/checkin")
    m = data(s.get("/api/staff/movements"))
    assert m["count_in"] >= 1 and m["items"][0]["kind"] == "in"


def test_pay_simulated(api, app, fresh_db):
    c = api()
    c.login("preecha@example.com")                       # payment for reservation 7 is Pending
    pays = data(c.get("/api/me/payments"))
    pending = [p for p in pays["items"] if p["status"] == "Pending"]
    assert pending and pays["unpaid_total"] > 0
    pid = pending[0]["payment_id"]
    qr = c.get(f"/api/payments/{pid}/qr")
    assert qr.status_code == 200 and qr.mimetype == "image/svg+xml" and b"<svg" in qr.data
    r = c.post(f"/api/payments/{pid}/pay", {"method": "QR"})
    assert data(r)["status"] == "Paid" and data(r)["method"] == "QR"
    assert data(c.post(f"/api/payments/{pid}/pay", {"method": "Cash"}))["code"] == "ALREADY_PAID"
    assert c.post(f"/api/payments/{pid}/pay", {"method": "Bitcoin"}).status_code == 400

    other = api()
    other.login("suda@example.com")
    assert other.post(f"/api/payments/{pid}/pay", {"method": "Cash"}).status_code == 403
    assert other.get(f"/api/payments/{pid}/qr").status_code == 403


def test_parking_page_shows_fee_if_leaving_now(api):
    c = api()
    c.login("somchai@example.com")
    p = data(c.get("/api/me/parking"))
    assert len(p["parked"]) == 2
    overstaying = next(x for x in p["parked"] if x["reservation_id"] == 12)
    assert overstaying["fee_if_leave_now"]["overstay_fee"] >= 40
