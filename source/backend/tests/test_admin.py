from tests.conftest import data, needs_mysql

pytestmark = needs_mysql


def admin(api):
    a = api()
    a.login("admin@parkwise.local")
    return a


def put(a, url, body):
    return a.c.put(url, json=body, headers=a._h())


def test_only_admins(api):
    for email in ("somchai@example.com", "staff1@parkwise.local"):
        c = api()
        c.login(email)
        for url in ("/api/admin/stats", "/api/admin/users", "/api/admin/logs", "/api/admin/outbox", "/api/admin/demo"):
            assert c.get(url).status_code == 403, (email, url)
        assert c.post("/api/admin/users", {"email": "x@y.zz"}).status_code == 403


def test_users_crud(api, fresh_db):
    a = admin(api)
    r = a.post("/api/admin/users", {"email": "newstaff@parkwise.local", "password": "Staff12345",
                                    "full_name": "New Staff", "role": "Staff"})
    assert r.status_code == 201
    uid = data(r)["user_id"]
    assert data(a.get("/api/admin/users", query_string={"role": "Staff"}))["total"] == 3
    assert data(a.patch(f"/api/admin/users/{uid}", {"phone": "021234567", "role": "Admin"}))["role"] == "Admin"
    assert data(a.delete(f"/api/admin/users/{uid}"))["is_active"] is False
    assert api().login("newstaff@parkwise.local", "Staff12345").status_code == 403      # deactivated
    r = a.patch("/api/admin/users/1", {"role": "Customer"})                               # myself
    assert r.status_code == 409 and data(r)["code"] == "SELF_LOCKOUT"
    assert a.post("/api/admin/users", {"email": "admin@parkwise.local", "password": "Passw0rd!x",
                                       "full_name": "Dup", "role": "Admin"}).status_code == 409


def test_unlock(api, fresh_db):
    for _ in range(5):
        api().login("suda@example.com", "nope")
    a = admin(api)
    assert data(a.get("/api/admin/users/5"))["is_locked"] is True
    assert data(a.post("/api/admin/users/5/unlock"))["is_locked"] is False
    assert api().login("suda@example.com").status_code == 200


def test_lots_and_slots(api, fresh_db):
    a = admin(api)
    lot = data(a.post("/api/admin/lots", {"lot_name": "Lot C", "location_desc": "Roof"}))
    s1 = data(a.post("/api/admin/slots", {"lot_id": lot["lot_id"], "slot_code": "c1", "slot_type": "EV"}))
    assert s1["slot_code"] == "C1" and s1["slot_type"] == "EV"
    assert a.post("/api/admin/slots", {"lot_id": lot["lot_id"], "slot_code": "C1"}).status_code == 409
    assert data(a.patch(f"/api/admin/slots/{s1['slot_id']}", {"slot_status": "Maintenance"}))["slot_status"] == "Maintenance"
    assert data(a.delete(f"/api/admin/slots/{s1['slot_id']}"))["delete"] == "hard"     # no booking history
    assert a.delete(f"/api/admin/lots/{lot['lot_id']}").status_code == 200
    r = a.patch("/api/admin/slots/1", {"slot_status": "Maintenance"})
    assert r.status_code == 409 and data(r)["code"] == "HAS_ACTIVE_RESERVATIONS" and data(r)["reservations"]
    assert a.delete("/api/admin/slots/1").status_code == 409
    assert data(a.delete("/api/admin/slots/6"))["delete"] == "soft"
    assert data(a.delete("/api/admin/lots/1"))["code"] == "LOT_IN_USE"
    assert data(a.patch("/api/admin/lots/1", {"is_active": False}))["code"] == "HAS_ACTIVE_RESERVATIONS"
    lots = data(a.get("/api/admin/lots"))
    assert {l["lot_name"] for l in lots} == {"Lot A", "Lot B"}
    assert any(s["display_status"] == "Occupied" for s in data(a.get("/api/admin/lots/1/slots")))


