import type { createApiClient } from "../api/client";
import type { SpatialPayload } from "./sceneData";

type PortPage = Pick<SpatialPayload, "scope" | "ports" | "port_page" | "truncated">;
const scopeKey = (scope: SpatialPayload["scope"]) => JSON.stringify([scope.tenant_id, scope.project_id, scope.location_id]);

/** Publish a complete port catalog or fail; an aborted/partial page is never editable. */
export async function loadScene(api: Pick<ReturnType<typeof createApiClient>, "request">, signal: AbortSignal, onProgress: (loaded: number, total: number) => void): Promise<SpatialPayload> {
  const scene = await api.request<SpatialPayload>("/scene", { signal });
  if (!scene.port_page) return scene; // Compatible with servers predating cursor support.
  const ports = new Map(scene.ports.map(port => [port.id, port]));
  const cursors = new Set<string>();
  const deviceIds = new Set(scene.devices.map(device => device.id));
  const query = new URLSearchParams();
  if (scene.scope.location_id) query.set("location_id", scene.scope.location_id);
  if (scene.scope.project_id) query.set("project_id", scene.scope.project_id);
  let page = scene.port_page;
  while (page.next_cursor) {
    if (signal.aborted) throw new DOMException("Scene request aborted", "AbortError");
    if (page.total > 100000 || ports.size >= 100000) throw new Error("端口超过 100,000 个，请选择楼层或房间后重试。");
    if (cursors.has(page.next_cursor) || cursors.size >= 100) throw new Error("端口分页未能完成，请刷新后重试。");
    cursors.add(page.next_cursor);
    onProgress(ports.size, page.total);
    query.set("after", page.next_cursor);
    const next = await api.request<PortPage>(`/scene/ports?${query}`, { signal });
    if (scopeKey(next.scope) !== scopeKey(scene.scope) || !next.port_page || next.port_page.total !== scene.port_page.total || next.truncated.some(kind => !scene.truncated.includes(kind))) throw new Error("场景范围或资料已变化，请刷新后重试。");
    for (const port of next.ports) {
      if (!deviceIds.has(port.device_id) || ports.has(port.id)) throw new Error("端口资料已变化，请刷新后重试。");
      ports.set(port.id, port);
    }
    page = next.port_page;
  }
  if (ports.size !== page.total) throw new Error("端口资料不完整，请刷新后重试。");
  return { ...scene, ports: [...ports.values()], port_page: page, truncated: scene.truncated.filter(kind => kind !== "ports") };
}
