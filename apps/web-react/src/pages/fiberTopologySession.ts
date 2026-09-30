import { useEffect, useMemo, useRef, useState } from "react";
import { createApiClient } from "../api/client";
import type { InfrastructureContext } from "../api/context";
import { fiberError } from "./fiberUi";

export type GetContext = () => InfrastructureContext;
export type ApiClient = ReturnType<typeof createApiClient>;

export interface FiberTopologySession {
  api: ApiClient;
  busy: boolean;
  notice: string;
  canRead: boolean;
  canWrite: boolean;
  run: (operation: () => Promise<void>) => void;
  post: <T>(path: string, body?: unknown) => Promise<T>;
}

export function useFiberTopologySession(getContext: GetContext): FiberTopologySession {
  const api = useMemo(() => createApiClient({ getContext }), [getContext]);
  const alive = useRef(true);
  const pending = useRef(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [permissions, setPermissions] = useState<string[]>([]);

  useEffect(() => {
    alive.current = true;
    let cancelled = false;
    api.request<{ principal: { permissions: string[] } }>("/tenants/current")
      .then(result => { if (!cancelled) setPermissions(result.principal.permissions); })
      .catch(error => { if (!cancelled) setNotice(fiberError(error).message); });
    return () => { cancelled = true; alive.current = false; };
  }, [api]);

  function run(operation: () => Promise<void>) {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setNotice("");
    void operation()
      .catch(error => { if (alive.current) setNotice(fiberError(error).message); })
      .finally(() => {
        pending.current = false;
        if (alive.current) setBusy(false);
      });
  }

  const post = <T,>(path: string, body?: unknown) => api.request<T>(path, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const canRead = permissions.includes("*") || permissions.includes("fiber:read") || permissions.includes("cable:trace");
  const canWrite = permissions.includes("*") || permissions.includes("fiber:write");
  return { api, busy, notice, canRead, canWrite, run, post };
}
