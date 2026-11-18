"""Outbox -> MongoDB worker, scheduler jobs, and customer data stored in MongoDB.

MongoDB is replaced by mongomock (in memory), filled by database/seed_mongo.py.
"""
from datetime import timedelta

from core import timeutil
from db import mongo as mongo_mod
from tests.conftest import data, needs_mysql

pytestmark = needs_mysql


def outbox(app, status):
    with app.app_context():
        from db.mysql import fetch_value
        return fetch_value("SELECT COUNT(*) FROM Integration_Outbox WHERE status = %s", (status,))


# ---- worker -------------------------------------------------------------------

def test_worker_sends_pending_events_once(app, fresh_db, mongo, api):
    from services import mongo_sync
    c = api()
    c.login("somchai@example.com")                      # makes a user.login event
    with app.app_context():
        assert mongo_sync.process_pending()["sent"] >= 2
    assert outbox(app, "Pending") == 0
    logs = mongo.Activity_Logs.count_documents({"action_type": "user.login", "userId": 4})
    assert logs >= 2                                    # seed one + ours

    # sending the same event again must not create a duplicate (idempotent upsert)
    with app.app_context():
        from db.mysql import fetch_one
        e = fetch_one("SELECT event_id, event_type, payload, created_at FROM Integration_Outbox "
                      "WHERE event_type = 'user.login' ORDER BY seq DESC LIMIT 1")
    before = mongo.Activity_Logs.count_documents({})
    mongo_sync.apply_event(mongo, e["event_id"], e["event_type"], e["payload"], e["created_at"])
    assert mongo.Activity_Logs.count_documents({}) == before


def test_mongo_down_then_retry(app, fresh_db, mongo):
    """Transaction demo scenario 4: Mongo down -> events wait -> Failed after 5 tries -> Retry -> Sent."""
    from services import mongo_sync
    mongo_mod.set_simulated_down(True)
    with app.app_context():
        for _ in range(mongo_sync.MAX_RETRIES):
            mongo_sync.process_pending()
    assert outbox(app, "Failed") >= 2                   # seed already had 1 Failed
    mongo_mod.set_simulated_down(False)
    with app.app_context():
        assert mongo_sync.retry() >= 2
        result = mongo_sync.process_pending()
    assert result["failed"] == 0 and outbox(app, "Failed") == 0 and outbox(app, "Pending") == 0


def test_checkout_reaches_mongo_session(app, fresh_db, mongo, api):
    from services import mongo_sync
    s = api()
    s.login("staff1@parkwise.local")
    s.post("/api/staff/reservations/3/checkin")
    s.post("/api/staff/reservations/3/checkout")
    with app.app_context():
        mongo_sync.process_pending()
    sess = mongo.Parking_Sessions.find_one({"reservationId": 3})
    assert sess["checkOutAt"] and sess["fees"]["total"] == 20 and sess["slotCode"] == "A3"
    note = mongo.Notifications.find_one({"userId": 6, "type": "checkout_done", "reservationId": 3})
    assert note and "THB 20" in note["message"]


def test_notification_switched_off_is_not_sent(app, fresh_db, mongo, api):
    from services import mongo_sync
    mongo.User_Preferences.update_one({"userId": 6}, {"$set": {"notifications.checkIn": False}})
    before = mongo.Notifications.count_documents({"userId": 6, "type": "checkin_done"})
    s = api()
    s.login("staff1@parkwise.local")
    s.post("/api/staff/reservations/3/checkin")
    with app.app_context():
        mongo_sync.process_pending()
    assert mongo.Notifications.count_documents({"userId": 6, "type": "checkin_done"}) == before
    assert mongo.Activity_Logs.count_documents({"action_type": "checkin.done", "entity_id": 3}) == 1   # still logged


def test_register_creates_default_preferences(app, fresh_db, mongo, client):
    from services import mongo_sync
    r = client.post("/api/auth/register", json={"full_name": "Pref Test", "email": "pref@example.com",
                                                "password": "longenough1"})
    uid = data(r)["user_id"]
    with app.app_context():
        mongo_sync.process_pending()
    assert mongo.User_Preferences.find_one({"userId": uid})["theme"] == "light"


# ---- scheduler jobs ----------------------------------------------------------------