def test_reservations_admin(api, fresh_db):
    a = admin(api)
    page = data(a.get("/api/admin/reservations", query_string={"per_page": 50}))
    assert page["total"] == 14 and page["items"][0]["customer"]
    assert data(a.get("/api/admin/reservations", query_string={"q": "1AB1234"}))["total"] >= 2
    assert data(a.get("/api/admin/reservations", query_string={"overstay": 1}))["total"] >= 1

    r = a.patch("/api/admin/reservations/13", {"slot_id": 16, "admin_reason": "B5 still occupied"})
    assert r.status_code == 200 and data(r)["slot"]["slot_code"] == "B6"
    assert a.patch("/api/admin/reservations/13", {"slot_id": 1, "admin_reason": "x"}).status_code == 409 
    assert a.patch("/api/admin/reservations/13", {"slot_id": 16}).status_code == 400                   

    assert data(a.post("/api/admin/reservations/2/cancel", {"reason": "Lot closed for an event"}))["status"] == "Cancelled"
    assert a.delete("/api/admin/reservations/2").status_code == 200        
    assert data(a.delete("/api/admin/reservations/1"))["code"] == "INVALID_STATE"  
    r = a.patch("/api/admin/reservations/9", {"status": "No-show", "admin_reason": "Customer called"})
    assert data(r)["status"] == "No-show"


def test_payments_admin(api, fresh_db):
    a = admin(api)
    items = data(a.get("/api/admin/payments"))["items"]
    pid = next(p["payment_id"] for p in items if p["status"] == "Pending")
    assert data(a.patch(f"/api/admin/payments/{pid}", {"status": "Failed"}))["status"] == "Failed"
    assert data(a.patch(f"/api/admin/payments/{pid}", {"status": "Paid", "method": "Cash"}))["paid_at"]
    assert a.delete(f"/api/admin/payments/{pid}").status_code == 200
    assert all(p["payment_id"] != pid for p in data(a.get("/api/admin/payments"))["items"])
    s = api()
    s.login("staff1@parkwise.local")
    s.post("/api/staff/reservations/3/checkin")
    s.post("/api/staff/reservations/3/checkout")
    with_payment = data(a.post("/api/admin/payments", {"reservation_id": 3}))
    assert with_payment["code"] == "HAS_PAYMENT"
    assert data(a.post("/api/admin/payments", {"reservation_id": 1}))["code"] == "INVALID_STATE"


def test_config_changes_rules(api, fresh_db, client):
    a = admin(api)
    cfg = {c["config_key"]: c["config_value"] for c in data(a.get("/api/admin/config"))}
    assert cfg["RATE_PER_HOUR"] == "20"
    put(a, "/api/admin/config/RATE_PER_HOUR", {"value": 25})
    assert data(client.get("/api/config/public"))["RATE_PER_HOUR"] == 25
    assert put(a, "/api/admin/config/HOLD_MINUTES", {"value": 0}).status_code == 400
    assert put(a, "/api/admin/config/NOPE", {"value": 1}).status_code == 404


def test_stats(api, mongo):
    d = data(admin(api).get("/api/admin/stats"))
    assert len(d["daily_occupancy"]) == 7 and d["users"]["customers"] == 5
    assert d["mongo"] == "ok" and d["outbox"]["Failed"] >= 1 and d["recent"]


def test_outbox_admin(api, fresh_db, mongo):
    a = admin(api)
    ob = data(a.get("/api/admin/outbox", query_string={"status": "Failed"}))
    assert ob["total"] == 1 and isinstance(ob["items"][0]["payload"], dict)
    eid = ob["items"][0]["event_id"]
    r = data(a.post(f"/api/admin/outbox/{eid}/retry"))
    assert r["retried"] == 1 and r["sent"] >= 1
    assert data(a.get("/api/admin/outbox"))["counts"].get("Failed", 0) == 0
    assert a.delete(f"/api/admin/outbox/{eid}").status_code == 200      
# ---- transaction demo ------------------------------------------------------------------

def test_demo_checkout_success(api, fresh_db):
    d = data(admin(api).post("/api/admin/demo/checkout-success", {"reservation_id": 1}))
    assert d["before"]["reservation"]["status"] == "In Use" and d["before"]["payment"] is None
    assert d["after"]["reservation"]["status"] == "Completed" and d["after"]["payment"]["status"] == "Pending"


