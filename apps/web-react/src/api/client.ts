import { contextHeaders, type InfrastructureContext } from "./context";
import { readAccessToken } from "../auth/tokenStore";

let accessBoundary: { key: string; fingerprint: string } | null | undefined;
let accessGeneration = 0;
export function apiContextKey(context: InfrastructureContext): string {
  return JSON.stringify([context.tenantId, context.actorId ?? "", context.projectId ?? "", context.locationId ?? ""]);
}
/** Scope is kept in this tab only. Events expose an opaque generation, never account data. */
export function setApiAccessBoundary(context: InfrastructureContext | null, fingerprint = ""): number {
  const next = context ? { key: apiContextKey(context), fingerprint } : null;
  if (JSON.stringify(next) !== JSON.stringify(accessBoundary)) { accessBoundary = next; accessGeneration++; }
  return accessGeneration;
}
export type ApiAccessEvent = CustomEvent<{ generation: number }>;
function responseGuard(context: InfrastructureContext, signal?: AbortSignal): () => void {
  const generation = accessGeneration;
  const key = apiContextKey(context);
  return () => {
    if (signal?.aborted || generation !== accessGeneration || (accessBoundary !== undefined && accessBoundary?.key !== key)) {
      // The server may have committed a write. Discard its stale UI response; never retry it.
      throw new DOMException("Request belongs to an inactive account or workspace", "AbortError");
    }
  };
}
function notifyAccessFailure(status: number): void {
  const event = status === 401 ? "sim:authentication-required" : status === 403 ? "sim:access-recheck" : undefined;
  if (event) window.dispatchEvent(new CustomEvent(event, { detail: { generation: accessGeneration } }));
}

let csrfCookieName = "sim_csrf";
let csrfHeaderName = "X-CSRF-Token";
export function configureCookieCsrf(config: { csrf_cookie_name: string; csrf_header_name: string }): void {
  csrfCookieName = config.csrf_cookie_name;
  csrfHeaderName = config.csrf_header_name;
}
function attachCsrf(headers: Headers, method = "GET"): void {
  if (headers.has("Authorization") || !["POST", "PUT", "PATCH", "DELETE"].includes(method.toUpperCase())) return;
  const entry = document.cookie.split(";").map(part => part.trim()).find(part => part.startsWith(`${csrfCookieName}=`));
  if (entry) {
    try { headers.set(csrfHeaderName, decodeURIComponent(entry.slice(csrfCookieName.length + 1))); } catch { /* Server rejects a malformed CSRF cookie. */ }
  }
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly requestId?: string,
    readonly body?: unknown,
  ) { super(message); }
}

function errorMessage(body: unknown, status: number): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (Array.isArray(detail)) {
      // FastAPI validation includes input values; expose only field paths and messages.
      const messages = detail.slice(0, 3).flatMap((item: unknown) => {
        if (!item || typeof item !== "object" || !("msg" in item) || typeof item.msg !== "string") return [];
        const loc = "loc" in item && Array.isArray(item.loc)
          ? item.loc.filter(part => (typeof part === "string" || typeof part === "number") && part !== "body").join(".") : "";
        return [loc ? `${loc}：${item.msg}` : item.msg];
      });
      if (messages.length) return messages.join("；");
    }
  }
  if (status === 401) return "登录已失效，请重新登录。";
  if (status === 403) return "没有执行此操作的权限。";
  if (status === 409) return "数据已发生变化，请刷新后核对再操作。";
  if (status === 422) return "提交的资料不符合要求，请检查后重试。";
  if (status === 429) return "操作过于频繁，请稍后再试。";
  if (status >= 500) return "服务暂时不可用，请稍后重试。";
  return `请求未完成（HTTP ${status}）。`;
}

async function readResponse(response: Response, guard: () => void, notify = true): Promise<unknown> {
  guard();
  // Clear an active expired session even when reading the response body fails.
  // This notification intentionally advances the boundary; do not reject our own
  // 401 as stale afterward or dispatch a second expiry for a later account.
  const expired = notify && response.status === 401;
  if (expired) notifyAccessFailure(401);
  if (response.status === 204) return undefined;
  let raw: string;
  try { raw = await response.text(); }
  catch (caught) {
    if (!expired) guard();
    if (!response.ok) {
      if (notify && !expired) notifyAccessFailure(response.status);
      throw new ApiError(errorMessage(undefined, response.status), response.status, response.headers.get("x-request-id") ?? undefined);
    }
    throw caught;
  }
  if (!expired) guard();
  const contentType = response.headers.get("content-type") ?? "";
  let body: unknown = raw;
  if (/application\/(?:[a-z0-9.+-]+\+)?json\b/i.test(contentType) && raw) {
    try { body = JSON.parse(raw); }
    catch {
      if (response.ok) throw new ApiError("服务返回了无法读取的数据，请刷新页面并核对操作结果。", response.status, response.headers.get("x-request-id") ?? undefined);
      body = undefined;
    }
  }
  if (!response.ok) {
    if (notify && !expired) notifyAccessFailure(response.status);
    throw new ApiError(errorMessage(body, response.status), response.status, response.headers.get("x-request-id") ?? undefined, body);
  }
  return raw ? body : undefined;
}

export interface ApiClientOptions {
  baseUrl?: string;
  getContext: () => InfrastructureContext;
}

export function createApiClient(options: ApiClientOptions) {
  const baseUrl = (options.baseUrl ?? "/api/v1").replace(/\/$/, "");

  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const context = options.getContext();
    // Explicit logout closes local state immediately and must still observe its server outcome.
    const logout = path === "/auth/logout";
    const guard = logout ? () => {} : responseGuard(context, init.signal ?? undefined);
    guard();
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    Object.entries(contextHeaders(context)).forEach(([key, value]) => headers.set(key, value));
    const token = logout ? null : readAccessToken();
    if (logout) headers.delete("Authorization");
    else if (token) headers.set("Authorization", `Bearer ${token}`);
    if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    attachCsrf(headers, init.method);
    const response = await fetch(`${baseUrl}${path}`, { ...init, headers, credentials: "same-origin" });
    return await readResponse(response, guard, !logout) as T;
  }

  async function download(path: string): Promise<Blob> {
    const context = options.getContext();
    const guard = responseGuard(context);
    guard();
    const headers = new Headers(contextHeaders(context));
    const token = readAccessToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const response = await fetch(`${baseUrl}${path}`, { headers, credentials: "same-origin" });
    guard();
    if (!response.ok) await readResponse(response, guard);
    const blob = await response.blob();
    guard();
    return blob;
  }

  return { request, download };
}
