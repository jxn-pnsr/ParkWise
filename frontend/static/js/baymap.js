import { html, icon, render } from "./ui.js";

export const BAY_NOTE = { Available: "Available", Occupied: "Occupied", Reserved: "Reserved", Held: "Hold", Maintenance: "Closed" };
const CLS = { Available: "s-available", Occupied: "s-occupied", Reserved: "s-reserved", Held: "s-held", Maintenance: "s-maintenance" };

function bay(s, { selectedId, showPlates, pickable }) {
  const ev = s.slot_type === "EV";
  const note = showPlates && s.plate_number ? s.plate_number
    : (s.display_status === "Available" && ev ? "EV charging" : BAY_NOTE[s.display_status]);
  const inner = html`<span class="slot-code">${s.slot_code}${ev ? icon("zap") : ""}</span>
    <span class="slot-body"><span class="slot-note">${note}</span></span>`;
  const label = `${s.slot_code}${ev ? ", EV charging" : ""}, ${BAY_NOTE[s.display_status]}`;
  if (pickable && s.display_status === "Available") {
    return html`<button type="button" class="slot ${CLS[s.display_status]}" data-slot="${s.slot_id}"
      aria-pressed="${s.slot_id === selectedId}" aria-label="${label}">${inner}</button>`;
  }
  return html`<div class="slot ${CLS[s.display_status] || "s-maintenance"}" role="img" aria-label="${label}"
    ${s.overstay ? html`title="Overstaying"` : ""}>${inner}</div>`;
}

export function renderBayMap(el, { slots = [], lotName = "", level = "Level 1", selectedId = null, onPick = null,
                                   showPlates = false, legend = true } = {}) {
  const half = Math.ceil(slots.length / 2);
  const opts = { selectedId, showPlates, pickable: !!onPick };
  const top = slots.slice(0, half), bottom = slots.slice(half);
  render(el, html`
    <div class="slot-map">
      <div class="map-meta"><span>${level}</span><span class="go">Entrance at left ${icon("arrow-right")}</span></div>
      <div class="slot-row">${top.map((s) => bay(s, opts))}</div>
      <div class="lane"><span>Driving lane</span></div>
      <div class="slot-row" data-side="bottom">${bottom.map((s) => bay(s, opts))}</div>
      <div class="slot-map-bars" aria-hidden="true"><i></i><i></i></div>
      <div class="slot-map-foot"><span>Entry and exit</span><span>${lotName}</span></div>
    </div>
    ${legend ? html`<div class="legend">
      <span class="s-available">Available</span><span class="s-held">On hold</span><span class="s-reserved">Reserved</span>
      <span class="s-occupied">Occupied</span><span class="s-maintenance">Closed</span></div>` : ""}`);
  if (onPick) {
    el.querySelectorAll("[data-slot]").forEach((b) => (b.onclick = () => {
      const s = slots.find((x) => String(x.slot_id) === b.dataset.slot);
      onPick(s);
    }));
  }
}

export function mapSkeleton(el) {
  render(el, html`<div class="slot-map"><div class="skeleton" style="height:220px"></div></div>`);
}