def test_demo_midway_rollback(api, fresh_db):
    d = data(admin(api).post("/api/admin/demo/midway-rollback", {"reservation_id": 1}))
    assert d["result"] == "ROLLBACK" and d["unchanged"] is True and "Simulated" in d["error"]


def test_demo_double_booking(api, fresh_db):
    d = data(admin(api).post("/api/admin/demo/double-booking", {"slot_id": 9, "attempts": 4}))
    held = [x for x in d["attempts"] if x["outcome"] == "HELD"]
    assert len(held) == 1 and d["new_rows"] == 1
    assert {x["code"] for x in d["attempts"] if x["outcome"] == "REJECTED"} <= {"SLOT_TAKEN", "VEHICLE_OVERLAP", "QUOTA_EXCEEDED"}


def test_demo_mongo_down_and_retry(api, fresh_db, mongo):
    a = admin(api)
    assert data(a.post("/api/admin/demo/mongo-down", {"enabled": True}))["simulated_down"] is True
    a.post("/api/admin/demo/checkout-success", {"reservation_id": 1})          # creates events
    from services import mongo_sync
    for _ in range(mongo_sync.MAX_RETRIES):
        a.post("/api/admin/outbox/process")
    state = data(a.get("/api/admin/demo"))
    assert state["outbox"]["Failed"] >= 1 and state["mongo_ok"] is False
    assert mongo.Parking_Sessions.find_one({"reservationId": 1, "checkOutAt": {"$ne": None}}) is None
    a.post("/api/admin/demo/mongo-down", {"enabled": False})
    d = data(a.post("/api/admin/demo/mongo-retry"))
    assert d["outbox"]["Failed"] == 0 and d["outbox"]["Pending"] == 0
    assert mongo.Parking_Sessions.count_documents({"reservationId": 1}) == 1   
    assert mongo.Parking_Sessions.find_one({"reservationId": 1})["checkOutAt"]


def test_demo_run_jobs(api, mongo):
    assert "outbox" in data(admin(api).post("/api/admin/demo/run-jobs"))
# ---- MongoDB collections ----------------------------------------------------------------

def test_sessions_crud(api, mongo):
    a = admin(api)
    s = data(a.post("/api/admin/sessions", {"userId": 4, "plateNumber": "1AB1234", "lotName": "Lot A", "slotCode": "A9",
                                           "checkInAt": "2026-11-01T09:00:00+07:00", "checkOutAt": "2026-11-01T11:00:00+07:00",
                                           "fees": {"base": 40, "overstay": 0}}))
    assert s["durationMinutes"] == 120 and s["fees"]["total"] == 40
    assert data(a.patch(f"/api/admin/sessions/{s['id']}", {"slotCode": "A10"}))["slotCode"] == "A10"
    assert a.delete(f"/api/admin/sessions/{s['id']}").status_code == 200
    assert a.delete(f"/api/admin/sessions/{s['id']}").status_code == 404
    assert data(a.get("/api/admin/sessions", query_string={"q": "1ab"}))["total"] >= 1


def test_announcement_and_notifications(api, mongo):
    a = admin(api)
    assert data(a.post("/api/admin/notifications", {"title": "Lot B closed Sunday", "message": "Resurfacing work."}))["sent"] == 5
    assert data(a.post("/api/admin/notifications", {"title": "Hi", "message": "Just you", "user_id": 4}))["sent"] == 1
    c = api()
    c.login("suda@example.com")
    assert any(n["title"] == "Lot B closed Sunday" for n in data(c.get("/api/me/notifications"))["items"])
    nid = data(a.get("/api/admin/notifications", query_string={"q": "Just you"}))["items"][0]["id"]
    assert a.delete(f"/api/admin/notifications/{nid}").status_code == 200


