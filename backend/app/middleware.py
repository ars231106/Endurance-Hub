"""HTTP hardening middleware.

Browsers enforce a lot of security for you, but only when the server asks.
These headers are the asking. Each one closes a specific attack:

  HSTS                     - stops a downgrade to plain HTTP
  X-Content-Type-Options   - stops MIME sniffing turning an upload into script
  X-Frame-Options          - stops clickjacking via an invisible iframe
  Referrer-Policy          - stops reset tokens leaking in the Referer header
  CSP                      - limits where scripts and data may come from
  Permissions-Policy       - turns off device APIs the app never uses
"""

from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import settings

# The frontend is a single HTML file with inline styles and scripts and one
# CDN for charts, so 'unsafe-inline' is required for it to run at all. That
# is a real weakening of CSP and the honest fix is moving to hashed or
# nonced scripts - noted rather than hidden.
CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com data:; "
    "img-src 'self' data: blob:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        # strict-origin-when-cross-origin still sends the full URL to the
        # same origin; "no-referrer" is right here because reset links put
        # a one-time token in the query string.
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "geolocation=(), microphone=(), camera=(), payment=(), usb=()"
        )
        response.headers["Content-Security-Policy"] = CSP
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"

        # Only meaningful over HTTPS, and dangerous on localhost - it would
        # pin a developer's browser to https for the whole machine.
        if settings.is_production:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )

        # Do not advertise the server software. MutableHeaders supports
        # deletion but not .pop(), so check first.
        if "server" in response.headers:
            del response.headers["server"]
        return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Rejects oversized bodies before they are read into memory.

    Without this, a single request declaring a 2 GB body can exhaust a
    worker's memory - a denial of service that costs the attacker nothing.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        declared = request.headers.get("content-length")
        if declared:
            try:
                if int(declared) > settings.max_request_bytes:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": "Request body too large"},
                    )
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length"})
        return await call_next(request)
