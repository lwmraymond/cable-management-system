# OIDC authentication and Principal mapping

## Implemented request path

```text
Browser / service
  -> Authorization: Bearer <JWT> or configured same-site auth cookie
  -> PyJWT verification against configured/discovered JWKS
  -> algorithm + kid + signature + issuer + audience + expiry + required claims
  -> UserIdentity.oidc_subject mapping
  -> optional verified-email linking (off by default)
  -> token tenant claim consistency check
  -> existing TenantMembership / AccessGrant Principal resolution
  -> permission + project + location authorization
```

JWT claims identify a provisioned user. They do not replace application authorization. The same
`resolve_principal` service still evaluates tenant membership or explicit scoped contractor grants.

## Auth modes

- `demo`: development-only `X-Actor-ID`; an OIDC token is rejected.
- `oidc`: bearer/cookie JWT is mandatory; demo headers are not credentials.
- `hybrid`: prefer OIDC and allow `X-Actor-ID` only when no token exists and `DEMO_MODE=true`.

Production settings reject anything except `AUTH_MODE=oidc` and `DEMO_MODE=false`.

## Verified token properties

The API validates signing algorithm, `kid`, JWKS key, signature, issuer, audience, expiry, clock
skew, required claims and optional `tenant_id` consistency. Authentication failures return 401
with `WWW-Authenticate: Bearer`; a tenant-claim mismatch is a 403 authorization failure.

## Key loading

Keys may come from `OIDC_JWKS_JSON`, a configured `OIDC_JWKS_URL`, or issuer discovery. Discovery
redirects are rejected and the discovery issuer must exactly equal the configured issuer. Cache
keys include issuer, JWKS URL and inline JWKS content so test or tenant configurations cannot
reuse another key set accidentally.

## Pre-authentication request limit

The HTTP security middleware applies one ingress request budget per client address before
verifying identity. `X-Tenant-ID`, `X-Actor-ID`, bearer values and source ports do not create
separate budgets; changing these unverified values cannot reset the quota. The existing
in-memory or shared database limiter uses this same address-based key. Database keys remain
HMAC-pseudonymized before persistence.

The address is the connection peer unless that peer is explicitly listed in
`TRUSTED_PROXY_IPS`. For an allowed proxy, the first `X-Forwarded-For` address is used;
without that header the peer remains the source. The trusted proxy must replace untrusted
incoming forwarding headers. Forwarded headers from other peers are ignored.

Users behind the same NAT or outbound proxy share this ingress budget, including users in
different workspaces. Size `RATE_LIMIT_REQUESTS` and `RATE_LIMIT_WINDOW_SECONDS` for that
shared traffic. Exempt paths, OPTIONS handling, retry headers and fail-open/fail-closed
behavior are unchanged. An additional quota based on a verified account is future work;
this ingress limit does not claim to be a per-user or per-tenant quota.

## Browser entry

The account/workspace UI and server-side OIDC browser entry are implemented; see [Account workspaces and SSO](ACCOUNT_WORKSPACES_AND_SSO.md) for setup, contracts and remaining live-provider validation.


New browser SSO uses server-side Authorization Code + PKCE, state/nonce validation and HttpOnly JWT cookies. The frontend bootstraps its verified account and allowed workspaces through `/api/v1/auth/session`, attaching CSRF headers for cookie mutations. Existing explicit sessionStorage bearer tokens remain compatible with API calls; browser-decoded claims are never an authorization decision. `/app-next/auth/entry` preserves a safe target page. `/app/` remains the legacy UI.

## Remaining identity work

- live Keycloak login, refresh, expiry, logout and MFA proof
- identity provisioning and subject-linking administration UI
- external IdP federation, SAML and SCIM
- browser E2E and session-expiry UX
