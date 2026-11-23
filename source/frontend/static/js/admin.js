// Shared building blocks for the admin pages: page setup, data table with
// paging, form dialog that shows API field errors, and small helpers.
import { mountShell, pageHeader } from "./shell.js";
import { html, icon, render, $, toast, toastError, setBusy, skeletonRows, emptyState, showFormErrors, clearFormErrors } from "./ui.js";

export async function adminPage({ active, title, subtitle, actions = "" }) {
  const user = await mountShell({ role: "Admin", active });
  pageHeader($("#head"), { title, subtitle, actions });
  return user;
}


export function dataTable(el, { columns, load, rowActions = null, wire = null, empty = "Nothing here yet", emptyIcon = "list" }) {
  let page = 1, rows = [];
  const cols = columns.length + (rowActions ? 1 : 0);
  render(el, html`<div class="table-wrap"><table class="table">
      <thead><tr>${columns.map((c) => html`<th class="${c.cls || ""}">${c.label}</th>`)}${rowActions ? html`<th class="actions"><span class="sr-only">Actions</span></th>` : ""}</tr></thead>
      <tbody></tbody></table></div><div class="flex between mt-2 small muted" data-pager></div>`);
  const body = el.querySelector("tbody"), pager = el.querySelector("[data-pager]");

  async function refresh(p = page) {
    page = p;
    render(body, skeletonRows(3, cols));
    let res;
    try { res = await load(page); } catch (err) {
      render(body, html`<tr><td colspan="${cols}">${emptyState({ iconName: "triangle-alert", title: "Could not load", text: err.message })}</td></tr>`);
      render(pager, "");
      return;
    }
    const list = Array.isArray(res) ? res : res.items;
    rows = list;
    if (!list.length) {
      render(body, html`<tr><td colspan="${cols}">${emptyState({ iconName: emptyIcon, title: empty })}</td></tr>`);
    } else {
      render(body, list.map((r) => html`<tr>${columns.map((c) => html`<td class="${c.cls || ""}">${c.render(r)}</td>`)}
        ${rowActions ? html`<td class="actions"><div class="flex" style="justify-content:flex-end;gap:6px">${rowActions(r)}</div></td>` : ""}</tr>`));
    }
    if (!Array.isArray(res) && res.total > res.per_page) {
      const pages = Math.ceil(res.total / res.per_page);
      render(pager, html`<span>${res.total} total · page ${res.page} of ${pages}</span><span class="flex">
        <button class="btn btn-secondary btn-sm" data-prev ${res.page <= 1 ? "disabled" : ""}>${icon("chevron-left", "icon icon-sm")} Previous</button>
        <button class="btn btn-secondary btn-sm" data-next ${res.page >= pages ? "disabled" : ""}>Next ${icon("chevron-right", "icon icon-sm")}</button></span>`);
      pager.querySelector("[data-prev]").onclick = () => refresh(page - 1);
      pager.querySelector("[data-next]").onclick = () => refresh(page + 1);
    } else {
      render(pager, html`<span>${Array.isArray(res) ? list.length : res.total} total</span>`);
    }
    wire?.(body, list);
  }
  return { refresh, rows: () => rows, find: (id, key) => rows.find((r) => String(r[key]) === String(id)) };
}

/** submit(values) is awaited; on ApiError the field errors are shown and the dialog stays open.
 * Resolves with submit()'s result, or null when cancelled. */
