"""Hold expiry and No-show (spec 7.3).

Two layers:
  1. The scheduler (phase 2) calls run_all() every 30 seconds.
  2. Pages call run_all() lazily before showing bays or a customer's list, and
     every check (confirm, check-in) re-checks the time itself, so a late
     scheduler can never let an expired booking be used.

Each change is its own transaction with its outbox event (Notification + log).
Rows are locked with SKIP LOCKED so two workers never process the same row.
"""
import logging
from datetime import timedelta

from core.settings import cfg
from core.timeutil import fmt_bkk, now_utc
from db.mysql import transaction
from services.outbox import SYSTEM, notification, write_event

log = logging.getLogger(__name__)
BATCH = 50


def expire_holds(now=None):
    now = now or now_utc()
    done = 0
    with transaction() as cur:
        cur.execute(
            "SELECT r.reservation_id, r.user_id, s.slot_code, l.lot_name FROM Reservations r "
            "JOIN Parking_Slots s ON s.slot_id = r.slot_id JOIN Parking_Lots l ON l.lot_id = s.lot_id "
            "WHERE r.status = 'Held' AND r.hold_expires_at <= %s "
            "ORDER BY r.reservation_id LIMIT %s FOR UPDATE OF r SKIP LOCKED", (now, BATCH))
        for r in cur.fetchall():
            cur.execute("UPDATE Reservations SET status = 'Expired', updated_at = %s "
                        "WHERE reservation_id = %s AND status = 'Held'", (now, r["reservation_id"]))
            write_event(cur, "reservation.expired", "reservation", r["reservation_id"],
                        metadata={"slot": r["slot_code"], "lot": r["lot_name"]},
                        notify=notification("hold_expired", "Reservation expired",
                                            f"Your hold on bay {r['slot_code']} ({r['lot_name']}) expired before you confirmed it."),
                        target_user_id=r["user_id"], who=SYSTEM)
            done += 1
    return done


def mark_no_shows(now=None):
    now = now or now_utc()
    cut = now - timedelta(minutes=cfg("NOSHOW_GRACE_MINUTES"))
    done = 0
    with transaction() as cur:
        cur.execute(
            "SELECT r.reservation_id, r.user_id, r.start_time, s.slot_code, l.lot_name FROM Reservations r "
            "JOIN Parking_Slots s ON s.slot_id = r.slot_id JOIN Parking_Lots l ON l.lot_id = s.lot_id "
            "WHERE r.status = 'Confirmed' AND r.start_time <= %s "
            "ORDER BY r.reservation_id LIMIT %s FOR UPDATE OF r SKIP LOCKED", (cut, BATCH))
        for r in cur.fetchall():
            cur.execute("UPDATE Reservations SET status = 'No-show', updated_at = %s "
                        "WHERE reservation_id = %s AND status = 'Confirmed'", (now, r["reservation_id"]))
            write_event(cur, "reservation.no_show", "reservation", r["reservation_id"],
                        metadata={"slot": r["slot_code"], "lot": r["lot_name"]},
                        notify=notification("no_show", "Reservation marked as no-show",
                                            f"You did not check in to bay {r['slot_code']} within "
                                            f"{cfg('NOSHOW_GRACE_MINUTES')} minutes of {fmt_bkk(r['start_time'], '%H:%M')}. "
                                            "There is no penalty; you can book again."),
                        target_user_id=r["user_id"], who=SYSTEM)
            done += 1
    return done


def run_all(now=None):
    """Safe to call often. Never raises: a failure here must not break the page that called it."""
    try:
        return {"expired": expire_holds(now), "no_show": mark_no_shows(now)}
    except Exception:
        log.exception("expiry sweep failed")
        return {"expired": 0, "no_show": 0}
