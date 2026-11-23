import base64
import os
import hashlib
import json
import re
from datetime import datetime, timedelta
from html.parser import HTMLParser

import pytest

from tests.conftest import PASSWORD, TEST_DB, data, needs_mysql

pytestmark = needs_mysql


def login(api, email, password=PASSWORD):
    c = api()
    r = c.login(email, password)
    assert r.status_code == 200, data(r)
    return c


def put(c, url, body):
    return c.c.put(url, json=body, headers=c._h())


def sql(app, query, args=None, one=False):
    from db.mysql import fetch_all, fetch_one
    with app.app_context():
        return fetch_one(query, args) if one else fetch_all(query, args)


def make_app(**overrides):
    from app import create_app
    from config import TestConfig

    class C(TestConfig):
        pass
    for k, v in overrides.items():
        setattr(C, k, v)
    return create_app(C)
# ---- H1: SECRET_KEY ---------------------------------------------------------------------

@pytest.mark.parametrize("key", ["", "dev-only-secret-change-me", "change-me-to-a-long-random-string", "short"])
def test_weak_secret_key_refused(key):
    from config import check_secret_key
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        check_secret_key({"SECRET_KEY": key})


def test_strong_secret_key_accepted():
    from config import check_secret_key
    check_secret_key({"SECRET_KEY": "9f" * 32})


def test_app_does_not_start_with_default_secret():
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        make_app(TESTING=False, SECRET_KEY="dev-only-secret-change-me", DEMO_MODE=True)


def test_forged_cookie_with_old_default_key_is_useless(app):
    """The PoC signs {"uid": 1} using the old default key, but the app uses a different key. 
    Even a valid cookie is rejected without the password fingerprint."""
    from flask.sessions import SecureCookieSessionInterface
    from flask import Flask
    attacker = Flask("attacker")
    attacker.secret_key = "dev-only-secret-change-me"
    forged = SecureCookieSessionInterface().get_signing_serializer(attacker).dumps({"uid": 1, "csrf": "x"})
    c = app.test_client()
    c.set_cookie("session", forged)
    assert c.get("/api/admin/users").status_code == 401
    # signed with the REAL key but made without logging in (no fingerprint) -> still refused
    real = SecureCookieSessionInterface().get_signing_serializer(app).dumps({"uid": 1, "csrf": "x"})
    c2 = app.test_client()
    c2.set_cookie("session", real)
    assert c2.get("/api/admin/users").status_code == 401
# ---- H2: seed passwords / demo buttons --------------------------------------------------------

def test_login_page_has_no_demo_password():
    import os
    from app import STATIC_DIR
    with open(os.path.join(STATIC_DIR, "login.html"), encoding="utf-8") as f:
        page = f.read()
    assert "Passw0rd!" not in page and "admin@parkwise.local" not in page


def test_demo_accounts_only_in_demo_mode():
    on = make_app(DEMO_MODE=True).test_client().get("/api/auth/demo-accounts")
    assert on.status_code == 200 and data(on)["accounts"]
    off = make_app(DEMO_MODE=False).test_client().get("/api/auth/demo-accounts")
    assert off.status_code == 404


def test_app_refuses_seed_accounts_outside_demo_mode(fresh_db):
    with pytest.raises(RuntimeError, match="seed"):
        make_app(TESTING=False, DEMO_MODE=False, SECRET_KEY="ab" * 32, SCHEDULER_ENABLED=False)


def test_all_hashes_use_one_method():
    from core import security
    assert security._DUMMY_HASH.startswith("scrypt:")
    assert security.hash_password("Abcdefg1").startswith("scrypt:")


def test_seed_hash_is_scrypt(app):
    row = sql(app, "SELECT password_hash FROM Users WHERE user_id = 1", one=True)
    assert row["password_hash"].startswith("scrypt:")
# ---- H3: admin status override cannot double book ------------------------------------------

