import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
DATABASE_DIR = os.path.join(BACKEND, "..", "database")
sys.path.insert(0, BACKEND)

# Must happen before config is imported (config reads the environment once).
from dotenv import load_dotenv 

load_dotenv(os.path.join(BACKEND, ".env"))
TEST_DB = os.getenv("TEST_MYSQL_DB", "parkwise_test")
os.environ["MYSQL_DB"] = TEST_DB
os.environ.setdefault("MONGO_SKIP_SECONDS", "0")  # never cache a Mongo outage

from app import create_app 
from config import TestConfig  
from db import mysql

TestConfig.MYSQL_DB = TEST_DB
PASSWORD = "Passw0rd!"  # every account in seed.sql


def _mysql_available():
    try:
        import pymysql
        conn = pymysql.connect(host=TestConfig.MYSQL_HOST, port=TestConfig.MYSQL_PORT,
                               user=TestConfig.MYSQL_USER, password=TestConfig.MYSQL_PASSWORD)
        conn.close()
        return True
    except Exception:
        return False


MYSQL_UP = _mysql_available()
needs_mysql = pytest.mark.skipif(not MYSQL_UP, reason="MySQL not reachable (check backend/.env)")


def reset_test_db():
    sys.path.insert(0, DATABASE_DIR)
    import reset_demo
    reset_demo.reset_mysql(TEST_DB)
    # Most booking tests use preecha (user 8), whose seed payment is still Pending.
    # The "unpaid blocks booking" rule has its own test (test_security.py), which turns it on.
    import pymysql
    conn = pymysql.connect(host=TestConfig.MYSQL_HOST, port=TestConfig.MYSQL_PORT, user=TestConfig.MYSQL_USER,
                           password=TestConfig.MYSQL_PASSWORD, database=TEST_DB, autocommit=True)
    with conn.cursor() as cur:
        cur.execute("UPDATE System_Config SET config_value = '0' WHERE config_key = 'BLOCK_BOOKING_WHEN_UNPAID'")
    conn.close()
    from core import settings, ratelimit
    settings.invalidate()
    ratelimit.reset()
    from services import users
    users.reset_phantom_lockouts()


@pytest.fixture(scope="module", autouse=True)
def _build_test_db():
    """Fresh seed data at the start of every test file, so files never depend on each other."""
    if MYSQL_UP:
        reset_test_db()


@pytest.fixture()
def fresh_db():
    """Rebuild the test database before this test (for tests that change seed rows)."""
    reset_test_db()


@pytest.fixture()
def app():
    return create_app(TestConfig)


@pytest.fixture()
def client(app):
    return app.test_client()


class ApiClient:
    """Test client that logs in and sends the CSRF header like the frontend does."""

    def __init__(self, app):
        self.c = app.test_client()
        self.csrf = None

    def login(self, email, password=PASSWORD):
        r = self.c.post("/api/auth/login", json={"email": email, "password": password})
        if r.status_code == 200:
            self.csrf = r.get_json()["data"]["csrf_token"]
        return r

    def _h(self):
        return {"X-CSRF-Token": self.csrf} if self.csrf else {}

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, json=None, **kw):
        return self.c.post(url, json=json if json is not None else {}, headers=self._h(), **kw)

    def patch(self, url, json=None, **kw):
        return self.c.patch(url, json=json or {}, headers=self._h(), **kw)

    def delete(self, url, **kw):
        return self.c.delete(url, headers=self._h(), **kw)


@pytest.fixture()
def api(app):
    return lambda: ApiClient(app)


def data(r):
    body = r.get_json()
    assert body is not None, r.get_data(as_text=True)
    return body.get("data") if body.get("ok") else body.get("error")


@pytest.fixture()
def mongo(monkeypatch):
    """An in-memory MongoDB (mongomock) filled by database/seed_mongo.py.

    Every module that imported get_mongo gets the fake one, and the
    'simulated outage' switch still works on top of it."""
    import mongomock

    from db import mongo as mongo_mod

    fake = mongomock.MongoClient().parkwise

    def get_fake():
        if mongo_mod.is_simulated_down():
            raise mongo_mod.MongoUnavailable("MongoDB is down (simulated)")
        return fake

    for target in ("db.mongo.get_mongo", "services.mongo_read.get_mongo", "services.mongo_sync.get_mongo"):
        monkeypatch.setattr(target, get_fake)
    sys.path.insert(0, DATABASE_DIR)
    import seed_mongo
    seed_mongo.seed(fake, seed_mongo.mysql_conn())
    yield fake
    mongo_mod.set_simulated_down(False)
