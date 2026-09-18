import { App as AntApp } from "antd";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SpatialWorkbench as SpatialPage } from "./SpatialWorkbench";
import { SceneInventory } from "./SceneInventory";
import type { SceneData, Selection } from "./render/sceneRenderer";
import type { SpatialCable, SpatialPayload } from "./sceneData";
import type { SceneCreated } from "./SceneCreateDrawer";

const request = vi.hoisted(() => vi.fn());
vi.mock("../api/client", () => ({ createApiClient: () => ({ request }) }));
vi.mock("./SpatialCanvas", () => ({ SpatialCanvas: ({ data, selection }: { data: SceneData; selection: Selection | null }) => <output data-testid="rendered-scene">{JSON.stringify({ rooms: data.rooms.map(room => room.id), racks: data.racks.map(rack => rack.id), cables: data.paths.filter(path => path.kind === "cable").map(path => ({ id: path.id, points: path.points })), selection })}</output> }));
vi.mock("./ConnectionWorkbench", () => ({ ConnectionWorkbench: ({ onCreated }: { onCreated: (result: SceneCreated) => void }) => <button onClick={() => onCreated({ kind: "cable", id: "new-cable" })}>保存测试线缆</button> }));
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

function cable(id = "new-cable"): SpatialCable {
  return { id, identifier: id === "new-cable" ? "CB-B-01" : "CB-THROUGH-A", media_type: "Cat6A copper", construction: "patch_cord", installation_status: "planned", length_m: null, test_status: null, route_segment_ids: [], endpoint_scope: "complete", terminations: [
    { side: "A", device_id: "device-b1", port_id: "port-b1", rack_id: "rack-b1", location_id: "room-b" },
    { side: "B", device_id: "device-b2", port_id: "port-b2", rack_id: "rack-b2", location_id: "room-b" },
  ] };
}
function scene(cables: SpatialCable[] = []): SpatialPayload {
  return {
    scope: { tenant_id: "tenant-a", project_id: "project-a", location_id: null },
    locations: ["a", "b"].map(side => ({ id: `room-${side}`, parent_id: null, identifier: `ROOM-${side}`, name: `房间 ${side.toUpperCase()}`, location_type: "room", dimensions: { width_m: 8, depth_m: 6, height_m: 3.6 }, coordinates: {}, transform_3d: {} })),
    racks: ["a", "b1", "b2"].map((name, index) => ({ id: `rack-${name}`, location_id: name === "a" ? "room-a" : "room-b", rack_identifier: `RACK-${name}`, name: `机柜 ${name}`, height_u: 42, width_mm: 600, depth_mm: 1000, position_x: index === 2 ? 4 : 1, position_y: 1, position_z: 0, rotation: 0, reserved_units: [], status: "active" })),
    devices: ["b1", "b2"].map(side => ({ id: `device-${side}`, rack_id: `rack-${side}`, location_id: "room-b", identifier: `DEV-${side}`, name: `设备 ${side}`, device_type: "switch", start_u: 1, rack_units: 2, face: "front", status: "active" })),
    ports: ["b1", "b2"].map(side => ({ id: `port-${side}`, device_id: `device-${side}`, identifier: `PORT-${side}`, label: `PORT-${side}`, connector_type: "RJ45", front_or_rear: "front", position_index: 1, status: "available" })),
    pathways: [], cables, truncated: [],
  };
}
function renderedScene() { return JSON.parse(screen.getByTestId("rendered-scene").textContent!); }

