# EnduranceHub — Complete Project Summary

*Reference document. Paste into a new chat to generate resume/portfolio/interview material.*

---

## 1. Identity

| | |
|---|---|
| **Name** | EnduranceHub |
| **Type** | Full-stack endurance training analytics platform (web app + REST API) |
| **Purpose** | Athletes log workouts across 28 sports; the app computes training load, injury-risk ratios, personal records, streaks and pace trends |
| **Positioning** | Inspired by Strava/Runna, but analytics-first rather than social — no feed of other users, no gamification |
| **Repository** | https://github.com/ars231106/Endurance-Hub (public) |
| **Live demo** | https://endurancehub-dyq3.onrender.com |
| **Built** | July 2026, solo project |
| **Scale** | 44 files, ~5,000 lines, 22 API endpoints, 28 sport types, 3 database tables, ~40 tests |

---

## 2. Technology stack

**Backend:** Python 3.11, FastAPI, SQLAlchemy 2 (ORM), Pydantic v2 (validation), PostgreSQL 18, python-jose (JWT), Passlib + bcrypt (hashing), uvicorn (ASGI server)

**Frontend:** Vanilla JavaScript (no framework), Chart.js, custom inline SVG icon sprite, CSS custom properties for theming — single `index.html`, served by FastAPI itself

**Infrastructure:** Docker (single image, API + frontend), Render (web service + managed PostgreSQL), GitHub Actions (CI/CD), Brevo (transactional email over HTTPS API)

**Tooling:** pytest, FastAPI TestClient, Ruff (linting), Git/GitHub

---

## 3. Architecture

Layered, with one responsibility per layer:

```
Client (frontend/index.html, served by FastAPI)
   ↓  HTTP + JWT in Authorization header
Routers        app/api/          endpoints only
   ↓
Schemas        app/schemas/      Pydantic request/response validation
   ↓
Services       app/services/     hashing, JWT, OTP, email, account purge
   ↓
Models         app/models/       SQLAlchemy table definitions
   ↓
Database       app/databases/    engine, session factory, DI, schema sync
PostgreSQL
```

**File layout**

```
Endurance-Hub/
├── Dockerfile, .dockerignore, render.yaml, .gitignore, README.md
├── .github/workflows/ci.yml
├── docs/           WALKTHROUGH.md (interview Q&A), DEPLOYMENT.md, PROJECT-SUMMARY.md
├── frontend/index.html        entire SPA: landing page + dashboard
└── backend/
    ├── app/
    │   ├── main.py            app assembly, CORS, static mount, error handler
    │   ├── constants.py       28-sport taxonomy, threshold minimums/defaults
    │   ├── dependencies.py    get_current_user (OAuth2 bearer → User)
    │   ├── env_loader.py      reads .env without external deps
    │   ├── api/               auth.py, activities.py, analytics.py
    │   ├── models/            base.py, user.py, activity.py, otp.py
    │   ├── schemas/           user_schemas.py, activity_schemas.py
    │   ├── services/          security.py, auth_services.py, otp_service.py,
    │   │                      email_service.py, account_service.py
    │   └── databases/         database.py, schema_sync.py
    ├── conftest.py, tests/    test_auth.py, test_activities.py, test_analytics.py
    ├── pyproject.toml         pytest + ruff config
    ├── requirements.txt, requirements-dev.txt
    ├── smoke_test.py          live-server end-to-end script
    └── migrate_*.py           standalone migration scripts (superseded by schema_sync)
```

---

## 4. Database schema

**users**
`id` PK · `name` · `email` (unique, indexed) · `password_hash` · `is_verified` (bool, default false) · `delete_requested_at` (nullable timestamp — soft delete) · `birth_year` · `resting_hr` · `max_hr` (all nullable — optional HR profile)

**activities**
`id` PK · `user_id` FK→users.id (indexed) · `activity_type` · `distance_km` (**nullable** — only distance sports) · `duration_min` · `rpe` (1–10) · `avg_hr` (nullable) · `date` (indexed) · `notes` (nullable)

**email_otps**
`id` PK · `email` (indexed) · `code` (6 digits) · `expires_at` · `attempts` (lockout counter) · `created_at` (drives resend cooldown)

Constraints enforced at the database level, not just in Python: unique email, non-null on required fields, foreign key from activities to users.

---

## 5. Sport taxonomy (28 types, 4 categories)

Single source of truth in `app/constants.py`, mirrored in the frontend.

- **Distance** (record km + duration + RPE): run, ride, swim, row, hike, walk
- **Strength** (duration + RPE only): strength, crossfit, calisthenics, hiit
- **Sports** (duration + RPE only): football, basketball, cricket, tennis, badminton, table tennis, volleyball, hockey, rugby, baseball, golf, boxing, martial arts, climbing, skiing
- **Other** (duration + RPE only): yoga, pilates, other

A Pydantic `model_validator` requires a distance for distance sports and **strips** any distance sent for the others rather than storing it. The update endpoint re-validates after applying a partial change, because converting a run to yoga must clear the distance.

