"""Simulated payment (spec 8.5). No bank or payment gateway is called.

Pay (one transaction): lock the payment -> must be Pending (or Failed, to try
again) -> Paid + method + paid_at -> outbox payment.paid -> COMMIT.
The QR code is generated on this server (no outside service).
"""
import io

from core.errors import Conflict, Forbidden, NotFound
from core.timeutil import now_utc
from db.mysql import fetch_all, fetch_one, transaction
from services.outbox import notification, write_event

METHODS = ("Cash", "QR")

PAYMENT_SELECT = """
SELECT p.*, r.start_time, r.end_time, r.checked_in_at, r.checked_out_at, r.duration_hours, r.is_overstay,
       v.plate_number, v.province, s.slot_code, l.lot_name, u.full_name AS customer_name, u.email AS customer_email
FROM Payments p
JOIN Reservations r      ON r.reservation_id = p.reservation_id
JOIN Customer_Vehicles v ON v.vehicle_id = r.vehicle_id
JOIN Parking_Slots s     ON s.slot_id = r.slot_id
JOIN Parking_Lots l      ON l.lot_id = s.lot_id
JOIN Users u             ON u.user_id = p.user_id
"""


def serialize(p):
    return {
        "payment_id": p["payment_id"], "reservation_id": p["reservation_id"], "user_id": p["user_id"],
        "base_fee": p["base_fee"], "overstay_fee": p["overstay_fee"], "total_amount": p["total_amount"],
        "method": p["method"], "status": p["status"], "paid_at": p["paid_at"], "created_at": p["created_at"],
        "is_deleted": bool(p["is_deleted"]),
        "reservation": {"start_time": p["start_time"], "end_time": p["end_time"],
                        "checked_in_at": p["checked_in_at"], "checked_out_at": p["checked_out_at"],
                        "duration_hours": p["duration_hours"], "is_overstay": bool(p["is_overstay"]),
                        "slot_code": p["slot_code"], "lot_name": p["lot_name"],
                        "plate_number": p["plate_number"], "province": p["province"]},
        "customer": {"full_name": p["customer_name"], "email": p["customer_email"]},
    }


def get(payment_id):
    p = fetch_one(PAYMENT_SELECT + " WHERE p.payment_id = %s AND p.is_deleted = 0", (payment_id,))
    if not p:
        raise NotFound("PAYMENT_NOT_FOUND", "Payment not found.")
    return p


def list_for_user(user_id, status=None):
    sql, args = PAYMENT_SELECT + " WHERE p.user_id = %s AND p.is_deleted = 0", [user_id]
    if status:
        sql += " AND p.status = %s"
        args.append(status)
    return [serialize(p) for p in fetch_all(sql + " ORDER BY (p.status = 'Paid'), p.created_at DESC", args)]


def pay(user, payment_id, method):
    now = now_utc()
    with transaction() as cur:
        cur.execute("SELECT p.*, s.slot_code FROM Payments p JOIN Reservations r ON r.reservation_id = p.reservation_id "
                    "JOIN Parking_Slots s ON s.slot_id = r.slot_id "
                    "WHERE p.payment_id = %s AND p.is_deleted = 0 FOR UPDATE OF p", (payment_id,))
        p = cur.fetchone()
        if not p:
            raise NotFound("PAYMENT_NOT_FOUND", "Payment not found.")
        if p["user_id"] != user["user_id"]:
            raise Forbidden()
        if p["status"] == "Paid":
            raise Conflict("ALREADY_PAID", "This payment has already been paid.")
        cur.execute("UPDATE Payments SET status = 'Paid', method = %s, paid_at = %s, updated_at = %s "
                    "WHERE payment_id = %s", (method, now, now, payment_id))
        write_event(cur, "payment.paid", "payment", payment_id,
                    metadata={"reservation_id": p["reservation_id"], "method": method, "amount": p["total_amount"]},
                    notify=notification("payment_paid", "Payment received",
                                        f"Thank you. THB {p['total_amount']} for bay {p['slot_code']} was paid by "
                                        f"{'cash' if method == 'Cash' else 'QR'}."),
                    target_user_id=p["user_id"])
    return serialize(get(payment_id))


def qr_svg(payment):
    """A QR code made locally (simulated PromptPay-style text; no bank is involved)."""
    import qrcode
    import qrcode.image.svg

    text = f"PARKWISE|PAYMENT|{payment['payment_id']}|THB|{payment['total_amount']}|SIMULATED"
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue()
