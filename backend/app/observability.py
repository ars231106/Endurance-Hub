"""Logging, request correlation, metrics and error reporting.

The goal is that when a user says "it was broken this morning", you can
answer the question. That needs three things:

  1. every log line is structured and carries a request id, so one
     request's lines can be pulled out of a million
  2. every request is timed, so "slow" becomes a number
  3. unhandled errors reach somewhere you will actually look

Request ids come from the incoming X-Request-ID header when a proxy
supplies one, so a trace can be followed across services, and are returned
on the response so a user can quote it in a bug report.
"""

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings

# ContextVar, not a global: each concurrent request gets its own value
# without threading it through every function signature.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

# Fields that logging puts on every record. Anything NOT in here was added
# by the caller via `extra=` and belongs in the structured output.
_STANDARD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message", "asctime", "taskName",
}


class JsonFormatter(logging.Formatter):
    """One JSON object per line - what log aggregators expect."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
            "service": settings.service_name,
            "version": settings.version,
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    """Readable while developing."""

    def format(self, record: logging.LogRecord) -> str:
        extras = " ".join(
            f"{k}={v}" for k, v in record.__dict__.items()
            if k not in _STANDARD and not k.startswith("_")
        )
        base = f"{self.formatTime(record, '%H:%M:%S')} {record.levelname:<7} {record.getMessage()}"
        if extras:
            base += f"  [{extras}]"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if settings.json_logs else TextFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level)

    # uvicorn installs its own handlers; let them bubble up to ours so all
    # output has one format instead of two.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True

    # SQLAlchemy and urllib are noisy at INFO and say nothing useful.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


# --- in-process metrics ---------------------------------------------------
# Deliberately tiny: counters and latency buckets, no dependency. Enough to
# answer "how many 500s" and "how slow" without running a metrics stack.

class Metrics:
    def __init__(self) -> None:
        self.requests = 0
        self.errors = 0
        self.by_status: dict = {}
        self.duration_sum_ms = 0.0
        self.slow_requests = 0  # over 1s

    def record(self, status_code: int, duration_ms: float) -> None:
        self.requests += 1
        self.duration_sum_ms += duration_ms
        bucket = f"{status_code // 100}xx"
        self.by_status[bucket] = self.by_status.get(bucket, 0) + 1
        if status_code >= 500:
            self.errors += 1
        if duration_ms > 1000:
            self.slow_requests += 1

    def snapshot(self) -> dict:
        return {
            "requests_total": self.requests,
            "errors_total": self.errors,
            "by_status": dict(self.by_status),
            "avg_duration_ms": round(self.duration_sum_ms / self.requests, 1) if self.requests else 0,
            "slow_requests_total": self.slow_requests,
        }


metrics = Metrics()


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request id, times the request, logs the outcome."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        incoming = request.headers.get("x-request-id")
        request_id = incoming or uuid.uuid4().hex[:16]
        token = request_id_var.set(request_id)

        started = time.perf_counter()
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            # Logged here with full context, then re-raised so the
            # exception handler can turn it into a JSON 500.
            duration_ms = (time.perf_counter() - started) * 1000
            logging.getLogger("http").exception(
                "request failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round(duration_ms, 1),
                },
            )
            metrics.record(500, duration_ms)
            request_id_var.reset(token)
            raise

        duration_ms = (time.perf_counter() - started) * 1000
        metrics.record(status_code, duration_ms)

        response.headers["X-Request-ID"] = request_id
        # Server-Timing shows up in browser devtools, so a slow endpoint is
        # visible without digging through server logs.
        response.headers["Server-Timing"] = f"app;dur={duration_ms:.1f}"

        # Health checks fire constantly and would drown everything else.
        if request.url.path not in {"/health", "/ready", "/metrics"}:
            logging.getLogger("http").info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": status_code,
                    "duration_ms": round(duration_ms, 1),
                },
            )

        request_id_var.reset(token)
        return response


def init_sentry() -> bool:
    """Error reporting, if a DSN is configured and the SDK is installed."""
    if not settings.sentry_dsn:
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
    except ImportError:
        logging.warning("SENTRY_DSN set but sentry-sdk is not installed")
        return False

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        release=settings.version,
        integrations=[FastApiIntegration(), SqlalchemyIntegration()],
        # Sample traces rather than sending all of them - full tracing on a
        # free tier burns the quota in hours.
        traces_sample_rate=0.1,
        # Never ship request bodies or headers: they contain passwords,
        # reset tokens and JWTs.
        send_default_pii=False,
    )
    logging.info("sentry initialised")
    return True
