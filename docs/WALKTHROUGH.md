# EnduranceHub — Interview Walkthrough

Read this before any interview. It explains every file, then answers the questions interviewers actually ask.

## The 30-second pitch

"EnduranceHub is a training analytics API I built with FastAPI and PostgreSQL. It has JWT authentication with bcrypt password hashing, per-user activity logging, and an analytics engine that computes weekly training load using the session-RPE method from sports science. It follows a layered architecture — routers, schemas, services, models — so each concern lives in one place."

## File-by-file

### `app/databases/database.py`
Creates one `engine` (a pool of reusable PostgreSQL connections) and a `SessionLocal` factory. `get_db()` is a FastAPI dependency: it yields a fresh session per request and guarantees closure via `finally`, preventing connection leaks. **One engine per app, one session per request.**

### `app/models/` (SQLAlchemy)
Python classes describing tables. `Base` (declarative base) is the registry; `Base.metadata.create_all()` creates missing tables at startup. Constraints live in the database itself: `unique=True` on email, `nullable=False`, and a `ForeignKey` from `activities.user_id` to `users.id`. Indexes on `user_id` and `date` because every query filters on them.

### `app/schemas/` (Pydantic)
Request/response shapes. `UserCreate` validates registration input; `UserOut` deliberately has no password field, so a hash can never leak into a response. `ActivityCreate` enforces business rules declaratively (`distance_km > 0`, `1 <= rpe <= 10`) — invalid requests get a 422 before endpoint code runs. **Models = database rows (permanent). Schemas = request/response shapes (temporary).**

### `app/services/security.py`
`hash_password` (bcrypt via Passlib) and `verify_password`. Bcrypt is one-way, salted and deliberately slow — three properties that protect passwords even if the database leaks.

### `app/services/auth_services.py`
Creates and verifies JWTs. Payload: `sub` (user email) + `exp` (expiry). Signed with `SECRET_KEY` (HS256). The signature makes tokens tamper-proof; the expiry limits damage from a stolen token.

### `app/dependencies.py`
`get_current_user`: reads the `Authorization: Bearer` header (via `OAuth2PasswordBearer`), verifies the token, loads the user from the DB, and returns the `User` object. Any protected endpoint just declares `Depends(get_current_user)`. All failure modes raise the same vague 401 so attackers can't tell which check failed.

### `app/api/auth.py`
`/register` (checks duplicates → 409, stores only the hash, sends an OTP), `/verify-email` (checks the code, marks the account verified, returns a JWT so verification logs you straight in), `/resend-otp` (60s cooldown against inbox spam), `/login` (verify password → issue token; unverified accounts get 403, same 401 for unknown email and wrong password), `/me` (returns the injected current user).

### `app/services/otp_service.py` + `app/models/otp.py`
Email verification codes: 6 cryptographically random digits (`secrets`, not `random`), 10-minute expiry, max 5 wrong attempts before the code locks, single-use (deleted on success), one active code per email (reissue invalidates the old one). `email_service.py` sends via SMTP when configured, or prints to the console in dev.