def _next_half_hour(iso):
    t = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")
    t = t.replace(second=0) + timedelta(minutes=(30 - t.minute % 30) % 30 or 30)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_admin_reconfirm_cannot_double_book(api, app, fresh_db):
    a = login(api, "admin@parkwise.local")
    old = data(a.get("/api/admin/reservations/8"))           
    assert old["status"] == "Cancelled"
    b = login(api, "preecha@example.com")
    r = b.post("/api/reservations", {"booking_type": "Advance", "slot_id": 11, "vehicle_id": 8,
                                     "duration_hours": 1, "start_time": _next_half_hour(old["start_time"])})
    assert r.status_code == 201, data(r)
    assert data(b.post(f"/api/reservations/{data(r)['reservation_id']}/confirm"))["status"] == "Confirmed"

    r = a.patch("/api/admin/reservations/8", {"status": "Confirmed", "admin_reason": "customer asked"})
    assert r.status_code == 409 and data(r)["code"] == "SLOT_TAKEN"
    rows = sql(app, "SELECT COUNT(*) AS n FROM Reservations WHERE slot_id = 11 AND status = 'Confirmed'", one=True)
    assert rows["n"] == 1


def test_admin_reconfirm_works_when_bay_is_free(api, fresh_db):
    a = login(api, "admin@parkwise.local")
    r = a.patch("/api/admin/reservations/8", {"status": "Confirmed", "admin_reason": "customer asked"})
    assert r.status_code == 200 and data(r)["status"] == "Confirmed"


def test_admin_cannot_make_impossible_moves(api, fresh_db):
    a = login(api, "admin@parkwise.local")
    r = a.patch("/api/admin/reservations/11", {"status": "Confirmed", "admin_reason": "x"})   # No-show, long past
    assert r.status_code == 409 and data(r)["code"] == "TOO_LATE"
    r = a.patch("/api/admin/reservations/8", {"status": "No-show", "admin_reason": "x"})      # Cancelled -> No-show
    assert r.status_code == 409 and data(r)["code"] == "INVALID_TRANSITION"
# ---- open redirect (frontend) ---------------------------------------------------------------

def test_safe_next_blocks_open_redirects():
    import os
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    from app import STATIC_DIR
    src = open(os.path.join(STATIC_DIR, "js", "api.js"), encoding="utf-8").read()
    fn = src[src.index("export function safeNext"):src.index("export function redirectToLogin")].replace("export ", "")
    script = f"""
globalThis.location = {{ origin: "https://parkwise.example" }};
{fn}
const bad = ["//evil.com", "/\\\\evil.com", "/\\t/evil.com", "/\\r/evil.com", "/\\n/evil.com", "/.//evil.com",
             "/..//evil.com", "/%2e//evil.com", "/x/..//evil.com", "https://evil.com", "javascript:alert(1)"];
for (const v of bad) {{
  const out = safeNext(v, "/");
  const u = new URL(out, location.origin);
  if (u.origin !== location.origin || out.startsWith("//")) {{ console.log("OPEN", JSON.stringify(v), out); process.exit(1); }}
}}
if (safeNext("/customer/dashboard.html?x=1", "/") !== "/customer/dashboard.html?x=1") process.exit(2);
"""
    r = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stdout + r.stderr
# ---- rate limits, enumeration, password strength --------------------------------------------

def test_login_rate_limit_per_email(app, fresh_db):
    from core import ratelimit
    ratelimit.reset()
    app.config["RATE_LIMIT_ENABLED"] = True
    c = app.test_client()
    codes = [c.post("/api/auth/login", json={"email": f"nobody{i % 1}@example.com", "password": "x"}).status_code
             for i in range(12)]
    assert 429 in codes
    ratelimit.reset()


def test_register_rate_limit_per_ip(app, fresh_db):
    from core import ratelimit
    ratelimit.reset()
    app.config["RATE_LIMIT_ENABLED"] = True
    c = app.test_client()
    codes = [c.post("/api/auth/register", json={"full_name": "Bot", "email": f"bot{i}@example.com",
                                                 "password": "Abcdef12x"}).status_code for i in range(8)]
    assert codes[:5] == [201] * 5 and 429 in codes[5:]
    ratelimit.reset()


def test_password_change_rate_limited(app, api, fresh_db):
    from core import ratelimit
    ratelimit.reset()
    app.config["RATE_LIMIT_ENABLED"] = True
    c = login(api, "somchai@example.com")
    codes = [c.post("/api/me/password", {"current_password": f"guess{i}", "new_password": "NewPass123"}).status_code
             for i in range(8)]
    assert codes[:5] == [400] * 5 and codes[5:] == [429] * 3
    ratelimit.reset()


