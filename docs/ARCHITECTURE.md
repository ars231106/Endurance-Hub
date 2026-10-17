# EnduranceHub — Architecture

A training-analytics platform: athletes log sessions, the system derives
load, fitness and injury-risk metrics from them.

This document is written to be argued with. Every significant decision
includes what it costs, not only what it buys.

---

## 1. System overview

```
                      ┌──────────────┐
                      │   Browser    │
                      └──────┬───────┘
                             │ HTTPS
                             ▼
                   ┌─────────────────────┐
                   │  Render (container) │
                   │                     │
                   │  gunicorn           │
                   │   └─ uvicorn x N    │
                   │       └─ FastAPI    │
                   │           ├─ API    │
                   │           └─ static │
                   └───┬─────────────┬───┘
                       │             │
            TLS        │             │  TLS
          ┌────────────▼──┐      ┌───▼──────────┐
          │ Neon Postgres │      │ Upstash Redis│
          │  (durable)    │      │  (ephemeral) │
          └───────────────┘      └──────────────┘
                                        │
                      ┌─────────────────┴──────┐
                      │ rate limits            │
                      │ token revocation       │
                      │ analytics cache        │
                      └────────────────────────┘

          Brevo (email, HTTPS)      Sentry (errors)
```

**One service, not three.** The API and the frontend ship in the same
container and are served from the same origin.

| Gained | Given up |
|---|---|
| No CORS in production — no cross-origin request exists | Frontend and API cannot scale independently |
| Password managers work (a real origin, not `file://`) | A CSS change rebuilds the whole image |
| One deploy, one URL, one set of logs | No CDN for static assets |

At this size the simplicity wins. The split becomes right when frontend
deploys outnumber backend deploys, or when static assets need edge caching.

---

## 2. Request lifecycle

A single `GET /analytics/summary` from end to end:

```
1.  TLS terminates at Render's proxy
2.  RequestContextMiddleware   assigns/propagates X-Request-ID, starts timer
3.  SecurityHeadersMiddleware  (applies on the way out)
4.  RequestSizeLimitMiddleware rejects oversized bodies pre-buffer
5.  CORSMiddleware             no-op, same-origin
6.  Route matching             → analytics.summary
7.  get_db                     checks out a pooled connection
8.  get_current_user           verify signature → type → revocation → DB lookup
9.  Cache read                 analytics:{user_id}:summary
10. On miss: ONE SQL aggregate → single row
11. Cache write                60s TTL
12. Serialisation              response_model strips anything not declared
13. Headers, timing, log line, connection returned to pool
```

Two properties fall out of this ordering:

**Authentication cannot be forgotten.** It is a dependency, not a check in
the body. A route with no `current_user` parameter has no way to know who
is calling, so it cannot accidentally serve private data — the thing that
enforces auth is also the thing that supplies the data you need.

**Rate limiting precedes expensive work.** On `/login` the limiter runs
*before* bcrypt. bcrypt is deliberately slow, which protects the password
and simultaneously makes every guess expensive for the server. Limiting
afterwards would protect the account while leaving a free denial of
service.

---

## 3. Data model

```
users ──1:N──> activities
  │
  ├──(by email)──> email_otps
  └──(by email)──> password_resets
```

```sql
users(id PK, name, email UNIQUE, password_hash, is_verified,
      delete_requested_at, birth_year, resting_hr, max_hr)

activities(id PK, user_id FK→users, activity_type, distance_km NULL,
           duration_min, rpe, avg_hr NULL, date, notes)

email_otps(id PK, email, code, expires_at, attempts, created_at)

password_resets(id PK, email, token_hash UNIQUE, expires_at,
                used_at NULL, created_at, requested_ip)
```

**Indexes**

| Index | Serves |
|---|---|
| `users.email` (unique) | login, every lookup by email |
| `activities.user_id` | ownership filter on every query |
| `activities.date` | date-range scans |
| `activities(user_id, date)` | the shape analytics actually uses |

The composite index is the one that matters. Every analytics query filters
`user_id` and ranges or orders on `date`; with only the single-column
index the database still has to sort the matched rows.

**`distance_km` is nullable on purpose.** A gym session has no meaningful
distance and 0 would be a lie that silently poisons every average. The
cost is that `NULL` must be handled explicitly everywhere — which is why
the aggregation code carries one shared predicate:

