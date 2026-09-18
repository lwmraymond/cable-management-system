import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, apiContextKey, createApiClient, setApiAccessBoundary, type ApiAccessEvent } from "../api/client";
import type { InfrastructureContext } from "../api/context";
import type { BrowserSession, Workspace } from "../workspaces/types";
import { clearAccessToken } from "./tokenStore";
import { loadBrowserSession, saveAccountContext, selectInitialContext, workspaceContext, type BrowserAuthConfig } from "./browserSession";

export const SESSION_SIGNAL_KEY = "sim.session-signal";
export const SESSION_CHANNEL = "sim.session-events";
type SessionSignal = { type: "session-changed" | "logout"; nonce: string };
type CheckOptions = { foreground?: boolean; force?: boolean; notify?: boolean };
function accessFingerprint(session: BrowserSession, context: InfrastructureContext): string {
  const workspace = session.workspaces.find(item => item.id === context.tenantId);
  return JSON.stringify([session.user.id, session.auth_method, workspace?.role, workspace?.can_manage, workspace?.access_revision,
    [...(workspace?.permissions ?? [])].sort(), (workspace?.scopes ?? []).map(scope => JSON.stringify(scope)).sort(), workspace?.project_id, workspace?.location_id]);
}
function isSignal(value: unknown): value is SessionSignal {
  return Boolean(value && typeof value === "object" && "type" in value && ["session-changed", "logout"].includes(String(value.type))
    && "nonce" in value && typeof value.nonce === "string" && value.nonce.length <= 100);
}

