import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SceneCreateDrawer } from "./SceneCreateDrawer";
import type { SpatialPayload } from "./sceneData";

const request = vi.hoisted(() => vi.fn());
vi.mock("../api/client", () => ({ createApiClient: () => ({ request }) }));
beforeAll(() => {
  const getStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation(element => getStyle(element));
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
beforeEach(() => { request.mockReset(); request.mockResolvedValue([]); });
afterEach(cleanup);
afterAll(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });
const getContext = () => ({ tenantId: "tenant", actorId: "owner", projectId: "project" });
function payload(): SpatialPayload {
  return { scope: { tenant_id: "tenant", project_id: "project", location_id: "room" }, locations: [{ id: "room", parent_id: null, identifier: "ROOM-001", name: "Room", location_type: "room", dimensions: { width_m: 8, depth_m: 6 }, coordinates: {}, transform_3d: {} }],
    racks: [{ id: "rack", location_id: "room", rack_identifier: "R1", name: "Rack", height_u: 8, reserved_units: [3, 7], width_mm: 600, depth_mm: 1000, position_x: 1, position_y: 1, position_z: 0, rotation: 0, status: "active" }],
    devices: [{ id: "front", identifier: "DEVICE-001", name: "Front", rack_id: "rack", location_id: "room", face: "front", start_u: 1, rack_units: 2, device_type: "server", status: "active" }, { id: "rear", identifier: "DEVICE-003", name: "Rear", rack_id: "rack", location_id: "room", face: "rear", start_u: 4, rack_units: 2, device_type: "server", status: "active" }],
    pathways: [{ id: "tray", identifier: "TRAY-001", name: "Tray", location_id: "room", type: "basket_tray", segments: [] }], ports: [], cables: [], truncated: [] };
}
const fill = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
const renderForm = (kind: "room" | "device" | "pathway", data = payload()) => render(<SceneCreateDrawer kind={kind} payload={data} locationId="room" rackId="rack" preset={kind === "device" ? { hardware: "server" } : undefined} getContext={getContext} onCreated={vi.fn()} onClose={vi.fn()} />);
const writes = () => request.mock.calls.filter(([, options]) => options?.method === "POST");

describe("creation form suggestions", () => {
  it.each([["room", "ROOM-002"], ["device", "DEVICE-002"], ["pathway", "TRAY-002"]] as const)("only fills %s identity after an explicit click and preserves a custom name", async (kind, identifier) => {
    renderForm(kind);
    fill("对象编号", "MANUAL-ID"); fill("名称", "我填写的名称");
    expect(screen.getByLabelText("对象编号")).toHaveValue("MANUAL-ID");
    fireEvent.click(screen.getByRole("button", { name: "填写建议编号" }));
    expect(screen.getByLabelText("对象编号")).toHaveValue(identifier);
    expect(screen.getByLabelText("名称")).toHaveValue("我填写的名称");
    expect(screen.getByText(/不保证全局唯一/)).toBeVisible();
    expect(writes()).toHaveLength(0);
    if (kind === "device") await waitFor(() => expect(screen.getByRole("button", { name: "采用空闲 U 位" })).toBeEnabled());
  });

  it("fills an empty name and submits the suggestions through the existing room API", async () => {
    request.mockResolvedValue({ id: "new-room" });
    renderForm("room");
    fill("房间宽度（m）", "14");
    fireEvent.click(screen.getByRole("button", { name: "填写建议编号" }));
    expect(screen.getByLabelText("名称")).toHaveValue("房间 002");
    expect(Number((screen.getByLabelText("房间宽度（m）") as HTMLInputElement).value)).toBe(14);
    expect(writes()).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "保存房间" }));
    await waitFor(() => expect(request).toHaveBeenCalledWith("/scene/rooms", expect.objectContaining({ method: "POST" })));
    expect(JSON.parse(writes()[0][1].body)).toMatchObject({ identifier: "ROOM-002", name: "房间 002", width_m: 14 });
  });

  it("uses the current template and face only on click, skipping occupied and reserved U positions", async () => {
    renderForm("device");
    await waitFor(() => expect(screen.getByRole("button", { name: "采用空闲 U 位" })).toBeEnabled());
    fill("起始 U 位", "6");
    fireEvent.click(screen.getByRole("button", { name: "采用空闲 U 位" }));
    expect(screen.getByLabelText("起始 U 位")).toHaveValue("4");
    expect(screen.getByRole("status")).toHaveTextContent("已填入 U4–U5");
    fireEvent.mouseDown(screen.getByLabelText("安装面"));
    fireEvent.click(await screen.findByText("后侧", { selector: ".ant-select-item-option-content" }));
    expect(screen.getByLabelText("起始 U 位")).toHaveValue("4");
    expect(screen.queryByText(/已填入 U4/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "采用空闲 U 位" }));
    expect(screen.getByLabelText("起始 U 位")).toHaveValue("1");
    expect(writes()).toHaveLength(0);
  });

  it("keeps the entered U position when no continuous slot exists", async () => {
    const data = payload(); data.racks[0].reserved_units = [1, 3, 5, 7]; data.devices = [];
    renderForm("device", data);
    await waitFor(() => expect(screen.getByRole("button", { name: "采用空闲 U 位" })).toBeEnabled());
    fill("起始 U 位", "6");
    fireEvent.click(screen.getByRole("button", { name: "采用空闲 U 位" }));
    expect(screen.getByRole("status")).toHaveTextContent("没有连续 2U 的空闲位置");
    expect(screen.getByLabelText("起始 U 位")).toHaveValue("6");
    expect(writes()).toHaveLength(0);
  });

  it("focuses the first invalid field instead of submitting a blank form", async () => {
    renderForm("room");
    fireEvent.click(screen.getByRole("button", { name: "保存房间" }));
    await waitFor(() => expect(screen.getByLabelText("对象编号")).toHaveFocus());
    expect(screen.getByLabelText("对象编号").closest(".ant-form-item")).toHaveClass("ant-form-item-has-error");
    expect(writes()).toHaveLength(0);
  });
});
