import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ConnectionWorkbench, type RouteCandidate } from "./ConnectionWorkbench";
import type { SpatialPayload } from "./sceneData";
const request = vi.hoisted(() => vi.fn());
vi.mock("../api/client", async importOriginal => ({ ...await importOriginal<typeof import("../api/client")>(), createApiClient: () => ({ request }) }));
beforeAll(() => {
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
beforeEach(() => { request.mockReset(); });
afterEach(cleanup);
afterAll(() => vi.unstubAllGlobals());
const getContext = () => ({ tenantId: "tenant-a", actorId: "owner-a", projectId: "project-a" });
function payload(): SpatialPayload {
  return {
    scope: { tenant_id: "tenant-a", project_id: "project-a", location_id: null },
    locations: [{ id: "room-a", parent_id: null, identifier: "ROOM-A", name: "A 机房", location_type: "room", dimensions: { width_m: 10, depth_m: 8, height_m: 3.6 }, coordinates: {}, transform_3d: {} }],
    racks: ["a", "b"].map((side, index) => ({ id: `rack-${side}`, location_id: "room-a", rack_identifier: `RACK-${side}`, name: `机柜 ${side}`, height_u: 42, width_mm: 600, depth_mm: 1000, position_x: index ? 4 : 1, position_y: 1, position_z: 0, rotation: 0, reserved_units: [], status: "active" })),
    devices: ["a", "b", "c"].map(side => ({ id: `device-${side}`, rack_id: side === "a" ? "rack-a" : "rack-b", location_id: "room-a", identifier: `D-${side}`, name: `设备 ${side}`, device_type: "switch", start_u: 1, rack_units: 2, face: "front", status: "active" })),
    ports: ["a", "b", "c"].map(side => ({ id: `port-${side}`, device_id: `device-${side}`, identifier: `P-${side}`, label: `P-${side}`, connector_type: "RJ45", media_type: "copper", front_or_rear: "front", position_index: 1, status: "available", occupied: false })),
    pathways: ["a", "b"].map(side => ({ id: `path-${side}`, location_id: "room-a", identifier: `TRAY-${side}`, name: `桥架 ${side}`, type: "basket_tray", segments: [{ id: `segment-${side}`, sequence: 1, name: `路径段 ${side}`, length_m: 10, coordinates: [{ x: 1, y: 1, z: 3 }, { x: 4, y: 1, z: 3 }] }] })),
    cables: [], truncated: [],
  };
}
function candidate(id = "a", label = "推荐主路由"): RouteCandidate {
  return { id, label, segment_ids: [`segment-${id}`], segments: [{ id: `segment-${id}`, pathway_id: `path-${id}`, pathway_identifier: `TRAY-${id}`, name: `路径段 ${id}`, length_m: 10 }], length_m: 12.5, warnings: [] };
}
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(value => { resolve = value; }); return { promise, resolve }; }
function props() { return { payload: payload(), getContext, startPortId: "port-a", endPortId: "port-b", onClose: vi.fn(), onCreated: vi.fn(), onPreview: vi.fn() }; }
const confirm = () => screen.getByRole("button", { name: "确认连接并保存" });
function identifier(value = "CABLE-01") { fireEvent.change(screen.getByLabelText("线缆编号"), { target: { value } }); }

describe("connection preview and explicit confirmation", () => {
  it("only previews until explicit confirmation and saves the chosen route and endpoint pair", async () => {
    const input = props();
    request.mockResolvedValueOnce({ candidates: [candidate()], warnings: [] }).mockResolvedValueOnce({ id: "cable-new" });
    render(<ConnectionWorkbench {...input} />);
    await screen.findByText(/推荐主路由/);
    expect(request).toHaveBeenCalledTimes(1);
    expect(request).toHaveBeenCalledWith("/scene/routes/preview", expect.objectContaining({ method: "POST", body: JSON.stringify({ port_a_id: "port-a", port_b_id: "port-b", media_type: "Cat6A copper", excluded_pathway_ids: [] }) }));
    expect(input.onCreated).not.toHaveBeenCalled();
    await waitFor(() => expect(input.onPreview).toHaveBeenLastCalledWith(["segment-a"], { portA: "port-a", portB: "port-b" }));
    identifier(); fireEvent.click(confirm());
    await waitFor(() => expect(request).toHaveBeenLastCalledWith("/scene/cables", { method: "POST", body: JSON.stringify({ identifier: "CABLE-01", media_type: "Cat6A copper", construction: "patch_cord", port_a_id: "port-a", port_b_id: "port-b", project_id: "project-a", length_m: 12.5, route_segment_ids: ["segment-a"] }) }));
    expect(input.onCreated).toHaveBeenCalledExactlyOnceWith({ kind: "cable", id: "cable-new" });
    expect(input.onClose).toHaveBeenCalledTimes(1);
  });

  it("reports the save boundary so parent actions can remain disabled until the write completes", async () => {
    const input = props(); const onBusyChange = vi.fn(); const saving = deferred<{ id: string }>();
    request.mockResolvedValueOnce({ candidates: [candidate()], warnings: [] }).mockReturnValueOnce(saving.promise);
    render(<ConnectionWorkbench {...input} onBusyChange={onBusyChange} />);
    await screen.findByText(/推荐主路由/);
    identifier(); fireEvent.click(confirm());
    await waitFor(() => expect(onBusyChange).toHaveBeenLastCalledWith(true));
    expect(screen.getByRole("button", { name: "关闭接线工作区" })).toBeDisabled();
    saving.resolve({ id: "cable-new" });
    await waitFor(() => expect(onBusyChange).toHaveBeenLastCalledWith(false));
  });

  it("does not preview an occupied or media-incompatible initial endpoint", () => {
    const input = props(); input.payload.ports[0].occupied = true;
    render(<ConnectionWorkbench {...input} />);
    expect(screen.getByText("端口组合不可用")).toBeInTheDocument();
    expect(confirm()).toBeDisabled(); expect(request).not.toHaveBeenCalled();
  });

  it("ignores an old preview response after a new dragged endpoint arrives", async () => {
    const input = props();
    const old = deferred<{ candidates: RouteCandidate[]; warnings: string[] }>();
    const fresh = deferred<{ candidates: RouteCandidate[]; warnings: string[] }>();
    request.mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise);
    const mounted = render(<ConnectionWorkbench {...input} />);
    await waitFor(() => expect(request).toHaveBeenCalledTimes(1));
    mounted.rerender(<ConnectionWorkbench {...input} endPortId="port-c" />);
    await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
    expect(confirm()).toBeDisabled();
    expect(JSON.parse(request.mock.calls[1][1].body)).toEqual({ port_a_id: "port-a", port_b_id: "port-c", media_type: "Cat6A copper", excluded_pathway_ids: [] });
    fresh.resolve({ candidates: [candidate("b", "新端口路由")], warnings: [] });
    await screen.findByText(/新端口路由/);
    old.resolve({ candidates: [candidate("a", "过期路由")], warnings: [] });
    await waitFor(() => expect(input.onPreview).toHaveBeenLastCalledWith(["segment-b"], { portA: "port-a", portB: "port-c" }));
    expect(screen.queryByText(/过期路由/)).not.toBeInTheDocument();
    expect(confirm()).not.toBeDisabled(); expect(input.onCreated).not.toHaveBeenCalled();
  });

  it("invalidates manual route edits and requires a fresh successful validation, including explicit direct routing", async () => {
    const input = props();
    request.mockResolvedValueOnce({ candidates: [candidate()], warnings: [] }).mockResolvedValueOnce({ candidates: [{ id: "direct", label: "端口直连", segment_ids: [], segments: [], length_m: 3, warnings: ["未记录线槽路径"] }], warnings: [] });
    render(<ConnectionWorkbench {...input} />);
    await screen.findByText(/推荐主路由/);
    fireEvent.click(screen.getByRole("checkbox", { name: "手动调整线槽段顺序" }));
    expect(confirm()).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "移除路径段 1" }));
    expect(request).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: /校验当前走线/ }));
    await screen.findByText(/端口直连/);
    expect(JSON.parse(request.mock.calls[1][1].body)).toEqual({ port_a_id: "port-a", port_b_id: "port-b", media_type: "Cat6A copper", excluded_pathway_ids: [], route_segment_ids: [] });
    expect(confirm()).not.toBeDisabled(); expect(input.onCreated).not.toHaveBeenCalled();
    await waitFor(() => expect(input.onPreview).toHaveBeenLastCalledWith([], { portA: "port-a", portB: "port-b" }));
  });

  it("keeps the identifier and selected route after server rejection without announcing creation", async () => {
    const input = props();
    request.mockResolvedValueOnce({ candidates: [candidate()], warnings: [] }).mockRejectedValueOnce(new Error("One or more ports are already physically terminated"));
    render(<ConnectionWorkbench {...input} />);
    await screen.findByText(/推荐主路由/);
    identifier("CABLE-RETRY"); fireEvent.click(confirm());
    await screen.findByText(/端口已被占用，请关闭抽屉、刷新场景后重新选择/);
    expect(screen.getByLabelText("线缆编号")).toHaveValue("CABLE-RETRY");
    expect(screen.getByRole("radio", { name: /推荐主路由/ })).toBeChecked();
    expect(confirm()).toBeDisabled();
    expect(input.onCreated).not.toHaveBeenCalled(); expect(input.onClose).not.toHaveBeenCalled();
  });

  it("adds reserve once to the selected route, recalculates on route switches and sends only the rounded canonical total", async () => {
    const input = props();
    const alternative: RouteCandidate = { ...candidate("b", "备用路由"), length_m: 15.12345 };
    request.mockResolvedValueOnce({ candidates: [candidate(), alternative], warnings: [] }).mockResolvedValueOnce({ id: "cable-with-reserve" });
    render(<ConnectionWorkbench {...input} />);
    await screen.findByText(/推荐主路由/);
    const reserve = screen.getByRole("spinbutton", { name: "预留长度（m）" });
    expect(reserve).toHaveAttribute("aria-valuenow", "0");
    fireEvent.change(reserve, { target: { value: "2.5" } });
    const summary = within(screen.getByRole("region", { name: "当前路线长度估算" }));
    expect(await summary.findByText("15 m")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: /备用路由/ }));
    expect(await summary.findByText("17.623 m")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: /推荐主路由/ }));
    expect(await summary.findByText("15 m")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: /备用路由/ }));
    expect(await summary.findByText("17.623 m")).toBeInTheDocument();
    expect(reserve).toHaveAttribute("aria-valuenow", "2.5");
    expect(request).toHaveBeenCalledTimes(1);
    identifier("CABLE-RESERVE"); fireEvent.click(confirm());
    await waitFor(() => expect(request).toHaveBeenLastCalledWith("/scene/cables", { method: "POST", body: JSON.stringify({ identifier: "CABLE-RESERVE", media_type: "Cat6A copper", construction: "patch_cord", port_a_id: "port-a", port_b_id: "port-b", project_id: "project-a", length_m: 17.623, route_segment_ids: ["segment-b"] }) }));
    expect(input.onCreated).toHaveBeenCalledExactlyOnceWith({ kind: "cable", id: "cable-with-reserve" });
  });

  it("saves positive reserve even when the candidate has zero geometric length", async () => {
    const input = props();
    request.mockResolvedValueOnce({ candidates: [{ id: "direct", label: "同位置端口", segment_ids: [], segments: [], length_m: 0, warnings: [] }], warnings: [] }).mockResolvedValueOnce({ id: "reserved-direct" });
    render(<ConnectionWorkbench {...input} />);
    await screen.findByText(/同位置端口/);
    fireEvent.change(screen.getByRole("spinbutton", { name: "预留长度（m）" }), { target: { value: "2.5" } });
    identifier("CABLE-DIRECT-RESERVE"); fireEvent.click(confirm());
    await waitFor(() => expect(request).toHaveBeenLastCalledWith("/scene/cables", { method: "POST", body: JSON.stringify({ identifier: "CABLE-DIRECT-RESERVE", media_type: "Cat6A copper", construction: "patch_cord", port_a_id: "port-a", port_b_id: "port-b", project_id: "project-a", length_m: 2.5, route_segment_ids: [] }) }));
  });

});