export function formDialog({ title, intro = "", fields, submitText = "Save", submit, danger = false }) {
  return new Promise((resolve) => {
    const dlg = document.createElement("dialog");
    dlg.className = "dialog wide";
    const field = (f) => {
      const id = `f-${f.name}`;
      const req = f.required ? "required" : "";
      let input;
      if (f.type === "select") {
        input = html`<select class="select" id="${id}" name="${f.name}" ${req}>${f.options.map(([v, l]) =>
          html`<option value="${v}" ${String(v) === String(f.value ?? "") ? "selected" : ""}>${l}</option>`)}</select>`;
      } else if (f.type === "textarea") {
        input = html`<textarea class="input textarea" id="${id}" name="${f.name}" rows="4" ${req} placeholder="${f.placeholder || ""}">${f.value ?? ""}</textarea>`;
      } else if (f.type === "checkbox") {
        return html`<label class="flex small" style="gap:8px;margin-top:6px"><input type="checkbox" name="${f.name}" ${f.value ? "checked" : ""}> ${f.label}</label>`;
      } else {
        input = html`<input class="input" id="${id}" name="${f.name}" type="${f.type || "text"}" value="${f.value ?? ""}" ${req}
          placeholder="${f.placeholder || ""}" ${f.type === "number" ? html`min="${f.min ?? 0}" step="1"` : ""} autocomplete="off">`;
      }
      return html`<div class="field"><label class="label" for="${id}">${f.label}</label>${input}${f.hint ? html`<span class="hint">${f.hint}</span>` : ""}</div>`;
    };
    render(dlg, html`<form novalidate><div class="dialog-body"><h3>${title}</h3>${intro ? html`<p>${intro}</p>` : ""}
        <div class="alert alert-error mt-2" data-form-error hidden></div>
        <div class="res-form mt-2">${fields.map(field)}</div></div>
      <div class="dialog-actions"><button type="button" class="btn btn-secondary" value="cancel">Cancel</button>
        <button type="submit" class="btn ${danger ? "btn-danger" : "btn-primary"}">${submitText}</button></div></form>`);
    document.body.append(dlg);
    const form = dlg.querySelector("form");
    const close = (v) => { dlg.close(); dlg.remove(); resolve(v); };
    dlg.querySelector("[value=cancel]").onclick = () => close(null);
    dlg.addEventListener("cancel", (e) => { e.preventDefault(); close(null); });
    form.onsubmit = async (e) => {
      e.preventDefault();
      clearFormErrors(form);
      const values = {};
      for (const f of fields) {
        const el = form.elements[f.name];
        if (f.type === "checkbox") values[f.name] = el.checked;
        else if (f.type === "number") values[f.name] = el.value === "" ? null : Number(el.value);
        else values[f.name] = el.value.trim();
      }
      const missing = Object.fromEntries(fields.filter((f) => f.required && f.type !== "checkbox" && (values[f.name] === "" || values[f.name] === null))
        .map((f) => [f.name, `${f.label} is required.`]));
      if (Object.keys(missing).length) return showFormErrors(form, { fields: missing });
      const btn = form.querySelector("[type=submit]");
      setBusy(btn, true);
      try { close(await submit(values)); }
      catch (err) { showFormErrors(form, err); setBusy(btn, false); }
    };
    dlg.showModal();
    form.querySelector("input, select, textarea")?.focus();
  });
}

/** Run an API call from a button: busy state, toast on success, toast on error. */
export async function act(btn, fn, success) {
  setBusy(btn, true);
  try {
    const out = await fn();
    if (success) toast(typeof success === "function" ? success(out) : success);
    return out;
  } catch (err) {
    toast(err.message, "error", 7000);
    return undefined;
  } finally {
    if (btn.isConnected) setBusy(btn, false);
  }
}

/** Read <select>/<input> filters inside a toolbar into an object (empty values dropped). */
export function filterValues(root) {
  const out = {};
  root.querySelectorAll("[data-filter]").forEach((el) => { if (el.value !== "") out[el.dataset.filter] = el.value; });
  return out;
}

export function onFilters(root, fn) {
  let t;
  root.querySelectorAll("[data-filter]").forEach((el) => {
    el.addEventListener(el.tagName === "INPUT" && el.type === "search" ? "input" : "change", () => { clearTimeout(t); t = setTimeout(fn, 250); });
  });
}

export const yesNo = (v) => (v ? html`<span class="badge tone-ok">Yes</span>` : html`<span class="badge tone-mute">No</span>`);
export const toLocalInput = (iso) => {
  if (!iso) return "";
  const d = new Date(new Date(iso).getTime() + 7 * 3600000);   // Bangkok wall time for <input type=datetime-local>
  return d.toISOString().slice(0, 16);
};
export const fromLocalInput = (v) => (v ? `${v}:00+07:00` : null);
export { toastError };
