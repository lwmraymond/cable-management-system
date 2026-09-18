import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SceneCreateDrawer } from "./SceneCreateDrawer";
import { installationPreset } from "./InstallPalette";
import type { SpatialPayload } from "./sceneData";

const request = vi.hoisted(() => vi.fn());
vi.mock("../api/client", () => ({ createApiClient: () => ({ request }) }));
beforeAll(() => {
  const getStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation(element => getStyle(element));
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
beforeEach(() => request.mockReset());
afterEach(cleanup);
afterAll(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });
const floorId = "12cacb2f-c984-4d3d-b5ab-889fe77114cc";
const getContext = () => ({ tenantId: "tenant", actorId: "owner", locationId: floorId });
function payload(): SpatialPayload {
  return {
    scope: { tenant_id: "tenant", project_id: null, location_id: floorId },
    locations: [
      { id: "empty-room", parent_id: floorId, identifier: "PREP", name: "空准备间", location_type: "room", dimensions: { width_m: 4, depth_m: 3, height_m: 3.2 }, coordinates: {}, transform_3d: {} },
      { id: floorId, parent_id: null, identifier: "MC-ENG-F02", name: "Floor 2", location_type: "floor", dimensions: { width_m: 80, depth_m: 40, height_m: 3.2 }, coordinates: {}, transform_3d: {} },
    ],
    racks: [], devices: [], ports: [], cables: [], truncated: [],
    pathways: [{ id: "floor-tray", location_id: floorId, identifier: "F02-TRAY", name: "楼层公共桥架", type: "basket_tray", segments: [{ id: "segment", name: "公共走廊", sequence: 1, length_m: 4, coordinates: [{ x: 1, y: 2.8, z: 2.7 }, { x: 5, y: 2.8, z: 2.7 }] }] }],
  };
}
function spacePicker() { return screen.getByLabelText("所属房间").closest(".ant-select")!; }

describe("scene placement space options", () => {
  it.each(["rack", "tray"] as const)("shows the real floor name for a %s drop and preserves that floor in the submission", async tool => {
    const data = payload();
    const before = structuredClone(data);
    const { kind, preset } = installationPreset(tool, { locationId: floorId, positionX: 2.5, positionY: 3.25 }, data);
    const created = vi.fn();
    request.mockResolvedValue(kind === "rack" ? { racks: [{ id: "new-rack", location_id: floorId }] } : { id: "new-tray" });
    render(<SceneCreateDrawer kind={kind} payload={data} locationId="empty-room" preset={preset} getContext={getContext} onClose={vi.fn()} onCreated={created} />);
    expect(spacePicker()).toHaveTextContent("Floor 2 · MC-ENG-F02");
    expect(spacePicker()).not.toHaveTextContent(floorId);
    if (kind === "pathway") {
      fireEvent.change(screen.getByLabelText("对象编号"), { target: { value: "TRAY-NEW" } });
      fireEvent.change(screen.getByLabelText("名称"), { target: { value: "楼层新增桥架" } });
    }
    fireEvent.click(screen.getByRole("button", { name: kind === "rack" ? "保存机柜" : "保存线槽" }));
    await waitFor(() => expect(request).toHaveBeenCalledTimes(1));
    expect(request.mock.calls[0][0]).toBe(kind === "rack" ? "/scene/racks" : "/scene/pathways");
    const body = JSON.parse(request.mock.calls[0][1].body);
    expect(body.location_id).toBe(floorId);
    if (kind === "rack") expect(body).toMatchObject({ position_x: 2.5, position_y: 3.25 });
    else expect(body.segments[0].coordinates[0]).toEqual({ x: 2.5, y: 3.25, z: 3 });
    expect(created).toHaveBeenCalledWith({ kind, id: kind === "rack" ? "new-rack" : "new-tray", locationId: floorId });
    expect(data).toEqual(before);
  });

  it("defaults to the scoped floor and still offers ordinary rooms without inventory", async () => {
    render(<SceneCreateDrawer kind="rack" payload={payload()} locationId="" getContext={getContext} onClose={vi.fn()} onCreated={vi.fn()} />);
    expect(spacePicker()).toHaveTextContent("Floor 2 · MC-ENG-F02");
    fireEvent.mouseDown(screen.getByLabelText("所属房间"));
    fireEvent.click(await screen.findByText("空准备间 · PREP"));
    expect(spacePicker()).toHaveTextContent("空准备间 · PREP");
    expect(request).not.toHaveBeenCalled();
  });
});