it("creates a project in an empty workspace, preserves the draft and saves the connection into it", async () => {
  const input = props();
  const unscoped = () => ({ tenantId: "tenant-a", actorId: "owner-a" });
  request.mockImplementation(async (path: string, options?: { method?: string }) => {
    if (path === "/projects" && options?.method === "POST") return { id: "new-project", project_number: "HPC-NET-01", name: "超算网络", status: "active" };
    if (path === "/projects") return { projects: [], can_create: true, truncated: false };
    if (path === "/scene/routes/preview") return { candidates: [candidate()], warnings: [] };
    if (path === "/scene/cables") return { id: "cable-new" };
    throw new Error("Unexpected path " + path);
  });
  render(<ConnectionWorkbench {...input} getContext={unscoped} />);
  await screen.findByText(/推荐主路由/);
  identifier("HPC-FIBER-01");
  expect(confirm()).toBeDisabled();
  fireEvent.click(await screen.findByRole("button", { name: "新建项目" }));
  fireEvent.change(screen.getByLabelText("项目编号"), { target: { value: "HPC-NET-01" } });
  fireEvent.change(screen.getByLabelText("项目名称"), { target: { value: "超算网络" } });
  fireEvent.click(screen.getByRole("button", { name: "创建并选择" }));
  await waitFor(() => expect(confirm()).not.toBeDisabled());
  expect(screen.getByLabelText("线缆编号")).toHaveValue("HPC-FIBER-01");
  fireEvent.click(confirm());
  await waitFor(() => expect(input.onCreated).toHaveBeenCalledExactlyOnceWith({ kind: "cable", id: "cable-new" }));
  const body = JSON.parse(request.mock.calls.find(call => call[0] === "/scene/cables")![1].body);
  expect(body).toMatchObject({ project_id: "new-project", identifier: "HPC-FIBER-01", port_a_id: "port-a", port_b_id: "port-b" });
});