def test_lockout_answers_do_not_reveal_emails(client, fresh_db):
    def sequence(email):
        return [client.post("/api/auth/login", json={"email": email, "password": "wrong-password"}).status_code
                for _ in range(6)]
    known, unknown = sequence("suda@example.com"), sequence("nobody-here@example.com")
    assert known == unknown == [401, 401, 401, 401, 423, 423]


@pytest.mark.parametrize("pw", ["12345678", "password", "aaaaaaaa", "abcdefgh", "Password", "11112222"])
def test_weak_passwords_refused(client, pw, fresh_db):
    r = client.post("/api/auth/register", json={"full_name": "W", "email": "weak@example.com", "password": pw})
    assert r.status_code == 400 and "password" in data(r)["fields"]


def test_login_still_accepts_old_short_rules(client):
    """Strength rules are for NEW passwords only: existing accounts can still log in."""
    r = client.post("/api/auth/login", json={"email": "somchai@example.com", "password": PASSWORD})
    assert r.status_code == 200
# ---- sessions can be revoked ----------------------------------------------------------------

def test_password_change_logs_out_other_sessions(api, fresh_db):
    a = login(api, "somchai@example.com")
    b = login(api, "somchai@example.com")
    assert b.get("/api/auth/me").status_code == 200
    r = a.post("/api/me/password", {"current_password": PASSWORD, "new_password": "NewPass123"})
    assert r.status_code == 200
    assert a.get("/api/auth/me").status_code == 200        # the session that changed it stays
    assert b.get("/api/auth/me").status_code == 401        # every other one is gone
    assert api().login("somchai@example.com", "NewPass123").status_code == 200


def test_admin_password_reset_logs_user_out(api, fresh_db):
    u = login(api, "suda@example.com")
    a = login(api, "admin@parkwise.local")
    assert a.patch("/api/admin/users/5", {"password": "Reset1234x"}).status_code == 200
    assert u.get("/api/auth/me").status_code == 401


def test_deactivation_cancels_open_bookings(api, app, fresh_db):
    u = login(api, "malee@example.com")                  # user 7: reservation 4 Held, 9 Confirmed
    a = login(api, "admin@parkwise.local")
    assert data(a.delete("/api/admin/users/7"))["is_active"] is False
    assert u.get("/api/auth/me").status_code == 401
    rows = sql(app, "SELECT reservation_id, status FROM Reservations WHERE reservation_id IN (4, 9)")
    assert {r["status"] for r in rows} == {"Cancelled"}
# ---- cash needs staff ------------------------------------------------------------------------

def test_customer_cannot_mark_cash_paid(api, fresh_db):
    c = login(api, "preecha@example.com")                # payment 3 is Pending
    r = c.post("/api/payments/3/pay", {"method": "Cash"})
    assert r.status_code == 200
    p = data(r)
    assert p["status"] == "Pending" and p["method"] == "Cash" and p["awaiting_cash_confirmation"] is True
    assert c.post("/api/staff/payments/3/confirm-cash").status_code == 403          # not staff
    s = login(api, "staff1@parkwise.local")
    done = data(s.post("/api/staff/payments/3/confirm-cash"))
    assert done["status"] == "Paid" and done["method"] == "Cash" and done["paid_at"]
    assert data(s.post("/api/staff/payments/3/confirm-cash"))["code"] == "ALREADY_PAID"


def test_qr_payment_still_simulated(api, fresh_db):
    c = login(api, "preecha@example.com")
    assert data(c.post("/api/payments/3/pay", {"method": "QR"}))["status"] == "Paid"


def test_unpaid_blocks_new_booking_when_switched_on(api, app, fresh_db):
    a = login(api, "admin@parkwise.local")
    assert put(a, "/api/admin/config/BLOCK_BOOKING_WHEN_UNPAID", {"value": 1}).status_code == 200
    c = login(api, "preecha@example.com")                # payment 3 still Pending
    r = c.post("/api/reservations", {"booking_type": "Instant", "slot_id": 9, "vehicle_id": 8, "duration_hours": 1})
    assert r.status_code == 409 and data(r)["code"] == "UNPAID_PAYMENTS"
    c.post("/api/payments/3/pay", {"method": "QR"})
    r = c.post("/api/reservations", {"booking_type": "Instant", "slot_id": 9, "vehicle_id": 8, "duration_hours": 1})
    assert r.status_code == 201, data(r)
