"""Overstay warnings (spec 8.4). Run by the scheduler every minute.

* OVERSTAY_WARN_MINUTES (15) before end_time: "your time ends soon" notification.
* After end_time: is_overstay = 1 and an overstay.detected event. If another
  booking is waiting for the same bay, the event says so and the staff pages
  show a warning. Nobody is forced out automatically (spec limitation).
"""
from datetime import timedelta

from core.settings import cfg
from core.timeutil import fmt_bkk, now_utc
from db.mysql import transaction
from services.outbox import SYSTEM, notification, write_event

SELECT = ("SELECT r.reservation_id, r.user_id, r.slot_id, r.end_time, s.slot_code, l.lot_name, v.plate_number "
          "FROM Reservations r JOIN Parking_Slots s ON s.slot_id = r.slot_id "
          "JOIN Parking_Lots l ON l.lot_id = s.lot_id JOIN Customer_Vehicles v ON v.vehicle_id = r.vehicle_id ")


def send_end_warnings(now=None):
    now = now or now_utc()
    soon = now + timedelta(minutes=cfg("OVERSTAY_WARN_MINUTES"))
    done = 0
    with transaction() as cur:
        cur.execute(SELECT + "WHERE r.status = 'In Use' AND r.end_warning_sent = 0 AND r.end_time <= %s "
                    "AND r.end_time > %s FOR UPDATE OF r SKIP LOCKED", (soon, now))
        for r in cur.fetchall():
            cur.execute("UPDATE Reservations SET end_warning_sent = 1 WHERE reservation_id = %s", (r["reservation_id"],))
            write_event(cur, "overstay.warning", "reservation", r["reservation_id"],
                        metadata={"slot": r["slot_code"], "lot": r["lot_name"]},
                        notify=notification("overstay_warning", "Your parking time ends soon",
                                            f"Bay {r['slot_code']} is booked until {fmt_bkk(r['end_time'], '%H:%M')}. "
                                            f"After {cfg('OVERSTAY_GRACE_MINUTES')} minutes extra, overstay fees apply "
                                            f"(THB {cfg('OVERSTAY_TIER1_RATE')} for the first hour, then THB "
                                            f"{cfg('OVERSTAY_TIER2_RATE')} per hour)."),
                        target_user_id=r["user_id"], who=SYSTEM)
            done += 1
    return done


def detect_overstays(now=None):
    now = now or now_utc()
    done = 0
    with transaction() as cur:
        late_from = now - timedelta(minutes=cfg("OVERSTAY_GRACE_MINUTES"))
        cur.execute(SELECT + "WHERE r.status = 'In Use' AND r.overstay_notified = 0 AND r.end_time < %s "
                    "FOR UPDATE OF r SKIP LOCKED", (late_from,))
        for r in cur.fetchall():
            cur.execute("SELECT reservation_id FROM Reservations WHERE slot_id = %s AND status IN ('Held','Confirmed') "
                        "AND start_time >= %s ORDER BY start_time LIMIT 1", (r["slot_id"], r["end_time"]))
            nxt = cur.fetchone()
            cur.execute("UPDATE Reservations SET is_overstay = 1, overstay_notified = 1, updated_at = %s "
                        "WHERE reservation_id = %s", (now, r["reservation_id"]))
            write_event(cur, "overstay.detected", "reservation", r["reservation_id"],
                        metadata={"slot": r["slot_code"], "lot": r["lot_name"], "plate": r["plate_number"],
                                  "next_reservation_id": nxt["reservation_id"] if nxt else None},
                        notify=notification("overstay", "You are over your booked time",
                                            f"Your booking for bay {r['slot_code']} ended at "
                                            f"{fmt_bkk(r['end_time'], '%H:%M')} and the "
                                            f"{cfg('OVERSTAY_GRACE_MINUTES')}-minute grace period is over. "
                                            "Overstay fees now apply."
                                            + (" Another customer has booked this bay next." if nxt else "")),
                        target_user_id=r["user_id"], who=SYSTEM)
            done += 1
    return done