def test_notification_edit(api, mongo, app):
    a = admin(api)
    a.post("/api/admin/notifications", {"title": "Old title", "message": "Old text", "user_id": 4})
    n = data(a.get("/api/admin/notifications", query_string={"q": "Old title"}))["items"][0]
    r = a.patch(f"/api/admin/notifications/{n['id']}", {"title": "New title", "message": "New text", "isRead": True})
    assert r.status_code == 200
    out = data(r)
    assert out["title"] == "New title" and out["message"] == "New text" and out["isRead"] is True and out["readAt"]
    assert data(a.patch(f"/api/admin/notifications/{n['id']}", {"isRead": False}))["readAt"] is None
    assert a.patch(f"/api/admin/notifications/{n['id']}", {"title": ""}).status_code == 400
    assert a.patch(f"/api/admin/notifications/{n['id']}", {"userId": 9}).status_code == 400
    assert a.patch(f"/api/admin/notifications/{n['id']}", {}).status_code == 400
    assert a.patch("/api/admin/notifications/not-an-id", {"title": "x"}).status_code == 404
    c = api()
    c.login("somchai@example.com")
    assert any(x["title"] == "New title" for x in data(c.get("/api/me/notifications"))["items"])
    assert c.patch(f"/api/admin/notifications/{n['id']}", {"title": "x"}).status_code == 403


def test_manual_log_entry(api, mongo):
    a = admin(api)
    r = a.post("/api/admin/logs", {"action_type": "lot-b.inspection", "message": "Checked the barrier",
                                   "entity_type": "lot", "entity_id": "2"})
    assert r.status_code == 201
    log = data(r)
    assert log["action_type"] == "manual.lot-b.inspection" and log["role"] == "Admin"
    assert log["entity_type"] == "lot" and log["entity_id"] == 2 and log["metadata"]["message"] == "Checked the barrier"
    assert log["is_archived"] is False and log["userId"] == 1
    assert any(l["id"] == log["id"] for l in data(a.get("/api/admin/logs", query_string={"action_type": "manual."}))["items"])
    assert "manual.lot-b.inspection" in data(a.get("/api/admin/logs/action-types"))
    assert data(a.post("/api/admin/logs", {"action_type": "manual.note", "message": "Hi"}))["action_type"] == "manual.note"
    assert a.post("/api/admin/logs", {"message": "No action"}).status_code == 400
    assert a.post("/api/admin/logs", {"action_type": "bad action!", "message": "x"}).status_code == 400
    assert a.post("/api/admin/logs", {"action_type": "ok", "message": ""}).status_code == 400
    assert a.post("/api/admin/logs", {"action_type": "ok", "message": "x", "entity_id": True}).status_code == 400
    assert data(a.patch(f"/api/admin/logs/{log['id']}", {"admin_note": "ok"}))["admin_note"] == "ok"
    assert a.patch(f"/api/admin/logs/{log['id']}", {"action_type": "x"}).status_code == 400
    c = api()
    c.login("staff1@parkwise.local")
    assert c.post("/api/admin/logs", {"action_type": "ok", "message": "x"}).status_code == 403


def test_logs_note_archive_delete(api, mongo):
    a = admin(api)
    logs = data(a.get("/api/admin/logs", query_string={"action_type": "user.login"}))
    assert logs["total"] >= 1
    lid = logs["items"][0]["id"]
    assert data(a.patch(f"/api/admin/logs/{lid}", {"admin_note": "Checked"}))["admin_note"] == "Checked"
    r = a.patch(f"/api/admin/logs/{lid}", {"action_type": "hacked"})
    assert r.status_code == 400                                       
    assert data(a.patch(f"/api/admin/logs/{lid}", {"is_archived": True}))["is_archived"] is True
    assert all(l["id"] != lid for l in data(a.get("/api/admin/logs"))["items"])    
    assert any(l["id"] == lid for l in data(a.get("/api/admin/logs", query_string={"archived": "1"}))["items"])
    assert a.delete(f"/api/admin/logs/{lid}").status_code == 200
    assert "user.login" in data(a.get("/api/admin/logs/action-types"))


def test_preferences_admin(api, mongo):
    a = admin(api)
    rows = data(a.get("/api/admin/preferences"))
    assert len(rows) == 8
    p = data(put(a, "/api/admin/preferences/5", {"notifications": {"noShow": False}}))
    assert p["notifications"]["noShow"] is False and "theme" not in p and "language" not in p
    assert a.delete("/api/admin/preferences/5").status_code == 200
    assert next(r for r in data(a.get("/api/admin/preferences")) if r["user_id"] == 5)["has_document"] is False