def test_jobs_expire_noshow_overstay(app, fresh_db):
    from services import expiry, overstay
    now = timeutil.now_utc()
    with app.app_context():
        from db.mysql import fetch_value
        last_seq = fetch_value("SELECT MAX(seq) FROM Integration_Outbox")
        assert expiry.expire_holds(now + timedelta(minutes=4)) == 1          # reservation 4
        assert expiry.mark_no_shows(now + timedelta(minutes=21)) == 1        # reservation 3
        assert overstay.send_end_warnings(now + timedelta(minutes=46)) == 1  # reservation 1 ends in 60 min
        assert overstay.detect_overstays(now + timedelta(minutes=61)) == 2   # reservation 12 (seed) + 1 (now late)
        from db.mysql import fetch_all
        rows = {r["reservation_id"]: r for r in fetch_all(
            "SELECT reservation_id, status, is_overstay FROM Reservations WHERE reservation_id IN (1,3,4)")}
        types = [r["event_type"] for r in fetch_all(
            "SELECT event_type FROM Integration_Outbox WHERE seq > %s ORDER BY seq", (last_seq,))]
    assert rows[4]["status"] == "Expired" and rows[3]["status"] == "No-show" and rows[1]["is_overstay"] == 1
    assert types == ["reservation.expired", "reservation.no_show", "overstay.warning",
                     "overstay.detected", "overstay.detected"]


def test_run_all_now(app, mongo):
    from jobs import scheduler
    with app.app_context():
        result = scheduler.run_all_now()
    assert set(result) >= {"expired", "no_show", "warned", "overstay", "outbox"}


# ---- customer MongoDB pages --------------------------------------------------------

def test_notifications_read_delete(api, mongo):
    c = api()
    c.login("somchai@example.com")
    n = data(c.get("/api/me/notifications"))
    assert n["total"] > 0 and n["unread_count"] > 0
    first = n["items"][0]["id"]
    assert c.post(f"/api/me/notifications/{first}/read").status_code == 200
    assert data(c.post("/api/me/notifications/read-all"))["updated"] >= 0
    assert data(c.get("/api/me/notifications"))["unread_count"] == 0
    assert c.delete(f"/api/me/notifications/{first}").status_code == 200
    assert c.delete(f"/api/me/notifications/{first}").status_code == 404
    assert c.delete("/api/me/notifications/not-an-id").status_code == 404

    other = api()
    other.login("suda@example.com")
    someone_elses = str(mongo.Notifications.find_one({"userId": 4})["_id"])
    assert other.post(f"/api/me/notifications/{someone_elses}/read").status_code == 404


def test_preferences(api, fresh_db, mongo):
    c = api()
    c.login("somchai@example.com")
    p = data(c.get("/api/me/preferences"))
    assert p["theme"] == "dark" and p["defaultVehicleId"] == 1
    p = data(c.c.put("/api/me/preferences", json={"theme": "light", "notifications": {"overstay": False},
                                                   "defaultVehicleId": 2}, headers=c._h()))
    assert p["theme"] == "light" and p["notifications"]["overstay"] is False and p["notifications"]["checkIn"] is True
    assert p["defaultVehicleId"] == 2
    assert data(c.get("/api/me/vehicles"))[0]["vehicle_id"] == 2        # MySQL main car changed too
    assert c.c.put("/api/me/preferences", json={"defaultVehicleId": 3}, headers=c._h()).status_code == 403
    p = data(c.delete("/api/me/preferences"))
    assert p["theme"] == "light" and p["notifications"]["overstay"] is True
    assert data(c.get("/api/auth/me"))["preferences"]["theme"] == "light"


def test_sessions_history(api, mongo):
    c = api()
    c.login("somchai@example.com")
    h = data(c.get("/api/me/sessions"))
    assert h["total"] >= 1 and all("plate_number" in s for s in h["items"])


def test_mongo_pages_fail_gracefully(api, mongo):
    c = api()
    c.login("somchai@example.com")
    mongo_mod.set_simulated_down(True)
    r = c.get("/api/me/notifications")
    assert r.status_code == 503 and data(r)["code"] == "MONGO_DOWN"
    assert c.get("/api/auth/me").status_code == 200                  # login still works
    assert c.get("/api/me/reservations").status_code == 200          # MySQL pages still work
