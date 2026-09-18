from __future__ import annotations

import hmac
import math
import time
import uuid

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import Settings
from app.shared_rate_limit import (
    DatabaseFixedWindowRateLimiter,
    FixedWindowRateLimiter,
    RateLimitDecision,
)


class SecurityBoundaryMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, settings: Settings) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self.settings = settings
        if settings.rate_limit_backend == "database":
            self.limiter = DatabaseFixedWindowRateLimiter(
                database_url=settings.rate_limit_database_url or settings.database_url,
                limit=settings.rate_limit_requests,
                window_seconds=settings.rate_limit_window_seconds,
                key_secret=settings.rate_limit_key_secret,
                retention_seconds=settings.rate_limit_retention_seconds,
                cleanup_interval_seconds=settings.rate_limit_cleanup_interval_seconds,
            )
        else:
            self.limiter = FixedWindowRateLimiter(
                limit=settings.rate_limit_requests,
                window_seconds=settings.rate_limit_window_seconds,
            )

    def _trusted_proxy(self, request: Request) -> bool:
        peer = request.client.host if request.client else ""
        return peer in self.settings.trusted_proxy_ips

    def _client_address(self, request: Request) -> str:
        if self._trusted_proxy(request):
            forwarded = request.headers.get("x-forwarded-for", "")
            candidate = forwarded.split(",", 1)[0].strip()
            if candidate:
                return candidate
        return request.client.host if request.client else "unknown"

    def _limiter_key(self, request: Request) -> str:
        # This middleware runs before authentication; identity headers cannot
        # establish quota ownership and must not create fresh request budgets.
        return f"ingress|{self._client_address(request)}"

    def _secure_request(self, request: Request) -> bool:
        if request.url.scheme == "https":
            return True
        if self._trusted_proxy(request):
            return request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip() == "https"
        return False

    def _add_security_headers(
        self,
        response: Response,
        *,
        request_id: str,
        secure_request: bool,
        rate: RateLimitDecision | None,
        degraded_rate_limit: bool = False,
    ) -> None:
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers["Permissions-Policy"] = "camera=(self), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'self'; frame-ancestors 'none'"
        )
        if secure_request and self.settings.hsts_max_age > 0:
            value = f"max-age={self.settings.hsts_max_age}"
            if self.settings.hsts_include_subdomains:
                value += "; includeSubDomains"
            if self.settings.hsts_preload:
                value += "; preload"
            response.headers["Strict-Transport-Security"] = value
        if rate is not None:
            response.headers["X-RateLimit-Limit"] = str(rate.limit)
            response.headers["X-RateLimit-Remaining"] = str(rate.remaining)
            response.headers["X-RateLimit-Reset"] = str(rate.reset_epoch)
        if degraded_rate_limit:
            response.headers["X-RateLimit-Policy"] = "degraded-open"

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        secure_request = self._secure_request(request)

        if self.settings.require_https and not secure_request:
            response = JSONResponse(
                status_code=426,
                content={"detail": "HTTPS is required", "request_id": request_id},
            )
            self._add_security_headers(
                response, request_id=request_id, secure_request=False, rate=None
            )
            return response

        rate: RateLimitDecision | None = None
        degraded_rate_limit = False
        if (
            self.settings.rate_limit_enabled
            and request.method != "OPTIONS"
            and request.url.path not in self.settings.rate_limit_exempt_paths
        ):
            try:
                rate = self.limiter.consume(self._limiter_key(request))
            except Exception:
                if self.settings.rate_limit_fail_mode == "closed":
                    response = JSONResponse(
                        status_code=503,
                        content={"detail": "Rate-limit backend unavailable", "request_id": request_id},
                    )
                    self._add_security_headers(
                        response, request_id=request_id, secure_request=secure_request, rate=None
                    )
                    return response
                degraded_rate_limit = True
            if rate is not None and not rate.allowed:
                response = JSONResponse(
                    status_code=429,
                    content={"detail": "Rate limit exceeded", "request_id": request_id},
                )
                response.headers["Retry-After"] = str(
                    max(rate.reset_epoch - math.floor(time.time()), 1)
                )
                self._add_security_headers(
                    response, request_id=request_id, secure_request=secure_request, rate=rate
                )
                return response

        if (
            self.settings.cookie_auth_enabled
            and request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}
            and request.cookies.get(self.settings.auth_cookie_name)
            and not request.headers.get("authorization")
        ):
            cookie_token = request.cookies.get(self.settings.csrf_cookie_name)
            header_token = request.headers.get(self.settings.csrf_header_name)
            if not cookie_token or not header_token or not hmac.compare_digest(cookie_token, header_token):
                response = JSONResponse(
                    status_code=403,
                    content={"detail": "CSRF token validation failed", "request_id": request_id},
                )
                self._add_security_headers(
                    response,
                    request_id=request_id,
                    secure_request=secure_request,
                    rate=rate,
                    degraded_rate_limit=degraded_rate_limit,
                )
                return response

        response = await call_next(request)
        if request.url.path.startswith(self.settings.api_prefix):
            response.headers.setdefault("Cache-Control", "no-store")
        self._add_security_headers(
            response,
            request_id=request_id,
            secure_request=secure_request,
            rate=rate,
            degraded_rate_limit=degraded_rate_limit,
        )
        return response