# ---- demo endpoints ---------------------------------------------------------------------------

def test_demo_endpoints_off_without_demo_mode(fresh_db):
    app = make_app(DEMO_MODE=False)
    from tests.conftest import ApiClient
    a = ApiClient(app)
    a.login("admin@parkwise.local")
    for url in ("/api/admin/demo",):
        r = a.get(url)
        assert r.status_code == 404 and data(r)["code"] == "DEMO_MODE_OFF"
    for url in ("/api/admin/demo/double-booking", "/api/admin/demo/checkout-success", "/api/admin/demo/mongo-down"):
        assert a.post(url, {"slot_id": 1, "reservation_id": 1, "enabled": True}).status_code == 404


def test_double_booking_demo_never_touches_real_customers(api, app, fresh_db):
    last = sql(app, "SELECT MAX(seq) AS s FROM Integration_Outbox", one=True)["s"]
    a = login(api, "admin@parkwise.local")
    out = data(a.post("/api/admin/demo/double-booking", {"slot_id": 9, "attempts": 4}))
    assert out["new_rows"] == 1
    assert sorted(x["outcome"] for x in out["attempts"]) == ["HELD", "REJECTED", "REJECTED", "REJECTED"]
    assert {x["code"] for x in out["attempts"] if x["outcome"] == "REJECTED"} == {"SLOT_TAKEN"}
    events = sql(app, "SELECT payload FROM Integration_Outbox WHERE seq > %s", (last,))
    real = {r["user_id"] for r in sql(app, "SELECT user_id FROM Users WHERE email NOT LIKE 'demo.racer%%'")}
    assert events
    for e in events:
        p = json.loads(e["payload"])
        assert p.get("target_user_id") not in real         
        if p["entity_type"] == "reservation":
            assert p["actor_user_id"] not in real or p["actor_role"] == "Admin"
    owners = sql(app, "SELECT DISTINCT u.email FROM Reservations r JOIN Users u ON u.user_id = r.user_id "
                      "WHERE r.slot_id = 9")
    assert all(o["email"].startswith("demo.racer") for o in owners)
# ---- config ranges ------------------------------------------------------------------------------

@pytest.mark.parametrize("key,value", [("HOLD_MINUTES", 100000), ("RATE_PER_HOUR", 0),
                                       ("MAX_ACTIVE_RESERVATIONS_PER_USER", 0), ("MAX_DURATION_HOURS", 25),
                                       ("LOGIN_MAX_FAILED", 0), ("BLOCK_BOOKING_WHEN_UNPAID", 2)])
def test_config_out_of_range_refused(api, key, value, fresh_db):
    a = login(api, "admin@parkwise.local")
    r = put(a, f"/api/admin/config/{key}", {"value": value})
    assert r.status_code == 400, (key, value)


def test_config_min_cannot_pass_max(api, fresh_db):
    a = login(api, "admin@parkwise.local")
    assert put(a, "/api/admin/config/MAX_DURATION_HOURS", {"value": 1}).status_code == 200
    r = put(a, "/api/admin/config/MIN_DURATION_HOURS", {"value": 5})
    assert r.status_code == 400 and "minimum" in data(r)["fields"]["value"]
    cfg = {c["config_key"]: c for c in data(a.get("/api/admin/config"))}
    assert cfg["HOLD_MINUTES"]["min"] == 1 and cfg["HOLD_MINUTES"]["max"] == 60
# ---- soft-deleted payment can be made again --------------------------------------------------------

def test_deleted_payment_can_be_recreated(api, fresh_db):
    a = login(api, "admin@parkwise.local")
    assert a.delete("/api/admin/payments/3").status_code == 200     
    r = a.post("/api/admin/payments", {"reservation_id": 7})
    assert r.status_code == 201, data(r)
    assert data(r)["payment_id"] == 3 and data(r)["status"] == "Pending" and not data(r)["is_deleted"]
    assert data(a.post("/api/admin/payments", {"reservation_id": 7}))["code"] == "HAS_PAYMENT"
# ---- GET does not write while the scheduler runs ---------------------------------------------------

