import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadBrowserSession, safeReturnTo, saveAccountContext, selectInitialContext, workspaceContext } from "./browserSession";
import { configureCookieCsrf, createApiClient } from "../api/client";
import type { BrowserSession } from "../workspaces/types";

const account: BrowserSession = {
  user: { id: "alice", display_name: "Alice", email: "alice@example.test" }, auth_method: "cookie",
  workspaces: [{ id: "private", name: "私人", kind: "personal", role: "Owner", can_manage: true }, { id: "team", name: "团队", kind: "shared", role: "Viewer", can_manage: false }],
  csrf_cookie_name: "sim_csrf", csrf_header_name: "X-CSRF-Token", can_create_workspaces: true,
};
const config = { auth_mode: "oidc", demo_mode: false, cookie_auth_enabled: true, sso_enabled: true, csrf_cookie_name: "sim_csrf", csrf_header_name: "X-CSRF-Token" };
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });

beforeEach(() => { localStorage.clear(); sessionStorage.clear(); });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); document.cookie = "custom_csrf=; Max-Age=0; Path=/"; configureCookieCsrf(config); });

describe("account scope and browser entry", () => {
  it("uses only authorized default scope when saved preferences are unavailable", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new DOMException("Storage unavailable", "SecurityError"); });
    expect(selectInitialContext(account)).toEqual({ tenantId: "private", actorId: "alice", projectId: undefined, locationId: undefined });
  });
  it("never restores another account's private scope", () => {
    saveAccountContext(account, { tenantId: "private", actorId: "alice", locationId: "secret-room" });
    const other = { ...account, user: { ...account.user, id: "bob" }, workspaces: [account.workspaces[1]] };
    expect(selectInitialContext(other)).toEqual({ tenantId: "team", actorId: "bob", locationId: undefined, projectId: undefined });
    expect(selectInitialContext(account).locationId).toBe("secret-room");
  });
  it("drops a revoked saved workspace and rejects unauthorized explicit deep links", () => {
    saveAccountContext(account, { tenantId: "removed", actorId: "alice", locationId: "secret-room" });
    expect(selectInitialContext(account).tenantId).toBe("private");
    expect(selectInitialContext(account).locationId).toBeUndefined();
    expect(() => selectInitialContext(account, "removed")).toThrow("没有这个工作空间");
  });
  it("clears previous project and room when selecting another workspace", () => {
    expect(workspaceContext(account, account.workspaces[1], { tenantId: "private", actorId: "alice", locationId: "room", projectId: "project" })).toEqual({ tenantId: "team", actorId: "alice", locationId: undefined, projectId: undefined });
  });
  it("restores explicit contractor boundaries rather than an unscoped tenant", () => {
    const scoped = { ...account.workspaces[1], project_id: "project", location_id: "room" };
    expect(workspaceContext(account, scoped)).toMatchObject({ projectId: "project", locationId: "room" });
  });
  it.each(["https://evil.example/path", "//evil.example", "/app-next/../evil", "/app-nextish", "/app-next/\\evil", "/app-next/%5cevil", "/app-next/login", "/app-next/auth/entry", "/app-next/oidc/callback", "/app-next/%00", "/app-next/3d#token", "/app-next/3d?access_token=secret", "/app-next/3d?Session=secret", "/app-next/./3d"])("rejects unsafe or looping return path %s", path => {
    expect(safeReturnTo(path)).toBe("/app-next/");
  });
  it("retains a safe room and workspace deep link", () => {
    expect(safeReturnTo("/app-next/3d?room=room&workspace=team")).toBe("/app-next/3d?room=room&workspace=team");
  });
  it("bootstraps a valid cookie account without caller-selected tenant or demo identity", async () => {
    localStorage.setItem("sim.infrastructure-context", JSON.stringify({ actorId: "other", tenantId: "other" }));
    const fetcher = vi.fn().mockResolvedValueOnce(json(config)).mockResolvedValueOnce(json(account));
    vi.stubGlobal("fetch", fetcher);
    expect(await loadBrowserSession()).toEqual({ config, session: account });
    expect(fetcher).toHaveBeenCalledTimes(2);
    const options = fetcher.mock.calls[1][1];
    expect(options.credentials).toBe("same-origin");
    expect(options.headers.has("X-Actor-ID")).toBe(false);
    expect(options.headers.has("X-Tenant-ID")).toBe(false);
  });
  it.each([401, 403, 503])("preserves session rejection status %s and auth config even when the body is not JSON", async status => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(json(config)).mockResolvedValueOnce(new Response("<html>upstream error</html>", { status, headers: { "Content-Type": "text/html" } })));
    await expect(loadBrowserSession()).rejects.toMatchObject({ status, authConfig: config });
  });
  it("does not downgrade a rejected bearer to demo mode", async () => {
    sessionStorage.setItem("sim.oidc.access-token", "invalid.token.value");
    const fetcher = vi.fn().mockResolvedValueOnce(json({ ...config, auth_mode: "hybrid", demo_mode: true })).mockResolvedValueOnce(json({ detail: "Invalid token" }, 401));
    vi.stubGlobal("fetch", fetcher);
    await expect(loadBrowserSession()).rejects.toMatchObject({ status: 401, authConfig: { demo_mode: true } });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls[1][1].headers.get("Authorization")).toBe("Bearer invalid.token.value");
  });
});

