# Deployment

**Live:** https://endurancehub-dyq3.onrender.com (Render web service, Neon PostgreSQL)

The app ships as a single Docker image serving both the API and the frontend, so there's one service to deploy rather than two.

## Continuous integration

`.github/workflows/ci.yml` runs on every push to `main` and every pull request:

| Job | What it does |
|---|---|
| **Lint** | `ruff check` across the backend |
| **Tests (SQLite)** | Full pytest suite against a throwaway database — fast feedback |
| **Tests (PostgreSQL)** | The same suite against Postgres 16 in a service container, so dialect-specific bugs can't hide |
| **Frontend checks** | Syntax-checks the app script, verifies every icon reference resolves, and fails on duplicate element IDs or inline handlers with no matching function |
| **Docker** | Builds the image, starts the container and polls `/health` until it responds |

Run the same checks locally before pushing:

```bash
cd backend
pip install -r requirements-dev.txt
ruff check .
pytest -v
```

## Deploying to Render

Render hosts the container; the database lives on Neon. Render's free Postgres expires after 30 days, whereas Neon's free plan is permanent and wakes from idle on the next connection — the right shape for a project that sits quiet between visits.

**1. Create the database (Neon)**

1. Sign up at [neon.com](https://neon.com) and create a project.
2. Copy the pooled connection string. It looks like
   `postgresql://user:pass@ep-xxx-pooler.region.aws.neon.tech/neondb?sslmode=require`
3. Keep the `?sslmode=require` suffix — Neon refuses unencrypted connections.

**2. Deploy the app (Render)**

1. Push the repository to GitHub.
2. In Render, choose **New → Blueprint** and select the repo.
3. Render reads `render.yaml`, creates the `endurancehub` web service and generates a `SECRET_KEY`.
4. Set **`DATABASE_URL`** in the dashboard to the Neon string (it is marked `sync: false`, so it is never committed).
5. Click **Apply**. First build takes a few minutes.

On first boot the app runs `create_all()` then `sync_schema()`, so an empty Neon database builds its own tables — no migration step.

Every push to `main` redeploys automatically (`autoDeploy: true`), and Render waits for `/health` before switching traffic to the new version.

### Sending real verification emails

**SMTP does not work on Render.** Like most cloud hosts, it blocks outbound SMTP ports as an anti-spam measure, so `smtp.gmail.com:465` fails with `[Errno 101] Network is unreachable` regardless of how correct the credentials are. Port 587 is blocked too.

Use an email API over HTTPS instead — port 443 is never blocked. The app picks its transport automatically from whichever credentials it finds, in this order:

| Env vars | Transport | Notes |
|---|---|---|
| `RESEND_API_KEY` (+ optional `MAIL_FROM`) | Resend | Fastest to set up. Without a verified domain it will only deliver to the address that owns the Resend account |
| `BREVO_API_KEY` + `MAIL_FROM` | Brevo | 300 emails/day free, delivers to any address once the sender address is verified — the better choice for a public demo |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` | SMTP | Works locally; blocked on most hosts |
| none | Log only | The code is printed to the server log so the deployment stays usable |

Delivery never raises. The account row is committed before the email goes out, so a mail failure must not turn a successful registration into a 500 — instead the failure is logged with the provider's reason, and the code is written to the log as a fallback.

### Free-tier caveats

Free web services on Render sleep after inactivity, so the first request after a quiet period takes 30–60 seconds while the container cold-starts. Neon adds a much shorter wake-up of its own — the engine is configured with `pool_pre_ping=True` so a connection dropped during that sleep is discarded and reopened rather than raising.

Render's own free Postgres expires 30 days after creation, which is why the database moved to Neon. If you ever move it back, budget for the upgrade.

## Other hosts

The image is plain Docker, so `fly deploy`, Railway or any container host works. The app needs exactly two environment variables:

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | yes | `postgres://` URLs are normalised to `postgresql://` automatically |
| `SECRET_KEY` | yes | Signs JWTs. Never ship the development default |
| `SMTP_*` | no | Unset means verification codes are logged instead of emailed |

## Before going public

The current setup is deliberately simple. For real users, tighten:

- **CORS** — `allow_origins=["*"]` is fine while frontend and API share an origin, but should name the real domain if they ever separate.
- **Migrations** — `schema_sync.py` handles additive changes only. Alembic is the right tool once columns need renaming or dropping.
- **Rate limiting** — `/login` and `/resend-otp` have no throttle beyond the OTP cooldown.
- **Token revocation** — JWTs stay valid until they expire; add refresh tokens or a denylist if sessions need to be killable.
