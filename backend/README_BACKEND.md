# ParkWise Backend

Flask + MySQL 8 + MongoDB. Everything under `/api` returns JSON.
The frontend lives in `../frontend/static` (served from `/`).

## Setup (first time)

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows   (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env            # then put your MySQL password in .env

python ../database/reset_demo.py  # builds MySQL (schema + seed) and fills MongoDB
```

MongoDB the first time only: `mongosh "mongodb://127.0.0.1:27017/parkwise" ../database/mongo_init.js`, then run `reset_demo.py` again.

## Run

```bash
python app.py                     # http://127.0.0.1:5000
```

Check: http://127.0.0.1:5000/api/health. `mongo: "down"` is allowed: booking keeps working on MySQL and outbox events wait.

Demo accounts (password `Passw0rd!`): `somchai@example.com` (customer), `staff1@parkwise.local`, `admin@parkwise.local`.

## Tests

```bash
python -m pytest -q
```

Tests use a separate database, **parkwise_test**, rebuilt automatically from `../database`, so they never change your demo data. Tests that need MySQL are skipped if it is not reachable.

## Folder layout

| Path | What it holds | Phase |
| --- | --- | --- |
| `config.py` | Settings from `.env` | 1 |
| `db/mysql.py` | Connection, `transaction()` (commit or rollback), deadlock retry | 1 |
| `db/mongo.py` | MongoDB client, simulated outage switch | 1 |
| `core/timeutil.py` | `now_utc()`, the only clock | 1 |
| `core/errors.py`, `responses.py` | JSON envelope and error codes | 1 |
| `core/security.py` | Password hashing, CSRF check | 2 |
| `core/auth.py` | `@login_required(role)`, `ensure_owner()`, current user | 2 |
| `core/settings.py` | Business rules from the `System_Config` table | 2 |
| `core/validate.py` | Checks request input, returns every field error at once | 2 |
| `services/users.py` | Register, login + lockout, profile, password | 2 |
| `services/vehicles.py` | Customer cars, main car, soft delete | 2 |
| `services/availability.py` | Bay colours for a time window | 2 |
| `services/booking.py` | Hold → confirm → cancel, prevents double booking | 2 |
| `services/expiry.py` | Hold expiry and No-show (scheduler + lazy check) | 2 |
| `services/fee.py` | Parking fee (pure function) | 2 |
| `services/outbox.py` | Writes events for MongoDB in the same transaction | 2 |
| `services/state_machine.py` | Allowed reservation status changes | 2 |
| `routes/auth.py`, `me.py`, `lots.py`, `reservations.py` | API endpoints | 2 |
| check-in/out, payments, outbox worker, scheduler, staff API | | 3 |
| admin CRUD, stats, transaction demo | | 4 |

## API added in phase 1

| Method | Path | Who |
| --- | --- | --- |
| POST | `/api/auth/register`, `/api/auth/login`, `/api/auth/logout` | anyone / logged in |
| GET | `/api/auth/me` | logged in (returns `csrf_token`) |
| GET, PATCH | `/api/me/profile` · POST `/api/me/password` | Customer |
| GET, POST | `/api/me/vehicles` · PATCH, DELETE `/api/me/vehicles/<id>` · POST `/api/me/vehicles/<id>/default` | Customer |
| GET | `/api/me/reservations?scope=active\|past\|all` · `/api/me/reservations/<id>` · `/api/me/parking` · `/api/me/dashboard` | Customer |
| GET | `/api/lots?start=&duration_hours=` · `/api/lots/<id>/slots?start=&duration_hours=` | logged in (staff/admin also see plates) |
| POST | `/api/reservations` · `/api/reservations/<id>/confirm` · `/api/reservations/<id>/cancel` | Customer |
| GET | `/api/config/public` | anyone |

## How double booking is prevented

`services/booking.py` runs one transaction per hold:

1. Lock the customer row (`FOR UPDATE`), so the quota and same-car checks cannot race.
2. Lock the bay row (`FOR UPDATE`).
3. Look for an overlapping booking that still holds the bay: `start_time < new_end AND end_time > new_start`, with status Held (not expired), Confirmed (not past no-show) or In Use.
4. Insert the reservation and its outbox event, then commit.

`tests/test_booking.py::test_two_customers_same_bay_same_moment` sends 10 requests at once: exactly one wins.

## Rules for writing backend code

1. Get the time only from `core.timeutil.now_utc()`. Never use MySQL `NOW()` in business logic.
2. Every write goes inside `with transaction() as cur:`. Any exception rolls the whole block back.
3. Use `%s` placeholders only. Never put user input into SQL with f-strings.
4. Report problems by raising `core.errors` (`Conflict("SLOT_TAKEN", "...")`), not by returning error JSON by hand.
5. Lock order is always **user → slot → reservation** (prevents deadlocks).
6. Every important change writes an outbox event with the same cursor (`services.outbox.write_event`).