describe("cookie-authenticated API mutations", () => {
  it("sends the configured CSRF cookie as a header only for cookie mutations", async () => {
    configureCookieCsrf({ csrf_cookie_name: "custom_csrf", csrf_header_name: "X-Custom-CSRF" });
    document.cookie = "custom_csrf=csrf-value; Path=/";
    const fetcher = vi.fn(() => Promise.resolve(json({ ok: true })));
    vi.stubGlobal("fetch", fetcher);
    const api = createApiClient({ getContext: () => ({ tenantId: "team" }) });
    await api.request("/workspaces", { method: "POST", body: "{}" });
    const post = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(new Headers(post[1].headers).get("X-Custom-CSRF")).toBe("csrf-value");
    expect(post[1].credentials).toBe("same-origin");
    await api.request("/workspaces");
    const get = fetcher.mock.calls[1] as unknown as [string, RequestInit];
    expect(new Headers(get[1].headers).has("X-Custom-CSRF")).toBe(false);
    sessionStorage.setItem("sim.oidc.access-token", "a.b.c");
    await api.request("/workspaces", { method: "POST" });
    const bearer = fetcher.mock.calls[2] as unknown as [string, RequestInit];
    expect(new Headers(bearer[1].headers).get("Authorization")).toBe("Bearer a.b.c");
    expect(new Headers(bearer[1].headers).has("X-Custom-CSRF")).toBe(false);
  });
  it("logs out with cookie CSRF even when a legacy bearer remains in session storage", async () => {
    configureCookieCsrf({ csrf_cookie_name: "custom_csrf", csrf_header_name: "X-Custom-CSRF" });
    document.cookie = "custom_csrf=csrf-value; Path=/";
    sessionStorage.setItem("sim.oidc.access-token", "legacy.token.value");
    const fetcher = vi.fn().mockResolvedValue(new Response(null, { status: 204 })); vi.stubGlobal("fetch", fetcher);
    await createApiClient({ getContext: () => ({ tenantId: "team" }) }).request("/auth/logout", { method: "POST", headers: { Authorization: "Bearer explicit-old-token" } });
    const headers = new Headers(fetcher.mock.calls[0][1].headers);
    expect(headers.has("Authorization")).toBe(false); expect(headers.get("X-Custom-CSRF")).toBe("csrf-value");
  });
  it("notifies the session gate on expired API and download authentication", async () => {
    const expired = vi.fn(); window.addEventListener("sim:authentication-required", expired);
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(json({ detail: "expired" }, 401))));
    const api = createApiClient({ getContext: () => ({ tenantId: "team" }) });
    await expect(api.request("/locations")).rejects.toMatchObject({ status: 401 });
    await expect(api.download("/export")).rejects.toMatchObject({ status: 401 });
    expect(expired).toHaveBeenCalledTimes(2);
    window.removeEventListener("sim:authentication-required", expired);
  });
});
