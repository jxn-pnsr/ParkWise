"""Tests transaction commit, rollback and retry behavior.

Requires MySQL with database/schema.sql loaded.
Test rows use unique lot names and are cleaned up afterwards.
"""
import uuid
from unittest import mock

import pymysql
import pytest

from db import mysql
from tests.conftest import needs_mysql

pytestmark = needs_mysql


@pytest.fixture()
def lot_name(app):
    name = "TEST-" + uuid.uuid4().hex[:8]
    yield name
    with app.app_context():
        with mysql.transaction() as cur:
            cur.execute("DELETE FROM Parking_Slots WHERE lot_id IN "
                        "(SELECT lot_id FROM Parking_Lots WHERE lot_name = %s)", (name,))
            cur.execute("DELETE FROM Parking_Lots WHERE lot_name = %s", (name,))


def _count(name):
    return mysql.fetch_value("SELECT COUNT(*) FROM Parking_Lots WHERE lot_name = %s", (name,))


def test_commit(app, lot_name):
    with app.app_context():
        with mysql.transaction() as cur:
            cur.execute("INSERT INTO Parking_Lots (lot_name) VALUES (%s)", (lot_name,))
        assert _count(lot_name) == 1


def test_rollback_undoes_every_step(app, lot_name):
    """A failure after step 2 rolls back both inserts."""
    with app.app_context():
        with pytest.raises(RuntimeError):
            with mysql.transaction() as cur:
                cur.execute("INSERT INTO Parking_Lots (lot_name) VALUES (%s)", (lot_name,))
                lot_id = cur.lastrowid
                cur.execute("INSERT INTO Parking_Slots (lot_id, slot_code) VALUES (%s, 'X1')", (lot_id,))
                raise RuntimeError("simulated failure after step 2")
        assert _count(lot_name) == 0


def test_rollback_on_constraint_error(app, lot_name):
    with app.app_context():
        with pytest.raises(pymysql.err.IntegrityError):
            with mysql.transaction() as cur:
                cur.execute("INSERT INTO Parking_Lots (lot_name) VALUES (%s)", (lot_name,))
                cur.execute("INSERT INTO Parking_Lots (lot_name) VALUES (%s)", (lot_name,))  # duplicate
        assert _count(lot_name) == 0


def test_connection_usable_after_rollback(app, lot_name):
    with app.app_context():
        with pytest.raises(RuntimeError):
            with mysql.transaction() as cur:
                cur.execute("INSERT INTO Parking_Lots (lot_name) VALUES (%s)", (lot_name,))
                raise RuntimeError()
        with mysql.transaction() as cur:
            cur.execute("INSERT INTO Parking_Lots (lot_name) VALUES (%s)", (lot_name,))
        assert _count(lot_name) == 1


def test_nested_transaction_is_refused(app):
    with app.app_context():
        with mysql.transaction():
            with pytest.raises(RuntimeError, match="nesting"):
                with mysql.transaction():
                    pass


def test_works_without_flask_context(lot_name):
    """Background jobs use their own connection."""
    with mysql.transaction() as cur:
        cur.execute("INSERT INTO Parking_Lots (lot_name) VALUES (%s)", (lot_name,))
    assert _count(lot_name) == 1


def test_reads_see_fresh_data_from_other_connections(app, lot_name):
    """Reads can see commits from other connections."""
    with app.app_context():
        assert _count(lot_name) == 0
        other = mysql.connect()
        try:
            with other.cursor() as cur:
                cur.execute("INSERT INTO Parking_Lots (lot_name) VALUES (%s)", (lot_name,))
            other.commit()
        finally:
            other.close()
        assert _count(lot_name) == 1


def test_deadlock_is_retried_once(app):
    calls = []

    def body(cur):
        calls.append(1)
        if len(calls) == 1:
            raise pymysql.err.OperationalError(1213, "Deadlock found")
        return "done"

    with app.app_context():
        assert mysql.run_in_transaction(body) == "done"
    assert len(calls) == 2


def test_second_deadlock_is_raised(app):
    def body(cur):
        raise pymysql.err.OperationalError(1213, "Deadlock found")

    with app.app_context():
        with pytest.raises(pymysql.err.OperationalError):
            mysql.run_in_transaction(body)


def test_session_is_utc(app):
    with app.app_context():
        assert mysql.fetch_value("SELECT @@session.time_zone") == "+00:00"
        assert mysql.fetch_value("SELECT @@session.transaction_isolation") == "REPEATABLE-READ"


def test_request_connection_closed_at_teardown(app):
    with app.app_context():
        conn = mysql.get_db()
        assert conn.open
    assert not conn.open


def test_mongo_simulated_down(app):
    from db import mongo
    with app.app_context():
        mongo.set_simulated_down(True)
        try:
            with pytest.raises(mongo.MongoUnavailable):
                mongo.get_mongo()
            assert mongo.ping() is False
        finally:
            mongo.set_simulated_down(False)


def test_mysql_down_does_not_crash_health(app):
    with mock.patch.object(mysql, "connect", side_effect=pymysql.err.OperationalError(2003, "down")):
        r = app.test_client().get("/api/health")
    assert r.status_code == 503
    assert r.get_json()["data"]["mysql"] == "down"