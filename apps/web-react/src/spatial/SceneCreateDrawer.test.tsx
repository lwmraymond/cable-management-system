import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SceneCreateDrawer } from "./SceneCreateDrawer";
import { availablePorts, pathLength } from "./sceneCreate";
import type { SpatialPayload } from "./sceneData";

const request = vi.hoisted(() => vi.fn());
vi.mock("../api/client", () => ({ createApiClient: () => ({ request }) }));
beforeAll(() => {
  const getStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation(element => getStyle(element));
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
beforeEach(() => { request.mockReset(); });
afterEach(cleanup);
afterAll(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });
const getContext = () => ({ tenantId: "tenant-a", actorId: "owner-a", projectId: "project-a" });
function payload(): SpatialPayload {
  return { scope: { tenant_id: "tenant-a", project_id: "project-a", location_id: null }, locations: [{ id: "room-a", parent_id: null, identifier: "RM-A", name: "A 机房", location_type: "room", dimensions: { width_m: 10, depth_m: 8 }, coordinates: {}, transform_3d: {} }], racks: [], devices: [], ports: [], pathways: [], cables: [], truncated: [] };
}
function fill(label: string, value: string) { fireEvent.change(screen.getByLabelText(label), { target: { value } }); }

describe("3D creation drawer submissions", () => {
  it("creates a server room with dimensions and closes only after a successful response", async () => {
    const onCreated = vi.fn(), onClose = vi.fn();
    let resolve: (value: unknown) => void = () => {};
    request.mockReturnValue(new Promise(value => { resolve = value; }));
    render(<SceneCreateDrawer kind="room" payload={payload()} locationId="room-a" getContext={getContext} onCreated={onCreated} onClose={onClose} />);
    fill("对象编号", "ROOM-02"); fill("名称", "服务器机房 B");
    fill("房间宽度（m）", "12");
    fireEvent.click(screen.getByRole("button", { name: "保存房间" }));
    await waitFor(() => expect(request).toHaveBeenCalledWith("/scene/rooms", { method: "POST", body: JSON.stringify({ parent_id: "room-a", identifier: "ROOM-02", name: "服务器机房 B", kind: "server_room", width_m: 12, depth_m: 8, height_m: 3.6 }) }));
    expect(onClose).not.toHaveBeenCalled(); expect(onCreated).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /取\s*消/ })).toBeDisabled();
    resolve({ id: "room-new" });
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith({ kind: "room", id: "room-new", locationId: "room-new" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("installs the selected template into the requested second rack, not the first rack in the room", async () => {
    const data = payload();
    data.racks = ["rack-a", "rack-b"].map((id, index) => ({ id, location_id: "room-a", rack_identifier: id, name: id, height_u: 42, width_mm: 600, depth_mm: 1000, position_x: index ? 4 : 1, position_y: 1, position_z: 0, rotation: 0, reserved_units: [], status: "active" }));
    const onCreated = vi.fn();
    request.mockResolvedValueOnce([{ id: "template-server", manufacturer: "ACME", model: "SRV-2", device_type: "server", rack_units: 2 }]).mockResolvedValueOnce({ id: "device-new", location_id: "room-a" });
    render(<SceneCreateDrawer kind="device" payload={data} locationId="room-a" rackId="rack-b" getContext={getContext} onCreated={onCreated} onClose={vi.fn()} />);
    fill("对象编号", "SRV-B01"); fill("名称", "B 柜服务器");
    await waitFor(() => expect(request).toHaveBeenCalledWith("/device-templates", expect.objectContaining({ signal: expect.any(AbortSignal) })));
    fireEvent.mouseDown(screen.getByLabelText("设备模板"));
    fireEvent.click(await screen.findByText("ACME SRV-2 · 2U"));
    fill("起始 U 位", "5");
    fireEvent.click(screen.getByRole("button", { name: "保存设备" }));
    await waitFor(() => expect(request).toHaveBeenLastCalledWith("/scene/devices", { method: "POST", body: JSON.stringify({ rack_id: "rack-b", template_id: "template-server", identifier: "SRV-B01", name: "B 柜服务器", start_u: 5, face: "front" }) }));
    expect(onCreated).toHaveBeenCalledWith({ kind: "device", id: "device-new", locationId: "room-a" });
  });

  it("submits ordered 3D pathway points with a calculated three-dimensional length", async () => {
    const onCreated = vi.fn(), onClose = vi.fn();
    request.mockResolvedValue({ id: "path-new", identifier: "TRAY-01" });
    render(<SceneCreateDrawer kind="pathway" payload={payload()} locationId="room-a" getContext={getContext} onCreated={onCreated} onClose={onClose} />);
    fill("对象编号", "TRAY-01"); fill("名称", "顶部桥架"); fill("点 3 高度", "4");
    fireEvent.click(screen.getByRole("button", { name: "保存线槽" }));
    await waitFor(() => expect(request).toHaveBeenCalledWith("/scene/pathways", { method: "POST", body: JSON.stringify({ location_id: "room-a", identifier: "TRAY-01", name: "顶部桥架", pathway_type: "basket_tray", segments: [{ name: "顶部桥架 · 01", sequence: 1, length_m: 5.236, coordinates: [{ x: 1, y: 1, z: 3 }, { x: 4, y: 1, z: 3 }, { x: 4, y: 3, z: 4 }] }] }) }));
    expect(onCreated).toHaveBeenCalledWith({ kind: "pathway", id: "path-new", locationId: "room-a" });
  });

  it("keeps user input and the drawer open after server rejection, while cancellation makes no extra write", async () => {
    const onCreated = vi.fn(), onClose = vi.fn();
    request.mockRejectedValue(new Error("identifier already exists"));
    render(<SceneCreateDrawer kind="room" payload={payload()} locationId="room-a" getContext={getContext} onCreated={onCreated} onClose={onClose} />);
    fill("对象编号", "ROOM-01"); fill("名称", "保留输入");
    fireEvent.click(screen.getByRole("button", { name: "保存房间" }));
    await screen.findByText("编号已存在，请使用另一个编号。");
    expect(screen.getByLabelText("名称")).toHaveValue("保留输入");
    expect(screen.getByRole("dialog")).toBeVisible();
    expect(onClose).not.toHaveBeenCalled(); expect(onCreated).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /取\s*消/ }));
    expect(onClose).toHaveBeenCalledTimes(1); expect(request).toHaveBeenCalledTimes(1);
  });
});

