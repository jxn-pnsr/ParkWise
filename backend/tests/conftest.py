"""Test setup.

Tests use a separate database (default: parkwise_test) to protect demo data.
The database is rebuilt from schema.sql and seed.sql once per session,
and again for tests that use `fresh_db`.
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
DATABASE_DIR = os.path.join(BACKEND, "..", "database")
sys.path.insert(0, BACKEND)

# Load environment variables before importing config.
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(BACKEND, ".env"))
TEST_DB = os.getenv("TEST_MYSQL_DB", "parkwise_test")
os.environ["MYSQL_DB"] = TEST_DB
os.environ.setdefault("MONGO_SKIP_SECONDS", "0")  # Disable Mongo outage caching in tests.

from app import create_app  # noqa: E402
from config import TestConfig  # noqa: E402
from db import mysql  # noqa: E402

TestConfig.MYSQL_DB = TEST_DB
PASSWORD = "Passw0rd!"  # Password used by seed accounts.


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
    from core import settings
    settings.invalidate()


@pytest.fixture(scope="session", autouse=True)
def _build_test_db():
    if MYSQL_UP:
        reset_test_db()


@pytest.fixture()
def fresh_db():
    """Rebuild the test database before tests that modify seed data."""
    reset_test_db()


@pytest.fixture()
def app():
    return create_app(TestConfig)


@pytest.fixture()
def client(app):
    return app.test_client()


class ApiClient:
    """Test client that handles login and CSRF headers."""

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
    """Create a seeded in-memory MongoDB for tests.

    All modules that use get_mongo receive the fake database.
    The simulated outage switch remains available.
    """
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