---

## 6. Features

### Authentication & accounts
- Registration with bcrypt-hashed passwords (never plaintext)
- **Email OTP verification**: 6 cryptographically random digits (`secrets`, not `random`), 10-minute expiry, 5-attempt lockout, single-use, one active code per email, 60-second resend cooldown; login blocked with 403 until verified
- **JWT sessions**: HS256, 12-hour expiry, payload `{sub: email, exp}`; `get_current_user` dependency reads the bearer header, verifies signature and expiry, loads the user
- **Remember me**: email in localStorage, password handed to the browser's Credential Management API (OS keychain) — never stored by the app; `navigator.credentials.get` enables silent re-login
- **Account deletion with 5-day grace period**: password re-confirmation required, soft-delete timestamp, cancellable; a purge job (startup + on login) permanently removes user, activities and OTPs, children before parent
- **Optional HR profile**: birth year, resting HR, max HR override

### Activity logging
Full CRUD, every query scoped to the authenticated user. Pagination (limit/offset, capped at 200), newest-first ordering, partial updates via `exclude_unset`, duplicate-to-today action, optional notes and average heart rate.

### Analytics (7 endpoints)
| Endpoint | Computes |
|---|---|
| `/analytics/summary` | Totals, average pace, average RPE, longest activity, distance vs other session counts |
| `/analytics/weekly?weeks=N` | Monday-anchored buckets: distance, duration, session count, training load |
| `/analytics/records` | Longest distance, longest session (all sports), best pace (≥1 km only) |
| `/analytics/streak` | Current and longest consecutive-day streaks |
| `/analytics/load-metrics` | ACWR, Foster monotony, strain, 28-day daily load series |
| `/analytics/thresholds` | Estimated threshold pace per distance sport |
| `/analytics/by-sport` | Totals grouped by sport |

**Formulas used**
- **Training load** = duration × RPE, summed (session-RPE method) — works across every sport, which is why it, not distance, is the headline metric
- **ACWR** = 7-day load ÷ trailing 28-day weekly average. Bands: <0.8 detraining, 0.8–1.3 optimal, 1.3–1.5 elevated, >1.5 spike (injury-risk pattern). Requires ≥14 days of history
- **Monotony** (Foster) = mean daily load ÷ population SD of daily load over 7 days. <1.5 good variation, 1.5–2 moderate, >2 too uniform
- **Strain** = weekly load × monotony
- **Threshold pace** = fastest session past sport-specific minimums (run 3 km, ride 8 km, swim 0.4 km, row 1 km, hike/walk 2 km; all ≥15 min). Falls back to a per-sport default flagged `is_default: true`
- **Pace-based RPE** — intensity factor = threshold pace ÷ session pace, mapped `1 + 9 × (IF − 0.55) / 0.5`, clamped 1–10
- **HR-based RPE** — Karvonen heart-rate reserve `(HRavg − HRrest) / (HRmax − HRrest)` × 10; falls back to %HRmax shifted `(%−50)/5` when no resting HR. Max HR from explicit value or **Tanaka** `208 − 0.7 × age`
- **Average pace** divides by distance-sport time only, so a gym session can't distort min/km

### Per-sport weekly goals
Independent target per sport — kilometres for distance sports, **hours + minutes** for everything else. Progress bars per sport; a combined ring averaging each sport's own completion (never summing km with minutes); stored in localStorage with migration from an earlier single-goal format.

### Frontend
**Landing page:** fixed nav with scroll-spy anchors, split hero with animated dashboard mock and two rotating feature badges, animated stat counters, two opposite-scrolling marquees, three alternating feature rows (load heatmap, flipping personal-record cards, draw-on-hover pace line), tabbed metrics explorer, four-step "how it works", three athlete personas, FAQ accordion, CTA band, four-column footer, scroll-progress bar, drifting dot-grid background, blurred gradient orbs.

**Dashboard (four full-width sections):** profile hero bar with animated counters; "This week" grid (goal ring, totals + load sparkline, training-status card with ACWR gauge and 28-day sparkline) plus a full-width volume chart with 4/8/12-week range switcher; activity feed as a two-column card grid with category tabs, scope and sort filters, live result count, per-card menus; records & consistency grid (personal records, 13-week calendar heatmap, recent sessions) plus a training-load explainer.

**Interaction details:** modal-based logging with category → sport pickers and a 1–10 RPE selector that names each level; live RPE suggestion from HR or pace with one-click apply; toast notifications instead of `alert()`; custom confirm modals; keyboard shortcuts (`N` to log, `Esc` to close); custom number steppers replacing unstyleable native spinners; custom scrollbars; 58-glyph inline SVG icon sprite (no icon CDN); **System / Light / Dark theme** with pre-paint application to avoid flash, OS-change following, and theme-aware Chart.js colours; `prefers-reduced-motion` support throughout.

---

## 7. Security decisions (each deliberate, each defensible)