def test_get_does_not_write_when_scheduler_runs(api, app, fresh_db, monkeypatch):
    from jobs import scheduler
    monkeypatch.setattr(scheduler, "is_running", lambda: True)
    from db.mysql import transaction
    with app.app_context():
        with transaction() as cur:
            cur.execute("UPDATE Reservations SET hold_expires_at = UTC_TIMESTAMP() - INTERVAL 1 MINUTE "
                        "WHERE reservation_id = 4")
    before = sql(app, "SELECT COUNT(*) AS n FROM Integration_Outbox", one=True)["n"]
    c = login(api, "somchai@example.com")
    after_login = sql(app, "SELECT COUNT(*) AS n FROM Integration_Outbox", one=True)["n"]
    assert c.get("/api/lots").status_code == 200
    assert sql(app, "SELECT status FROM Reservations WHERE reservation_id = 4", one=True)["status"] == "Held"
    assert sql(app, "SELECT COUNT(*) AS n FROM Integration_Outbox", one=True)["n"] == after_login == before + 1
    # ...and the page still shows the truth
    a = login(api, "admin@parkwise.local")
    assert data(a.get("/api/admin/reservations/4"))["status"] == "Expired"
# ---- headers --------------------------------------------------------------------------------------

class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.inline, self._in, self._buf = [], False, []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and not dict(attrs).get("src"):
            self._in, self._buf = True, []

    def handle_data(self, d):
        if self._in:
            self._buf.append(d)

    def handle_endtag(self, tag):
        if tag == "script" and self._in:
            self.inline.append("".join(self._buf))
            self._in = False


@pytest.mark.parametrize("page", ["/login.html", "/register.html", "/customer/payments.html", "/admin/demo.html",
                                  "/staff/search.html"])
