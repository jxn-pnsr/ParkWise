import { me, logout, homeFor, redirectToLogin, api } from "./api.js";
import { html, icon, render, $ } from "./ui.js";
import { ROLE_LABEL, initials, fmtFullDate } from "./format.js";

// key -> used by `active`; href -> page file
export const NAV = {
  Customer: [
    { key: "dashboard", label: "Overview", icon: "house", href: "/customer/dashboard.html" },
    { key: "lots", label: "Find a lot", icon: "map-pin", href: "/customer/lots.html" },
    { key: "reservations", label: "My reservations", icon: "calendar", href: "/customer/reservations.html", countKey: "active_reservations" },
    { key: "parking", label: "Current parking", icon: "car", href: "/customer/parking.html" },
    { key: "vehicles", label: "My vehicles", icon: "car", href: "/customer/vehicles.html" },
    { key: "history", label: "Parking history", icon: "clock", href: "/customer/history.html" },
    { key: "payments", label: "Payments", icon: "credit-card", href: "/customer/payments.html" },
    // Account pages: reached from the bell and the avatar menu (top right), not the sidebar
    { key: "notifications", label: "Notifications", icon: "bell", href: "/customer/notifications.html", countKey: "unread_notifications", inMenu: false },
    { key: "preferences", label: "Preferences", icon: "sliders-horizontal", href: "/customer/preferences.html", inMenu: false },
    { key: "profile", label: "Profile", icon: "user", href: "/customer/profile.html", inMenu: false },
  ],
  Staff: [
    { section: "Operations" },
    { key: "dashboard", label: "Overview", icon: "layout-grid", href: "/staff/dashboard.html" },
    { key: "search", label: "Find a booking", icon: "search", href: "/staff/search.html" },
    { key: "slots", label: "Bay status", icon: "map-pin", href: "/staff/slots.html" },
    { key: "movements", label: "Entries and exits", icon: "car", href: "/staff/movements.html" },
  ],
  Admin: [
    { section: "Overview" },
    { key: "dashboard", label: "System overview", icon: "layout-grid", href: "/admin/dashboard.html" },
    { section: "Relational data (MySQL)" },
    { key: "users", label: "Users", icon: "users", href: "/admin/users.html" },
    { key: "vehicles", label: "Vehicles", icon: "car", href: "/admin/vehicles.html" },
    { key: "lots", label: "Lots and bays", icon: "map-pin", href: "/admin/lots.html" },
    { key: "reservations", label: "Reservations", icon: "circle-parking", href: "/admin/reservations.html" },
    { key: "payments", label: "Payments", icon: "credit-card", href: "/admin/payments.html" },
    { section: "Document data (MongoDB)" },
    { key: "sessions", label: "Parking sessions", icon: "clock", href: "/admin/sessions.html" },
    { key: "notifications", label: "Notifications", icon: "bell", href: "/admin/notifications.html" },
    { key: "preferences", label: "User preferences", icon: "sliders-horizontal", href: "/admin/preferences.html" },
    { key: "logs", label: "Activity log", icon: "file-text", href: "/admin/logs.html" },
    { section: "System" },
    { key: "outbox", label: "Integration outbox", icon: "refresh-cw", href: "/admin/outbox.html" },
    { key: "config", label: "System settings", icon: "settings", href: "/admin/config.html" },
    { key: "demo", label: "Transaction demo", icon: "flask-conical", href: "/admin/demo.html" },
  ],
};

// Call applySavedTheme() in <head> to avoid a flash; preferences sync later.
export function applyTheme(theme) {
  const t = theme === "dark" ? "dark" : "light";
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("pw-theme", t); } catch { /* storage blocked */ }
}

async function toggleTheme(user) {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  applyTheme(next);
  if (user && user.role === "Customer") {
    // keep User_Preferences (MongoDB) in sync; ignore if the endpoint is not ready yet
    try { await api.put("/me/preferences", { theme: next }, { redirectOn401: false }); } catch { /* not ready */ }
  }
}

// render
function navHtml(items, active, counts) {
  return items.map((it) => {
    if (it.section) return html`<div class="side-label">${it.section}</div>`;
    if (it.inMenu === false) return "";
    const n = it.countKey ? counts[it.countKey] : 0;
    return html`<a class="nav-item ${it.key === active ? "active" : ""}" href="${it.href}"
        ${it.key === active ? html`aria-current="page"` : ""}>
        ${icon(it.icon)}<span>${it.label}</span>${n ? html`<span class="nav-count">${n}</span>` : ""}</a>`;
  });
}

