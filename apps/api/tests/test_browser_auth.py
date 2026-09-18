from __future__ import annotations

import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import auth
from app.api import browser_auth, deps
from app.api.deps import AuthenticatedActor, get_authenticated_actor
from app.config import Settings
from app.exceptions import DomainError
from app.http_security import SecurityBoundaryMiddleware
from app.models import UserIdentity

ISSUER = "https://identity.example/realm"
CLIENT_ID = "workspace-browser"
CALLBACK = "https://testserver/api/v1/auth/callback"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
JWK = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key()))
JWK.update({"kid": "test-browser", "alg": "RS256", "use": "sig"})


def settings_for(**overrides) -> Settings:
    values = {
        "auth_mode": "oidc",
        "demo_mode": False,
        "sso_enabled": True,
        "cookie_auth_enabled": True,
        "cors_allow_credentials": True,
        "sso_client_id": CLIENT_ID,
        "sso_redirect_uri": CALLBACK,
        "sso_authorization_url": f"{ISSUER}/authorize",
        "sso_token_url": f"{ISSUER}/token",
        "sso_state_secret": "isolated-test-only-random-state-secret-1234",
        "oidc_issuer_url": ISSUER,
        "oidc_audience": "sim-api",
        "oidc_jwks_json": json.dumps({"keys": [JWK]}),
        "oidc_clock_skew_seconds": 0,
        "rate_limit_enabled": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def encoded_token(**claims) -> str:
    now = int(time.time())
    payload = {
        "iss": ISSUER,
        "sub": "browser-admin",
        "aud": "sim-api",
        "iat": now,
        "exp": now + 300,
    }
    payload.update(claims)
    return jwt.encode(payload, KEY, algorithm="RS256", headers={"kid": "test-browser"})


def client_for(world, monkeypatch, settings=None):
    settings = settings or settings_for()
    monkeypatch.setattr(browser_auth, "get_settings", lambda: settings)
    monkeypatch.setattr(deps, "get_settings", lambda: settings)
    monkeypatch.setattr(deps, "PlatformSessionLocal", world.session_factory)
    auth.clear_jwks_cache()
    with world.session_factory() as db:
        actor = db.get(UserIdentity, world.admin)
        actor.oidc_subject = "browser-admin"
        db.commit()
    application = FastAPI()
    application.include_router(browser_auth.build_browser_auth_router(), prefix="/api/v1")
    application.add_middleware(SecurityBoundaryMiddleware, settings=settings)

    @application.exception_handler(DomainError)
    def error(_request: Request, exc: DomainError):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)

    @application.get("/whoami")
    def whoami(identity: AuthenticatedActor = Depends(get_authenticated_actor)):
        return {
            "id": str(identity.actor.id),
            "method": identity.auth_method,
            "claims": identity.claims,
        }

    return TestClient(application, base_url="https://testserver"), settings


def begin(client, path="/app-next/3d?workspace=example&room=room-1"):
    response = client.get("/api/v1/auth/login", params={"return_to": path}, follow_redirects=False)
    assert response.status_code == 302
    return parse_qs(urlsplit(response.headers["location"]).query), response


def mock_exchange(
    monkeypatch, world, authorization, *, id_claims=None, access_claims=None, payload_extra=None
):
    observed = []
    access = {"tenant_id": str(world.tenant_a), **(access_claims or {})}
    identity = {"aud": CLIENT_ID, "nonce": authorization["nonce"][0], **(id_claims or {})}
    payload = {
        "token_type": "Bearer",
        "access_token": encoded_token(**access),
        "id_token": encoded_token(**identity),
        **(payload_extra or {}),
    }

    def post(self, url, *, data, auth, headers):
        observed.append({"url": url, "data": data, "auth": auth})
        return httpx.Response(200, json=payload, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.Client, "post", post)
    return observed


def complete(client, authorization, **extra):
    return client.get(
        "/api/v1/auth/callback",
        params={
            "code": "one-time-code",
            "state": authorization["state"][0],
            **extra,
        },
        follow_redirects=False,
    )