```python
HAS_DISTANCE = and_(Activity.distance_km.isnot(None), Activity.distance_km > 0)
```

**Soft delete, then purge.** `delete_requested_at` is a timestamp, not a
boolean, so the 5-day grace period is derivable rather than tracked
separately. Purging runs opportunistically at startup and on login instead
of on a scheduler: the query is indexed and idempotent, so running it
often is free, and there is no cron to configure or to fail silently. The
honest cost: if nobody logs in, nothing is purged. A legal deletion
deadline would need a real scheduler.

---

## 4. Authentication and session design

```
          password (once)
               │
               ▼
      ┌──────────────────┐
      │  access  (30m)   │ ──► every request, not revocable cheaply
      │  refresh (30d)   │ ──► only /refresh, revocable, rotated
      └──────────────────┘
```

**Why two tokens.** A single long-lived token forces a choice between
"convenient" and "revocable". Splitting them lets the token that travels
constantly be short, while the one that grants longevity is used rarely
and can be invalidated.

**Rotation.** Each `/refresh` issues a new pair and revokes the one just
used. A stolen refresh token therefore breaks the legitimate client's next
refresh — silent theft becomes a visible logout.

**Revocation at two granularities:**

| Scope | Mechanism | Cost |
|---|---|---|
| One session | `jti` in a denylist, TTL = token's remaining life | one key per logout |
| All sessions | per-user epoch timestamp; tokens with `iat` < epoch are dead | one key, any number of sessions |

The epoch approach is what makes "log out everywhere" and "password
changed" cheap. Invalidating a thousand sessions is a single write.

**The honest limitation.** Revocation needs Redis. Without it the app falls
back to expiry alone, and `/logout` returns `"immediate": false` rather
than pretending. Access tokens are 30 minutes precisely because that is
the worst-case exposure in the degraded mode.

**Why the database is still consulted on every request.** A token stays
cryptographically valid after the account is deleted. The signature proves
who issued it, not that the subject still exists. This costs one indexed
lookup per request and is the reason a purged account cannot keep using
the API.

---

## 5. The analytics engine

Seven endpoints over one underlying quantity:

```
training load = duration × RPE          (session-RPE method)
```

| Endpoint | Derives |
|---|---|
| `/summary` | totals, average pace, average RPE |
| `/weekly` | Monday-anchored buckets |
| `/records` | longest distance, longest session, best pace |
| `/streak` | consecutive active days |
| `/load-metrics` | ACWR, Foster monotony, strain |
| `/thresholds` | estimated threshold pace per sport |
| `/by-sport` | totals grouped by sport |

### The rewrite that mattered

The original implementation did this, seven times per dashboard load:

```python
def _user_activities(db, user):
    return db.query(Activity).filter(Activity.user_id == user.id).all()
```

Every row, into Python, then looped over. Cost is O(all history) per
request. Invisible at 50 sessions; a memory problem at 50,000.

Now aggregation happens in Postgres and a single row comes back:

```python
db.query(
    func.count(Activity.id),
    func.sum(Activity.duration_min),
    func.sum(case((HAS_DISTANCE, Activity.distance_km), else_=0.0)),
    ...
).filter(Activity.user_id == user.id).one()
```

| | Before | After |
|---|---|---|
| Rows crossing the wire | all of them | 1 |
| Memory | O(history) | O(1) |
| Work done by | Python | Postgres, on an index |

### Where SQL was deliberately *not* used

`/thresholds` applies per-sport minimums and a Riegel power-law correction
with branching logic. Expressing that in portable SQL would be unreadable
and dialect-specific. Instead the **query** is narrowed — one sport family,
inside a 90-day window, above a duration floor — and the tier logic runs in
Python over a few dozen rows.

The principle: push the *filtering* down, keep the *judgement* up.

`/weekly` is similar. Truncating a date to a week is `date_trunc` on
Postgres and `strftime` on SQLite, and the test suite runs on both. So the
query groups by plain date and Python folds days into weeks. Portability
beat a marginal gain.

### Caching

Keyed `analytics:{user_id}:{endpoint}:{params}`, 60-second TTL, invalidated
on any write to that user's activities.

The user id in the key is not decoration — serving one athlete's data to
another is the worst bug a cache can have, so the key makes it structurally
impossible rather than relying on care.

---

## 6. Failure modes

Designed so that a dependency failing degrades the service rather than
ending it.