function shellHtml({ user, role, active, title, counts }) {
  const items = NAV[role];
  const current = items.find((i) => i.key === active);
  const pageTitle = title || current?.label || "";
  const showBell = role === "Customer";
  return html`
  <aside class="sidebar" aria-label="Main navigation">
    <a class="brand" href="${homeFor({ role })}">
      <span class="brand-mark">P</span><span class="brand-text">Park<b>Wise</b></span>
    </a>
    <nav class="nav">${navHtml(items, active, counts)}</nav>
    <div class="side-foot">
      <div class="me">
        <span class="avatar">${initials(user.full_name)}</span>
        <div class="me-text" title="${user.email}"><b>${user.full_name}</b><small>${ROLE_LABEL[role]}</small></div>
        <button class="icon-btn" data-logout title="Sign out" aria-label="Sign out">${icon("log-out")}</button>
      </div>
    </div>
  </aside>
  <div class="main">
    <header class="topbar">
      <button class="icon-btn menu-toggle" data-menu aria-label="Open menu">${icon("list")}</button>
      <div class="crumbs"><span>ParkWise</span>${icon("chevron-right", "icon icon-sm")}<b>${pageTitle}</b></div>
      <div class="topbar-right">
        <span class="today">${fmtFullDate()}</span>
        ${showBell ? html`
        <a class="icon-btn bell" href="/customer/notifications.html" aria-label="Notifications" data-bell>
          ${icon("bell", "icon icon-lg")}${counts.unread_notifications ? html`<span class="ping"></span>` : ""}</a>
        <div class="user-menu" data-user-menu>
          <button class="avatar-btn" type="button" data-user-btn aria-haspopup="true" aria-expanded="false" aria-label="Account menu">${initials(user.full_name)}</button>
          <div class="user-pop" role="menu">
            <a href="/customer/profile.html" role="menuitem">${icon("user")}Profile</a>
            <a href="/customer/preferences.html" role="menuitem">${icon("sliders-horizontal")}Preferences</a>
            <button type="button" data-theme-toggle role="menuitem">${icon("moon", "icon theme-dark")}${icon("sun", "icon theme-light")}Switch theme</button>
            <hr>
            <button type="button" data-logout role="menuitem">${icon("log-out")}Sign out</button>
          </div>
        </div>` : html`
        <button class="icon-btn" data-theme-toggle aria-label="Switch between light and dark theme" title="Switch theme">
          ${icon("moon", "icon theme-dark")}${icon("sun", "icon theme-light")}
        </button>`}
      </div>
    </header>
    <div class="content" data-content></div>
  </div>`;
}

function themeIconCss() {
  if ($("#pw-theme-icons")) return;
  const s = document.createElement("style");
  s.id = "pw-theme-icons";
  s.textContent = `[data-theme="dark"] .theme-dark, :root:not([data-theme="dark"]) .theme-light { display: none; }`;
  document.head.append(s);
}

function footerHtml() {
  return html`<footer class="footer"><span>© ${new Date().getFullYear()} ParkWise</span>
    <nav><a href="#">Help centre</a><a href="#">Terms of service</a></nav></footer>`;
}

/**
 * Wrap #page in the app layout.
 * @param {object} o
 * @param {"Customer"|"Staff"|"Admin"} o.role  role this page is for
 * @param {string} o.active  NAV key to highlight
 * @param {string} [o.title] breadcrumb title (defaults to the NAV label)
 * @param {object} [o.previewUser] render without login (styleguide only, shows no real data)
 * @returns {Promise<object>} the logged-in user
 */
export async function mountShell({ role, active, title, previewUser } = {}) {
  let user = previewUser;
  if (!user) {
    user = await me();
    if (!user) { redirectToLogin(); return new Promise(() => {}); }
    if (user.role !== role) { location.replace(homeFor(user)); return new Promise(() => {}); }
  }
  if (user.preferences?.theme) applyTheme(user.preferences.theme);

  const page = document.getElementById("page");
  const app = document.createElement("div");
  app.className = "app";
  app.dataset.role = role;
  render(app, shellHtml({ user, role, active, title, counts: user.counts || {} }));
  const content = $("[data-content]", app);
  content.append(page);
  content.insertAdjacentHTML("beforeend", String(footerHtml()));
  document.body.prepend(app);
  page.hidden = false;
  themeIconCss();

  app.querySelectorAll("[data-logout]").forEach((b) => (b.onclick = () => (previewUser ? (location.href = "/login.html") : logout())));
  app.querySelectorAll("[data-theme-toggle]").forEach((b) => (b.onclick = () => toggleTheme(previewUser ? null : user)));

  // avatar menu (customers): click to open, click outside or press Esc to close
  const menu = $("[data-user-menu]", app);
  if (menu) {
    const btn = $("[data-user-btn]", menu);
    const setOpen = (open) => { menu.classList.toggle("open", open); btn.setAttribute("aria-expanded", String(open)); };
    btn.onclick = (e) => { e.stopPropagation(); setOpen(!menu.classList.contains("open")); };
    document.addEventListener("click", (e) => { if (!menu.contains(e.target)) setOpen(false); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") setOpen(false); });
  }
  $("[data-menu]", app).onclick = () => app.classList.toggle("nav-open");
  document.title = `${title || NAV[role].find((i) => i.key === active)?.label || "ParkWise"} – ParkWise`;
  return user;
}

/** Fill the title + subtitle + actions at the top of a page. (Today's date is in the topbar.) */
export function pageHeader(el, { title, subtitle, actions = "" }) {
  render(el, html`<div>
      <h1>${title}</h1>
      ${subtitle ? html`<p>${subtitle}</p>` : ""}
    </div><div class="page-actions">${actions}</div>`);
}