describe("scene scope after inventory changes", () => {
  it("retains the verified room entry when a cable is saved from a legacy room deep link", async () => {
    const before = scene();
    before.scope.location_id = "room-b";
    before.locations = before.locations.filter(item => item.id === "room-b");
    before.racks = before.racks.filter(item => item.location_id === "room-b");
    request.mockResolvedValueOnce(before).mockResolvedValueOnce({ ...before, cables: [cable()] });
    function Address() { return <output aria-label="当前地址">{useLocation().search}</output>; }
    render(<AntApp><MemoryRouter initialEntries={["/3d?room=room-b"]}><SpatialPage getContext={getContext} /><Address /></MemoryRouter></AntApp>);
    await waitFor(() => expect(renderedScene().rooms).toEqual(["room-b"]));
    fireEvent.click(screen.getByRole("button", { name: "安装铜缆" }));
    fireEvent.click(screen.getByRole("button", { name: "保存测试线缆" }));
    await waitFor(() => expect(renderedScene().selection).toEqual({ kind: "cable", id: "new-cable" }));
    const query = new URLSearchParams(screen.getByLabelText("当前地址").textContent!);
    expect(query.get("location")).toBe("room-b");
    expect(query.get("cable")).toBe("new-cable");
    expect(query.has("room")).toBe(false);
  });
  it("keeps a room root's nested rack rows visible when restoring its legacy room link", async () => {
    const payload = scene();
    const root = payload.locations.find(item => item.id === "room-b")!;
    payload.scope.location_id = root.id;
    payload.locations = [root, { ...root, id: "row-b", parent_id: root.id, name: "机柜列 B", location_type: "row" }];
    payload.racks = payload.racks.filter(item => item.id !== "rack-a").map(item => item.id === "rack-b2" ? { ...item, location_id: "row-b" } : item);
    payload.devices = payload.devices.map(item => item.id === "device-b2" ? { ...item, location_id: "row-b" } : item);
    request.mockResolvedValue(payload);
    render(<AntApp><MemoryRouter initialEntries={["/3d?room=room-b"]}><SpatialPage getContext={getContext} /></MemoryRouter></AntApp>);
    await waitFor(() => expect(renderedScene().racks).toEqual(["rack-b1", "rack-b2"]));
    expect(renderedScene().rooms).toEqual(["room-b", "row-b"]);
  });
  it("refetches a new cable without a response location and switches from room A to its visible endpoints in room B", async () => {
    const context = getContext();
    const onContextChange = vi.fn();
    request.mockResolvedValueOnce(scene()).mockResolvedValueOnce(scene([cable()]));
    render(<AntApp><MemoryRouter initialEntries={["/3d?room=room-a"]}><SpatialPage getContext={getContext} onContextChange={onContextChange} /></MemoryRouter></AntApp>);
    await waitFor(() => expect(renderedScene()).toEqual({ rooms: ["room-a"], racks: ["rack-a"], cables: [], selection: null }));
    fireEvent.click(screen.getByRole("button", { name: /新\s*建/ }));
    fireEvent.click(await screen.findByText("线缆连接"));
    fireEvent.click(screen.getByRole("button", { name: "保存测试线缆" }));
    await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
    expect(request.mock.calls.map(([path]) => path)).toEqual(["/scene", "/scene"]);
    await waitFor(() => expect(renderedScene()).toEqual({
      rooms: ["room-b"], racks: ["rack-b1", "rack-b2"], selection: { kind: "cable", id: "new-cable" },
      cables: [{ id: "new-cable", points: [[1, expect.closeTo(0.15795, 6), expect.closeTo(1.453, 6)], [1, expect.closeTo(0.15795, 6), expect.closeTo(1.653, 6)], [4, expect.closeTo(0.15795, 6), expect.closeTo(1.653, 6)], [4, expect.closeTo(0.15795, 6), expect.closeTo(1.453, 6)]] }],
    }));
    expect(screen.getByText("选中线缆 · 端口连接示意")).toBeInTheDocument();
    expect(onContextChange).not.toHaveBeenCalled();
    expect(getContext()).toEqual(context);
  });

  it("includes a cable routed through room A even when both endpoints are in room B", () => {
    const routed = { ...cable("through-a"), route_segment_ids: ["segment-a"] };
    const outside = { ...cable("outside-a"), identifier: "CB-OUTSIDE-A" };
    const payload = scene([routed, outside]);
    payload.pathways = [{ id: "path-a", location_id: "room-a", identifier: "TRAY-A", name: "A 桥架", type: "basket_tray", segments: [{ id: "segment-a", name: "A 路径段", sequence: 1, length_m: 3, coordinates: [{ x: 1, y: 1, z: 3 }, { x: 4, y: 1, z: 3 }] }] }];
    const onSelect = vi.fn();
    render(<AntApp><MemoryRouter><SceneInventory payload={payload} locationId="room-a" selection={null} loading={false} onLocation={vi.fn()} onSelect={onSelect} onInspectRack={vi.fn()} /></MemoryRouter></AntApp>);
    fireEvent.click(screen.getByRole("button", { name: /^线缆/ }));
    const visibleCable = screen.getByRole("button", { name: /CB-THROUGH-A/ });
    expect(screen.queryByRole("button", { name: /CB-OUTSIDE-A/ })).not.toBeInTheDocument();
    fireEvent.click(visibleCable);
    expect(onSelect).toHaveBeenCalledExactlyOnceWith({ kind: "cable", id: "through-a" });
  });
});


describe("3D cable lifecycle", () => {
  it("removes the selected cable after confirmation while keeping the explicit floor entry", async () => {
    const before = scene([cable()]); before.scope.location_id = "floor-b";
    const after = { ...before, cables: [] };
    let finish!: (value: unknown) => void;
    const save = new Promise(resolve => { finish = resolve; });
    request.mockResolvedValueOnce(before).mockResolvedValueOnce({ id: "new-cable", identifier: "CB-B-01", action: "delete", version: 7, status: "planned", allowed: true, blockers: [], endpoints: [], route_segment_count: 0 }).mockReturnValueOnce(save).mockResolvedValueOnce(after);
    function Address() { return <output aria-label="当前地址">{useLocation().search}</output>; }
    render(<AntApp><MemoryRouter initialEntries={["/3d?location=floor-b&cable=new-cable"]}><SpatialPage getContext={getContext} /><Address /></MemoryRouter></AntApp>);
    fireEvent.click(await screen.findByRole("button", { name: /删除规划线缆/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: "确认删除" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape", code: "Escape" });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(renderedScene().selection).toEqual({ kind: "cable", id: "new-cable" });
    await act(async () => finish({ id: "new-cable", action: "delete", version: 8 }));
    await waitFor(() => expect(renderedScene().cables).toEqual([]));
    expect(renderedScene().selection).toBeNull();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    const query = new URLSearchParams(screen.getByLabelText("当前地址").textContent!);
    expect(query.get("location")).toBe("floor-b"); expect(query.has("cable")).toBe(false);
    expect(request.mock.calls.map(([path]) => path)).toEqual(["/scene", "/cables/new-cable/deletion-preview", "/cables/new-cable?expected_version=7", "/scene"]);
  });
  it("requires leaving an active placement tool before cable retirement", async () => {
    request.mockResolvedValue(scene([cable()]));
    render(<AntApp><MemoryRouter initialEntries={["/3d?location=floor-b&cable=new-cable"]}><SpatialPage getContext={getContext} /></MemoryRouter></AntApp>);
    await screen.findByRole("button", { name: /删除规划线缆/ });
    fireEvent.click(screen.getByRole("button", { name: "安装机柜" }));
    expect(screen.getByRole("button", { name: /删除规划线缆/ })).toBeDisabled();
    expect(screen.getByText("请先完成或取消当前编辑与临时测距，再删除或拆除线缆。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "选择工具" }));
    expect(screen.getByRole("button", { name: /删除规划线缆/ })).toBeEnabled();
    expect(request).toHaveBeenCalledTimes(1);
  });
});
