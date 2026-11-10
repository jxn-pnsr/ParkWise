import { RESERVATION_STATUS, DISPLAY_LABEL, PAYMENT_STATUS, OUTBOX_STATUS, SLOT_STATUS } from "./format.js";

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;", "`": "&#96;" };
export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"'`]/g, (c) => ESC[c]);
}

class Raw { constructor(s) { this.s = s; } toString() { return this.s; } }
export const raw = (s) => new Raw(String(s));

function part(v) {
  if (v instanceof Raw) return v.s;
  if (Array.isArray(v)) return v.map(part).join("");
  if (v === null || v === undefined || v === false) return "";
  return escapeHtml(v);
}

export function html(strings, ...values) {
  let out = strings[0];
  values.forEach((v, i) => { out += part(v) + strings[i + 1]; });
  return new Raw(out);
}

export function render(el, content) {
  el.innerHTML = part(content);
  return el;
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// icons
const SPRITE = "/vendor/icons/sprite.svg";
export function icon(name, cls = "icon") {
  return raw(`<svg class="${escapeHtml(cls)}" aria-hidden="true"><use href="${SPRITE}#${escapeHtml(name)}"/></svg>`);
}

// badges
function badgeFrom(map, key, fallbackTone = "mute") {
  const s = map[key] || { label: key ?? "–", tone: fallbackTone };
  return html`<span class="badge tone-${s.tone}">${s.label}</span>`;
}
export const reservationBadge = (status, displayLabel) =>
  displayLabel && DISPLAY_LABEL[displayLabel] ? badgeFrom(DISPLAY_LABEL, displayLabel) : badgeFrom(RESERVATION_STATUS, status);
export const paymentBadge = (status) => badgeFrom(PAYMENT_STATUS, status);
export const outboxBadge = (status) => badgeFrom(OUTBOX_STATUS, status);
export const slotClass = (displayStatus) => (SLOT_STATUS[displayStatus] || SLOT_STATUS.Maintenance).cls;
export const slotLabel = (displayStatus) => (SLOT_STATUS[displayStatus] || { label: displayStatus }).label;

// toast
let toastBox = null;
export function toast(message, type = "success", timeout = 3500) {
  if (!toastBox) {
    toastBox = document.createElement("div");
    toastBox.className = "toasts";
    toastBox.setAttribute("role", "status");
    toastBox.setAttribute("aria-live", "polite");
    document.body.append(toastBox);
  }
  const name = type === "error" ? "circle-x" : type === "info" ? "info" : "circle-check";
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  render(el, html`${icon(name)}<span>${message}</span><button class="close" aria-label="Close">${icon("x", "icon icon-sm")}</button>`);
  el.querySelector(".close").onclick = () => el.remove();
  toastBox.append(el);
  if (timeout) setTimeout(() => el.remove(), timeout);
}

/** Show any error from api.js as a toast. */
export function toastError(err) {
  toast(err?.message || "Something went wrong. Please try again.", "error", 5000);
}

// buttons / forms
export function setBusy(button, busy) {
  if (!button) return;
  button.disabled = busy;
  button.setAttribute("aria-busy", busy ? "true" : "false");
}

/** {name: trimmed value} for every named field in a form. */
export function formValues(form) {
  const out = {};
  for (const [k, v] of new FormData(form).entries()) out[k] = typeof v === "string" ? v.trim() : v;
  return out;
}

export function clearFormErrors(form) {
  $$(".field.invalid", form).forEach((f) => f.classList.remove("invalid"));
  const box = $("[data-form-error]", form);
  if (box) { box.hidden = true; box.textContent = ""; }
}

/**
 * Put an ApiError (or {field: message} object) on the form.
 * Field messages go under <input name="field">; anything else goes to [data-form-error].
 */
export function showFormErrors(form, err) {
  clearFormErrors(form);
  const fields = err?.fields || (err && !err.message ? err : null);
  let shown = false;
  if (fields) {
    for (const [name, msg] of Object.entries(fields)) {
      const input = form.elements[name];
      const field = input?.closest?.(".field");
      if (!field) continue;
      field.classList.add("invalid");
      let slot = $(".field-error", field);
      if (!slot) { slot = document.createElement("div"); slot.className = "field-error"; field.append(slot); }
      slot.textContent = msg;
      if (!shown) input.focus();
      shown = true;
    }
  }
  if (err?.message) {
    const box = $("[data-form-error]", form);
    if (box) {
      render(box, html`${icon("triangle-alert")}<span>${err.message}</span>`);
      box.hidden = false;
    } else if (!shown) {
      toastError(err);
    }
  }
}

// confirm dialog
export function confirmDialog({ title, message = "", confirmText = "Confirm", cancelText = "Cancel", danger = false }) {
  return new Promise((resolve) => {
    const dlg = document.createElement("dialog");
    dlg.className = "dialog";
    render(dlg, html`
      <div class="dialog-body"><h3>${title}</h3>${message ? html`<p>${message}</p>` : ""}</div>
      <div class="dialog-actions">
        <button class="btn btn-secondary" value="cancel">${cancelText}</button>
        <button class="btn ${danger ? "btn-danger" : "btn-primary"}" value="ok">${confirmText}</button>
      </div>`);
    document.body.append(dlg);
    const done = (result) => { dlg.close(); dlg.remove(); resolve(result); };
    dlg.querySelector('[value="ok"]').onclick = () => done(true);
    dlg.querySelector('[value="cancel"]').onclick = () => done(false);
    dlg.addEventListener("cancel", (e) => { e.preventDefault(); done(false); });
    dlg.showModal();
  });
}

// countdown (5-minute hold)
/**
 * Count down to `expiresAt` using the server clock (serverTime from the API)
 * so a wrong phone clock does not matter. The backend still decides expiry.
 * Returns a stop() function.
 */
export function startCountdown(el, expiresAt, serverTime, onExpire) {
  const skew = serverTime ? new Date(serverTime).getTime() - Date.now() : 0;
  const end = new Date(expiresAt).getTime();
  let timer;
  const tick = () => {
    const left = Math.max(0, end - (Date.now() + skew));
    const m = Math.floor(left / 60000), s = Math.floor((left % 60000) / 1000);
    el.textContent = `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
    if (left <= 0) { clearInterval(timer); onExpire?.(); }
  };
  tick();
  timer = setInterval(tick, 250);
  return () => clearInterval(timer);
}

// misc
export function debounce(fn, ms = 300) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

export function skeletonRows(n = 3, cols = 4) {
  const row = html`<tr>${Array.from({ length: cols }, () => html`<td><div class="skeleton" style="height:14px"></div></td>`)}</tr>`;
  return Array.from({ length: n }, () => row);
}

export function emptyState({ iconName = "list", title, text = "", action = "" }) {
  return html`<div class="empty"><div class="ic">${icon(iconName, "icon icon-lg")}</div><b>${title}</b>${text ? html`<p>${text}</p>` : ""}${action}</div>`;
}

export function getParam(name) {
  return new URLSearchParams(location.search).get(name);
}
