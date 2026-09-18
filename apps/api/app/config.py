from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    app_name: str = "Structured Infrastructure Manager"
    api_prefix: str = "/api/v1"
    database_url: str = "sqlite+pysqlite:///./sim.db"
    platform_database_url: str | None = None
    migration_database_url: str | None = None

    cors_origins: list[str] | str = ["http://localhost:8000"]
    cors_methods: list[str] | str = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    cors_headers: list[str] | str = [
        "Authorization",
        "Content-Type",
        "X-Request-ID",
        "X-Tenant-ID",
        "X-Actor-ID",
        "X-Project-ID",
        "X-Location-ID",
        "X-CSRF-Token",
    ]
    cors_expose_headers: list[str] | str = [
        "Content-Disposition",
        "X-Request-ID",
        "X-RateLimit-Limit",
        "X-RateLimit-Remaining",
        "X-RateLimit-Reset",
        "X-Result-Count",
        "X-Result-Total",
        "X-Result-Truncated",
    ]
    cors_allow_credentials: bool = False
    allowed_hosts: list[str] | str = ["localhost", "127.0.0.1", "testserver"]

    platform_bootstrap_key: str = "replace-this-secret"
    demo_mode: bool = True
    auth_mode: Literal["demo", "oidc", "hybrid"] = "demo"

    oidc_issuer_url: str = "http://keycloak:8080/realms/sim"
    oidc_audience: str = "sim-api"
    oidc_jwks_url: str | None = None
    oidc_jwks_json: str | None = None
    oidc_algorithms: list[str] | str = ["RS256"]
    oidc_clock_skew_seconds: int = 30
    oidc_jwks_cache_seconds: int = 300
    oidc_http_timeout_seconds: float = 5.0
    oidc_tenant_claim: str = "tenant_id"
    oidc_allow_verified_email_linking: bool = False

    # Browser SSO is server-side and opt-in; no issuer credentials reach the SPA.
    sso_enabled: bool = False
    sso_client_id: str = ""
    sso_client_secret: str | None = None
    sso_redirect_uri: str = ""
    sso_authorization_url: str | None = None
    sso_token_url: str | None = None
    sso_state_secret: str = ""
    sso_state_ttl_seconds: int = 300

    rate_limit_enabled: bool = True
    rate_limit_backend: Literal["memory", "database"] = "memory"
    rate_limit_requests: int = 240
    rate_limit_window_seconds: int = 60
    rate_limit_exempt_paths: list[str] | str = ["/api/v1/health", "/api/v1/ready"]
    rate_limit_database_url: str | None = None
    rate_limit_key_secret: str = "replace-rate-limit-secret"
    rate_limit_retention_seconds: int = 3600
    rate_limit_cleanup_interval_seconds: int = 60
    rate_limit_fail_mode: Literal["open", "closed"] = "closed"

    require_https: bool = False
    trusted_proxy_ips: list[str] | str = []
    hsts_max_age: int = 31536000
    hsts_include_subdomains: bool = True
    hsts_preload: bool = False

    cookie_auth_enabled: bool = False
    auth_cookie_name: str = "sim_access_token"
    cookie_secure: bool = True
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    csrf_cookie_name: str = "sim_csrf"
    csrf_header_name: str = "X-CSRF-Token"

    object_storage_backend: str = "local"
    object_storage_path: Path = Path("./storage")

    @field_validator(
        "cors_origins",
        "cors_methods",
        "cors_headers",
        "cors_expose_headers",
        "allowed_hosts",
        "oidc_algorithms",
        "rate_limit_exempt_paths",
        "trusted_proxy_ips",
        mode="before",
    )
    @classmethod
    def parse_csv_lists(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @model_validator(mode="after")
    def validate_security_configuration(self) -> "Settings":
        if self.rate_limit_requests < 1 or self.rate_limit_window_seconds < 1:
            raise ValueError("Rate-limit count and window must both be positive")
        if self.rate_limit_retention_seconds < self.rate_limit_window_seconds:
            raise ValueError("Rate-limit retention must cover at least one complete window")
        if self.rate_limit_cleanup_interval_seconds < 1:
            raise ValueError("Rate-limit cleanup interval must be positive")
        if self.oidc_clock_skew_seconds < 0 or self.oidc_jwks_cache_seconds < 0:
            raise ValueError("OIDC clock skew and JWKS cache duration cannot be negative")
        if self.cors_allow_credentials and "*" in self.cors_origins:
            raise ValueError("Credentialed CORS cannot use a wildcard origin")
        if self.cookie_auth_enabled and not self.cors_allow_credentials:
            raise ValueError("Cookie authentication requires credentialed CORS")
        if self.cookie_samesite == "none" and not self.cookie_secure:
            raise ValueError("SameSite=None cookies must be Secure")

        if self.sso_enabled:
            if self.auth_mode == "demo" or not self.cookie_auth_enabled:
                raise ValueError("Browser SSO requires OIDC/hybrid and cookie authentication")
            if not self.sso_client_id.strip() or len(self.sso_state_secret) < 32:
                raise ValueError(
                    "Browser SSO requires a client ID and a state secret of 32+ characters"
                )
            if not 60 <= self.sso_state_ttl_seconds <= 900:
                raise ValueError("Browser SSO state lifetime must be between 60 and 900 seconds")
            for value in (
                self.oidc_issuer_url,
                self.sso_redirect_uri,
                self.sso_authorization_url,
                self.sso_token_url,
            ):
                if value:
                    self.validate_sso_url(value)
            callback = urlparse(self.sso_redirect_uri)
            if (
                not self.sso_redirect_uri
                or callback.query
                or callback.fragment
                or callback.path != f"{self.api_prefix}/auth/callback"
            ):
                raise ValueError("SSO redirect URI must be the absolute API /auth/callback URL")
            if callback.scheme == "https" and not self.cookie_secure:
                raise ValueError("HTTPS browser SSO requires Secure cookies")
            if any(
                algorithm.startswith("HS") or algorithm == "none"
                for algorithm in self.oidc_algorithms
            ):
                raise ValueError("Browser SSO requires asymmetric OIDC signing algorithms")

        if self.app_env.lower() == "production":
            if self.auth_mode != "oidc" or self.demo_mode:
                raise ValueError("Production requires AUTH_MODE=oidc and DEMO_MODE=false")
            if not self.require_https:
                raise ValueError("Production requires HTTPS enforcement")
            if self.platform_bootstrap_key == "replace-this-secret":
                raise ValueError("Production bootstrap key must be replaced")
            if self.rate_limit_backend != "database":
                raise ValueError("Production requires the shared database rate-limit backend")
            if self.rate_limit_key_secret == "replace-rate-limit-secret":
                raise ValueError("Production rate-limit key secret must be replaced")
            if "*" in self.allowed_hosts:
                raise ValueError("Production allowed hosts cannot contain a wildcard")
            if urlparse(self.oidc_issuer_url).scheme != "https":
                raise ValueError("Production OIDC issuer must use HTTPS")
            if self.oidc_jwks_url and urlparse(self.oidc_jwks_url).scheme != "https":
                raise ValueError("Production OIDC JWKS URL must use HTTPS")
            insecure_origins = [
                origin for origin in self.cors_origins if not origin.startswith("https://")
            ]
            if insecure_origins:
                raise ValueError("Production CORS origins must use HTTPS")
        return self

    def validate_sso_url(self, value: str) -> None:
        parsed = urlparse(value)
        local_http = (
            self.app_env.lower() != "production"
            and parsed.scheme == "http"
            and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        )
        if (
            not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
            or (parsed.scheme != "https" and not local_http)
            or any(ord(char) < 33 for char in value)
        ):
            raise ValueError(
                "Browser SSO URLs require HTTPS (HTTP is allowed only on local loopback)"
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
