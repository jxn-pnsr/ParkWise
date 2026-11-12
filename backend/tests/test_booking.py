"""Tests bay status, booking rules and booking conflicts."""
import threading
from datetime import timedelta

from core import timeutil
from tests.conftest import data, needs_mysql

pytestmark = needs_mysql

SLOT_A5, SLOT_A9, SLOT_A10, SLOT_B10 = 5, 9, 10, 20


def bkk_iso(dt_utc):
    return timeutil.to_bkk(dt_utc).isoformat()


def next_half_hour(minutes_ahead):
    t = timeutil.now_utc() + timedelta(minutes=minutes_ahead)
    return t.replace(second=0, microsecond=0) + timedelta(minutes=(30 - t.minute % 30) % 30)


def instant(a, slot, vehicle, hours=1):
    return a.post("/api/reservations", {"booking_type": "Instant", "slot_id": slot, "vehicle_id": vehicle,
                                        "duration_hours": hours})


def advance(a, slot, vehicle, start_utc, hours=2):
    return a.post("/api/reservations", {"booking_type": "Advance", "slot_id": slot, "vehicle_id": vehicle,
                                        "duration_hours": hours, "start_time": bkk_iso(start_utc)})


# Bay status

def test_bay_colours_now(api, fresh_db):
    a = api()
    a.login("somchai@example.com")
    lot_a = data(a.get("/api/lots/1/slots"))
    colour = {s["slot_code"]: s["display_status"] for s in lot_a["slots"]}
    assert colour["A1"] == "Occupied"       # Reservation 1 is in use.
    assert colour["A3"] == "Reserved"       # Reservation 3 is confirmed.
    assert colour["A4"] == "Held"           # Reservation 4 is still on hold.
    assert colour["A2"] == "Available"      # Reservation 2 starts in 3 hours.
    assert colour["A5"] == "Available"
    assert "plate_number" not in lot_a["slots"][0]  # Customers cannot see plates.
    assert next(s for s in lot_a["slots"] if s["slot_code"] == "A1")["slot_type"] == "EV"
    lot_b = data(a.get("/api/lots/2/slots"))
    colour_b = {s["slot_code"]: s["display_status"] for s in lot_b["slots"]}
    assert colour_b["B10"] == "Maintenance" and colour_b["B5"] == "Occupied"  # B5 is overdue.


def test_bay_colours_future_window(api):
    a = api()
    a.login("somchai@example.com")
    start = timeutil.now_utc() + timedelta(hours=3, minutes=30)
    r = a.get("/api/lots/1/slots", query_string={"start": bkk_iso(start), "duration_hours": 1})
    colour = {s["slot_code"]: s["display_status"] for s in data(r)["slots"]}
    assert colour["A2"] == "Reserved"       # Reservation 2 overlaps this time.
    assert colour["A1"] == "Available"      # Reservation 1 ends in 1 hour.


def test_staff_sees_plates(api):
    a = api()
    a.login("staff1@parkwise.local")
    a1 = next(s for s in data(a.get("/api/lots/1/slots"))["slots"] if s["slot_code"] == "A1")
    assert a1["plate_number"] == "1AB1234"


def test_lot_list_counts(api):
    a = api()
    a.login("somchai@example.com")
    lots = data(a.get("/api/lots"))["items"]
    assert [l["lot_name"] for l in lots] == ["Lot A", "Lot B"]
    assert lots[0]["total_slots"] == 10 and sum(lots[0]["counts"].values()) == 10


# Normal booking flow

def test_instant_hold_confirm_cancel(api, fresh_db):
    a = api()
    a.login("preecha@example.com")  # User 8, vehicle 8, no active bookings.
    r = instant(a, SLOT_A5, 8, hours=2)
    assert r.status_code == 201, data(r)
    res = data(r)
    assert res["status"] == "Held" and res["hold_expires_at"] and res["estimated_fee"] == 40
    assert res["slot"]["slot_code"] == "A5"

    colour = {s["slot_code"]: s["display_status"] for s in data(a.get("/api/lots/1/slots"))["slots"]}
    assert colour["A5"] == "Held"

    r = a.post(f"/api/reservations/{res['reservation_id']}/confirm")
    assert r.status_code == 200 and data(r)["status"] == "Confirmed"
    colour = {s["slot_code"]: s["display_status"] for s in data(a.get("/api/lots/1/slots"))["slots"]}
    assert colour["A5"] == "Reserved"

    detail = data(a.get(f"/api/me/reservations/{res['reservation_id']}"))
    assert detail["can_cancel"] and not detail["can_confirm"]
    r = a.post(f"/api/reservations/{res['reservation_id']}/cancel", {"reason": "Plans changed"})
    assert data(r)["status"] == "Cancelled"
    assert a.post(f"/api/reservations/{res['reservation_id']}/confirm").status_code == 409  # Final status.


