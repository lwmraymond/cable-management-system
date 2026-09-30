import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { contextHeaders, normalizeContext } from "./context";
import { hasUsableToken } from "../auth/tokenStore";
import { ApiError, createApiClient, setApiAccessBoundary } from "./client";

describe("client context helpers", () => {
  it("normalizes and emits scoped headers", () => {
    const context = normalizeContext({ tenantId: " tenant ", actorId: " actor ", projectId: "" });
    expect(contextHeaders(context)).toEqual({ "X-Tenant-ID": "tenant", "X-Actor-ID": "actor" });
  });
  it("recognizes JWT-shaped access tokens without trusting their claims", () => {
    expect(hasUsableToken("a.b.c")).toBe(true);
    expect(hasUsableToken("opaque")).toBe(false);
  });
});


const api = createApiClient({ getContext: () => ({ tenantId: "tenant", actorId: "actor" }) });
beforeEach(() => { setApiAccessBoundary({ tenantId: "tenant", actorId: "actor" }); });
afterEach(() => { vi.unstubAllGlobals(); });

function respond(body: string | null, status: number, contentType = "application/json") {
  const fetchMock = vi.fn().mockResolvedValue(new Response(body, { status, headers: { "Content-Type": contentType, "X-Request-ID": "request-123" } }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("API response recovery", () => {
  it("retains scoped headers while allowing the browser to supply a multipart boundary", async () => {
    const fetchMock = respond("{}", 201);
    const body = new FormData(); body.append("file", new File(["synthetic"], "scene.dxf"));
    await api.request("/scene/cad/imports", { method: "POST", body });
    const init = fetchMock.mock.calls[0][1];
    expect(init.body).toBe(body);
    expect(init.headers.get("X-Tenant-ID")).toBe("tenant");
    expect(init.headers.has("Content-Type")).toBe(false);
  });
  it("accepts empty successful deletes without trying to parse JSON", async () => {
    const fetchMock = respond(null, 204);
    await expect(api.request("/members/alice", { method: "DELETE" })).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledOnce();
  });
  it("presents validation paths and messages without echoing submitted input", async () => {
    respond(JSON.stringify({ detail: [{ loc: ["body", "name"], msg: "Field required", input: "private-value" }] }), 422);
    await expect(api.request("/workspaces")).rejects.toMatchObject({ status: 422, message: "name：Field required", requestId: "request-123" });
  });
  it("clears an expired login even when the upstream JSON error response is malformed", async () => {
    respond("{broken", 401);
    const listener = vi.fn();
    window.addEventListener("sim:authentication-required", listener);
    try {
      await expect(api.request("/cables")).rejects.toMatchObject({ status: 401, message: "登录已失效，请重新登录。" });
      expect(listener).toHaveBeenCalledOnce();
    } finally { window.removeEventListener("sim:authentication-required", listener); }
  });
  it("gives a recoverable proxy failure and request ID without displaying HTML", async () => {
    respond("<html>proxy internal stack</html>", 502, "text/html");
    await expect(api.request("/cables")).rejects.toMatchObject({ status: 502, message: "服务暂时不可用，请稍后重试。", requestId: "request-123" });
  });
  it("gives downloads the same permission error detail without expiring authentication", async () => {
    respond(JSON.stringify({ detail: "Report export is not allowed" }), 403, "application/problem+json");
    const expired = vi.fn(); window.addEventListener("sim:authentication-required", expired);
    try {
      await expect(api.download("/report.csv")).rejects.toMatchObject({ status: 403, message: "Report export is not allowed" });
      expect(expired).not.toHaveBeenCalled();
    } finally { window.removeEventListener("sim:authentication-required", expired); }
  });
  it.each([false, true])("expires once before reading a 401 body (read failure: %s), retaining the 401 outcome", async broken => {
    const response = new Response(JSON.stringify({ detail: "expired" }), { status: 401, headers: { "Content-Type": "application/json" } });
    if (broken) vi.spyOn(response, "text").mockRejectedValue(new TypeError("Body connection lost"));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
    const expired = vi.fn(() => setApiAccessBoundary(null));
    window.addEventListener("sim:authentication-required", expired);
    try {
      await expect(api.request("/locations")).rejects.toMatchObject({ status: 401 });
      expect(expired).toHaveBeenCalledOnce();
    } finally { window.removeEventListener("sim:authentication-required", expired); }
  });
  it("reports invalid success JSON without automatically retrying a write", async () => {
    const fetchMock = respond("{broken", 200);
    await expect(api.request("/cables", { method: "POST", body: "{}" })).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledOnce();
  });
  it.each([false, 0, null])("preserves valid JSON result %s", async value => {
    respond(JSON.stringify(value), 200);
    await expect(api.request("/value")).resolves.toBe(value);
  });
  it("preserves cancellation instead of turning it into a user-facing server error", async () => {
    const error = new DOMException("Request cancelled", "AbortError");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(error));
    await expect(api.request("/cables", { signal: new AbortController().signal })).rejects.toBe(error);
  });
});


describe("account-bound API responses", () => {
  it.each([200, 401, 403])("discards a prior workspace response (%s) without notifying or retrying a write", async status => {
    let complete!: (response: Response) => void;
    const fetcher = vi.fn(() => new Promise<Response>(resolve => { complete = resolve; }));
    vi.stubGlobal("fetch", fetcher);
    const expired = vi.fn(); const denied = vi.fn();
    window.addEventListener("sim:authentication-required", expired); window.addEventListener("sim:access-recheck", denied);
    try {
      const pending = api.request("/cables", { method: "POST", body: JSON.stringify({ identifier: "private-label" }) });
      setApiAccessBoundary({ tenantId: "other", actorId: "other-user" });
      complete(new Response(JSON.stringify({ detail: "Old response" }), { status, headers: { "Content-Type": "application/json" } }));
      await expect(pending).rejects.toMatchObject({ name: "AbortError" });
      expect(fetcher).toHaveBeenCalledOnce(); expect(expired).not.toHaveBeenCalled(); expect(denied).not.toHaveBeenCalled();
    } finally { window.removeEventListener("sim:authentication-required", expired); window.removeEventListener("sim:access-recheck", denied); }
  });
  it("emits only an opaque generation for a current permission rejection", async () => {
    const generation = setApiAccessBoundary({ tenantId: "tenant", actorId: "actor" }, "permissions-v2");
    respond(JSON.stringify({ detail: "No export permission" }), 403);
    const denied = vi.fn(); window.addEventListener("sim:access-recheck", denied);
    try {
      await expect(api.request("/reports/private-account-data")).rejects.toMatchObject({ status: 403 });
      expect(denied).toHaveBeenCalledOnce(); expect(denied.mock.calls[0][0].detail).toEqual({ generation });
    } finally { window.removeEventListener("sim:access-recheck", denied); }
  });
  it("blocks a retained old client before it can send requests under a new account", async () => {
    const fetcher = respond("{}", 200);
    setApiAccessBoundary({ tenantId: "new-tenant", actorId: "new-user" });
    await expect(api.request("/racks")).rejects.toMatchObject({ name: "AbortError" });
    await expect(api.download("/reports/cable-schedule.csv")).rejects.toMatchObject({ name: "AbortError" });
    expect(fetcher).not.toHaveBeenCalled();
  });
});
