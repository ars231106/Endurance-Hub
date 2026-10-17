"""Redis access, with graceful degradation when Redis is absent.

Redis is optional on purpose: the app must still boot and serve traffic
without it, because an outage in a *supporting* service should never take
down the *core* service. Every function here either works or returns a
neutral value - none of them raise, and none of them block a request.

What is lost without Redis:
  - rate limiting falls back to a per-process counter (correct for one
    worker, approximate across several)
  - token revocation is unavailable, so logout relies on short access
    token lifetimes alone
  - analytics responses are recomputed on every request
"""

import json
import logging
import threading
import time
from typing import Any, Optional

from app.config import settings

log = logging.getLogger(__name__)

_client = None
_unavailable_until = 0.0
# Once Redis fails, stop trying for a while. Without this, every request
# pays a connection timeout and a slow Redis becomes a slow API.
_RETRY_AFTER_SECONDS = 30
_lock = threading.Lock()


def _connect():
    try:
        import redis  # imported lazily so the package is optional
    except ImportError:
        log.info("redis package not installed; running without cache")
        return None

    try:
        client = redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            health_check_interval=30,
        )
        client.ping()
        log.info("connected to redis")
        return client
    except Exception as exc:  # noqa: BLE001 - any failure means "no cache"
        log.warning("redis unavailable: %s", exc)
        return None


def get_client():
    """Returns a live client, or None. Never raises, never blocks long."""
    global _client, _unavailable_until

    if not settings.redis_url:
        return None
    if _client is not None:
        return _client
    if time.monotonic() < _unavailable_until:
        return None

    with _lock:
        if _client is None and time.monotonic() >= _unavailable_until:
            _client = _connect()
            if _client is None:
                _unavailable_until = time.monotonic() + _RETRY_AFTER_SECONDS
    return _client


def _drop_client() -> None:
    """Forget a client that just failed, so the next call reconnects."""
    global _client, _unavailable_until
    _client = None
    _unavailable_until = time.monotonic() + _RETRY_AFTER_SECONDS


def available() -> bool:
    return get_client() is not None


# --- generic key/value ----------------------------------------------------

def cache_get(key: str) -> Optional[Any]:
    client = get_client()
    if client is None:
        return None
    try:
        raw = client.get(key)
        return json.loads(raw) if raw else None
    except Exception as exc:  # noqa: BLE001
        log.warning("cache read failed for %s: %s", key, exc)
        _drop_client()
        return None


def cache_set(key: str, value: Any, ttl_seconds: int) -> None:
    client = get_client()
    if client is None:
        return
    try:
        # set(..., ex=) rather than setex(): redis-py deprecated setex in
        # 2.6, and the two are equivalent.
        client.set(key, json.dumps(value, default=str), ex=max(ttl_seconds, 1))
    except Exception as exc:  # noqa: BLE001
        log.warning("cache write failed for %s: %s", key, exc)
        _drop_client()


def cache_delete_prefix(prefix: str) -> None:
    """Invalidates every key under a prefix.

    Uses SCAN rather than KEYS: KEYS blocks the whole Redis server while
    it walks the keyspace, which is fine with ten keys and catastrophic
    with ten million.
    """
    client = get_client()
    if client is None:
        return
    try:
        for key in client.scan_iter(match=f"{prefix}*", count=200):
            client.delete(key)
    except Exception as exc:  # noqa: BLE001
        log.warning("cache invalidation failed for %s: %s", prefix, exc)
        _drop_client()


# --- set membership, used by the token denylist ---------------------------

def set_add(key: str, ttl_seconds: int) -> bool:
    """Marks a key as present for a bounded time. True if Redis took it."""
    client = get_client()
    if client is None:
        return False
    try:
        client.set(key, "1", ex=max(ttl_seconds, 1))
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("denylist write failed: %s", exc)
        _drop_client()
        return False


def exists(key: str) -> bool:
    client = get_client()
    if client is None:
        return False
    try:
        return bool(client.exists(key))
    except Exception as exc:  # noqa: BLE001
        log.warning("denylist read failed: %s", exc)
        _drop_client()
        return False


# --- atomic counter, used by the rate limiter -----------------------------

def incr_with_ttl(key: str, window_seconds: int) -> Optional[int]:
    """Increments a counter and sets its expiry in one round trip.

    Returns the new count, or None when Redis is unavailable so the
    caller can fall back. The pipeline matters: INCR then EXPIRE as two
    separate calls can lose the expiry if the process dies between them,
    leaving a counter that never resets and locks the user out forever.
    """
    client = get_client()
    if client is None:
        return None
    try:
        pipe = client.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_seconds)
        count, _ = pipe.execute()
        return int(count)
    except Exception as exc:  # noqa: BLE001
        log.warning("rate limit counter failed: %s", exc)
        _drop_client()
        return None