def test_advance_booking_and_outbox_events(api, app, fresh_db):
    a = api()
    a.login("preecha@example.com")
    start = next_half_hour(24 * 60)
    res = data(advance(a, SLOT_A9, 8, start, hours=3))
    assert res["booking_type"] == "Advance"
    assert res["end_time"] == timeutil.to_iso(start + timedelta(hours=3))
    data(a.post(f"/api/reservations/{res['reservation_id']}/confirm"))
    with app.app_context():
        from db.mysql import fetch_all
        rows = fetch_all("SELECT event_type, JSON_EXTRACT(payload, '$.notify.type') AS n FROM Integration_Outbox "
                         "WHERE aggregate_id = %s ORDER BY seq", (str(res["reservation_id"]),))
    assert [r["event_type"] for r in rows] == ["reservation.held", "reservation.confirmed"]
    assert rows[1]["n"] == '"reservation_confirmed"'


# Booking rules

def test_slot_taken_suggests_alternatives(api, fresh_db):
    a, b = api(), api()
    a.login("preecha@example.com")
    b.login("suda@example.com")  # User 5, vehicle 3.
    assert instant(a, SLOT_A5, 8).status_code == 201
    r = instant(b, SLOT_A5, 3)
    assert r.status_code == 409 and data(r)["code"] == "SLOT_TAKEN"
    alts = [s["slot_code"] for s in data(r)["alternatives"]]
    assert alts and "A5" not in alts and "A1" not in alts and "A3" not in alts


def test_maintenance_bay_refused(api):
    a = api()
    a.login("preecha@example.com")
    r = instant(a, SLOT_B10, 8)
    assert r.status_code == 409 and data(r)["code"] == "SLOT_MAINTENANCE"


def test_vehicle_must_be_mine(api):
    a = api()
    a.login("preecha@example.com")
    r = instant(a, SLOT_A9, 1)  # Somchai's vehicle.
    assert r.status_code == 400 and "vehicle_id" in data(r)["fields"]


def test_same_car_cannot_be_in_two_places(api, fresh_db):
    a = api()
    a.login("preecha@example.com")
    assert instant(a, SLOT_A9, 8).status_code == 201
    r = instant(a, SLOT_A10, 8)
    assert r.status_code == 409 and data(r)["code"] == "VEHICLE_OVERLAP"


def test_quota_three_active(api, fresh_db):
    a = api()
    a.login("somchai@example.com")  # Already has 2 active bookings.
    start = next_half_hour(48 * 60)
    assert advance(a, SLOT_A9, 1, start).status_code == 201  # 3rd booking.
    r = advance(a, SLOT_A10, 2, start + timedelta(days=1))
    assert r.status_code == 409 and data(r)["code"] == "QUOTA_EXCEEDED"


def test_time_rules(api):
    a = api()
    a.login("preecha@example.com")
    past = next_half_hour(-120)
    assert "start_time" in data(advance(a, SLOT_A9, 8, past))["fields"]
    too_far = next_half_hour(8 * 24 * 60)
    assert "start_time" in data(advance(a, SLOT_A9, 8, too_far))["fields"]
    odd = next_half_hour(24 * 60) + timedelta(minutes=10)
    assert "start_time" in data(advance(a, SLOT_A9, 8, odd))["fields"]
    assert "duration_hours" in data(instant(a, SLOT_A9, 8, hours=13))["fields"]
    r = a.post("/api/reservations", {"booking_type": "Advance", "slot_id": SLOT_A9, "vehicle_id": 8,
                                     "duration_hours": 1, "start_time": bkk_iso(next_half_hour(600)),
                                     "end_time": "2030-01-01T00:00:00Z"})
    assert "end_time" in data(r)["fields"]  # The client cannot set end_time.


