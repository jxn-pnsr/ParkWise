# ParkWise Frontend

Plain HTML + CSS + JavaScript modules, served by Flask from `static/`.
No CDN, no build step: fonts and icons are local in `static/vendor/`.

Run the backend (`python app.py`) and open http://127.0.0.1:5000

| Page | URL | Status |
| --- | --- | --- |
| Landing | `/` | design |
| Login / Register | `/login.html`, `/register.html` | **live (phase 1)** |
| Customer overview | `/customer/dashboard.html` | **live (phase 1)**: real counts, spending, bay map |
| Find a lot + booking | `/customer/lots.html` | **live (phase 1)**: pick bay → hold 5 min → confirm |
| My reservations | `/customer/reservations.html` | **live (phase 1)**: upcoming / past, confirm, cancel |
| My vehicles | `/customer/vehicles.html` | **live (phase 1)**: add, edit, main vehicle, delete |
| Profile | `/customer/profile.html` | **live (phase 1)**: details + password |
| Current parking | `/customer/parking.html` | **live (phase 2)**: bay, time left, fee if you leave now |
| Parking history | `/customer/history.html` | **live (phase 2)**: from MongoDB |
| Payments | `/customer/payments.html` | **live (phase 2)**: pay by QR or cash, receipt |
| Notifications | `/customer/notifications.html` | **live (phase 2)**: read, read all, delete |
| Preferences | `/customer/preferences.html` | **live (phase 2)**: theme, notification switches, main vehicle |
| Staff overview | `/staff/dashboard.html` | **live (phase 2)**: arrivals, parked cars, overstay alerts |
| Find a booking | `/staff/search.html` | **live (phase 2)**: search by plate → check in / check out |
| Bay status | `/staff/slots.html` | **live (phase 2)**: bay map with plates |
| Entries and exits | `/staff/movements.html` | **live (phase 2)**: day log |
| Admin pages + transaction demo | `/admin/*.html` | phase 3 |
| Component gallery (no login needed) | `/styleguide.html` (`?role=Staff` or `?role=Admin`) | |

## Files

| File | What it does |
| --- | --- |
| `css/app.css` | Every style: colours, light/dark theme, layout, buttons, forms, panels, tables, badges, bay map, toast, dialog |
| `js/api.js` | Calls the backend. Returns `data`, throws `ApiError` (`code`, `message`, `fields`). Adds the CSRF header and sends you to login on 401 |
| `js/format.js` | Bangkok time, Gregorian dates in English, THB money, and the label and colour for every status |
| `js/ui.js` | `html\`\`` (safe HTML), `icon()`, `toast()`, form errors, confirm dialog, Hold countdown, empty state |
| `js/shell.js` | Sidebar + topbar for each role, login check, theme switch. Menu list = `NAV` |
| `js/baymap.js` | Draws the bay map from `/api/lots/<id>/slots` (used by overview, booking and staff pages) |
| `js/staff.js` | Check-in / check-out dialogs and booking cards shared by the staff pages |
| `_template.html` | **Copy this file to start a new page** |

## Making a new page

1. Copy `_template.html` to e.g. `customer/vehicles.html`.
2. Set `role` and `active` in `mountShell(...)` (keys are in `NAV` in `js/shell.js`).
3. Load data with `api.get("/me/vehicles")`, then draw it with `render(el, html\`...\`)`.
4. Look at `styleguide.html` for the markup of every component.

## Rules

1. **Never** put API data into `innerHTML` directly. Use the `html` tag, which escapes every `${value}` (security rule 4: XSS).
2. Never add a `<link>` or `<script>` that points to another website. Download it into `static/vendor/`.
3. Show times with `format.js` (the API sends UTC and the page shows Bangkok time). Send times with `toApiDateTime()`.
4. Hiding a button is only for convenience. The backend still checks every permission.
5. The Hold countdown is only for display. Always call the confirm API, and handle the `HOLD_EXPIRED` error.

## Phase 2 CSS

New styles are at the end of `css/app.css` ("phase 2"): 4-column stats, staff search cards, notification list, switches, receipt.
Dark theme now uses Plus Jakarta Sans too (the old "Barlow" font was not in `vendor/`, so dark pages fell back to a system font), and outline buttons are readable in dark.

## Licenses

Plus Jakarta Sans: SIL Open Font License 1.1. Icons: Lucide (ISC).
