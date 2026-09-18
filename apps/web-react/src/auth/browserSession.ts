import { ApiError, configureCookieCsrf } from "../api/client";
import { loadContext, normalizeContext, type InfrastructureContext } from "../api/context";
import { readAccessToken } from "./tokenStore";
import type { BrowserSession, Workspace } from "../workspaces/types";

export interface BrowserAuthConfig {
  auth_mode: "demo" | "hybrid" | "oidc";
  demo_mode: boolean;
  cookie_auth_enabled: boolean;
  sso_enabled: boolean;
  csrf_cookie_name: string;
  csrf_header_name: string;
}

export function safeReturnTo(value: string | null | undefined): string {
  if (!value || value.length > 1600 || !value.startsWith("/app-next") || /[\\\x00-\x20\x7f]/.test(value)) return "/app-next/";
  try {
    const url = new URL(value, window.location.origin);
    const path = decodeURIComponent(url.pathname);
    const credentials = new Set(["token", "access_token", "id_token", "refresh_token", "code", "session", "cookie"]);
    if (url.origin !== window.location.origin || !/^\/app-next(?:\/|$)/.test(path)
      || url.hash || path !== url.pathname || path.includes("%")
      || /[\\\x00-\x20\x7f]/.test(decodeURIComponent(value))
      || value.split("?")[0].split("/").some(part => part === "." || part === "..")
      || Array.from(url.searchParams.keys()).some(key => credentials.has(key.toLowerCase()))
      || /^\/app-next\/(?:auth|login|oidc)(?:\/|$)/.test(path)) return "/app-next/";
    return `${url.pathname}${url.search}`;
  } catch { return "/app-next/"; }
}

async function authRequest<T>(path: string, headers: Headers, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api/v1/auth/${path}`, { headers, signal, credentials: "same-origin", cache: "no-store" });
  let body: unknown;
  try { body = await response.json(); }
  catch {
    if (response.ok) throw new ApiError("登录服务返回了无法读取的数据，请重试。", 502);
  }
  if (!response.ok) {
    const detail = body && typeof body === "object" && "detail" in body && typeof body.detail === "string" ? body.detail : undefined;
    const fallback = response.status === 401 ? "登录已过期，请重新登录。" : response.status === 403 ? "当前账号无法访问登录服务。" : "登录服务暂时不可用，请稍后重试。";
    throw new ApiError(detail || fallback, response.status);
  }
  return body as T;
}

export async function loadBrowserSession(signal?: AbortSignal): Promise<{ config: BrowserAuthConfig; session: BrowserSession }> {
  const config = await authRequest<BrowserAuthConfig>("config", new Headers({ Accept: "application/json" }), signal);
  configureCookieCsrf(config);
  const headers = new Headers({ Accept: "application/json" });
  const token = readAccessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (config.demo_mode && config.auth_mode !== "oidc" && !token) {
    let legacy: InfrastructureContext = { tenantId: "" };
    try { legacy = loadContext(); } catch { /* Server-provided demo identity still requires session validation. */ }
    let actorId = legacy.actorId;
    if (!actorId) {
      const response = await fetch("/api/v1/demo/context", { signal, cache: "no-store" });
      if (response.ok) {
        const demo = await response.json();
        actorId = demo.owner_id;
        // Only migrate the old local demo scope once; authenticated accounts are keyed separately.
        if (!legacy.tenantId) {
          try { window.localStorage.setItem("sim.infrastructure-context", JSON.stringify({
            tenantId: demo.tenant_id, actorId, projectId: demo.project_id, locationId: demo.location_id,
          })); } catch { /* A saved preference is optional. */ }
        }
      }
    }
    if (actorId) headers.set("X-Actor-ID", actorId);
  }
  try {
    const session = await authRequest<BrowserSession>("session", headers, signal);
    return { config, session };
  } catch (error) {
    if (error instanceof ApiError) Object.assign(error, { authConfig: config });
    throw error;
  }
}

function accountKey(userId: string): string { return `sim.account-context:${userId}`; }
export function loadAccountContext(session: BrowserSession): InfrastructureContext {
  try {
    const raw = window.localStorage.getItem(accountKey(session.user.id));
    if (raw) return normalizeContext(JSON.parse(raw));
  } catch { /* An unavailable preference does not grant access. */ }
  try {
    const legacy = loadContext();
    return session.auth_method === "demo" && legacy.actorId === session.user.id ? legacy : { tenantId: "" };
  } catch { return { tenantId: "" }; }
}
export function saveAccountContext(session: BrowserSession, value: InfrastructureContext): InfrastructureContext {
  const normalized = normalizeContext({ ...value, actorId: session.user.id });
  try { window.localStorage.setItem(accountKey(session.user.id), JSON.stringify(normalized)); } catch { /* The account remains usable without saved preferences. */ }
  return normalized;
}
export function workspaceContext(session: BrowserSession, workspace: Workspace, preferred?: InfrastructureContext): InfrastructureContext {
  const sameWorkspace = preferred?.tenantId === workspace.id && preferred.actorId === session.user.id;
  return normalizeContext({
    tenantId: workspace.id,
    actorId: session.user.id,
    projectId: sameWorkspace ? preferred.projectId : workspace.project_id ?? undefined,
    locationId: sameWorkspace ? preferred.locationId : workspace.location_id ?? undefined,
  });
}
export function selectInitialContext(session: BrowserSession, workspaceId?: string | null): InfrastructureContext {
  const preferred = loadAccountContext(session);
  const workspace = workspaceId
    ? session.workspaces.find(item => item.id === workspaceId)
    : session.workspaces.find(item => item.id === preferred.tenantId) ?? session.workspaces[0];
  if (workspaceId && !workspace) throw new ApiError("当前账号没有这个工作空间的访问权限。", 403);
  return workspace ? workspaceContext(session, workspace, preferred) : { tenantId: "", actorId: session.user.id };
}