it("previews and saves the exact partial tray range with reserve included once", async () => {
  const input = props();
  const range = { segment_id: "segment-a", start_offset_m: 2, end_offset_m: 4.5, geometry_hash: "a".repeat(64) };
  const partial: RouteCandidate = { ...candidate(), route_portions: [range], length_m: 3.25,
    segments: [{ ...candidate().segments[0], length_m: 2.5, full_length_m: 10, start_offset_m: 2, end_offset_m: 4.5 }] };
  request.mockResolvedValueOnce({ candidates: [partial], warnings: [] }).mockResolvedValueOnce({ id: "partial-cable" });
  render(<ConnectionWorkbench {...input} />);
  await screen.findByText("使用 2.50 / 全段 10.00 m");
  expect(screen.getByText(/接入 2 m → 离开 4.5 m/)).toBeInTheDocument();
  await waitFor(() => expect(input.onPreview).toHaveBeenLastCalledWith(["segment-a"], { portA: "port-a", portB: "port-b" }, [range]));
  fireEvent.change(screen.getByRole("spinbutton", { name: "预留长度（m）" }), { target: { value: "2" } });
  identifier("PARTIAL-01"); fireEvent.click(confirm());
  await waitFor(() => expect(input.onCreated).toHaveBeenCalledExactlyOnceWith({ kind: "cable", id: "partial-cable" }));
  const body = JSON.parse(request.mock.calls.find(call => call[0] === "/scene/cables")![1].body);
  expect(body).toMatchObject({ route_segment_ids: ["segment-a"], route_portions: [range], length_m: 5.25 });
});
