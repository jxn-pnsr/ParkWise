"""Test setup.

Tests run against a SEPARATE database (default parkwise_test) so they never
change the demo data. It is rebuilt from database/schema.sql + seed.sql:
once per test session, and again by tests that ask for `fresh_db`.
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
DATABASE_DIR = os.path.join(BACKEND, "..", "database")
sys.path.insert(0, BACKEND)

# Must happen before config is imported (config reads the environment once).
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(BACKEND, ".env"))
TEST_DB = os.getenv("TEST_MYSQL_DB", "parkwise_test")
os.environ["MYSQL_DB"] = TEST_DB
os.environ.setdefault("MONGO_SKIP_SECONDS", "0")  # tests: never cache a Mongo outage

from app import create_app  # noqa: E402
from config import TestConfig  # noqa: E402
from db import mysql  # noqa: E402

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
    from core import settings
    settings.invalidate()


@pytest.fixture(scope="session", autouse=True)
def _build_test_db():
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
