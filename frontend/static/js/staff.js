// Check-in / check-out actions shared by every staff page.
//
//   import { checkIn, checkOut, reservationCard } from "/js/staff.js";
//   await checkIn(reservation);   // asks, calls the API, shows the result; returns true on success
//
// The backend checks the rules (time window, previous car still parked, status).
// These dialogs only show what will happen and the result.

import { api } from "./api.js";
import { html, icon, render, toast, confirmDialog, reservationBadge } from "./ui.js";
import { fmtMoney, fmtRange, fmtTime, fmtDuration, fmtDateTime } from "./format.js";

export function infoDialog(title, body, { button = "Done" } = {}) {
  return new Promise((resolve) => {
    const dlg = document.createElement("dialog");
    dlg.className = "dialog wide";
    render(dlg, html`<div class="dialog-body"><h3>${title}</h3><div class="mt-2">${body}</div></div>
      <div class="dialog-actions"><button class="btn btn-primary" value="ok">${button}</button></div>`);
    document.body.append(dlg);
    const done = () => { dlg.close(); dlg.remove(); resolve(); };
    dlg.querySelector("[value=ok]").onclick = done;
    dlg.addEventListener("cancel", (e) => { e.preventDefault(); done(); });
    dlg.showModal();
  });
}

export async function checkIn(r) {
  const ok = await confirmDialog({
    title: `Check in ${r.vehicle.plate_number}?`,
    message: `Bay ${r.slot.slot_code} (${r.slot.lot_name}) · ${fmtRange(r.start_time, r.end_time)} · ${r.customer?.full_name || ""}`,
    confirmText: "Check in",
  });
  if (!ok) return false;
  try {
    await api.post(`/staff/reservations/${r.reservation_id}/checkin`);
    toast(`${r.vehicle.plate_number} checked in to bay ${r.slot.slot_code}.`);
    return true;
  } catch (err) {
    toast(err.message, "error", 8000);   // e.g. too early, previous car still in the bay
    return false;
  }
}

export async function checkOut(r) {
  const ok = await confirmDialog({
    title: `Check out ${r.vehicle.plate_number}?`,
    message: `Bay ${r.slot.slot_code} · parked since ${fmtTime(r.checked_in_at)} · booked until ${fmtTime(r.end_time)}. The fee is calculated now.`,
    confirmText: "Check out",
  });
  if (!ok) return false;
  try {
    const out = await api.post(`/staff/reservations/${r.reservation_id}/checkout`);
    const f = out.fee;
    await infoDialog(`${out.vehicle.plate_number} checked out`, html`
      <div class="receipt"><div class="kv">
        <div><span class="k">Parked</span><span class="v">${fmtDuration(f.minutes_parked)} (${fmtTime(out.checked_in_at)}–${fmtTime(out.checked_out_at)})</span></div>
        <div><span class="k">Parking fee (${f.billed_hours} h)</span><span class="v">${fmtMoney(f.base_fee, 2)}</span></div>
        <div><span class="k">Overstay${f.overstay_hours ? ` (${f.overstay_hours} h)` : ""}</span><span class="v">${fmtMoney(f.overstay_fee, 2)}</span></div>
      </div>
      <div class="total-row"><span>Total due</span><span class="amount">${fmtMoney(f.total_amount, 2)}</span></div></div>
      <p class="hint mt-2">The customer pays in the app (cash or QR). The payment is now waiting as Unpaid.</p>`);
    return true;
  } catch (err) {
    toast(err.message, "error", 8000);
    return false;
  }
}

/** One booking as a card with the right action button. */
export function reservationCard(r) {
  const note = r.checkin_note === "too_early" ? `Check-in opens ${fmtDateTime(r.checkin_window.from)}`
    : r.checkin_note === "too_late" ? "Check-in window closed" : "";
  return html`<article class="result" data-res="${r.reservation_id}">
    <div class="result-top">
      <div><div class="result-plate">${r.vehicle.plate_number}</div><div class="muted small">${r.vehicle.province}</div></div>
      ${reservationBadge(r.status, r.display_label)}
    </div>
    <div class="kv">
      <div><span class="k">Customer</span><span class="v">${r.customer?.full_name || "–"}</span></div>
      <div><span class="k">Bay</span><span class="v">${r.slot.slot_code} · ${r.slot.lot_name}</span></div>
      <div><span class="k">Booked</span><span class="v">${fmtRange(r.start_time, r.end_time)}</span></div>
      ${r.checked_in_at ? html`<div><span class="k">Checked in</span><span class="v">${fmtDateTime(r.checked_in_at)}</span></div>` : ""}
      ${r.customer?.phone ? html`<div><span class="k">Phone</span><span class="v">${r.customer.phone}</span></div>` : ""}
    </div>
    ${note ? html`<p class="hint">${icon("clock", "icon icon-sm")} ${note}</p>` : ""}
    <div class="result-actions">
      ${r.can_checkin ? html`<button class="btn btn-primary btn-rect" data-in="${r.reservation_id}">${icon("circle-check")} Check in</button>` : ""}
      ${r.can_checkout ? html`<button class="btn btn-primary btn-rect" data-out="${r.reservation_id}">${icon("log-out")} Check out</button>` : ""}
    </div>
  </article>`;
}

/** Wire the buttons made by reservationCard / tables. `find(id)` returns the reservation; `after()` reloads. */
export function wireActions(root, find, after) {
  root.querySelectorAll("[data-in]").forEach((b) => (b.onclick = async () => { if (await checkIn(find(b.dataset.in))) after(); }));
  root.querySelectorAll("[data-out]").forEach((b) => (b.onclick = async () => { if (await checkOut(find(b.dataset.out))) after(); }));
}
