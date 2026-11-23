"""Background jobs using APScheduler within the Flask process.

    every 30 s   expire holds and mark no-shows
    every 5 s    sync Pending outbox events to MongoDB
    every 60 s   send ending-soon warnings and detect overstays

Each job uses its own MySQL connection and transaction.
Errors are logged without stopping the scheduler.
Disable with SCHEDULER_ENABLED=0 (used in tests).
"""
import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler

log = logging.getLogger(__name__)
_scheduler = None


def _safe(name, fn):
    def run():
        try:
            result = fn()
            if result and any(result.values() if isinstance(result, dict) else [result]):
                log.info("%s: %s", name, result)
        except Exception:
            log.exception("job %s failed", name)
    return run


def _expiry():
    from services import expiry
    return expiry.run_all()


def _outbox():
    from services import mongo_sync
    return mongo_sync.process_pending()


def _overstay():
    from services import overstay
    return {"warned": overstay.send_end_warnings(), "overstay": overstay.detect_overstays()}


def run_all_now():
    """Run all jobs once for the admin demo button."""
    from services import expiry, mongo_sync, overstay
    return {**expiry.run_all(), "warned": overstay.send_end_warnings(),
            "overstay": overstay.detect_overstays(), "outbox": mongo_sync.process_pending()}


def start(app):
    global _scheduler
    if _scheduler or not app.config.get("SCHEDULER_ENABLED"):
        return None
    # Prevent duplicate jobs in the Flask debug reloader.
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return None
    _scheduler = BackgroundScheduler(timezone="UTC", job_defaults={"coalesce": True, "max_instances": 1})
    _scheduler.add_job(_safe("expiry", _expiry), "interval", seconds=30, id="expiry")
    _scheduler.add_job(_safe("outbox", _outbox), "interval", seconds=5, id="outbox")
    _scheduler.add_job(_safe("overstay", _overstay), "interval", seconds=60, id="overstay")
    _scheduler.start()
    log.info("scheduler started")
    return _scheduler


def status():
    if not _scheduler:
        return {"running": False, "jobs": []}
    return {"running": _scheduler.running,
            "jobs": [{"id": j.id, "next_run": j.next_run_time.replace(tzinfo=None) if j.next_run_time else None}
                     for j in _scheduler.get_jobs()]}