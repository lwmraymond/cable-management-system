import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, createApiClient } from "../api/client";
import { loadBrowserSession } from "./browserSession";
import { SESSION_CHANNEL, SESSION_SIGNAL_KEY, useWorkspaceSession } from "./useWorkspaceSession";
import type { BrowserSession } from "../workspaces/types";

vi.mock("./browserSession", async importOriginal => ({ ...await importOriginal<typeof import("./browserSession")>(), loadBrowserSession: vi.fn() }));
const session: BrowserSession = {
  user: { id: "alice", display_name: "Alice", email: "alice@example.test" }, auth_method: "cookie",
  workspaces: [{ id: "personal", name: "Personal", kind: "personal", role: "Owner", can_manage: true }, { id: "team", name: "Team", kind: "shared", role: "Viewer", can_manage: false }],
  csrf_cookie_name: "sim_csrf", csrf_header_name: "X-CSRF-Token", can_create_workspaces: true,
};
const config = { auth_mode: "oidc" as const, demo_mode: false, cookie_auth_enabled: true, sso_enabled: true, csrf_cookie_name: "sim_csrf", csrf_header_name: "X-CSRF-Token" };
class Channel {
  static instances: Channel[] = [];
  onmessage: ((event: MessageEvent) => void) | null = null;
  postMessage = vi.fn(); close = vi.fn();
  constructor(readonly name: string) { Channel.instances.push(this); }
}
beforeEach(() => {
  localStorage.clear(); sessionStorage.clear(); vi.mocked(loadBrowserSession).mockReset();
  Channel.instances = []; vi.stubGlobal("BroadcastChannel", Channel);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
function changed(nonce: string) {
  window.dispatchEvent(new StorageEvent("storage", { key: SESSION_SIGNAL_KEY, newValue: JSON.stringify({ type: "session-changed", nonce }) }));
}
async function ready(workspaceId?: string) {
  vi.mocked(loadBrowserSession).mockResolvedValue({ session, config });
  const hook = renderHook(() => useWorkspaceSession(workspaceId));
  await waitFor(() => expect(hook.result.current.loading).toBe(false));
  return hook;
}

describe("account session gate", () => {
  it("keeps private pages unbound until authentication returns and enforces the authenticated actor on updates", async () => {
    let resolve!: (value: { session: BrowserSession; config: typeof config }) => void;
    vi.mocked(loadBrowserSession).mockReturnValue(new Promise(done => { resolve = done; }));
    const { result } = renderHook(() => useWorkspaceSession());
    expect(result.current.loading).toBe(true);
    expect(result.current.session).toBeUndefined();
    expect(result.current.context.tenantId).toBe("");
    await act(async () => { resolve({ session, config }); });
    expect(result.current.context).toMatchObject({ tenantId: "personal", actorId: "alice" });
    act(() => result.current.updateContext({ tenantId: "personal", actorId: "bob", locationId: "room" }));
    expect(result.current.context).toMatchObject({ actorId: "alice", locationId: "room" });
    act(() => result.current.updateContext({ tenantId: "forbidden", actorId: "bob" }));
    expect(result.current.context.tenantId).toBe("personal");
  });
  it("clears stale scope on explicit switching and drops a revoked workspace after refresh", async () => {
    vi.mocked(loadBrowserSession).mockResolvedValue({ session, config });
    const { result } = renderHook(() => useWorkspaceSession());
    await waitFor(() => expect(result.current.loading).toBe(false));
    act(() => result.current.updateContext({ tenantId: "personal", locationId: "private-room", projectId: "p" }));
    act(() => result.current.selectWorkspace(session.workspaces[1]));
    expect(result.current.context).toMatchObject({ tenantId: "team", locationId: undefined, projectId: undefined });
    vi.mocked(loadBrowserSession).mockResolvedValue({ session: { ...session, workspaces: [session.workspaces[0]] }, config });
    await act(async () => { await result.current.refresh(); });
    expect(result.current.context.tenantId).toBe("personal");
  });
  it("clears authenticated page state on expiry and ignores a late successful refresh", async () => {
    vi.mocked(loadBrowserSession).mockResolvedValue({ session, config });
    const { result } = renderHook(() => useWorkspaceSession());
    await waitFor(() => expect(result.current.loading).toBe(false));
    let resolve!: (value: { session: BrowserSession; config: typeof config }) => void;
    vi.mocked(loadBrowserSession).mockReturnValue(new Promise(done => { resolve = done; }));
    let refresh!: Promise<void>;
    act(() => { refresh = result.current.refresh(); });
    act(() => window.dispatchEvent(new Event("sim:authentication-required")));
    await act(async () => { resolve({ session, config }); await refresh; });
    expect(result.current.session).toBeUndefined();
    expect(result.current.context.tenantId).toBe("");
    expect(result.current.error).toMatchObject({ status: 401 });
  });
  it("keeps unauthorized explicit workspace navigation outside protected pages", async () => {
    vi.mocked(loadBrowserSession).mockResolvedValue({ session, config });
    const { result } = renderHook(() => useWorkspaceSession("other-account-private"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.session?.user.id).toBe("alice");
    expect(result.current.context.tenantId).toBe("");
    expect(result.current.accessDenied).toBe(true);
    expect(result.current.error).toBeInstanceOf(ApiError);
    expect(result.current.error).toMatchObject({ status: 403 });
  });
});


describe("session revalidation and isolation", () => {
  it("revalidates on focus and remounts content for a different account without restoring the prior private scope", async () => {
    const { result } = await ready();
    act(() => result.current.updateContext({ tenantId: "personal", locationId: "alice-secret" }));
    const oldKey = result.current.contentKey;
    const bob = { ...session, user: { id: "bob", display_name: "Bob", email: "bob@example.test" }, workspaces: [session.workspaces[1]] };
    vi.mocked(loadBrowserSession).mockResolvedValue({ session: bob, config });
    act(() => window.dispatchEvent(new Event("focus")));
    await waitFor(() => expect(result.current.session?.user.id).toBe("bob"));
    expect(result.current.context).toEqual({ tenantId: "team", actorId: "bob", projectId: undefined, locationId: undefined });
    expect(result.current.contentKey).not.toBe(oldKey);
  });
  it("remains usable when both preference writes and cross-tab transports are unavailable", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("Storage blocked", "SecurityError"); });
    vi.stubGlobal("BroadcastChannel", class { constructor() { throw new Error("Channel blocked"); } });
    const { result } = await ready();
    expect(result.current.session?.user.id).toBe("alice"); expect(result.current.context.tenantId).toBe("personal");
    await act(async () => { await result.current.refresh(); });
    expect(result.current.error).toBeUndefined();
  });
  it("retains a verified account and its content key on transient revalidation failure", async () => {
    const { result } = await ready(); const oldKey = result.current.contentKey;
    vi.mocked(loadBrowserSession).mockRejectedValue(new ApiError("Session service unavailable", 503));
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    await waitFor(() => expect(result.current.error?.message).toBe("Session service unavailable"));
    expect(result.current.session?.user.id).toBe("alice"); expect(result.current.context.tenantId).toBe("personal");
    expect(result.current.loading).toBe(false); expect(result.current.contentKey).toBe(oldKey);
  });
  it.each([401, 503])("handles a malformed real session response (%s) as authentication expiry only for 401", async status => {
    const { result } = await ready(); const oldKey = result.current.contentKey;
    const actual = await vi.importActual<typeof import("./browserSession")>("./browserSession");
    vi.mocked(loadBrowserSession).mockImplementation(actual.loadBrowserSession);
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(config), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response("{broken", { status, headers: { "Content-Type": "application/json" } })));
    act(() => window.dispatchEvent(new Event("focus")));
    await waitFor(() => expect(result.current.error).toMatchObject({ status }));
    if (status === 401) {
      expect(result.current.session).toBeUndefined(); expect(result.current.context.tenantId).toBe(""); expect(result.current.contentKey).not.toBe(oldKey);
    } else {
      expect(result.current.session?.user.id).toBe("alice"); expect(result.current.context.tenantId).toBe("personal"); expect(result.current.contentKey).toBe(oldKey);
    }
  });
  it("coalesces ordinary permission failures without logging out or clearing unchanged content", async () => {
    const { result } = await ready(); const oldKey = result.current.contentKey;
    let complete!: (value: { session: BrowserSession; config: typeof config }) => void;
    vi.mocked(loadBrowserSession).mockReturnValue(new Promise(resolve => { complete = resolve; }));
    act(() => {
      for (let i = 0; i < 5; i++) window.dispatchEvent(new CustomEvent("sim:access-recheck", { detail: { generation: oldKey } }));
    });
    expect(loadBrowserSession).toHaveBeenCalledTimes(2);
    await act(async () => complete({ session, config }));
    expect(result.current.session?.user.id).toBe("alice"); expect(result.current.contentKey).toBe(oldKey);
    expect(result.current.error).toBeUndefined();
  });
  it("drops an old 403 after an explicit workspace switch instead of rechecking or expiring the new context", async () => {
    const { result } = await ready(); const oldContext = result.current.context; const oldKey = result.current.contentKey;
    let complete!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(resolve => { complete = resolve; })));
    const pending = createApiClient({ getContext: () => oldContext }).request("/racks");
    act(() => result.current.selectWorkspace(session.workspaces[1]));
    complete(new Response(JSON.stringify({ detail: "Revoked old workspace" }), { status: 403, headers: { "Content-Type": "application/json" } }));
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    act(() => window.dispatchEvent(new CustomEvent("sim:access-recheck", { detail: { generation: oldKey } })));
    expect(loadBrowserSession).toHaveBeenCalledOnce(); expect(result.current.context.tenantId).toBe("team");
  });
  it("invalidates cached content and saved scope when granted permissions or scope boundaries change", async () => {
    const { result } = await ready();
    act(() => result.current.updateContext({ tenantId: "personal", projectId: "old-project", locationId: "old-room" }));
    const oldKey = result.current.contentKey;
    const limited: BrowserSession = { ...session, workspaces: [{ ...session.workspaces[0], role: "Scoped Contractor", can_manage: false, permissions: ["location:read"], project_id: "allowed-project", location_id: "allowed-room", scopes: [{ project_id: "allowed-project", location_id: "allowed-room" }] }] };
    vi.mocked(loadBrowserSession).mockResolvedValue({ session: limited, config });
    await act(async () => { await result.current.refresh(); });
    expect(result.current.context).toEqual({ tenantId: "personal", actorId: "alice", projectId: "allowed-project", locationId: "allowed-room" });
    expect(result.current.contentKey).not.toBe(oldKey);
  });
  it("discards loaded content and late responses when only a secondary scope access revision changes", async () => {
    const scoped: BrowserSession = { ...session, workspaces: [{ ...session.workspaces[1],
      role: "Scoped Contractor", permissions: ["location:read"], access_revision: "revision-before",
      project_id: "p", location_id: "first-room", scopes: [
        { project_id: "p", location_id: "first-room" }, { project_id: "p", location_id: "second-room" },
      ],
    }] };
    vi.mocked(loadBrowserSession).mockResolvedValue({ session: scoped, config });
    const { result } = renderHook(() => useWorkspaceSession());
    await waitFor(() => expect(result.current.loading).toBe(false));
    act(() => result.current.updateContext({ tenantId: "team", projectId: "p", locationId: "second-room" }));
    const oldKey = result.current.contentKey;
    const oldContext = result.current.context;
    // Ordinary revalidation keeps the selected scope and mounted content.
    await act(async () => { await result.current.refresh(); });
    expect(result.current.contentKey).toBe(oldKey);
    expect(result.current.context).toEqual(oldContext);
    let complete!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(resolve => { complete = resolve; })));
    const pending = createApiClient({ getContext: () => oldContext }).request("/cables");
    const changed = { ...scoped, workspaces: [{ ...scoped.workspaces[0], access_revision: "revision-after" }] };
    vi.mocked(loadBrowserSession).mockResolvedValue({ session: changed, config });
    await act(async () => { await result.current.refresh(); });
    expect(result.current.contentKey).not.toBe(oldKey);
    expect(result.current.context).toMatchObject({ tenantId: "team", projectId: "p", locationId: "first-room" });
    complete(new Response(JSON.stringify([{ id: "previously-visible-cable" }]), { status: 200, headers: { "Content-Type": "application/json" } }));
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });
  it("keeps revoked explicit workspace links blocked instead of choosing another accessible workspace", async () => {
    const { result } = await ready("team");
    vi.mocked(loadBrowserSession).mockResolvedValue({ session: { ...session, workspaces: [session.workspaces[0]] }, config });
    await act(async () => { await result.current.refresh(); });
    expect(result.current.accessDenied).toBe(true); expect(result.current.context.tenantId).toBe("");
    expect(result.current.session?.workspaces).toEqual([session.workspaces[0]]);
  });
  it("lets a cross-tab identity check supersede a slow refresh and ignores its late prior-account success", async () => {
    const { result } = await ready();
    let complete!: (value: { session: BrowserSession; config: typeof config }) => void;
    vi.mocked(loadBrowserSession).mockReturnValueOnce(new Promise(resolve => { complete = resolve; }));
    let pending!: Promise<void>;
    act(() => { pending = result.current.refresh(); });
    const bob = { ...session, user: { id: "bob", display_name: "Bob", email: "bob@example.test" }, workspaces: [session.workspaces[1]] };
    vi.mocked(loadBrowserSession).mockResolvedValue({ session: bob, config });
    act(() => changed("account-changed"));
    await waitFor(() => expect(result.current.session?.user.id).toBe("bob"));
    await act(async () => { complete({ session, config }); await pending; });
    expect(result.current.session?.user.id).toBe("bob"); expect(result.current.context.tenantId).toBe("team");
  });
  it("deduplicates storage/BroadcastChannel hints, sends only opaque signals, and closes the channel", async () => {
    const { unmount } = await ready();
    const transport = Channel.instances[0];
    expect(transport.name).toBe(SESSION_CHANNEL);
    expect(transport.postMessage.mock.calls[0][0]).toEqual({ type: "session-changed", nonce: expect.any(String) });
    expect(JSON.parse(localStorage.getItem(SESSION_SIGNAL_KEY)!)).toEqual({ type: "session-changed", nonce: expect.any(String) });
    await act(async () => {
      transport.onmessage?.(new MessageEvent("message", { data: { type: "session-changed", nonce: "same-hint" } }));
      changed("same-hint");
    });
    expect(loadBrowserSession).toHaveBeenCalledTimes(2);
    expect(transport.postMessage).toHaveBeenCalledOnce(); // No echo loop after receiving a hint.
    unmount(); expect(transport.close).toHaveBeenCalledOnce();
  });
  it("closes private content immediately on cross-tab logout and prevents focus or stale work from restoring it", async () => {
    const { result } = await ready(); const oldKey = result.current.contentKey;
    sessionStorage.setItem("sim.oidc.access-token", "old.token.value");
    let complete!: (value: { session: BrowserSession; config: typeof config }) => void;
    vi.mocked(loadBrowserSession).mockReturnValueOnce(new Promise(resolve => { complete = resolve; }));
    let pending!: Promise<void>; act(() => { pending = result.current.refresh(); });
    act(() => Channel.instances[0].onmessage?.(new MessageEvent("message", { data: { type: "logout", nonce: "logout-hint" } })));
    expect(result.current.session).toBeUndefined(); expect(result.current.context.tenantId).toBe(""); expect(result.current.contentKey).not.toBe(oldKey);
    expect(sessionStorage.getItem("sim.oidc.access-token")).toBeNull();
    await act(async () => { complete({ session, config }); await pending; window.dispatchEvent(new Event("focus")); });
    expect(result.current.session).toBeUndefined(); expect(loadBrowserSession).toHaveBeenCalledTimes(2);
  });
  it("can close access and submit logout when token storage is unreadable", async () => {
    const { result } = await ready();
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new DOMException("Storage blocked", "SecurityError"); });
    const fetcher = vi.fn().mockRejectedValue(new Error("Logout network error")); vi.stubGlobal("fetch", fetcher);
    await act(async () => { await result.current.signOut(); });
    expect(result.current.session).toBeUndefined(); expect(result.current.context.tenantId).toBe("");
    expect(fetcher).toHaveBeenCalledOnce(); expect(fetcher.mock.calls[0][0]).toBe("/api/v1/auth/logout");
  });
  it("closes local access even when logout fails and never silently retries logout or restores content on focus", async () => {
    const { result } = await ready();
    sessionStorage.setItem("sim.oidc.access-token", "old.token.value");
    const fetcher = vi.fn().mockRejectedValue(new Error("Network unavailable")); vi.stubGlobal("fetch", fetcher);
    await act(async () => { await result.current.signOut(); });
    expect(result.current.session).toBeUndefined(); expect(result.current.context.tenantId).toBe("");
    expect(result.current.error?.message).toContain("服务器退出尚未确认");
    expect(sessionStorage.getItem("sim.oidc.access-token")).toBeNull();
    act(() => window.dispatchEvent(new Event("focus")));
    expect(loadBrowserSession).toHaveBeenCalledOnce(); expect(fetcher).toHaveBeenCalledOnce();
    expect(Channel.instances[0].postMessage.mock.calls.at(-1)?.[0]).toEqual({ type: "logout", nonce: expect.any(String) });
  });
});