### `app/api/activities.py`
CRUD locked to the owner: every query filters `Activity.user_id == current_user.id`, and `user_id` on create comes from the token — never the request body. Wrong owner and missing record both return 404 (don't reveal that the record exists). `PUT` uses `model_dump(exclude_unset=True)` for partial updates.

### `app/api/analytics.py`
Computes summary stats, per-week buckets (Monday-anchored), personal records and streaks in Python over the user's activities. Training load = `duration × RPE` summed per week (session-RPE method).

### `frontend/index.html`
Single-page vanilla JS app. Stores the JWT in localStorage, attaches it to every request via a small `fetch` wrapper, auto-logs-out on 401, and renders stats + a Chart.js chart from the analytics endpoints.

## Likely interview questions — with answers

**Walk me through what happens on login.**
Client POSTs email+password → Pydantic validates the body → I query the user by email → `verify_password` re-hashes the attempt with the salt embedded in the stored hash and compares → on success I sign a JWT containing `sub` (email) and `exp`, and return it. Wrong email and wrong password produce the identical 401 so attackers can't enumerate accounts.

**Why bcrypt and not SHA-256?**
SHA-256 is designed to be fast — an attacker can try billions of guesses per second. Bcrypt is deliberately slow and salted, making brute force and rainbow tables impractical.

**Why do the hashes differ for two users with the same password?**
Each hash includes a random salt. Without it, identical passwords would produce identical hashes, so cracking one cracks all of them.

**How does the server know who's calling without a session store?**
The JWT is self-contained: the payload says who (`sub`), and the signature proves *this server* issued it and nobody modified it. I verify the signature and expiry on every request, then load the user. Stateless — no server-side session table needed.

**Can someone forge a token with someone else's email?**
No. They can build the header and payload, but the signature is computed with the server's `SECRET_KEY`, which they don't have. Verification fails and they get a 401.

**JWT vs sessions — trade-offs?**
JWTs scale without shared session storage and work well across services, but can't be revoked individually before expiry (mitigations: short expiry, refresh tokens, a denylist). Server-side sessions are revocable but need a shared store.

**How do you stop user A reading user B's activities?**
Every activity query filters by both the record id AND `user_id == current_user.id`, where `current_user` comes from the verified token. A wrong-owner request is indistinguishable from a missing record (404).

**Why dependency injection for the DB session?**
One place controls the session lifecycle (open → yield → guaranteed close), endpoints just declare what they need, and tests can override `get_db` to point at a test database.

**What would you improve for production?**
Alembic migrations instead of `create_all`; refresh tokens; rate limiting on login; env-managed secrets (already env-overridable, defaults are dev-only); moving analytics aggregation into SQL for large datasets; restricting CORS to the real frontend origin; HTTPS everywhere.

**How does your email verification work, and how is it protected?**
Registration creates the account unverified and emails a 6-digit code (generated with `secrets` for cryptographic randomness). The code expires in 10 minutes, locks after 5 wrong attempts (so 6 digits can't be brute-forced), is single-use, and resending has a 60-second cooldown so the endpoint can't spam inboxes. Login returns 403 until verified, and successful verification returns a JWT directly.

**Your login has a "remember me" — where does the password go?**
Nowhere I control. The checkbox saves only the email address in localStorage; the password is handed to the browser via the Credential Management API (`navigator.credentials.store`), backed by a real `<form>` with `autocomplete="username"` / `"current-password"`. The browser shows its own "Save password?" prompt and keeps the password in the OS keychain. On a return visit, `navigator.credentials.get` with `mediation: "optional"` can sign the user straight back in without typing. Storing a plaintext password in localStorage would mean any XSS on the page could read it — the convenience isn't worth that.

**Why does FastAPI serve the frontend instead of just opening the HTML file?**
The Credential Management API requires a secure context and `file://` isn't one, so the save-password prompt never appears. Serving `index.html` from FastAPI gives the app a real `http://localhost` origin, which browsers treat as secure. It also makes frontend and API same-origin, so CORS preflights disappear, and it's a prerequisite for OAuth — every provider rejects `file://` redirect URIs.

**How does account deletion work?**
Soft delete with a grace period, like real SaaS products. Requesting deletion requires re-entering the password (a stolen token alone can't destroy an account) and stamps `delete_requested_at`. For 5 days the user can cancel; after that, a purge job — run at startup and opportunistically on logins, no cron needed at this scale — permanently removes the user, their activities and any OTPs, children before parent so foreign keys are never violated.

**How do you handle sports that don't have a distance?**
`app/constants.py` is the single source of truth: 28 activity types mapped to four categories, and only the `distance` category (run, ride, swim, row, hike, walk) records kilometres. `distance_km` is nullable, and a Pydantic `model_validator` enforces the pairing — distance sports must supply one, and for anything else a distance sent by the client is stripped rather than stored. The update endpoint re-checks after applying a partial change, because switching a run to yoga has to clear the distance. The analytics layer then filters accordingly: mileage, longest distance and pace only consider distance sports, while training load, streaks and longest session span everything — which is exactly why session-RPE load is the headline metric rather than kilometres.

**Why does average pace divide by distance-sport time only?**
If you summed all duration and divided by total distance, a 55-minute gym session would drag your pace per kilometre down even though you covered no ground. Pace uses only the time spent in distance sports, so the number stays meaningful for a multi-sport athlete.

**RPE is self-reported — isn't that a weak input?**
It's subjective by design, and that's the honest trade-off: the automated alternatives all need hardware. TRIMP and Strava's Relative Effort derive intensity from heart rate; TSS uses a power meter for cycling or threshold pace for running. Rather than fake a sensor, I did two things. First, the app estimates your threshold pace from your own history (fastest session of at least 20 minutes and 3 km) and uses the ratio of session pace to threshold pace — an intensity factor — to *suggest* an RPE you can accept or override. Second, I focused on automating the interpretation instead of the input, which is where `/analytics/load-metrics` comes in.

**What does your CI actually test?**
Five jobs. Ruff for lint; the pytest suite twice — once on SQLite for speed and once against PostgreSQL 16 in a service container, because SQLite alone would hide dialect-specific bugs; frontend checks that catch the mistakes static HTML tooling misses (an icon referenced but never defined, a duplicate element ID, an inline `onclick` pointing at a function that doesn't exist); and a Docker build that starts the container and polls `/health` until it answers, so a broken image can't merge. The tests run in-process with FastAPI's `TestClient` against a throwaway database, so there's no server to start and no fixture data left behind.

**Which test would you point at first?**
`test_users_cannot_see_or_touch_each_others_activities`. It creates two accounts, has the second try to read, update and delete the first one's activity, and asserts all three return 404 — not 403, because confirming a record exists is itself a leak. Authorisation bugs are the ones that actually matter, and they're invisible in manual testing when you only ever log in as yourself.

**How do you handle schema changes?**
`Base.metadata.create_all()` is a common beginner trap: it creates missing *tables* but never adds a *column* to a table that already exists, so every new field crashed the app with "column ... does not exist" until a migration script was run by hand. `app/databases/schema_sync.py` now applies the additive changes at startup — each statement is idempotent (`ADD COLUMN IF NOT EXISTS`, and `DROP NOT NULL`, which is a no-op when already dropped) and runs in its own transaction so one failure can't roll back the rest. It's skipped entirely on SQLite, which the tests create fresh. A production project would use Alembic for versioned, reversible migrations including destructive ones; this is the lightweight equivalent for a single-database app.

**How does the heart-rate effort estimate work?**
If the user has a wearable, they enter an average heart rate for the session and the app converts it to an effort score. With a resting HR on file it uses **Karvonen's heart-rate reserve** — `(HRavg − HRrest) / (HRmax − HRrest)` — which places the session between the user's own resting and max rather than as a raw percentage, and maps roughly 1:1 onto the ten-point scale. Without a resting HR it falls back to plain %HRmax, shifted because even sitting still sits near 40% of max. Max HR comes from an explicit value if given, otherwise the **Tanaka formula** (`208 − 0.7 × age`), which is more accurate across ages than the old `220 − age`. Heart rate takes precedence over the pace estimate when both are available, because it measures internal load rather than output.

**Why do some threshold paces say "typical default"?**
A threshold estimate needs a sustained session to derive from, and the qualifying minimum is sport-specific — 3 km is a warm-up on a bike but a long way in a pool, so one global minimum would have excluded swimmers entirely. Until a sport has a qualifying session, the API returns a sensible default pace flagged `is_default: true`, so every distance sport can offer a suggestion from day one and the UI can be honest that it isn't personalised yet.

**What are ACWR, monotony and strain?**
Three coaching metrics derived from the session-RPE load already stored on every activity. ACWR is the acute:chronic workload ratio — this week's load against the trailing four-week weekly average; roughly 0.8–1.3 is the sustainable range and above 1.5 marks the spikes associated with injury risk. Foster's monotony is mean daily load divided by the standard deviation of daily load across the week: above about 2.0 means every day looks the same, which predicts staleness. Strain is weekly load multiplied by monotony — high volume *and* high sameness together. Two edge cases were worth handling explicitly: identical load every day gives a standard deviation of zero, which is maximum monotony rather than an absence of it, and a week with fewer than three training days makes the ratio meaningless because rest days dominate the spread.

**Why compute analytics in Python instead of SQL?**
At this scale, loading one user's activities and aggregating in Python is simple, readable and fast enough. At scale I'd push aggregation into SQL (`GROUP BY` week with `SUM`) or maintain pre-computed rollups. Knowing when each is appropriate is the real answer.
