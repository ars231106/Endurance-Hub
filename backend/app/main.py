from app.env_loader import load_env

# Load backend/.env BEFORE anything else imports app.config, which reads
# the environment at import time.
load_env()

import logging  # noqa: E402
import traceback  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from pathlib import Path  # noqa: E402

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.api.activities import router as activities_router  # noqa: E402
from app.api.analytics import router as analytics_router  # noqa: E402
from app.api.auth import router as auth_router  # noqa: E402
from app.config import settings  # noqa: E402
from app.databases.database import SessionLocal, engine  # noqa: E402
from app.middleware import RequestSizeLimitMiddleware, SecurityHeadersMiddleware  # noqa: E402
from app.models.activity import Activity  # noqa: F401,E402 - registers the table
from app.models.base import Base  # noqa: E402
from app.models.otp import EmailOTP  # noqa: F401,E402 - registers the table
from app.models.password_reset import PasswordReset  # noqa: F401,E402 - registers the table
from app.models.user import User  # noqa: F401,E402 - registers the table
from app.observability import (  # noqa: E402
    RequestContextMiddleware,
    configure_logging,
    init_sentry,
    metrics,
)
from app.services import cache  # noqa: E402

configure_logging()
log = logging.getLogger(__name__)


def _prepare_database() -> None:
    """Brings the schema up to date, then does the startup housekeeping.

    Alembic owns the schema when it is available. create_all plus
    schema_sync remain as a fallback so a fresh database still works if
    migrations have not been wired up in a given environment - both are
    idempotent, so running them after Alembic is harmless.
    """
    from app.databases.migrations import run_migrations

    if not run_migrations():
        Base.metadata.create_all(bind=engine)
        from app.databases.schema_sync import sync_schema

        sync_schema(engine)

    db = SessionLocal()
    try:
        from app.services.account_service import purge_expired_accounts
        from app.services.password_reset_service import purge_expired_resets

        purged = purge_expired_accounts(db)
        stale = purge_expired_resets(db)
        if purged or stale:
            log.info("startup purge", extra={"accounts": purged, "reset_tokens": stale})
    except Exception as exc:  # noqa: BLE001 - housekeeping must not block boot
        log.warning("startup purge skipped: %s", exc)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup work lives here rather than at import time, so importing the
    # module (for tests, for tooling) has no side effects.
    sentry_on = init_sentry()
    log.info(
        "starting",
        extra={
            "environment": settings.environment,
            "version": settings.version,
            "redis": cache.available(),
            "sentry": sentry_on,
        },
    )
    _prepare_database()
    yield
    log.info("shutting down")


app = FastAPI(
    title="EnduranceHub API",
    description="Endurance training analytics platform - log workouts, track load, break records.",
    version="2.0.0",
    lifespan=lifespan,
    # The interactive docs expose every endpoint and schema. Useful in
    # development, needless attack surface in production.
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None,
    openapi_url=None if settings.is_production else "/openapi.json",
)

# Middleware runs in reverse order of registration, so the LAST one added
# is the outermost. Request context is outermost: it must time and tag
# every request, including those rejected by the layers beneath it.
app.add_middleware(
    CORSMiddleware,
    # Empty by default. In production the frontend is served by this same
    # app, so no cross-origin request happens and no origin needs allowing.
    # A wildcard here would let any website on the internet call this API
    # with a user's credentials.
    allow_origins=settings.cors_origins,
    allow_credentials=bool(settings.cors_origins),
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    max_age=600,
)
app.add_middleware(RequestSizeLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestContextMiddleware)

app.include_router(auth_router)
app.include_router(activities_router)
app.include_router(analytics_router)


@app.exception_handler(Exception)
def unhandled_exception_handler(request: Request, exc: Exception):
    """Turns a crash into a proper JSON 500.

    Without this, an unhandled error escapes past the CORS middleware, so
    the response carries no CORS headers and the browser reports a useless
    "Failed to fetch" instead of the real problem. The detail stays generic
    on purpose - the traceback belongs in the log, not the client - but the
    request id is returned so a user can quote it and the exact trace can
    be found.
    """
    from app.observability import request_id_var

    request_id = request_id_var.get()
    log.error(
        "unhandled exception",
        extra={
            "method": request.method,
            "path": request.url.path,
            "traceback": "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            ),
        },
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Something went wrong on the server.",
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id},
    )


# --- operational endpoints ------------------------------------------------

@app.get("/health", include_in_schema=False)
def health():
    """Liveness: is the process running? Must not touch dependencies.

    A liveness probe that checks the database restarts the app when the
    database blips - which fixes nothing and turns a recoverable outage
    into a restart loop.
    """
    return {"status": "healthy", "version": settings.version}


@app.get("/ready", include_in_schema=False)
def ready():
    """Readiness: can this instance actually serve traffic?

    Unlike liveness, this DOES check dependencies, because an instance that
    cannot reach its database should be taken out of the load balancer
    rather than restarted.
    """
    checks = {}
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"error: {exc.__class__.__name__}"

    # Redis is optional, so its absence is reported but not fatal.
    checks["cache"] = "ok" if cache.available() else "unavailable (degraded)"

    healthy = checks["database"] == "ok"
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"ready": healthy, "checks": checks, "version": settings.version},
    )


@app.get("/metrics", include_in_schema=False)
def metrics_endpoint():
    """Per-process counters. Resets on restart, which is fine for a scrape
    target - the collector computes rates from the deltas."""
    return metrics.snapshot()


# --- frontend -------------------------------------------------------------
# Served by the API itself. Opening index.html as a file:// page means the
# browser has no real origin, so Chrome's password manager won't offer to
# save credentials and OAuth redirects are impossible. Served over HTTP the
# app gets a proper origin, and frontend and API become same-origin - which
# is also why CORS can stay empty.
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def serve_frontend():
        return FileResponse(FRONTEND_DIR / "index.html")

    @app.get("/learn", include_in_schema=False)
    def serve_study_guide():
        """A mobile-friendly walkthrough of how this codebase works."""
        return FileResponse(FRONTEND_DIR / "learn.html")
else:
    @app.get("/", include_in_schema=False)
    def home():
        return {"message": "Welcome to EnduranceHub", "docs": "/docs"}