- Passwords bcrypt-hashed with per-password salt — slow and one-way by design
- Identical vague 401 for unknown email and wrong password, so accounts can't be enumerated
- Wrong-owner access returns **404, not 403** — confirming a record exists is itself a leak
- `user_id` taken from the verified token, never from the request body
- Response schemas omit password fields entirely, so a hash can't leak by accident
- OTP: cryptographic randomness, expiry, attempt lockout, single-use, resend cooldown
- Account deletion requires password re-entry, so a stolen token alone can't destroy data
- Passwords delegated to the OS keychain via Credential Management API rather than localStorage (XSS-readable)
- Secrets via environment variables; `.env` gitignored; `SECRET_KEY` generated by the host
- Global exception handler so 500s carry CORS headers and don't surface as opaque "Failed to fetch"

---

## 8. Testing & CI/CD

**Test suite** (`pytest` + FastAPI `TestClient`, in-process against a throwaway database):
- `test_auth.py` — registration, duplicate rejection, password rules, OTP happy path/wrong code/format/single-use/rate limit, login, token requirements, profile validation, full deletion lifecycle
- `test_activities.py` — CRUD, distance-required rules, distance-stripping for duration sports, unknown sport rejection, value bounds, HR round-trip, partial updates, type-switch clearing distance, **cross-user isolation on read/update/delete**
- `test_analytics.py` — every endpoint's shape and values, pace excluding non-distance time, records ignoring distance-less sports, streaks, load-metric bands, threshold defaults vs personalised, auth required on all

**GitHub Actions — 5 jobs, all green:**
1. Ruff lint
2. pytest on SQLite (fast feedback)
3. pytest on PostgreSQL 16 service container (catches dialect-specific bugs)
4. Frontend checks — script syntax via `node --check`, every icon reference resolves, no duplicate element IDs, no inline handler without a function
5. Docker build, container start, poll `/health` until it answers

**Deployment:** Docker image (dependency layer cached separately), `render.yaml` blueprint provisioning web service + PostgreSQL together, `SECRET_KEY` auto-generated, `DATABASE_URL` wired automatically, health-check gate before traffic switches, auto-deploy on push to `main`.

---

## 9. Problems diagnosed and solved

1. **SMTP silently fails in production** — Gmail SMTP worked locally, failed on Render with `[Errno 101] Network is unreachable`; cloud hosts block outbound SMTP as anti-spam. Migrated to an HTTPS email API (Brevo), with auto-detected transport (Resend → Brevo → SMTP → log) and non-raising delivery, since the account row commits before the email sends.
2. **`create_all()` never adds columns** — only creates missing tables, so every new field crashed with "column does not exist" until a script was run by hand. Wrote `schema_sync.py` applying idempotent additive migrations at startup (`ADD COLUMN IF NOT EXISTS`, `DROP NOT NULL`), each in its own transaction, skipped on non-Postgres.
3. **500s surfacing as "Failed to fetch"** — unhandled exceptions escaped past the CORS middleware, so error responses had no CORS headers and the browser refused to read them. Added a global exception handler returning proper JSON.
4. **Password manager never prompted** — the Credential Management API needs a secure context and `file://` isn't one. Made FastAPI serve the frontend so the app has a real `http://localhost` origin (which also removed CORS entirely and made OAuth possible).
5. **Chart growing infinitely** — `maintainAspectRatio: false` inside a height-less container created a resize feedback loop. Fixed with a fixed-height wrapper.
6. **Zero-variance monotony inverted** — identical daily load gives SD 0, which my first version reported as "not enough variation" when it is in fact *maximum* monotony. Now capped and labelled correctly; also withheld when a week has fewer than 3 training days, since rest days dominate the spread.
7. **passlib/bcrypt incompatibility** — passlib 1.7.4 reads `bcrypt.__about__`, removed in bcrypt 4.1; pinned `bcrypt==4.0.1`.
8. **`postgres://` URL scheme** — Render/Heroku hand out a scheme SQLAlchemy 2 rejects; normalised at startup.
9. **Test fixture collision** — the isolation test shared one email fixture between both users, so the second registration would 409. Caught by review before it ever ran.
10. **CI broken by a second `<script>`** — adding a pre-paint theme script broke the workflow's greedy regex; now selects the largest block.

---

## 10. Not built (honest scope)

Google/Meta/Apple OAuth (Apple requires a paid developer account), GPX/FIT file upload, Alembic versioned migrations, rate limiting beyond the OTP cooldown, JWT revocation/refresh tokens, restricted CORS origins.

---

## 11. Skills demonstrated

REST API design · layered architecture · relational data modelling · ORM usage · request validation · dependency injection · authentication vs authorisation · password and token security · algorithm implementation from domain formulas · edge-case reasoning · frontend state management without a framework · CSS theming and animation · accessibility (reduced motion, ARIA labels) · automated testing strategy · CI pipeline design · containerisation · cloud deployment · log-driven debugging of environment-specific failures · technical writing (three docs including an interview walkthrough)