def test_expired_hold_cannot_be_confirmed(api, app, fresh_db):
    a = api()
    a.login("malee@example.com")  # Owns reservation 4.
    with timeutil.freeze(timeutil.now_utc() + timedelta(minutes=4)):
        r = a.post("/api/reservations/4/confirm")
    assert r.status_code == 409 and data(r)["code"] == "HOLD_EXPIRED"
    with app.app_context():
        from db.mysql import fetch_value
        assert fetch_value("SELECT status FROM Reservations WHERE reservation_id = 4") == "Expired"


def test_expired_hold_frees_the_bay_without_scheduler(api, fresh_db):
    a = api()
    a.login("preecha@example.com")
    with timeutil.freeze(timeutil.now_utc() + timedelta(minutes=4)):
        r = instant(a, 4, 8)  # Reservation 4 held bay A4.
    assert r.status_code == 201


def test_cancel_window_advance(api, fresh_db):
    a = api()
    a.login("preecha@example.com")
    start = next_half_hour(90)
    res = data(advance(a, SLOT_A9, 8, start, hours=1))
    data(a.post(f"/api/reservations/{res['reservation_id']}/confirm"))
    with timeutil.freeze(start - timedelta(minutes=59)):
        r = a.post(f"/api/reservations/{res['reservation_id']}/cancel")
    assert r.status_code == 409 and data(r)["code"] == "CANCEL_WINDOW_PASSED"
    assert a.post(f"/api/reservations/{res['reservation_id']}/cancel").status_code == 200


def test_cancel_window_instant(api, fresh_db):
    a = api()
    a.login("preecha@example.com")
    res = data(instant(a, SLOT_A9, 8))
    data(a.post(f"/api/reservations/{res['reservation_id']}/confirm"))
    with timeutil.freeze(timeutil.now_utc() + timedelta(minutes=11)):
        r = a.post(f"/api/reservations/{res['reservation_id']}/cancel")
    assert data(r)["code"] == "CANCEL_WINDOW_PASSED"


def test_lazy_no_show(api, fresh_db):
    """Reservation 3 becomes No-show after the allowed time, without the scheduler."""
    a = api()
    a.login("anan@example.com")
    with timeutil.freeze(timeutil.now_utc() + timedelta(minutes=21)):
        items = data(a.get("/api/me/reservations", query_string={"scope": "past"}))["items"]
    assert any(i["reservation_id"] == 3 and i["status"] == "No-show" for i in items)


# Concurrent booking test

def test_two_customers_same_bay_same_moment(app, fresh_db):
    """Only one of 10 parallel booking attempts can succeed."""
    from tests.conftest import ApiClient
    users = [("preecha@example.com", 8), ("anan@example.com", 5), ("suda@example.com", 3),
             ("malee@example.com", 7)] * 3
    clients = []
    for email, vid in users[:10]:
        c = ApiClient(app)
        c.login(email)
        clients.append((c, vid))
    results, barrier = [], threading.Barrier(len(clients))

    def go(c, vid):
        barrier.wait()
        r = instant(c, SLOT_A10, vid)
        results.append((r.status_code, (r.get_json().get("error") or {}).get("code")))

    threads = [threading.Thread(target=go, args=cv) for cv in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wins = [r for r in results if r[0] == 201]
    assert len(wins) == 1, results
    bad = [r for r in results if r[0] != 201 and r[1] not in ("SLOT_TAKEN", "VEHICLE_OVERLAP", "QUOTA_EXCEEDED")]
    assert not bad, bad
    with app.app_context():
        from db.mysql import fetch_value
        assert fetch_value("SELECT COUNT(*) FROM Reservations WHERE slot_id = %s AND status = 'Held'", (SLOT_A10,)) == 1


def test_dashboard(api):
    a = api()
    a.login("somchai@example.com")
    d = data(a.get("/api/me/dashboard"))
    assert d["total_bays"] == 20 and 0 <= d["open_bays"] <= 20
    assert d["upcoming"] == 2 and d["default_vehicle"]["plate_number"] == "1AB1234"
    assert {"spent_month", "spent_change_pct", "lots"} <= set(d)


def test_public_config(client):
    cfg = data(client.get("/api/config/public"))
    assert cfg["HOLD_MINUTES"] == 5 and cfg["RATE_PER_HOUR"] == 20