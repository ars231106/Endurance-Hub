# Single image serving both the API and the frontend.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first: this layer is cached until requirements.txt changes,
# so code edits rebuild in seconds rather than reinstalling everything.
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ backend/
COPY frontend/ frontend/

# Run as an unprivileged user. If the app is ever compromised, the attacker
# lands as `app` rather than root, and cannot write to the application code
# it is executing.
RUN useradd --create-home --uid 10001 app \
    && chown -R app:app /app
USER app

WORKDIR /app/backend
EXPOSE 8000

# Stamped at build time so logs and Sentry releases identify the exact
# commit a running container came from.
ARG GIT_COMMIT=dev
ENV GIT_COMMIT=${GIT_COMMIT}

# Docker's own health check, separate from Render's: an orchestrator can
# see an unhealthy container even when no external prober exists.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,os,sys; \
sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"PORT\",\"8000\")}/health', timeout=4).status==200 else 1)"

# Gunicorn supervises uvicorn workers: several processes on one container,
# with automatic restart of any worker that dies. Worker count comes from
# the environment because it depends on the instance's CPU allocation -
# too many workers on a small instance thrash rather than scale.
# Shell form so ${PORT} is expanded; hosting platforms assign it at runtime.
CMD ["sh", "-c", "gunicorn app.main:app \
--worker-class uvicorn.workers.UvicornWorker \
--workers ${WEB_CONCURRENCY:-2} \
--bind 0.0.0.0:${PORT:-8000} \
--timeout 60 \
--graceful-timeout 30 \
--keep-alive 5 \
--max-requests 2000 \
--max-requests-jitter 200 \
--access-logfile -"]
