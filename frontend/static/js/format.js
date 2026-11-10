// Display helpers. The API always sends UTC ("...Z"); everything shown is Asia/Bangkok.
// Dates use the Gregorian calendar in English.

const TZ = "Asia/Bangkok";
const LOCALE = "en-GB";
const toDate = (v) => (v instanceof Date ? v : new Date(v));
const fmt = (opts, locale = LOCALE) => new Intl.DateTimeFormat(locale, { timeZone: TZ, ...opts });

/** "Monday 9 November 2026" */
export function fmtFullDate(v = new Date()) {
  return fmt({ weekday: "long", day: "numeric", month: "long", year: "numeric" }).format(toDate(v)).replace(",", "");
}

/** "9 Nov 2026" */
export function fmtDate(v) {
  if (!v) return "–";
  return fmt({ day: "numeric", month: "short", year: "numeric" }).format(toDate(v));
}

/** "10:00" */
export function fmtTime(v) {
  if (!v) return "–";
  return fmt({ hour: "2-digit", minute: "2-digit", hour12: false }).format(toDate(v));
}

/** "Mon 9 Nov, 10:00" */
export function fmtDateTime(v) {
  if (!v) return "–";
  const d = toDate(v);
  return `${fmt({ weekday: "short", day: "numeric", month: "short" }).format(d).replace(",", "")}, ${fmtTime(d)}`;
}

/** "Mon 10:00–12:00" (same day) or "Mon 9 Nov, 22:00 – Tue 10 Nov, 02:00" */
export function fmtRange(start, end) {
  if (!start || !end) return "–";
  const s = toDate(start), e = toDate(end);
  const day = (d) => fmt({ year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
  if (day(s) === day(e)) {
    return `${fmt({ weekday: "short" }).format(s)} ${fmtTime(s)}–${fmtTime(e)}`;
  }
  return `${fmtDateTime(s)} – ${fmtDateTime(e)}`;
}

/** 40 -> "THB 40", 40.5 -> "THB 40.50", fmtMoney(40, 2) -> "THB 40.00" */
export function fmtMoney(n, decimals) {
  if (n === null || n === undefined || n === "") return "–";
  const num = Number(n);
  const d = decimals ?? (Number.isInteger(num) ? 0 : 2);
  return "THB " + num.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
}

/** 75 -> "1 h 15 min" */
export function fmtDuration(minutes) {
  if (minutes === null || minutes === undefined) return "–";
  const h = Math.floor(minutes / 60), m = Math.round(minutes % 60);
  if (!h) return `${m} min`;
  return m ? `${h} h ${m} min` : `${h} h`;
}

export function fmtNumber(n) {
  return n === null || n === undefined ? "–" : Number(n).toLocaleString("en-US");
}

/** Plate as one line: "1AB 2345, Bangkok" */
export function fmtPlate(vehicle) {
  if (!vehicle) return "–";
  return vehicle.province ? `${vehicle.plate_number}, ${vehicle.province}` : vehicle.plate_number;
}

/** Date + time picked on the page (Bangkok) -> value the API accepts. */
export function toApiDateTime(dateStr, timeStr) {
  return `${dateStr}T${timeStr.length === 5 ? timeStr + ":00" : timeStr}+07:00`;
}

/** "YYYY-MM-DD" of today in Bangkok (for <input type=date> min/max). */
export function bkkDateString(v = new Date(), addDays = 0) {
  const d = new Date(toDate(v).getTime() + addDays * 86400000);
  return fmt({ year: "numeric", month: "2-digit", day: "2-digit" }, "en-CA").format(d);
}

export function initials(name = "") {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return (parts[0]?.[0] || "?").toUpperCase();
}

// status labels

export const RESERVATION_STATUS = {
  "Held":      { label: "Held",          tone: "warn" },
  "Confirmed": { label: "Confirmed",     tone: "info" },
  "In Use":    { label: "Parked",        tone: "ok" },
  "Completed": { label: "Completed",     tone: "mute" },
  "Cancelled": { label: "Cancelled",     tone: "mute" },
  "Expired":   { label: "Hold expired",  tone: "mute" },
  "No-show":   { label: "No-show",       tone: "bad" },
};

// Extra labels the backend may send in `display_label` (not stored in DB)
export const DISPLAY_LABEL = {
  "awaiting_checkin": { label: "Awaiting check-in", tone: "warn" },
  "overstay":         { label: "Overstay",          tone: "bad" },
};

export const SLOT_STATUS = {
  Available:   { label: "Available",   cls: "s-available" },
  Held:        { label: "Held",        cls: "s-held" },
  Reserved:    { label: "Reserved",    cls: "s-reserved" },
  Occupied:    { label: "Occupied",    cls: "s-occupied" },
  Maintenance: { label: "Closed",      cls: "s-maintenance" },
};

export const PAYMENT_STATUS = {
  Pending: { label: "Unpaid", tone: "warn" },
  Paid:    { label: "Paid",   tone: "ok" },
  Failed:  { label: "Failed", tone: "bad" },
};

export const OUTBOX_STATUS = {
  Pending: { label: "Queued", tone: "warn" },
  Sent:    { label: "Sent",   tone: "ok" },
  Failed:  { label: "Failed", tone: "bad" },
};

export const BOOKING_TYPE = { Advance: "Book ahead", Instant: "Book now" };
export const ROLE_LABEL = { Customer: "Customer", Staff: "Staff", Admin: "Administrator" };
export const PAYMENT_METHOD = { Cash: "Cash", QR: "PromptPay QR (simulated)" };