export function useWorkspaceSession(workspaceId?: string | null) {
  const [session, setSession] = useState<BrowserSession>();
  const [config, setConfig] = useState<BrowserAuthConfig>();
  const [context, setContext] = useState<InfrastructureContext>({ tenantId: "" });
  const [contentKey, setContentKey] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error>();
  const current = useRef({ session, context });
  const requestVersion = useRef(0);
  const pending = useRef<{ controller: AbortController; promise: Promise<void> } | undefined>(undefined);
  const generation = useRef(0);
  const active = useRef(true);
  const logoutLocked = useRef(false);
  const logoutPending = useRef(false);
  const logoutNeedsRetry = useRef(false);
  const lastAutomatic = useRef(0);
  const channel = useRef<BroadcastChannel | undefined>(undefined);
  const seenSignals = useRef(new Set<string>());
  const requestedWorkspace = useRef(workspaceId);
  requestedWorkspace.current = workspaceId;

  const broadcast = useCallback((type: SessionSignal["type"]) => {
    const signal: SessionSignal = { type, nonce: `${Date.now()}:${Math.random().toString(36).slice(2)}` };
    try { channel.current?.postMessage(signal); } catch { /* Another tab may already have closed. */ }
    try { window.localStorage.setItem(SESSION_SIGNAL_KEY, JSON.stringify(signal)); } catch { /* Focus checks still work without storage. */ }
  }, []);
  const closeAccess = useCallback((reason?: Error) => {
    requestVersion.current++;
    pending.current?.controller.abort(); pending.current = undefined;
    const empty = { tenantId: "" };
    current.current = { session: undefined, context: empty };
    generation.current = setApiAccessBoundary(null);
    setContentKey(generation.current); setSession(undefined); setContext(empty); setLoading(false); setError(reason);
  }, []);
  const apply = useCallback((next: BrowserSession, value: InfrastructureContext) => {
    const saved = saveAccountContext(next, value);
    const sameContext = apiContextKey(saved) === apiContextKey(current.current.context);
    const nextContext = sameContext ? current.current.context : saved;
    generation.current = setApiAccessBoundary(nextContext, accessFingerprint(next, nextContext));
    current.current = { session: next, context: nextContext };
    setContentKey(generation.current); setSession(next); setContext(nextContext); setError(undefined);
  }, []);

  const check = useCallback((options: CheckOptions = {}): Promise<void> => {
    if (!active.current || logoutLocked.current) return Promise.resolve();
    if (pending.current && !options.force) return pending.current.promise;
    pending.current?.controller.abort();
    const controller = new AbortController();
    const version = ++requestVersion.current;
    const explicitWorkspace = requestedWorkspace.current;
    if (options.foreground) setLoading(true);
    const promise = loadBrowserSession(controller.signal).then(result => {
      if (!active.current || controller.signal.aborted || version !== requestVersion.current || logoutLocked.current) return;
      setConfig(result.config);
      const previous = current.current;
      const sameIdentity = previous.session?.user.id === result.session.user.id && previous.session?.auth_method === result.session.auth_method;
      const workspace = result.session.workspaces.find(item => item.id === (explicitWorkspace || (sameIdentity ? previous.context.tenantId : "")));
      if (explicitWorkspace && !workspace) {
        apply(result.session, { tenantId: "", actorId: result.session.user.id });
        setError(new ApiError("当前账号没有这个工作空间的访问权限，请明确选择其他获授权空间。", 403));
      } else {
        const unchangedAccess = sameIdentity && previous.session && workspace && accessFingerprint(previous.session, previous.context) === accessFingerprint(result.session, { ...previous.context, tenantId: workspace.id });
        const value = workspace ? workspaceContext(result.session, workspace, unchangedAccess ? previous.context : undefined) : selectInitialContext(result.session, explicitWorkspace);
        apply(result.session, value);
      }
      if (options.notify) broadcast("session-changed");
    }).catch(caught => {
      if (!active.current || controller.signal.aborted || version !== requestVersion.current || logoutLocked.current) return;
      const failure = caught instanceof Error ? caught : new Error(String(caught));
      if (caught && typeof caught === "object" && "authConfig" in caught) setConfig(caught.authConfig as BrowserAuthConfig);
      if (failure instanceof ApiError && [401, 403].includes(failure.status)) closeAccess(failure);
      else setError(failure); // A network failure is not evidence that a valid account changed.
      throw failure;
    }).finally(() => {
      if (active.current && version === requestVersion.current) { pending.current = undefined; setLoading(false); }
    });
    pending.current = { controller, promise };
    return promise;
  }, [apply, broadcast, closeAccess]);

  useEffect(() => {
    active.current = true;
    generation.current = setApiAccessBoundary(null);
    setContentKey(generation.current);
    return () => { active.current = false; requestVersion.current++; pending.current?.controller.abort(); setApiAccessBoundary(null); };
  }, []);
  useEffect(() => { void check({ foreground: true, force: true, notify: true }).catch(() => {}); }, [check, workspaceId]);
  useEffect(() => {
    let delayed: ReturnType<typeof setTimeout> | undefined;
    const automatic = (force = false) => {
      if (logoutLocked.current) return;
      const remaining = 1000 - (Date.now() - lastAutomatic.current);
      if (!force && remaining > 0) {
        // Coalesce concurrent failures; a later denial still gets a trailing check.
        if (!pending.current && !delayed) delayed = setTimeout(() => { delayed = undefined; automatic(); }, remaining + 1);
        return;
      }
      if (delayed) { clearTimeout(delayed); delayed = undefined; }
      lastAutomatic.current = Date.now();
      void check({ force }).catch(() => {});
    };
    const onFocus = () => automatic();
    const onVisibility = () => { if (document.visibilityState === "visible") automatic(); };
    const accepts = (event: Event) => !(event instanceof CustomEvent) || (event as ApiAccessEvent).detail?.generation === generation.current;
    const onExpired = (event: Event) => { if (accepts(event)) { closeAccess(new ApiError("登录已过期，请重新使用统一账号登录。", 401)); } };
    const onForbidden = (event: Event) => { if (accepts(event)) automatic(); };
    const onSignal = (value: unknown) => {
      if (!isSignal(value) || seenSignals.current.has(value.nonce)) return;
      seenSignals.current.add(value.nonce);
      if (seenSignals.current.size > 50) seenSignals.current.delete(seenSignals.current.values().next().value!);
      if (value.type === "logout") {
        logoutLocked.current = true; logoutNeedsRetry.current = false;
        try { clearAccessToken(); } catch { /* Local page access is still closed. */ }
        closeAccess(new Error("账号已在其他标签页退出。请重新登录。"));
      } else automatic(true);
    };
    const onStorage = (event: StorageEvent) => {
      if (event.key !== SESSION_SIGNAL_KEY || !event.newValue) return;
      try { onSignal(JSON.parse(event.newValue)); } catch { /* Ignore unrelated or malformed signals. */ }
    };
    try {
      if (typeof BroadcastChannel !== "undefined") {
        channel.current = new BroadcastChannel(SESSION_CHANNEL);
        channel.current.onmessage = event => onSignal(event.data);
      }
    } catch { /* Storage and focus remain available. */ }
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("storage", onStorage);
    window.addEventListener("sim:authentication-required", onExpired);
    window.addEventListener("sim:access-recheck", onForbidden);
    return () => {
      if (delayed) clearTimeout(delayed);
      window.removeEventListener("focus", onFocus); document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("storage", onStorage); window.removeEventListener("sim:authentication-required", onExpired); window.removeEventListener("sim:access-recheck", onForbidden);
      channel.current?.close(); channel.current = undefined;
    };
  }, [check, closeAccess]);

  const refresh = useCallback(() => check({ force: true, notify: true }), [check]);
  const selectWorkspace = useCallback((workspace: Workspace) => {
    const identity = current.current.session;
    const allowed = identity?.workspaces.find(item => item.id === workspace.id);
    if (identity && allowed) apply(identity, workspaceContext(identity, allowed));
  }, [apply]);
  const updateContext = useCallback((value: InfrastructureContext) => {
    const identity = current.current.session;
    const allowed = identity?.workspaces.find(item => item.id === value.tenantId);
    if (identity && allowed) apply(identity, workspaceContext(identity, allowed, { ...value, actorId: identity.user.id }));
  }, [apply]);
  const signOut = useCallback(async () => {
    if (logoutPending.current) return;
    logoutPending.current = true; logoutLocked.current = true; logoutNeedsRetry.current = false;
    const captured = current.current.context;
    try { clearAccessToken(); } catch { /* Always close the page, even when token storage fails. */ }
    const completion = createApiClient({ getContext: () => captured }).request("/auth/logout", { method: "POST" });
    closeAccess(); broadcast("logout");
    try { await completion; window.location.assign("/app-next/login"); }
    catch (caught) {
      logoutNeedsRetry.current = true;
      if (active.current) setError(new Error(`本页已退出，但服务器退出尚未确认。请重试退出：${caught instanceof Error ? caught.message : String(caught)}`));
    } finally { logoutPending.current = false; }
  }, [broadcast, closeAccess]);
  const retry = useCallback(() => {
    if (logoutPending.current) return;
    if (logoutLocked.current && logoutNeedsRetry.current) { void signOut(); return; }
    logoutLocked.current = false; // Explicit retry may sign in again; passive events cannot.
    void check({ foreground: !current.current.session, force: true }).catch(() => {});
  }, [check, signOut]);
  const accessDenied = Boolean(session && workspaceId && context.tenantId !== workspaceId);
  return { session, config, context, contentKey, accessDenied, loading, error, refresh, selectWorkspace, updateContext, signOut, retry };
}