describe("creation geometry and endpoint availability", () => {
  it("rejects incomplete and degenerate paths instead of saving invented geometry", () => {
    expect(() => pathLength([{ x: 0, y: 0, z: 3 }])).toThrow("至少需要两个");
    expect(() => pathLength([{ x: 0, y: 0, z: 3 }, { x: Number.NaN, y: 0, z: 3 }])).toThrow("完整填写");
    expect(() => pathLength([{ x: 0, y: 0, z: 3 }, { x: 0, y: 0, z: 3 }])).toThrow("不能全部重合");
    expect(pathLength([{ x: 0, y: 0, z: 1 }, { x: 3, y: 0, z: 5 }])).toBe(5);
  });

  it("excludes hidden occupied ports, visible cable endpoints, disabled ports, the opposite endpoint and incompatible media", () => {
    const data = payload();
    data.ports = ["free", "hidden-claimed", "connected", "disabled", "opposite", "fiber", "other-device"].map((id, index) => ({ id, device_id: id === "other-device" ? "device-b" : "device-a", identifier: id, label: id, connector_type: id === "fiber" ? "LC" : "RJ45", front_or_rear: "front", position_index: index, status: id === "disabled" ? "disabled" : "available", ...(id === "hidden-claimed" ? { occupied: true } : {}) }));
    data.cables = [{ id: "existing", identifier: "existing", media_type: "copper", construction: "patch_cord", installation_status: "active", length_m: null, test_status: null, route_segment_ids: [], endpoint_scope: "partial", terminations: [{ side: "A", port_id: "connected", device_id: "device-a", rack_id: null, location_id: "room-a" }] }];
    const before = structuredClone(data);
    expect(availablePorts(data, "device-a", "Cat6A copper", "opposite").map(port => ({ id: port.id, connector: port.connector_type }))).toEqual([{ id: "free", connector: "RJ45" }]);
    expect(data).toEqual(before);
  });
});
