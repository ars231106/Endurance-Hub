from app.env_loader import load_env

# Load backend/.env BEFORE the imports below, because services read
# env vars (SMTP, SECRET_KEY, DATABASE_URL) at import time.
load_env()

import logging
import traceback
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.activities import router as activities_router
from app.api.analytics import router as analytics_router
from app.api.auth import router as auth_router
from app.databases.database import engine
from app.models.activity import Activity  # noqa: F401 - registers the table
from app.models.base import Base
from app.models.otp import EmailOTP  # noqa: F401 - registers the table
from app.models.user import User  # noqa: F401 - registers the table

# Create any missing tables at startup (dev convenience; production
# projects use migration tools like Alembic instead).
Base.metadata.create_all(bind=engine)

# create_all() adds missing tables but never missing COLUMNS, so bring
# existing tables up to date too. Idempotent - safe on every boot.
from app.databases.schema_sync import sync_schema  # noqa: E402

sync_schema(engine)

# Purge accounts whose 5-day deletion grace period has expired.
from app.databases.database import SessionLocal
from app.services.account_service import purge_expired_accounts

_db = SessionLocal()
try:
    purge_expired_accounts(_db)
finally:
    _db.close()

app = FastAPI(
    title="EnduranceHub API",
    description="Endurance training analytics platform - log workouts, track load, break records.",
    version="1.0.0",
)

# CORS lets the browser-based frontend (a different origin) call this API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(activities_router)
app.include_router(analytics_router)


@app.exception_handler(Exception)
def unhandled_exception_handler(request: Request, exc: Exception):
    """Turn crashes into a proper JSON 500.

    Without this, an unhandled error escapes past the CORS middleware, so
    the response carries no CORS headers and the browser reports a useless
    "Failed to fetch" instead of the real problem. The detail stays generic
    on purpose - the traceback belongs in the server log, not the client.
    """
    logging.error("Unhandled error on %s %s\n%s", request.method, request.url.path,
                  "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    return JSONResponse(
        status_code=500,
        content={"detail": "Something went wrong on the server. Check the API logs."},
    )


# Serve the frontend from the API itself. Opening index.html as a file://
# page means the browser has no real origin, so Chrome's password manager
# won't offer to save credentials and OAuth redirects are impossible.
# Served over http://localhost the app gets a proper origin, and the
# frontend and API become same-origin (no CORS preflight at all).
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/")
    def serve_frontend():
        return FileResponse(FRONTEND_DIR / "index.html")
else:
    @app.get("/")
    def home():
        return {"message": "Welcome to EnduranceHub", "docs": "/docs"}


@app.get("/health")
def health():
    # Standard liveness probe: monitoring systems ping this to check
    # whether the API is up.
    return {"status": "healthy"}