def test_server_side_login_exchange_sets_verified_host_only_cookies(world, monkeypatch):
    client, settings = client_for(world, monkeypatch)
    authorization, started = begin(client)
    assert authorization["redirect_uri"] == [CALLBACK]
    assert authorization["response_type"] == ["code"]
    assert authorization["code_challenge_method"] == ["S256"]
    assert "code_verifier" not in authorization
    state_header = started.headers["set-cookie"]
    assert (
        "HttpOnly" in state_header and "Secure" in state_header and "SameSite=lax" in state_header
    )
    assert "Domain=" not in state_header
    observed = mock_exchange(monkeypatch, world, authorization)
    completed = complete(client, authorization, iss=ISSUER)
    assert completed.status_code == 303
    assert completed.headers["location"] == "/app-next/3d?workspace=example&room=room-1"
    assert completed.headers["cache-control"] == "no-store"
    assert completed.headers["referrer-policy"] == "no-referrer"
    posted = observed[0]["data"]
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(posted["code_verifier"].encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert challenge == authorization["code_challenge"][0]
    assert posted["redirect_uri"] == CALLBACK
    assert client.cookies.get(settings.auth_cookie_name)
    assert client.cookies.get(settings.csrf_cookie_name)
    assert client.cookies.get("sim_oidc_state") is None
    cookies = completed.headers.get_list("set-cookie")
    access_cookie = next(
        value for value in cookies if value.startswith(settings.auth_cookie_name + "=")
    )
    assert (
        "HttpOnly" in access_cookie and "Secure" in access_cookie and "Domain=" not in access_cookie
    )
    who = client.get("/whoami")
    assert who.status_code == 200
    assert who.json()["id"] == str(world.admin)
    assert who.json()["method"] == "cookie"
    assert complete(client, authorization).status_code == 401
    assert len(observed) == 1


@pytest.mark.parametrize(
    "path",
    [
        "https://evil.example",
        "//evil.example",
        "/app-next-evil",
        "/app-next/../api",
        "/app-next/%2e%2e/api",
        "/app-next/%252e%252e/api",
        "/app-next/\\evil",
        "/app-next/?token=secret",
        "/app-next/?access_token=secret",
        "/app-next/login",
        "/app-next/auth/entry",
        "/app-next/oidc/callback",
        "/app-next/%0d%0aevil",
    ],
)
def test_entry_rejects_external_paths_credentials_and_authentication_loops(
    world, monkeypatch, path
):
    client, _settings = client_for(world, monkeypatch)
    response = client.get("/api/v1/auth/login", params={"return_to": path}, follow_redirects=False)
    assert response.status_code == 422
    assert "sim_oidc_state" not in client.cookies


@pytest.mark.parametrize(
    "fault", ["absent", "mismatch", "tampered", "expired", "wrong_issuer", "unicode"]
)
def test_invalid_browser_state_never_exchanges_code(world, monkeypatch, fault):
    client, settings = client_for(world, monkeypatch)
    authorization, _ = begin(client)
    observed = mock_exchange(monkeypatch, world, authorization)
    if fault == "absent":
        client.cookies.clear()
    elif fault in {"mismatch", "unicode"}:
        authorization["state"] = ["非ASCII" if fault == "unicode" else "other-browser"]
    else:
        state = client.cookies.get("sim_oidc_state")
        decoded = jwt.decode(state, options={"verify_signature": False})
        if fault == "expired":
            decoded["exp"] = int(time.time()) - 1
        elif fault == "wrong_issuer":
            decoded["iss"] = "attacker"
        else:
            decoded["nonce"] = "attacker"
        key = (
            "wrong-signing-key-with-32-characters"
            if fault == "tampered"
            else settings.sso_state_secret
        )
        client.cookies.clear()
        client.cookies.set(
            "sim_oidc_state", jwt.encode(decoded, key, algorithm="HS256"), path="/api/v1/auth"
        )
    response = complete(client, authorization)
    assert response.status_code == 401
    assert observed == []
    assert settings.auth_cookie_name not in client.cookies


@pytest.mark.parametrize(
    "id_claims,access_claims",
    [
        ({"nonce": "wrong-nonce"}, {}),
        ({"nonce": "错误"}, {}),
        ({"aud": "other-browser"}, {}),
        ({"iss": "https://wrong-issuer.example"}, {}),
        ({"azp": "other-browser"}, {}),
        ({"sub": "other-user"}, {}),
        ({"exp": 1}, {}),
        ({"iat": None}, {}),
        ({"iat": float("inf")}, {}),
        ({}, {"exp": float("inf")}),
        ({}, {"aud": "other-api"}),
        ({}, {"exp": 1}),
        ({"sub": "not-provisioned"}, {"sub": "not-provisioned"}),
    ],
)
def test_callback_rejects_invalid_tokens_before_issuing_session(
    world, monkeypatch, id_claims, access_claims
):
    client, settings = client_for(world, monkeypatch)
    authorization, _ = begin(client)
    mock_exchange(
        monkeypatch, world, authorization, id_claims=id_claims, access_claims=access_claims
    )
    response = complete(client, authorization)
    assert response.status_code == 401
    assert settings.auth_cookie_name not in client.cookies
    assert "sim_oidc_state" not in client.cookies


def test_oidc_error_and_wrong_response_issuer_do_not_exchange(world, monkeypatch):
    client, _settings = client_for(world, monkeypatch)
    authorization, _ = begin(client)
    observed = mock_exchange(monkeypatch, world, authorization)
    assert complete(client, authorization, iss="https://other.example").status_code == 401
    authorization, _ = begin(client)
    assert complete(client, authorization, error="access_denied").status_code == 401
    assert not observed


def test_local_logout_requires_csrf_and_clears_expired_session(world, monkeypatch):
    client, settings = client_for(world, monkeypatch)
    client.cookies.set(settings.auth_cookie_name, "expired-or-invalid-token")
    client.cookies.set(settings.csrf_cookie_name, "csrf-test")
    assert client.post("/api/v1/auth/logout").status_code == 403
    assert (
        client.post("/api/v1/auth/logout", headers={"Authorization": "Bearer fake"}).status_code
        == 403
    )
    result = client.post("/api/v1/auth/logout", headers={settings.csrf_header_name: "csrf-test"})
    assert result.status_code == 204
    assert all("Max-Age=0" in value for value in result.headers.get_list("set-cookie"))


def test_bootstrap_rejects_spoofed_headers_conflicting_tokens_and_inactive_users(
    world, monkeypatch
):
    client, settings = client_for(world, monkeypatch)
    assert client.get("/whoami", headers={"X-Actor-ID": str(world.admin)}).status_code == 401
    token = encoded_token(tenant_id=str(world.tenant_a))
    client.cookies.set(settings.auth_cookie_name, token)
    assert client.get("/whoami", headers={"Authorization": "Bearer conflict"}).status_code == 401
    with world.session_factory() as db:
        db.get(UserIdentity, world.admin).active = False
        db.commit()
    assert client.get("/whoami").status_code == 401


def test_demo_bootstrap_is_explicit_and_checks_active_identity(world, monkeypatch):
    settings = settings_for(sso_enabled=False, auth_mode="demo", demo_mode=True)
    client, _ = client_for(world, monkeypatch, settings)
    assert client.get("/whoami").status_code == 401
    assert (
        client.get("/whoami", headers={"X-Actor-ID": str(world.admin)}).json()["method"] == "demo"
    )
    with world.session_factory() as db:
        db.get(UserIdentity, world.admin).active = False
        db.commit()
    assert client.get("/whoami", headers={"X-Actor-ID": str(world.admin)}).status_code == 401


def test_discovery_checks_issuer_and_endpoint_transport(world, monkeypatch):
    settings = settings_for(sso_authorization_url=None, sso_token_url=None)
    client, _ = client_for(world, monkeypatch, settings)
    monkeypatch.setattr(
        browser_auth,
        "_fetch_json",
        lambda *args, **kwargs: {
            "issuer": ISSUER,
            "authorization_endpoint": f"{ISSUER}/authorize",
            "token_endpoint": f"{ISSUER}/token",
        },
    )
    begin(client)
    monkeypatch.setattr(
        browser_auth, "_fetch_json", lambda *args, **kwargs: {"issuer": "https://evil.example"}
    )
    assert client.get("/api/v1/auth/login").status_code == 401
    monkeypatch.setattr(
        browser_auth,
        "_fetch_json",
        lambda *args, **kwargs: {
            "issuer": ISSUER,
            "authorization_endpoint": f"{ISSUER}/authorize",
            "token_endpoint": "http://remote.example/token",
        },
    )
    assert client.get("/api/v1/auth/login").status_code == 401


def test_disabled_config_exposes_no_secret_and_does_not_enable_demo_headers(world, monkeypatch):
    client, settings = client_for(world, monkeypatch, settings_for(sso_enabled=False))
    response = client.get("/api/v1/auth/config")
    assert response.json() == {
        "auth_mode": "oidc",
        "demo_mode": False,
        "cookie_auth_enabled": True,
        "sso_enabled": False,
        "csrf_cookie_name": settings.csrf_cookie_name,
        "csrf_header_name": settings.csrf_header_name,
    }
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/api/v1/auth/login").status_code == 401


@pytest.mark.parametrize(
    "changes",
    [
        {"auth_mode": "demo"},
        {"cookie_auth_enabled": False},
        {"sso_client_id": ""},
        {"sso_state_secret": "short"},
        {"sso_state_ttl_seconds": 10},
        {"sso_redirect_uri": "https://evil.example/wrong-path"},
        {"sso_redirect_uri": CALLBACK + "?redirect=elsewhere"},
        {"sso_token_url": "http://remote.example/token"},
        {"cookie_secure": False},
        {"oidc_algorithms": ["HS256"]},
    ],
)
def test_sso_configuration_rejects_unsafe_combinations(changes):
    with pytest.raises(ValidationError):
        settings_for(**changes)


@pytest.mark.parametrize("failure", ["redirect", "error", "malformed", "transport"])
def test_exchange_errors_do_not_forward_tokens_or_set_cookies(world, monkeypatch, failure):
    client, settings = client_for(world, monkeypatch)
    authorization, _ = begin(client)

    def post(self, url, **kwargs):
        if failure == "transport":
            raise httpx.ConnectError("unreachable")
        if failure == "malformed":
            return httpx.Response(200, text="invalid JSON", request=httpx.Request("POST", url))
        return httpx.Response(
            302 if failure == "redirect" else 500,
            headers={"location": "https://evil.example"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.Client, "post", post)
    response = complete(client, authorization)
    assert response.status_code == 401
    assert settings.auth_cookie_name not in client.cookies
    assert "sim_oidc_state" not in client.cookies


def test_return_path_that_expands_past_cookie_limit_is_rejected(world, monkeypatch):
    client, _settings = client_for(world, monkeypatch)
    response = client.get(
        "/api/v1/auth/login",
        params={"return_to": "/app-next/?q=" + "界" * 1000},
        follow_redirects=False,
    )
    assert response.status_code == 422
    assert "sim_oidc_state" not in client.cookies


def test_callback_failure_hides_referrer_but_normal_pages_keep_default(world, monkeypatch):
    client, _settings = client_for(world, monkeypatch)
    response = client.get("/api/v1/auth/callback", params={"code": "test-code"})
    assert response.status_code == 401
    assert response.headers["referrer-policy"] == "no-referrer"
    assert client.get("/whoami").headers["referrer-policy"] == "strict-origin-when-cross-origin"
