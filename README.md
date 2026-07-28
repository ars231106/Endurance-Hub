# EnduranceHub

[![CI](https://github.com/YOUR_USERNAME/Endurance-Hub/actions/workflows/ci.yml/badge.svg)](https://github.com/YOUR_USERNAME/Endurance-Hub/actions/workflows/ci.yml)

An endurance training analytics platform for runners, cyclists and endurance athletes. Log workouts, track weekly training load, monitor streaks and break personal records — inspired by Strava and Runna, built from scratch.

## Features

- **JWT authentication** — register/login with bcrypt-hashed passwords and signed, expiring tokens
- **Email OTP verification** — 6-digit codes with expiry, attempt limits and resend cooldown; SMTP-ready, console fallback in dev
- **Account deletion with grace period** — password-confirmed soft delete, 5-day cancellation window, automatic purge of all user data
- **28 sports across 4 categories** — distance sports (run, ride, swim, row, hike, walk) record kilometres; strength work, team/racket sports and classes are logged with duration and effort only
- **Activity logging** — full CRUD for workouts (type, distance, duration, RPE, date, notes), strictly scoped to the logged-in user
- **Per-sport weekly goals** — a target per sport, in km for distance sports and minutes for everything else
- **Analytics engine** — weekly mileage, session-RPE training load, average pace, personal records, streak tracking
- **Training status** — acute:chronic workload ratio, Foster monotony and strain, computed from logged load with no sensors required
- **Estimated RPE** — suggests an effort score two ways: from average heart rate (Karvonen heart-rate reserve, for any sport) or from pace against an estimated threshold pace (all six distance sports)
- **Wearable-friendly** — optional heart-rate profile (birth year, resting HR, max HR) and per-session average HR
- **Dashboard frontend** — single-page app with live stats, an 8-week training chart and inline editing

## Tech stack

| Layer | Technology |
|---|---|
| API | FastAPI (Python) |
| Database | PostgreSQL |
| ORM | SQLAlchemy |
| Validation | Pydantic v2 |
| Auth | JWT (python-jose) + bcrypt (Passlib) |
| Frontend | Vanilla JS + Chart.js |

## Architecture

```
Client (frontend/index.html)
   ↓ HTTP + JWT
FastAPI routers        (app/api)        - endpoints only
   ↓
Pydantic schemas       (app/schemas)    - request/response validation
   ↓
Services               (app/services)   - hashing, JWT logic
   ↓
SQLAlchemy models      (app/models)     - table definitions
   ↓
PostgreSQL             (app/databases)  - engine, sessions, DI
```

Authentication is dependency-injected: any endpoint adds `Depends(get_current_user)` and receives the verified `User` object, or the request is rejected with 401 before the endpoint body runs.

## Getting started

### 1. Database

Install PostgreSQL and create a database named `endurancehub`.

### 2. Backend

```bash
cd backend
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt

# No manual migrations needed - the app applies additive schema changes
# itself at startup (see app/databases/schema_sync.py). The individual
# migrate_*.py scripts remain for reference and one-off use.

uvicorn app.main:app --reload
```

API runs at `http://localhost:8000` — interactive docs at `http://localhost:8000/docs`.

Configuration is via environment variables (sensible dev defaults are built in):

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string |
| `SECRET_KEY` | JWT signing key — **always override in production** |
| `SMTP_HOST` / `SMTP_USER` / `SMTP_PASS` / `SMTP_PORT` | Optional. Set all three (e.g. Gmail + app password) to send real OTP emails; unset = codes print to the server console |

The easiest way to set these: copy `backend/.env.example` to `backend/.env` and fill it in — it loads automatically at startup and is gitignored.

### 3. Frontend

Open **http://localhost:8000** — FastAPI serves the frontend itself.

Don't open `frontend/index.html` as a file. A `file://` page has no real origin, so the browser's password manager won't offer to save credentials and OAuth redirects are rejected. Served over `http://localhost` the app is a proper origin, and frontend and API are same-origin (no CORS at all).

### 4. Tests

```bash
cd backend
pip install -r requirements-dev.txt
pytest -v          # full suite against a throwaway database
ruff check .       # linting
```

The suite covers registration and OTP verification, login and token auth, activity CRUD, the sport taxonomy's validation rules, every analytics endpoint, account deletion, and — most importantly — that one user can neither read nor modify another's activities.

`smoke_test.py` still exists for hitting a *running* server end to end, but `pytest` is the suite CI runs.

## CI/CD

Every push and pull request runs [GitHub Actions](.github/workflows/ci.yml): linting, the test suite on both SQLite and PostgreSQL 16, frontend checks (script syntax, icon references, duplicate IDs, orphaned handlers), and a Docker build that must answer `/health` before the job passes.

Merging to `main` deploys automatically. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## API reference

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/register` | — | Create an account (unverified) + send OTP |
| POST | `/verify-email` | — | Submit OTP; returns a JWT on success |
| POST | `/resend-otp` | — | Re-send the code (60s cooldown) |
| POST | `/login` | — | Exchange credentials for a JWT (verified accounts only) |
| GET | `/me` | ✓ | Current user profile |
| PUT | `/me/profile` | ✓ | Update heart-rate profile (birth year, resting HR, max HR) |
| POST | `/me/delete` | ✓ | Schedule account deletion (password confirm, 5-day grace) |
| POST | `/me/cancel-deletion` | ✓ | Cancel a scheduled deletion |
| POST | `/activities` | ✓ | Log a workout |
| GET | `/activities` | ✓ | List workouts (paginated, newest first) |
| GET | `/activities/{id}` | ✓ | Single workout |
| PUT | `/activities/{id}` | ✓ | Partial update |
| DELETE | `/activities/{id}` | ✓ | Delete |
| GET | `/analytics/summary` | ✓ | Totals, average pace, average RPE |
| GET | `/analytics/weekly` | ✓ | Per-week distance + training load |
| GET | `/analytics/records` | ✓ | Personal records |
| GET | `/analytics/streak` | ✓ | Current + longest daily streak |
| GET | `/analytics/by-sport` | ✓ | Totals grouped by sport |
| GET | `/analytics/load-metrics` | ✓ | ACWR, Foster monotony, strain, 28-day load series |
| GET | `/analytics/thresholds` | ✓ | Estimated threshold pace per distance sport |
| GET | `/health` | — | Liveness probe |

## Roadmap

- GPX/FIT file upload and parsing
- Goal setting and progress tracking
- Alembic migrations
- Deployment (Docker + cloud Postgres)
