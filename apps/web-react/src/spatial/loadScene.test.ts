import { describe, expect, it, vi } from "vitest";
import type { createApiClient } from "../api/client";
import { loadScene } from "./loadScene";
import type { SpatialPayload, SpatialPort } from "./sceneData";
const port = (id: string, device_id = "device") => ({ id, device_id, identifier: id, label: id, connector_type: "RJ45", front_or_rear: "front", position_index: 1, status: "available", occupied: false }) satisfies SpatialPort;
const scope = { tenant_id: "workspace", project_id: null, location_id: "floor" };
function scene(): SpatialPayload { return { scope, locations: [], racks: [], devices: [{ id: "device" } as SpatialPayload["devices"][number]], ports: [port("p1")], pathways: [], cables: [], port_page: { total: 3, next_cursor: "p1" }, truncated: ["ports", "cables"] }; }
function client(request: ReturnType<typeof vi.fn>) { return { request } as Pick<ReturnType<typeof createApiClient>, "request">; }
const progress = vi.fn();
const signal = () => new AbortController().signal;

describe("complete scene port loading", () => {
  it("collects all pages in selected scope and removes only the resolved truncation warning", async () => {
    const input = scene();
    const request = vi.fn().mockResolvedValueOnce(input)
      .mockResolvedValueOnce({ scope, ports: [port("p2")], port_page: { total: 3, next_cursor: "p2" }, truncated: [] })
      .mockResolvedValueOnce({ scope, ports: [port("p3")], port_page: { total: 3, next_cursor: null }, truncated: [] });
    const result = await loadScene(client(request), signal(), progress);
    expect(result.ports.map(p => p.id)).toEqual(["p1", "p2", "p3"]);
    expect(result.truncated).toEqual(["cables"]);
    expect(input.ports).toHaveLength(1);
    expect(request.mock.calls.map(call => call[0])).toEqual(["/scene", "/scene/ports?location_id=floor&after=p1", "/scene/ports?location_id=floor&after=p2"]);
    expect(progress).toHaveBeenCalledWith(2, 3);
  });
  it.each([
    { scope: { ...scope, tenant_id: "other" } },
    { scope: { ...scope, location_id: "other-floor" } },
    { ports: [port("p1")] },
    { ports: [port("p2", "unknown-device")] },
    { port_page: { total: 4, next_cursor: null } },
    { port_page: { total: 3, next_cursor: "p1" } },
    { ports: [], port_page: { total: 3, next_cursor: null } },
  ])("rejects stale, inconsistent or incomplete pages instead of making them editable: %j", async changed => {
    const request = vi.fn().mockResolvedValueOnce(scene()).mockResolvedValueOnce({ scope, ports: [port("p2"), port("p3")], port_page: { total: 3, next_cursor: null }, truncated: [], ...changed });
    await expect(loadScene(client(request), signal(), progress)).rejects.toThrow();
  });
  it("propagates failure and cancellation without returning a partial catalog", async () => {
    const request = vi.fn().mockResolvedValueOnce(scene()).mockRejectedValueOnce(new Error("Page unavailable"));
    await expect(loadScene(client(request), signal(), progress)).rejects.toThrow("Page unavailable");
    const controller = new AbortController(); controller.abort();
    const aborted = vi.fn().mockResolvedValueOnce(scene());
    await expect(loadScene(client(aborted), controller.signal, progress)).rejects.toMatchObject({ name: "AbortError" });
    expect(aborted).toHaveBeenCalledTimes(1);
  });
});