| Failure | Effect | Why |
|---|---|---|
| Redis down | Rate limiting falls back to per-process; no revocation; no cache | Every cache call returns a neutral value and never raises; a 30s circuit breaker stops retry storms |
| Email provider down | Registration still succeeds; code goes to the log | Account committed before send; delivery never raises |
| Postgres down | `/ready` returns 503, `/health` stays 200 | Liveness must not depend on a dependency, or the app restart-loops during a blip |
| Migration fails | Logged as error, app boots on the existing schema | A schema that may already be correct should not prevent startup; `/ready` catches a genuinely broken one |
| Neon idle-sleep drops a pooled connection | Transparent reconnect | `pool_pre_ping` tests before handing out |
| Slow query | Killed at 15s | `statement_timeout`, so one query cannot hold a connection and starve the pool |
| Worker wedges | Killed and replaced | gunicorn `--timeout 60`, `--max-requests 2000` |

**`/health` vs `/ready` is a deliberate distinction**, not duplication:

- **liveness** — is the process alive? Restart if not. Must not check
  dependencies, or a database blip triggers restarts that fix nothing.
- **readiness** — can this instance serve? Remove from the load balancer
  if not. Should check dependencies.

---

## 7. Security posture

| Concern | Measure |
|---|---|
| Password storage | bcrypt, per-password salt, cost factor default |
| Password policy | One shared validator used by register, reset and change — so no path can be weaker |
| Account enumeration | Identical 401 for unknown email and wrong password; `/forgot-password` always 202 |
| Brute force | Per-IP and per-account limits, enforced before bcrypt |
| Reset tokens | 256-bit, SHA-256 at rest, single-use, 30-minute expiry, constant-time compare |
| Ownership | Enforced in the WHERE clause, not checked after fetch |
| Wrong owner | 404, not 403 — 403 confirms the row exists |
| Token in transit | Short-lived; `Referrer-Policy: no-referrer` so reset tokens cannot leak via `Referer` |
| Transport | HSTS in production only (it would pin a developer's browser) |
| XSS / clickjacking | CSP, `X-Frame-Options: DENY`, `nosniff` |
| Request flooding | Body size cap before buffering |
| Secrets | Environment only; app refuses to boot in production with the default key |
| Container | Non-root user (uid 10001) |
| Supply chain | `pip-audit` and `bandit` in CI |

**Known weakness, stated rather than hidden.** The CSP includes
`'unsafe-inline'` because the frontend is a single HTML file with inline
scripts and styles. That materially weakens the XSS protection CSP exists
to provide. The real fix is hashed or nonced scripts, which requires
extracting the inline script — not yet done.

**Why ownership lives in the query:**

```python
db.query(Activity).filter(Activity.id == activity_id,
                          Activity.user_id == user.id).first()
```

Fetch-then-compare has a window where the wrong row is in memory and one
missing `if` leaks it. Filtering makes the wrong row unfetchable.

---

## 8. Scaling

### Current capacity

Free Render instance, 2 workers, 512 MB. Roughly **50–100 concurrent
users** before latency degrades. First request after idle pays a ~50s cold
start — a free-tier property, not an architectural one.

### What the design already supports

**The API is stateless.** Sessions are JWTs, rate limits and cache live in
Redis, nothing is held in process memory that matters. So horizontal
scaling needs no code change: run more containers behind a load balancer.

That is the hard part of scaling, and it is done.

### Bottlenecks in the order they would appear

| # | Limit | Fix |
|---|---|---|
| 1 | Single instance CPU | More instances — already stateless |
| 2 | Postgres connections | Already pooled via PgBouncer; `workers × (pool + overflow)` is sized under the ceiling |
| 3 | Write throughput on `activities` | Partition by date, or shard by `user_id` — the data is perfectly user-partitioned |
| 4 | Analytics on huge histories | Materialised daily rollups; recompute on write |
| 5 | Email volume | Queue + worker instead of inline send |

### 10× (≈10k daily actives)

Mostly operational, little architectural: paid instances with 4 workers,
read replica for analytics, CDN for static assets, backups with a tested
restore, longer cache TTLs with explicit invalidation.

### 100× (≈100k daily actives)

Now the shape changes:

- **Pre-aggregate.** A `daily_user_load` rollup table updated on write
  turns every analytics endpoint into a point lookup.
- **Split the service.** Analytics is read-heavy and CPU-bound; auth is
  write-heavy and latency-sensitive. They deserve independent scaling.
- **Queue the side effects.** Email, rollup recomputation, purging.
- **Read replicas** for analytics, primary for writes.
- **Partition `activities` by month.** Queries are date-bounded, so most
  touch one or two partitions.

### What would *not* change

The data model, the auth design, the ownership rule, and the stateless
API. Those are the decisions that are expensive to revisit later — which is
why they were worth getting right at this size.

---

## 9. Deliberate tradeoffs

| Decision | Chosen | Rejected | Reason |
|---|---|---|---|
| Frontend | Vanilla JS | React | No build step, no bundler, one file. Costs component reuse — the wrong call at 10× the size |
| Monolith | Single container | Separate services | Same-origin removes CORS and makes password managers work |
| Migrations at boot | Alembic on startup | Separate release step | No release phase on Render. Breaks down with many instances; Alembic's lock serialises them for now |
| Purging | Opportunistic | Cron / scheduler | One fewer moving part. Nothing purges if nobody logs in |
| Redis | Optional | Required | A supporting service must not be able to take down the core one |
| Cache invalidation | TTL + explicit on write | TTL only | 60s of staleness is invisible; staleness after *your own* write is not |
| Sessions | JWT | Server-side sessions | Statelessness is what makes horizontal scaling free. Costs cheap revocation — bought back with Redis |
| Wrong owner | 404 | 403 | 403 confirms the resource exists |
| RPE | Self-reported, HR-assisted | Device integration | Works without a wearable; HR refines it when present |

---

## 10. What is still missing

Stated plainly, because knowing the gaps is the point of the document.

- **No backups.** Neon's free tier has none. A paid tier plus a *tested*
  restore is the first thing to buy.
- **No staging environment.** CI gates deployment, but there is nowhere to
  verify a release before users see it.
- **No distributed tracing.** Request ids correlate logs within this
  service; there is nothing to correlate across services yet because there
  is only one.
- **No load testing.** Capacity figures above are reasoned, not measured.
- **CSP `unsafe-inline`.** See §7.
- **Migrations run on every instance boot.** Correct today, wrong at scale.
- **No audit log.** Security-relevant events are logged but not stored
  queryably.

---

## 11. Technology choices

| Layer | Choice | Why this one |
|---|---|---|
| Language | Python 3.11 | Analytics domain; the ecosystem is where this kind of maths lives |
| Framework | FastAPI | Validation and dependency injection are built in, so auth and schemas cannot be forgotten |
| Server | gunicorn + uvicorn workers | Process supervision plus async handling |
| ORM | SQLAlchemy 2 | Lets the same code target SQLite in tests and Postgres in production |
| Database | PostgreSQL (Neon) | Fixed-shape relational data; Neon's free tier does not expire and wakes from idle |
| Cache | Redis (Upstash) | Atomic counters and TTLs are exactly what rate limiting and denylists need |
| Migrations | Alembic | Rename, drop, backfill and roll back — none of which `create_all` can do |
| Frontend | Vanilla JS + Chart.js | No build step; see §9 |
| Container | Docker | One artefact, identical locally and in production |
| CI/CD | GitHub Actions → Render | Tests gate deployment |

---

## 12. Running it

```bash
# Local
cd backend
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload

# Tests (SQLite)
pytest -v

# Tests against Postgres, as CI does
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/eh_test pytest -v

# Container
docker build -t endurancehub .
docker run -p 8000:8000 -e DATABASE_URL=... -e SECRET_KEY=... endurancehub
```

| Variable | Required | Effect if unset |
|---|---|---|
| `DATABASE_URL` | yes | Falls back to local Postgres |
| `SECRET_KEY` | in production | Boot fails in production; dev default otherwise |
| `ENVIRONMENT` | no | `development`; set `production` to enable strict validation and HSTS, and to hide `/docs` |
| `REDIS_URL` | no | Degraded mode: no revocation, local rate limits, no cache |
| `SENTRY_DSN` | no | No error reporting |
| `BREVO_API_KEY` + `MAIL_FROM` | no | Codes and reset links are logged instead of emailed |
| `WEB_CONCURRENCY` | no | 2 workers |
| `CORS_ORIGINS` | no | Same-origin only |

See [DEPLOYMENT.md](DEPLOYMENT.md) for the hosting setup and
[WALKTHROUGH.md](WALKTHROUGH.md) for a code-level tour.
