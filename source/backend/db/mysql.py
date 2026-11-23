"""MySQL access with pooled request connections and transaction helpers.

Use in services:

    from db.mysql import transaction, fetch_one, fetch_all

    # Read
    lot = fetch_one("SELECT * FROM Parking_Lots WHERE lot_id = %s", (lot_id,))

    # Write in one transaction: COMMIT on success, ROLLBACK on error.
    with transaction() as cur:
        cur.execute("SELECT ... FOR UPDATE", (...))
        cur.execute("INSERT ...", (...))

    # Retry once on deadlock.
    run_in_transaction(create_hold, user_id, ...)  # create_hold(cur, user_id, ...)

Always use %s placeholders. Never build SQL with user input.
"""
import logging
import threading
from contextlib import contextmanager

import pymysql
from flask import current_app, g, has_app_context
from pymysql.cursors import DictCursor

log = logging.getLogger(__name__)

DEADLOCK_ERRORS = (1213, 1205)  # Deadlock / lock wait timeout


def _settings():
    if has_app_context():
        c = current_app.config
        return dict(host=c["MYSQL_HOST"], port=c["MYSQL_PORT"], user=c["MYSQL_USER"],
                    password=c["MYSQL_PASSWORD"], database=c["MYSQL_DB"])
    from config import Config  # Used by scripts and scheduler jobs
    return dict(host=Config.MYSQL_HOST, port=Config.MYSQL_PORT, user=Config.MYSQL_USER,
                password=Config.MYSQL_PASSWORD, database=Config.MYSQL_DB)


def connect():
    """Open a connection with UTC, REPEATABLE READ, and autocommit disabled."""
    conn = pymysql.connect(
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
        connect_timeout=5,
        init_command="SET time_zone = '+00:00', SESSION transaction_isolation = 'REPEATABLE-READ'",
        **_settings())
    conn.pw_in_tx = False
    return conn


# Reuse idle connections after rolling back any unfinished transaction.
_pool = {}
_pool_lock = threading.Lock()


def _pool_key(settings):
    return (settings["host"], settings["port"], settings["user"], settings["database"])


def _checkout():
    settings = _settings()
    key = _pool_key(settings)
    while True:
        with _pool_lock:
            idle = _pool.get(key)
            conn = idle.pop() if idle else None
        if conn is None:
            return connect()
        try:
            conn.ping(reconnect=False)
            conn.select_db(settings["database"])  # Handle database rebuilds
            conn.pw_in_tx = False
            return conn
        except Exception:
            try:
                conn.close()
            except Exception:
                pass


def _checkin(conn, pool_size):
    try:
        conn.rollback()  # Release locks and end read snapshots
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        return
    conn.pw_in_tx = False
    key = _pool_key(_settings())
    with _pool_lock:
        idle = _pool.setdefault(key, [])
        if len(idle) < pool_size:
            idle.append(conn)
            return
    conn.close()


def clear_pool():
    """Close idle connections (tests or MySQL settings changes)."""
    with _pool_lock:
        conns = [c for idle in _pool.values() for c in idle]
        _pool.clear()
    for c in conns:
        try:
            c.close()
        except Exception:
            pass


def get_db():
    """Get the current request's connection, creating it on first use."""
    if "mysql" not in g:
        g.mysql = _checkout()
    return g.mysql


def close_db(_exc=None):
    conn = g.pop("mysql", None)
    if conn is not None:
        try:
            size = current_app.config.get("MYSQL_POOL_SIZE", 0) if has_app_context() else 0
            if size > 0:
                _checkin(conn, size)
            else:
                if conn.pw_in_tx:
                    conn.rollback()
                conn.close()
        except Exception:  # Connection may already be closed
            pass


@contextmanager
def _connection():
    """Use the request connection or a temporary connection outside Flask."""
    if has_app_context():
        yield get_db()
        return
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction():
    """Commit on success or roll back on error.

    Nested transactions are not allowed. Pass the cursor to other services
    that need to share the same transaction.
    """
    with _connection() as conn:
        if conn.pw_in_tx:
            raise RuntimeError("transaction() is already open on this connection (nesting is not allowed)")
        conn.begin()
        conn.pw_in_tx = True
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except BaseException:
            try:
                conn.rollback()
            except Exception:
                log.exception("rollback failed")
            raise
        finally:
            conn.pw_in_tx = False
            cur.close()


def is_deadlock(exc):
    return isinstance(exc, pymysql.err.OperationalError) and exc.args and exc.args[0] in DEADLOCK_ERRORS


def run_in_transaction(fn, *args, retries=1, **kwargs):
    """Run fn(cur, *args, **kwargs), retrying on deadlocks."""
    attempt = 0
    while True:
        try:
            with transaction() as cur:
                return fn(cur, *args, **kwargs)
        except pymysql.err.OperationalError as e:
            if is_deadlock(e) and attempt < retries:
                attempt += 1
                log.warning("deadlock, retrying (%s/%s)", attempt, retries)
                continue
            raise


def _read(sql, params, many):
    with _connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall() if many else cur.fetchone()
        if not conn.pw_in_tx:
            conn.commit()  # End the read transaction to get fresh data next time
        return rows


def fetch_one(sql, params=None):
    return _read(sql, params, many=False)


def fetch_all(sql, params=None):
    return list(_read(sql, params, many=True))


def fetch_value(sql, params=None):
    row = fetch_one(sql, params)
    return next(iter(row.values())) if row else None


def ping():
    """Check MySQL connectivity for /api/health."""
    try:
        return fetch_value("SELECT 1") == 1
    except Exception as e:
        log.warning("MySQL ping failed: %s", e)
        return False


def init_app(app):
    app.teardown_appcontext(close_db)
