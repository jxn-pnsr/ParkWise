import logging
from contextlib import contextmanager

import pymysql
from flask import current_app, g, has_app_context
from pymysql.cursors import DictCursor

log = logging.getLogger(__name__)

DEADLOCK_ERRORS = (1213, 1205)  # Deadlock and lock timeout


def _settings():
    if has_app_context():
        c = current_app.config
        return dict(host=c["MYSQL_HOST"], port=c["MYSQL_PORT"], user=c["MYSQL_USER"],
                    password=c["MYSQL_PASSWORD"], database=c["MYSQL_DB"])
    from config import Config  # Used by scripts and scheduled jobs
    return dict(host=Config.MYSQL_HOST, port=Config.MYSQL_PORT, user=Config.MYSQL_USER,
                password=Config.MYSQL_PASSWORD, database=Config.MYSQL_DB)


def connect():
    """Open a MySQL connection."""
    conn = pymysql.connect(
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
        connect_timeout=5,
        init_command="SET time_zone = '+00:00', SESSION transaction_isolation = 'REPEATABLE-READ'",
        **_settings())
    conn.pw_in_tx = False
    return conn


def get_db():
    """Get the current request's connection."""
    if "mysql" not in g:
        g.mysql = connect()
    return g.mysql


def close_db(_exc=None):
    conn = g.pop("mysql", None)
    if conn is not None:
        try:
            if conn.pw_in_tx:
                conn.rollback()
            conn.close()
        except Exception:  # Connection may already be closed.
            pass


@contextmanager
def _connection():
    """Get a connection and close it when finished if outside a request."""
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
    """Commit on success or rollback on error."""
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
    """Run a function in a transaction and retry on deadlock."""
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


# Read helpers

def _read(sql, params, many):
    with _connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall() if many else cur.fetchone()
        if not conn.pw_in_tx:
            # End the read transaction to get fresh data next time.
            conn.commit()
        return rows


def fetch_one(sql, params=None):
    return _read(sql, params, many=False)


def fetch_all(sql, params=None):
    return list(_read(sql, params, many=True))


def fetch_value(sql, params=None):
    row = fetch_one(sql, params)
    return next(iter(row.values())) if row else None


def ping():
    """Check if MySQL is available."""
    try:
        return fetch_value("SELECT 1") == 1
    except Exception as e:
        log.warning("MySQL ping failed: %s", e)
        return False


def init_app(app):
    app.teardown_appcontext(close_db)