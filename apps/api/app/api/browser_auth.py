"""Opt-in OIDC browser entry using server-side code exchange and host-only cookies."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

import httpx
import jwt
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.api.deps import get_platform_db
from app.auth import _fetch_json, resolve_oidc_actor, verify_oidc_token
from app.config import Settings, get_settings
from app.exceptions import AuthenticationError, DomainError, ValidationError

_STATE_COOKIE = "sim_oidc_state"
_NO_CACHE = {"Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer"}


def safe_return_to(value: str) -> str:
    """Accept only normalized application paths, never URLs or authentication loops."""
    if len(value) > 1600:
        raise ValidationError("Return path is too long")
    decoded = unquote(value)
    parsed = urlsplit(value)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.fragment
        or any(ord(char) < 32 or ord(char) == 127 for char in decoded)
        or "\\" in decoded
        or not value.startswith("/app-next")
        or (parsed.path != "/app-next" and not parsed.path.startswith("/app-next/"))
        or "%" in unquote(parsed.path)
        or any(part in {".", ".."} for part in unquote(parsed.path).split("/"))
        or unquote(parsed.path) != parsed.path
        or parsed.path.rstrip("/")
        in {
            "/app-next/login",
            "/app-next/auth/entry",
            "/app-next/oidc/callback",
        }
    ):
        raise ValidationError("Return path must be a local /app-next page")
    sensitive = {"token", "access_token", "id_token", "refresh_token", "code", "session", "cookie"}
    if any(key.lower() in sensitive for key, _ in parse_qsl(parsed.query)):
        raise ValidationError("Credentials are not accepted in return paths")
    return value


def _endpoints(settings: Settings) -> tuple[str, str]:
    authorization_url, token_url = settings.sso_authorization_url, settings.sso_token_url
    if not authorization_url or not token_url:
        metadata = _fetch_json(
            f"{settings.oidc_issuer_url.rstrip('/')}/.well-known/openid-configuration",
            timeout=settings.oidc_http_timeout_seconds,
        )
        if metadata.get("issuer") != settings.oidc_issuer_url:
            raise AuthenticationError("OIDC discovery issuer does not match configuration")
        authorization_url = authorization_url or metadata.get("authorization_endpoint")
        token_url = token_url or metadata.get("token_endpoint")
    for value in (authorization_url, token_url):
        if not isinstance(value, str) or not value:
            raise AuthenticationError("OIDC discovery has no authorization/token endpoint")
        try:
            settings.validate_sso_url(value)
        except ValueError as exc:
            raise AuthenticationError("OIDC endpoint URL is not permitted") from exc
    return authorization_url, token_url


def _state_path(settings: Settings) -> str:
    return f"{settings.api_prefix}/auth"


def _clear_state(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        _STATE_COOKIE,
        path=_state_path(settings),
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def _enabled(settings: Settings) -> None:
    if not settings.sso_enabled:
        raise AuthenticationError("Browser SSO is not configured")


def _read_state(request: Request, state: str | None, settings: Settings) -> dict[str, Any]:
    encoded = request.cookies.get(_STATE_COOKIE)
    if not encoded or not state:
        raise AuthenticationError("OIDC callback has no browser login state")
    try:
        payload = jwt.decode(
            encoded,
            settings.sso_state_secret,
            algorithms=["HS256"],
            audience=settings.sso_redirect_uri,
            issuer="sim-browser-login",
            options={
                "require": ["exp", "iat", "jti", "nonce", "verifier", "return_to", "iss", "aud"]
            },
        )
    except jwt.PyJWTError as exc:
        raise AuthenticationError("OIDC browser login state is invalid or expired") from exc
    if not isinstance(payload.get("jti"), str) or not hmac.compare_digest(
        payload["jti"].encode(), state.encode()
    ):
        raise AuthenticationError("OIDC callback state does not match this browser")
    if any(not isinstance(payload.get(key), str) for key in ("nonce", "verifier", "return_to")):
        raise AuthenticationError("OIDC browser login state is malformed")
    safe_return_to(payload["return_to"])
    return payload


def _exchange(code: str, verifier: str, settings: Settings) -> dict[str, Any]:
    _authorization_url, token_url = _endpoints(settings)
    data = {
        "grant_type": "authorization_code",
        "client_id": settings.sso_client_id,
        "code": code,
        "redirect_uri": settings.sso_redirect_uri,
        "code_verifier": verifier,
    }
    auth = (
        httpx.BasicAuth(settings.sso_client_id, settings.sso_client_secret)
        if settings.sso_client_secret
        else None
    )
    try:
        with httpx.Client(
            timeout=settings.oidc_http_timeout_seconds, follow_redirects=False
        ) as client:
            response = client.post(
                token_url, data=data, auth=auth, headers={"Accept": "application/json"}
            )
        if response.status_code != 200:
            raise AuthenticationError("OIDC authorization-code exchange failed")
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise AuthenticationError("OIDC authorization-code exchange failed") from exc
    if not isinstance(payload, dict) or str(payload.get("token_type", "")).lower() != "bearer":
        raise AuthenticationError("OIDC token response is invalid")
    return payload


def _verify_browser_tokens(
    payload: dict[str, Any], nonce: str, settings: Settings
) -> tuple[str, dict[str, Any]]:
    access_token, id_token = payload.get("access_token"), payload.get("id_token")
    if (
        not isinstance(access_token, str)
        or not isinstance(id_token, str)
        or len(access_token) > 3800
    ):
        raise AuthenticationError(
            "OIDC response must contain an ID token and a cookie-sized API token"
        )
    try:
        claims = verify_oidc_token(access_token, settings)
    except (TypeError, ValueError, OverflowError) as exc:
        raise AuthenticationError("OIDC API token claims are malformed") from exc
    # Reuse the exact existing signature/issuer/JWKS checks, with the browser
    # client's audience for the ID token rather than the API audience.
    try:
        id_claims = verify_oidc_token(
            id_token,
            settings.model_copy(update={"oidc_audience": settings.sso_client_id}),
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise AuthenticationError("OIDC ID token claims are malformed") from exc
    actual_nonce = id_claims.get("nonce")
    if not isinstance(actual_nonce, str) or not hmac.compare_digest(
        actual_nonce.encode(), nonce.encode()
    ):
        raise AuthenticationError("OIDC ID token nonce is invalid")
    if not isinstance(id_claims.get("iat"), (int, float)):
        raise AuthenticationError("OIDC ID token has no issue time")
    audience, authorized_party = id_claims.get("aud"), id_claims.get("azp")
    if (isinstance(audience, list) and len(audience) > 1 and not authorized_party) or (
        authorized_party is not None and authorized_party != settings.sso_client_id
    ):
        raise AuthenticationError("OIDC ID token authorized party is invalid")
    if id_claims["sub"] != claims["sub"]:
        raise AuthenticationError("OIDC access and ID token identities do not match")
    if float(claims["exp"]) <= time.time():
        raise AuthenticationError("OIDC API token has expired")
    return access_token, claims


def build_browser_auth_router() -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["browser-authentication"])

    @router.get("/config")
    def configuration() -> Response:
        settings = get_settings()
        return JSONResponse(
            {
                "auth_mode": settings.auth_mode,
                "demo_mode": settings.demo_mode,
                "cookie_auth_enabled": settings.cookie_auth_enabled,
                "sso_enabled": settings.sso_enabled,
                "csrf_cookie_name": settings.csrf_cookie_name,
                "csrf_header_name": settings.csrf_header_name,
            },
            headers=_NO_CACHE,
        )

    @router.get("/login")
    def login(return_to: str = Query(default="/app-next/", max_length=1600)) -> Response:
        settings = get_settings()
        _enabled(settings)
        return_to = safe_return_to(return_to)
        authorization_url, _token_url = _endpoints(settings)
        now = int(time.time())
        verifier, nonce, state = (
            secrets.token_urlsafe(48),
            secrets.token_urlsafe(32),
            secrets.token_urlsafe(32),
        )
        encoded = jwt.encode(
            {
                "iss": "sim-browser-login",
                "aud": settings.sso_redirect_uri,
                "iat": now,
                "exp": now + settings.sso_state_ttl_seconds,
                "jti": state,
                "nonce": nonce,
                "verifier": verifier,
                "return_to": return_to,
            },
            settings.sso_state_secret,
            algorithm="HS256",
        )
        if len(encoded) > 3800:
            raise ValidationError("Return path is too large for browser login state")
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        parsed = urlsplit(authorization_url)
        parameters = dict(parse_qsl(parsed.query))
        parameters.update(
            {
                "client_id": settings.sso_client_id,
                "redirect_uri": settings.sso_redirect_uri,
                "response_type": "code",
                "response_mode": "query",
                "scope": "openid profile email",
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        target = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(parameters), ""))
        response = RedirectResponse(target, status_code=302, headers=_NO_CACHE)
        response.set_cookie(
            _STATE_COOKIE,
            encoded,
            max_age=settings.sso_state_ttl_seconds,
            path=_state_path(settings),
            secure=settings.cookie_secure,
            httponly=True,
            samesite="lax",
        )
        return response

    @router.get("/callback")
    def callback(
        request: Request,
        code: str | None = Query(default=None, max_length=4096),
        state: str | None = Query(default=None, max_length=256),
        error: str | None = Query(default=None, max_length=180),
        iss: str | None = Query(default=None, max_length=2048),
        db: Session = Depends(get_platform_db),
    ) -> Response:
        settings = get_settings()
        try:
            _enabled(settings)
            login_state = _read_state(request, state, settings)
            if iss is not None and iss != settings.oidc_issuer_url:
                raise AuthenticationError("OIDC authorization response issuer is invalid")
            if error or not code:
                raise AuthenticationError("Identity provider did not complete sign-in")
            payload = _exchange(code, login_state["verifier"], settings)
            token, claims = _verify_browser_tokens(payload, login_state["nonce"], settings)
            resolve_oidc_actor(
                db,
                claims,
                requested_tenant_id=str(claims.get(settings.oidc_tenant_claim, "")),
                settings=settings,
            )
            db.commit()  # Persist explicitly enabled verified-email subject linking.
            response = RedirectResponse(
                login_state["return_to"], status_code=303, headers=_NO_CACHE
            )
            max_age = max(1, int(float(claims["exp"]) - time.time()))
            response.set_cookie(
                settings.auth_cookie_name,
                token,
                max_age=max_age,
                path="/",
                secure=settings.cookie_secure,
                httponly=True,
                samesite=settings.cookie_samesite,
            )
            response.set_cookie(
                settings.csrf_cookie_name,
                secrets.token_urlsafe(32),
                max_age=max_age,
                path="/",
                secure=settings.cookie_secure,
                httponly=False,
                samesite=settings.cookie_samesite,
            )
        except DomainError as exc:
            db.rollback()
            response = JSONResponse(
                {"detail": str(exc)}, status_code=exc.status_code, headers=_NO_CACHE
            )
        _clear_state(response, settings)
        return response

    @router.post("/logout")
    def logout(request: Request) -> Response:
        settings = get_settings()
        # Local logout must also work for expired credentials; protect cookie
        # clearing from cross-site requests without requiring a valid access token.
        if settings.cookie_auth_enabled and request.cookies.get(settings.auth_cookie_name):
            cookie = request.cookies.get(settings.csrf_cookie_name)
            header = request.headers.get(settings.csrf_header_name)
            if (
                not cookie
                or not header
                or not hmac.compare_digest(cookie.encode(), header.encode())
            ):
                return JSONResponse(
                    {"detail": "CSRF token validation failed"}, status_code=403, headers=_NO_CACHE
                )
        response = Response(status_code=204, headers=_NO_CACHE)
        response.delete_cookie(
            settings.auth_cookie_name,
            path="/",
            secure=settings.cookie_secure,
            httponly=True,
            samesite=settings.cookie_samesite,
        )
        response.delete_cookie(
            settings.csrf_cookie_name,
            path="/",
            secure=settings.cookie_secure,
            samesite=settings.cookie_samesite,
        )
        _clear_state(response, settings)
        return response

    return router