def test_security_headers_and_csp_hashes(client, page):
    r = client.get(page)
    assert r.status_code == 200
    h = r.headers
    assert h["X-Frame-Options"] == "DENY" and h["X-Content-Type-Options"] == "nosniff"
    csp = h["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in csp and "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
    p = _Scripts()
    p.feed(r.get_data(as_text=True))
    assert p.inline, "page has inline scripts"
    for body in p.inline:                    # every inline script on the page is allowed by its hash
        digest = base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode()
        assert f"'sha256-{digest}'" in csp, page


def test_api_responses_have_headers(client):
    h = client.get("/api/config/public").headers
    assert h["X-Content-Type-Options"] == "nosniff" and "no-store" in h["Cache-Control"]
# ---- staff search wildcards --------------------------------------------------------------------

@pytest.mark.parametrize("q", ["%%", "__", "%_", "\\\\"])
def test_staff_search_wildcards_are_not_wildcards(api, q):
    s = login(api, "staff1@parkwise.local")
    r = s.get("/api/staff/search", query_string={"plate": q})
    assert r.status_code == 400


def test_staff_search_literal_match(api):
    s = login(api, "staff1@parkwise.local")
    out = data(s.get("/api/staff/search", query_string={"plate": "1ab"}))
    plates = {x["vehicle"]["plate_number"] for x in out["active"] + out["recent"]}
    assert plates and all("1AB" in p.replace(" ", "").upper() for p in plates)


def test_admin_search_escapes_wildcards(api):
    a = login(api, "admin@parkwise.local")
    assert data(a.get("/api/admin/users", query_string={"q": "%"}))["total"] == 0
# ---- plates ----------------------------------------------------------------------------------------

def test_same_plate_allowed_for_another_customer(api, fresh_db):
    other = login(api, "suda@example.com")
    r = other.post("/api/me/vehicles", {"plate_number": "1AB1234", "province": "Bangkok"})   # somchai's plate
    assert r.status_code == 201, data(r)
    again = other.post("/api/me/vehicles", {"plate_number": "1ab 1234", "province": "Bangkok"})
    assert again.status_code in (201, 409)
    same = other.post("/api/me/vehicles", {"plate_number": "1AB1234", "province": "Bangkok"})
    assert same.status_code == 409 and data(same)["code"] == "PLATE_TAKEN"   # twice for one customer


def test_plate_of_car_with_bookings_cannot_change(api, fresh_db):
    c = login(api, "somchai@example.com")
    r = c.patch("/api/me/vehicles/1", {"plate_number": "9ZZ9999"})
    assert r.status_code == 409 and data(r)["code"] == "VEHICLE_HAS_BOOKINGS"
    new = data(c.post("/api/me/vehicles", {"plate_number": "7KK7777", "province": "Bangkok"}))
    assert data(c.patch(f"/api/me/vehicles/{new['vehicle_id']}", {"plate_number": "7KK7778"}))["plate_number"] == "7KK7778"
# ---- audit trail -----------------------------------------------------------------------------------

def test_logs_are_archived_not_deleted(api, mongo):
    a = login(api, "admin@parkwise.local")
    lid = data(a.get("/api/admin/logs"))["items"][0]["id"]
    before = mongo.Activity_Logs.count_documents({})
    r = a.delete(f"/api/admin/logs/{lid}")
    assert r.status_code == 200 and data(r)["is_archived"] is True
    assert mongo.Activity_Logs.count_documents({}) == before


def test_deleted_session_is_kept_in_audit(api, app, mongo):
    a = login(api, "admin@parkwise.local")
    sess = data(a.get("/api/admin/sessions"))["items"][0]
    assert a.delete(f"/api/admin/sessions/{sess['id']}").status_code == 200
    row = sql(app, "SELECT payload FROM Integration_Outbox WHERE event_type = 'admin.session_deleted' "
                   "ORDER BY seq DESC LIMIT 1", one=True)
    copy = json.loads(row["payload"])["metadata"]["deleted_document"]
    assert copy["plateNumber"] == sess["plateNumber"]


@pytest.mark.parametrize("fees", [{"base": float("nan")}, {"base": True}, {"base": -1}, {"overstay": 1e9}])
def test_session_fees_must_be_real_numbers(api, mongo, fees):
    a = login(api, "admin@parkwise.local")
    sess = data(a.get("/api/admin/sessions"))["items"][0]
    body = json.dumps({"fees": fees}, allow_nan=True)
    r = a.c.patch(f"/api/admin/sessions/{sess['id']}", data=body, content_type="application/json", headers=a._h())
    assert r.status_code == 400
# ----  bad input is 400, not 500 -----------------------------------------------------------------------

@pytest.mark.parametrize("raw", ['{"booking_type":"Instant","slot_id":9,"vehicle_id":8,"duration_hours":1e400}',
                                 '{"booking_type":"Instant","slot_id":9,"vehicle_id":8,"duration_hours":Infinity}',
                                 '{"booking_type":"Instant","slot_id":9,"vehicle_id":8,"duration_hours":NaN}',
                                 '{"booking_type":"Instant","slot_id":99999999999999999999999,"vehicle_id":8,"duration_hours":1}',
                                 '{"booking_type":"Instant","slot_id":"\\ud800","vehicle_id":8,"duration_hours":1}'])
def test_bad_numbers_are_400(api, raw):
    c = login(api, "preecha@example.com")
    r = c.c.post("/api/reservations", data=raw, content_type="application/json", headers=c._h())
    assert r.status_code == 400, r.get_data(as_text=True)


def test_lone_surrogate_text_is_400(api):
    c = login(api, "somchai@example.com")
    r = c.c.patch("/api/me/profile", data='{"full_name":"A\\ud800B"}', content_type="application/json",
                  headers=c._h())
    assert r.status_code == 400


@pytest.mark.parametrize("start", ["0001-01-01T00:00:00+07:00", "9999-12-31T23:59:59-07:00", "0001-01-01"])
def test_extreme_dates_are_400(api, start):
    c = login(api, "somchai@example.com")
    assert c.get("/api/lots", query_string={"start": start}).status_code == 400


def test_extreme_dates_in_body_are_400(api):
    c = login(api, "preecha@example.com")
    r = c.post("/api/reservations", {"booking_type": "Advance", "slot_id": 9, "vehicle_id": 8, "duration_hours": 1,
                                     "start_time": "0001-01-01T00:00:00+07:00"})
    assert r.status_code == 400


def test_staff_movements_extreme_date_is_400(api):
    s = login(api, "staff1@parkwise.local")
    assert s.get("/api/staff/movements", query_string={"date": "0001-01-01"}).status_code == 400


# ---- minor ------------------------------------------------------------------------------------------------

def test_hold_expired_message_uses_real_hold_length(api, app, fresh_db):
    from core import settings, timeutil
    a = login(api, "admin@parkwise.local")
    assert put(a, "/api/admin/config/HOLD_MINUTES", {"value": 7}).status_code == 200
    settings.invalidate()
    c = login(api, "preecha@example.com")
    r = data(c.post("/api/reservations", {"booking_type": "Instant", "slot_id": 9, "vehicle_id": 8, "duration_hours": 1}))
    with timeutil.freeze(timeutil.now_utc() + timedelta(minutes=8)):
        out = c.post(f"/api/reservations/{r['reservation_id']}/confirm")
    assert data(out)["code"] == "HOLD_EXPIRED" and "7-minute" in data(out)["message"]


def test_leaving_inside_grace_is_not_overstay():
    from services.fee import calculate_fee
    from core.settings import DEFAULTS
    t = datetime(2026, 11, 20, 3)
    f = calculate_fee(t, t + timedelta(hours=1), 1, t, t + timedelta(minutes=70), DEFAULTS)
    assert f["overstay_fee"] == 0 and not f["is_overstay"]
    f = calculate_fee(t, t + timedelta(hours=1), 1, t, t + timedelta(minutes=76), DEFAULTS)
    assert f["overstay_fee"] > 0 and f["is_overstay"]


def test_reset_demo_env_reader_strips_quotes():
    import os
    import sys
    from tests.conftest import DATABASE_DIR
    sys.path.insert(0, DATABASE_DIR)
    import reset_demo
    assert reset_demo._clean_value('"p@ss word"') == "p@ss word"
    assert reset_demo._clean_value("'x'") == "x"
    assert reset_demo._clean_value("abc  # comment") == "abc"
    assert reset_demo._clean_value("a#b") == "a#b"


def test_requirements_split():
    import os
    from tests.conftest import BACKEND
    run = open(os.path.join(BACKEND, "requirements.txt"), encoding="utf-8").read()
    dev = open(os.path.join(BACKEND, "requirements-dev.txt"), encoding="utf-8").read()
    assert "pytest" not in run and "mongomock" in dev and "pytest" in dev
    assert all("==" in line for line in run.splitlines() if line and not line.startswith("#"))


def test_no_inline_event_handlers_in_pages():
    """CSP allows inline <script> blocks by hash, but never onclick=/onerror= attributes."""
    import os
    from app import STATIC_DIR
    bad = []
    for root, _d, files in os.walk(STATIC_DIR):
        for name in files:
            if name.endswith(".html"):
                text = open(os.path.join(root, name), encoding="utf-8").read()
                bad += [(name, m) for m in re.findall(r"<[a-zA-Z][^>]*\son[a-z]+\s*=", text)]
    assert not bad, bad


def test_csp_follows_edited_pages(tmp_path):
    """Editing a page while the server runs updates the CSP (no restart needed)."""
    import os
    import time
    from core.security import _CspCache
    page = tmp_path / "p.html"
    page.write_text("<script>one()</script>", encoding="utf-8")
    cache = _CspCache(str(tmp_path))
    first = cache.get()
    time.sleep(0.01)
    page.write_text("<script>two()</script>", encoding="utf-8")
    os.utime(page, None)
    second = cache.get()
    h = base64.b64encode(hashlib.sha256(b"two()").digest()).decode()
    assert first != second and f"'sha256-{h}'" in second

@pytest.mark.parametrize("page", ["login.html", "register.html"])
def test_auth_pages_have_back_button_and_home_logo(page):
    import os
    from app import STATIC_DIR
    html = open(os.path.join(STATIC_DIR, page), encoding="utf-8").read()
    assert 'id="back-btn"' in html and 'class="brand" href="/"' in html
    assert 'getParam("next")' in html.split('$("#back-btn")')[1].split("});")[0]


def test_env_file_is_read_from_backend_folder_from_any_cwd(tmp_path):
    import subprocess
    import sys
    from tests.conftest import BACKEND
    code = ("import sys; sys.path.insert(0, %r); import config, os; "
            "print(os.path.dirname(os.path.abspath(config.__file__)))" % BACKEND)
    out = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    src = open(os.path.join(BACKEND, "config.py"), encoding="utf-8").read()
    assert 'os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")' in src


def test_pymongo_debug_logs_are_quiet(app):
    import logging
    assert logging.getLogger("pymongo").level == logging.WARNING
