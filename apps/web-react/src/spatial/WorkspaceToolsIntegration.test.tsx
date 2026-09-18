import type { ComponentProps } from "react";
import { App as AntApp } from "antd";
import { MemoryRouter } from "react-router-dom";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { SpatialWorkbench as SpatialPage } from "./SpatialWorkbench";
import type { SceneData, ScenePlacement, SceneMeasurementPoint, Selection } from "./render/sceneRenderer";
import type { SpatialPayload } from "./sceneData";
import type { SceneCreateDrawer } from "./SceneCreateDrawer";

const request = vi.hoisted(() => vi.fn());
const renderer = vi.hoisted(() => ({
  created: vi.fn(), placement: vi.fn(), pan: vi.fn(), connection: vi.fn(), focus: vi.fn(), measurementMode: vi.fn(), measurement: vi.fn(),
  onSelect: undefined as ((value: Selection) => void) | undefined,
  onPlacement: undefined as ((value: ScenePlacement | null) => void) | undefined,
  onMeasure: undefined as ((value: SceneMeasurementPoint | null) => void) | undefined,
  data: undefined as SceneData | undefined,
}));
vi.mock("../api/client", () => ({ createApiClient: () => ({ request }) }));
// Keep the real Canvas and its callback/mode effects; replace only WebGL.
vi.mock("./render/sceneRenderer", () => ({ InfrastructureScene: class {
  constructor(_canvas: HTMLCanvasElement, onSelect: (selection: Selection) => void, options: { onPlacement: (value: ScenePlacement | null) => void; onMeasure: (value: SceneMeasurementPoint | null) => void }) { renderer.created(); renderer.onSelect = onSelect; renderer.onPlacement = options.onPlacement; renderer.onMeasure = options.onMeasure; }
  setData(data: SceneData) { renderer.data = data; }
  setPlacementMode(value: boolean) { renderer.placement(value); }
  setPanMode(value: boolean) { renderer.pan(value); }
  setConnectionMode(value: boolean) { renderer.connection(value); }
  setMeasurementMode(value: boolean) { renderer.measurementMode(value); }
  setMeasurement(points: SceneMeasurementPoint[]) { renderer.measurement(points); }
  select() {} setLayers() {} focus(id?: string) { renderer.focus(id); } zoom() {} setView() {} dispose() {}
} }));
// The creation form has its own request tests; capture this page's chosen target and preset.
vi.mock("./SceneCreateDrawer", () => ({ SceneCreateDrawer: ({ kind, locationId, rackId, preset, onClose }: ComponentProps<typeof SceneCreateDrawer>) => kind && <section role="dialog" aria-label="安装参数"><output data-testid="creation-target">{JSON.stringify({ kind, locationId, rackId, preset })}</output><button onClick={onClose}>取消测试安装</button></section> }));
beforeAll(() => {
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
beforeEach(() => {
  vi.clearAllMocks(); request.mockReset(); renderer.data = undefined;
  const getStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation(element => getStyle(element));
  request.mockImplementation((path: string) => {
    if (path === "/scene") return Promise.resolve(scene());
    if (path === "/scene/routes/preview") return Promise.resolve({ candidates: [{ id: "direct", label: "端口直连预览", segment_ids: [], segments: [], length_m: 8.5, warnings: [] }], warnings: [] });
    throw new Error(`Unexpected request ${path}`);
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
afterAll(() => vi.unstubAllGlobals());
const getContext = () => ({ tenantId: "tenant-a", actorId: "owner-a", projectId: "project-a" });
function scene(): SpatialPayload {
  return {
    scope: { tenant_id: "tenant-a", project_id: "project-a", location_id: null },
    locations: ["a", "b"].map(side => ({ id: `room-${side}`, parent_id: null, identifier: `ROOM-${side}`, name: `房间 ${side.toUpperCase()}`, version: 4, location_type: "room", dimensions: { width_m: 8, depth_m: 6, height_m: 3.6 }, coordinates: {}, transform_3d: {} })),
    racks: ["a", "b"].map(side => ({ id: `rack-${side}`, location_id: `room-${side}`, rack_identifier: `RACK-${side}`, name: `机柜 ${side.toUpperCase()}`, height_u: 42, width_mm: 600, depth_mm: 1000, position_x: 1, position_y: 1, position_z: 0, rotation: 0, reserved_units: [], status: "active" })),
    devices: ["a", "b", "blocked"].map(side => ({ id: `device-${side}`, rack_id: side === "b" ? "rack-b" : "rack-a", location_id: side === "b" ? "room-b" : "room-a", identifier: `DEV-${side}`, name: `设备 ${side.toUpperCase()}`, device_type: "switch", start_u: side === "blocked" ? 3 : 1, rack_units: 2, face: "front", status: "active", cable_policy: { allows_cables: side !== "blocked", allowed_media: ["copper", "fiber"] } })),
    ports: [
      ...["a", "b"].flatMap(side => [
        { id: `copper-${side}`, device_id: `device-${side}`, identifier: "ETH1", label: "ETH1", connector_type: "RJ45", media_type: "copper", front_or_rear: "front", position_index: 1, status: "available", occupied: false },
        { id: `fiber-${side}`, device_id: `device-${side}`, identifier: "LC1", label: "LC1", connector_type: "LC", media_type: "fiber", front_or_rear: "front", position_index: 2, status: "available", occupied: false },
        { id: `occupied-${side}`, device_id: `device-${side}`, identifier: "USED", label: "USED", connector_type: "RJ45", media_type: "copper", front_or_rear: "front", position_index: 3, status: "available", occupied: true },
      ]),
      { id: "copper-a2", device_id: "device-a", identifier: "ETH2", label: "ETH2", connector_type: "RJ45", media_type: "copper", front_or_rear: "front", position_index: 4, status: "available", occupied: false },
      { id: "blocked-port", device_id: "device-blocked", identifier: "BLOCKED", label: "BLOCKED", connector_type: "RJ45", media_type: "copper", front_or_rear: "front", position_index: 1, status: "available", occupied: false },
    ],
    pathways: [], cables: [], truncated: [],
  };
}
async function openWorkspace(onBrowse?: (id?: string) => void) {
  render(<AntApp><MemoryRouter><SpatialPage getContext={getContext} onBrowse={onBrowse} /></MemoryRouter></AntApp>);
  await waitFor(() => expect(screen.getByRole("button", { name: "安装服务器" })).not.toBeDisabled());
}
function canvas() { return screen.getByLabelText(/^三维(?:空间|放置模式|平移模式|端口连接模式|图示测距)/); }
function chooseObject(value: Selection) { act(() => renderer.onSelect!(value)); }
function place(value: ScenePlacement | null) { act(() => renderer.onPlacement!(value)); }
function measure(value: SceneMeasurementPoint | null) { act(() => renderer.onMeasure!(value)); }
function previewRequests() { return request.mock.calls.filter(([path]) => path === "/scene/routes/preview").map(([, options]) => JSON.parse(options.body)); }

describe("workspace tool integration", () => {
  it("returns to the location picker without a broader scene request", async () => {
    const browse = vi.fn();
    await openWorkspace(browse);
    fireEvent.click(screen.getByRole("button", { name: /切换楼层 \/ 房间/ }));
    expect(browse).toHaveBeenCalledTimes(1);
    expect(request.mock.calls.map(([path]) => path)).toEqual(["/scene"]);
  });

  it("keeps a connection draft until abandoning it to switch spaces is confirmed", async () => {
    const browse = vi.fn();
    await openWorkspace(browse);
    fireEvent.click(screen.getByRole("button", { name: "安装铜缆" }));
    fireEvent.change(screen.getByLabelText("线缆编号"), { target: { value: "KEEP-DRAFT" } });
    fireEvent.click(screen.getByRole("button", { name: /切换楼层 \/ 房间/ }));
    expect(browse).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: "继续编辑" }));
    expect(screen.getByLabelText("线缆编号")).toHaveValue("KEEP-DRAFT");
    expect(browse).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /切换楼层 \/ 房间/ }));
    fireEvent.click(await screen.findByRole("button", { name: "放弃草稿并切换" }));
    await waitFor(() => expect(browse).toHaveBeenCalledTimes(1));
    expect(request.mock.calls.filter(([path]) => path === "/scene/cables")).toHaveLength(0);
  });
  it("keeps browser shortcuts and text input separate from canvas shortcuts", async () => {
    await openWorkspace();
    const view = canvas();
    for (const modifier of [{ ctrlKey: true }, { metaKey: true }, { altKey: true }, { isComposing: true }, { keyCode: 229 }]) {
      expect(fireEvent.keyDown(view, { key: "f", ...modifier })).toBe(true);
    }
    expect(renderer.focus).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "安装铜缆" }));
    const identifier = screen.getByLabelText("线缆编号");
    fireEvent.keyDown(identifier, { key: "f" });
    expect(renderer.focus).not.toHaveBeenCalled();
    expect(fireEvent.keyDown(view, { key: "f" })).toBe(false);
    expect(renderer.focus).toHaveBeenCalledTimes(1);
  });

  it("arms installation, requires a rack for hardware, and forwards the clicked rack and local coordinates to creation", async () => {
    await openWorkspace();
    fireEvent.click(screen.getByRole("button", { name: "安装服务器" }));
    expect(renderer.placement).toHaveBeenLastCalledWith(true);
    expect(screen.queryByRole("dialog", { name: "安装参数" })).not.toBeInTheDocument();
    place({ locationId: "room-b", positionX: 3.215, positionY: 2.345 });
    expect(screen.queryByRole("dialog", { name: "安装参数" })).not.toBeInTheDocument();
    place({ locationId: "room-b", positionX: 3.215, positionY: 2.345, rackId: "rack-b", startU: 34 });
    expect(JSON.parse(screen.getByTestId("creation-target").textContent!)).toEqual({ kind: "device", locationId: "room-b", rackId: "rack-b", preset: { values: { location_id: "room-b", position_x: 3.22, position_y: 2.35, rack_id: "rack-b", start_u: 34 }, hardware: "server", dropped: true } });
    expect(renderer.placement).toHaveBeenLastCalledWith(false);
    fireEvent.click(screen.getByRole("button", { name: "取消测试安装" }));
    expect(screen.getByRole("button", { name: "选择工具" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("dialog", { name: "安装参数" })).not.toBeInTheDocument();
    expect(request.mock.calls.map(([path]) => path)).toEqual(["/scene"]);
  });

  it("leaves placement with Escape or selection and switches pan without recreating the canvas engine", async () => {
    await openWorkspace();
    fireEvent.click(screen.getByRole("button", { name: "安装机柜" }));
    expect(renderer.placement).toHaveBeenLastCalledWith(true);
    fireEvent.keyDown(canvas(), { key: "Escape" });
    expect(renderer.placement).toHaveBeenLastCalledWith(false);
    expect(screen.getByRole("button", { name: "选择工具" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "安装线槽 / Tray" }));
    expect(renderer.placement).toHaveBeenLastCalledWith(true);
    fireEvent.click(screen.getByRole("button", { name: "选择工具" }));
    expect(renderer.placement).toHaveBeenLastCalledWith(false);
    fireEvent.click(screen.getByRole("button", { name: "平移工具" }));
    expect(renderer.pan).toHaveBeenLastCalledWith(true);
    expect(renderer.connection).toHaveBeenLastCalledWith(false);
    fireEvent.keyDown(canvas(), { key: "Escape" });
    expect(renderer.pan).toHaveBeenLastCalledWith(false);
    expect(renderer.created).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog", { name: "安装参数" })).not.toBeInTheDocument();
  });

  it.each([
    { name: "铜缆", family: "copper", media: "Cat6A copper", port: "ETH1", availableA: ["选择接口 DEV-a ETH1", "选择接口 DEV-a ETH2"] },
    { name: "光纤", family: "fiber", media: "OS2 fiber", port: "LC1", availableA: ["选择接口 DEV-a LC1"] },
  ])("opens $name connection and chooses compatible free A/B ports through real object pickers", async ({ name, family, media, port, availableA }) => {
    await openWorkspace();
    const toolbox = within(screen.getByRole("region", { name: "工具箱" }));
    const stage = within(screen.getByRole("region", { name: "三维视图" }));
    fireEvent.click(toolbox.getByRole("button", { name: `安装${name}` }));
    const workbenchElement = screen.getByRole("complementary", { name: "端口接线工作区" });
    const workbench = within(workbenchElement);
    expect(renderer.connection).toHaveBeenLastCalledWith(true);
    chooseObject({ kind: "rack", id: "rack-a" });
    const pickerA = within(stage.getByRole("complementary", { name: "选择 A 端接口" }));
    expect(pickerA.getAllByRole("button", { name: /^选择接口/ }).map(button => button.getAttribute("aria-label"))).toEqual(availableA);
    fireEvent.click(pickerA.getByRole("button", { name: `选择接口 DEV-a ${port}` }));
    expect(stage.queryByRole("complementary", { name: "选择 A 端接口" })).not.toBeInTheDocument();
    chooseObject({ kind: "device", id: "device-a" });
    const sameDevicePicker = within(stage.getByRole("complementary", { name: "选择 B 端接口" }));
    expect(sameDevicePicker.queryByRole("button", { name: `选择接口 DEV-a ${port}` })).not.toBeInTheDocument();
    fireEvent.click(sameDevicePicker.getByRole("button", { name: "关闭接口选择" }));
    chooseObject({ kind: "device", id: "device-b" });
    fireEvent.click(within(stage.getByRole("complementary", { name: "选择 B 端接口" })).getByRole("button", { name: `选择接口 DEV-b ${port}` }));
    await waitFor(() => expect(previewRequests()).toEqual([{ port_a_id: `${family}-a`, port_b_id: `${family}-b`, media_type: media, excluded_pathway_ids: [] }]));
    await workbench.findByText(/端口直连预览/);
    await waitFor(() => expect(renderer.data?.paths.some(path => path.draft)).toBe(true));
    expect(renderer.data?.rooms.map(room => room.id)).toEqual(["room-a", "room-b"]);
    expect(request.mock.calls.some(([path]) => path === "/scene/cables")).toBe(false);
    fireEvent.click(toolbox.getByRole("button", { name: "选择工具" }));
    expect(workbenchElement).not.toBeInTheDocument();
    expect(renderer.connection).toHaveBeenLastCalledWith(false);
    expect(renderer.data?.paths.some(path => path.draft)).toBe(false);
  }, 10000); // Real Page + Ant form + two pickers; allow headroom under parallel test load.

  it("preserves a manually selected A endpoint when choosing B from the canvas device picker", async () => {
    await openWorkspace();
    fireEvent.click(screen.getByRole("button", { name: "安装铜缆" }));
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "A 端端口" }));
    fireEvent.click(await screen.findByText("机柜 A / 设备 A / ETH1 · RJ45 · 前侧"));
    expect(previewRequests()).toEqual([]);
    chooseObject({ kind: "rack", id: "rack-b" });
    fireEvent.click(within(screen.getByRole("complementary", { name: "选择 B 端接口" })).getByRole("button", { name: "选择接口 DEV-b ETH1" }));
    await waitFor(() => expect(previewRequests()).toEqual([{ port_a_id: "copper-a", port_b_id: "copper-b", media_type: "Cat6A copper", excluded_pathway_ids: [] }]));
    expect(screen.queryByText("端口组合不可用")).not.toBeInTheDocument();
    expect(request.mock.calls.some(([path]) => path === "/scene/cables")).toBe(false);
  });

  it("closes a stale picker after manual A selection and reopens it for a distinct B endpoint", async () => {
    await openWorkspace();
    fireEvent.click(screen.getByRole("button", { name: "安装铜缆" }));
    chooseObject({ kind: "rack", id: "rack-a" });
    expect(screen.getByRole("complementary", { name: "选择 A 端接口" })).toBeInTheDocument();
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "A 端端口" }));
    fireEvent.click(await screen.findByText("机柜 A / 设备 A / ETH1 · RJ45 · 前侧"));
    await waitFor(() => expect(screen.queryByRole("complementary", { name: "选择 A 端接口" })).not.toBeInTheDocument());
    chooseObject({ kind: "rack", id: "rack-a" });
    const pickerB = within(await screen.findByRole("complementary", { name: "选择 B 端接口" }));
    expect(pickerB.queryByRole("button", { name: "选择接口 DEV-a ETH1" })).not.toBeInTheDocument();
    fireEvent.click(pickerB.getByRole("button", { name: "选择接口 DEV-a ETH2" }));
    await waitFor(() => expect(previewRequests()).toEqual([{ port_a_id: "copper-a", port_b_id: "copper-a2", media_type: "Cat6A copper", excluded_pathway_ids: [] }]));
  });

});


describe("measurement integration", () => {
  it("keeps a completed polyline, resumes it, and converts its world coordinates into editable local tray points without writing", async () => {
    await openWorkspace();
    fireEvent.click(screen.getByRole("button", { name: "测距工具" }));
    const measurement = within(screen.getByRole("complementary", { name: "折线测距" }));
    expect(renderer.measurementMode).toHaveBeenLastCalledWith(true);
    const points: SceneMeasurementPoint[] = [
      { locationId: "room-b", point: [11.1234, 1.2, 1.4321] },
      { locationId: "room-b", point: [14.1234, 1.2, 5.4321] },
      { locationId: "room-b", point: [14.1234, 2.2, 5.4321] },
    ];
    measure(points[0]);
    expect(screen.getByLabelText("图示总长")).toHaveTextContent("—");
    measure(points[1]); measure(points[2]);
    expect(["图示总长", "水平投影距离", "累计高差"].map(label => screen.getByLabelText(label).textContent)).toEqual(["6.000 m", "5.000 m", "1.000 m"]);
    expect(renderer.measurement).toHaveBeenLastCalledWith(points);
    fireEvent.click(measurement.getByRole("button", { name: "完成测距" }));
    expect(screen.getByText("测距已完成")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "选择工具" })).toHaveAttribute("aria-pressed", "true");
    expect(renderer.measurementMode).toHaveBeenLastCalledWith(false);
    expect(renderer.measurement).toHaveBeenLastCalledWith(points);
    fireEvent.click(screen.getByRole("button", { name: "对象列表" }));
    expect(screen.getByRole("complementary", { name: "折线测距" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "测距工具" })).toBeInTheDocument();
    fireEvent.click(measurement.getByRole("button", { name: "继续取点" }));
    expect(renderer.measurementMode).toHaveBeenLastCalledWith(true);
    expect(screen.getByLabelText("图示总长")).toHaveTextContent("6.000 m");
    fireEvent.click(measurement.getByRole("button", { name: "用于创建线槽" }));
    expect(JSON.parse(screen.getByTestId("creation-target").textContent!)).toEqual({
      kind: "pathway", locationId: "room-b", rackId: "rack-b", preset: {
        values: { location_id: "room-b", points: [{ x: 1.123, y: 1.432, z: 1.2 }, { x: 4.123, y: 5.432, z: 1.2 }, { x: 4.123, y: 5.432, z: 2.2 }] },
        warning: "已带入测距折线。请确认每个点的实际 X/Y 与线槽高度，再保存线槽。",
      },
    });
    expect(renderer.measurementMode).toHaveBeenLastCalledWith(false);
    expect(renderer.measurement).toHaveBeenLastCalledWith([]);
    expect(screen.queryByRole("complementary", { name: "折线测距" })).not.toBeInTheDocument();
    expect(request.mock.calls.map(([path]) => path)).toEqual(["/scene"]);
    fireEvent.click(screen.getByRole("button", { name: "取消测试安装" }));
    expect(screen.queryByRole("dialog", { name: "安装参数" })).not.toBeInTheDocument();
    expect(screen.queryByRole("complementary", { name: "折线测距" })).not.toBeInTheDocument();
    expect(renderer.measurement).toHaveBeenLastCalledWith([]);
    expect(request.mock.calls.map(([path]) => path)).toEqual(["/scene"]);
  }, 10000);

  it("rejects cross-room and invalid samples, ignores duplicate clicks, and clears all temporary points with Escape", async () => {
    await openWorkspace();
    fireEvent.click(screen.getByRole("button", { name: "测距工具" }));
    const first: SceneMeasurementPoint = { locationId: "room-a", point: [1, 0, 1] };
    measure(first);
    measure({ locationId: "room-b", point: [11, 0, 1] });
    await screen.findByText("不同房间采用示意布局，不能直接跨房间测距。请先清空当前测距。");
    measure({ locationId: "room-a", point: [1.0001, 0, 1] });
    measure({ locationId: "room-a", point: [Number.NaN, 0, 1] });
    measure(null);
    expect(renderer.measurement).toHaveBeenLastCalledWith([first]);
    expect(screen.getByLabelText("图示总长")).toHaveTextContent("—");
    measure({ locationId: "room-a", point: [4, 0, 5] });
    expect(screen.getByLabelText("图示总长")).toHaveTextContent("5.000 m");
    fireEvent.keyDown(canvas(), { key: "Escape" });
    expect(renderer.measurement).toHaveBeenLastCalledWith([]);
    expect(renderer.measurementMode).toHaveBeenLastCalledWith(false);
    expect(screen.queryByRole("complementary", { name: "折线测距" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "选择工具" })).toHaveAttribute("aria-pressed", "true");
    expect(request.mock.calls.map(([path]) => path)).toEqual(["/scene"]);
  });

  it("keeps placement, measuring, connecting and panning mutually exclusive and discards samples when switching tools", async () => {
    await openWorkspace();
    const toolbox = within(screen.getByRole("region", { name: "工具箱" }));
    fireEvent.click(toolbox.getByRole("button", { name: "安装服务器" }));
    expect(renderer.placement).toHaveBeenLastCalledWith(true);
    fireEvent.click(toolbox.getByRole("button", { name: "测距工具" }));
    expect(renderer.placement).toHaveBeenLastCalledWith(false);
    expect(renderer.measurementMode).toHaveBeenLastCalledWith(true);
    measure({ locationId: "room-a", point: [1, 0, 1] });
    fireEvent.click(toolbox.getByRole("button", { name: "选择工具" }));
    fireEvent.click(toolbox.getByRole("button", { name: "安装铜缆" }));
    expect(renderer.measurementMode).toHaveBeenLastCalledWith(false);
    expect(renderer.measurement).toHaveBeenLastCalledWith([]);
    expect(renderer.connection).toHaveBeenLastCalledWith(true);
    measure({ locationId: "room-a", point: [2, 0, 2] });
    expect(renderer.measurement).toHaveBeenLastCalledWith([]);
    expect(screen.queryByRole("complementary", { name: "折线测距" })).not.toBeInTheDocument();
    fireEvent.click(toolbox.getByRole("button", { name: "测距工具" }));
    expect(renderer.connection).toHaveBeenLastCalledWith(false);
    expect(screen.queryByRole("complementary", { name: "端口接线工作区" })).not.toBeInTheDocument();
    expect(renderer.measurementMode).toHaveBeenLastCalledWith(true);
    fireEvent.click(toolbox.getByRole("button", { name: "平移工具" }));
    expect(renderer.measurementMode).toHaveBeenLastCalledWith(false);
    expect(renderer.pan).toHaveBeenLastCalledWith(true);
    expect(screen.queryByRole("complementary", { name: "折线测距" })).not.toBeInTheDocument();
    expect(renderer.created).toHaveBeenCalledTimes(1);
    expect(request.mock.calls.map(([path]) => path)).toEqual(["/scene"]);
  }, 10000);
});


describe("workspace navigation and assistant", () => {
  it("ignores modified and IME Escape through both native canvas events and React bubbling, but ordinary Escape clears measurement", async () => {
    await openWorkspace();
    const tool = screen.getByRole("button", { name: "测距工具" });
    fireEvent.click(tool);
    const points: SceneMeasurementPoint[] = [{ locationId: "room-a", point: [1, 0, 1] }, { locationId: "room-a", point: [4, 0, 5] }];
    points.forEach(measure);
    const view = canvas();
    for (const modifier of [{ ctrlKey: true }, { metaKey: true }, { altKey: true }, { isComposing: true }, { keyCode: 229 }]) {
      // The native event's IME flags must survive React's wrapper and the parent handler.
      const nativeEvent = new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true, ...modifier });
      act(() => { expect(view.dispatchEvent(nativeEvent)).toBe(true); });
      expect(fireEvent.keyDown(tool, { key: "Escape", ...modifier })).toBe(true);
      expect(renderer.measurement).toHaveBeenLastCalledWith(points);
      expect(tool).toHaveAttribute("aria-pressed", "true");
    }
    expect(screen.getByLabelText("图示总长")).toHaveTextContent("5.000 m");
    expect(fireEvent.keyDown(tool, { key: "Escape" })).toBe(false);
    expect(renderer.measurement).toHaveBeenLastCalledWith([]);
    expect(renderer.measurementMode).toHaveBeenLastCalledWith(false);
    expect(screen.queryByRole("complementary", { name: "折线测距" })).not.toBeInTheDocument();
    expect(request.mock.calls.map(([path]) => path)).toEqual(["/scene"]);
  });

  it("focuses the selected rack when entering inspection and refits all rooms when returning to overview", async () => {
    await openWorkspace();
    chooseObject({ kind: "rack", id: "rack-a" });
    const stage = within(screen.getByRole("region", { name: "三维视图" }));
    fireEvent.click(stage.getByRole("radio", { name: "机柜视图" }));
    expect(renderer.focus).toHaveBeenLastCalledWith("rack-a");
    expect(renderer.data?.racks.map(rack => rack.id)).toEqual(["rack-a"]);
    fireEvent.click(stage.getByRole("radio", { name: "空间总览" }));
    expect(renderer.focus).toHaveBeenLastCalledWith(undefined);
    expect(renderer.data?.racks.map(rack => rack.id)).toEqual(["rack-a", "rack-b"]);
    expect(renderer.created).toHaveBeenCalledTimes(1);
  });

  it("offers retry after the initial scene fails without presenting an actionable empty-room creation state", async () => {
    request.mockRejectedValueOnce(new Error("场景暂时不可用"));
    render(<AntApp><MemoryRouter><SpatialPage getContext={getContext} /></MemoryRouter></AntApp>);
    await screen.findByText("场景暂时不可用");
    expect(screen.getByText("场景加载失败")).toBeInTheDocument();
    expect(screen.queryByText("从一个房间开始")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "创建房间" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "安装房间" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /重\s*试/ }));
    await waitFor(() => expect(renderer.data?.rooms.map(room => room.id)).toEqual(["room-a", "room-b"]));
    expect(screen.queryByText("场景加载失败")).not.toBeInTheDocument();
    expect(request.mock.calls.map(([path]) => path)).toEqual(["/scene", "/scene"]);
  });

  it("searches help, opens current-scene checks and reaches the actual room entrance editor without writing on cancel", async () => {
    await openWorkspace();
    fireEvent.click(screen.getByRole("button", { name: /使用指南/ }));
    const guide = within(screen.getByRole("dialog", { name: "3D 工作区教程" }));
    fireEvent.change(guide.getByRole("textbox", { name: "搜索教程" }), { target: { value: "检查当前场景" } });
    expect(guide.getByRole("heading", { name: "查线并核对长度来源" })).toBeInTheDocument();
    expect(guide.queryByRole("heading", { name: "建立房间与出入口" })).not.toBeInTheDocument();
    fireEvent.click(guide.getByRole("button", { name: "检查当前场景" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "3D 工作区教程" })).not.toBeInTheDocument());
    const checks = within(within(screen.getByRole("region", { name: "工作助手" })).getByRole("region", { name: "自动检查结果" }));
    const roomIssue = checks.getByText("房间 A · ROOM-a").closest("li")!;
    expect(within(roomIssue).getByRole("heading", { name: "出入口资料待完善" })).toBeInTheDocument();
    fireEvent.click(within(roomIssue).getByRole("button", { name: "配置出入口" }));
    const editor = within(await screen.findByRole("dialog", { name: "房间 A · 出入口" }));
    expect(editor.getByRole("textbox", { name: "出入口 1 名称" })).toHaveValue("出入口");
    expect(editor.getByRole("button", { name: "保存出入口" })).not.toBeDisabled();
    expect(renderer.focus).toHaveBeenLastCalledWith("room-a");
    fireEvent.change(editor.getByRole("textbox", { name: "出入口 1 名称" }), { target: { value: "待核对入口" } });
    fireEvent.click(editor.getByRole("button", { name: /取\s*消/ }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "房间 A · 出入口" })).not.toBeInTheDocument());
    expect(request.mock.calls.map(([path]) => path)).toEqual(["/scene"]);
  }, 10000);
});


describe("read-only sidebar navigation with unfinished work", () => {
  it("preserves the same connection form, endpoints and reserve through assistant and guide navigation without saving", async () => {
    await openWorkspace();
    const sidebar = within(screen.getByRole("button", { name: "工具箱" }).closest("aside")!);
    const stage = within(screen.getByRole("region", { name: "三维视图" }));
    fireEvent.click(sidebar.getByRole("button", { name: "安装铜缆" }));
    const workbenchElement = screen.getByRole("complementary", { name: "端口接线工作区" });
    const workbench = within(workbenchElement);
    const identifier = workbench.getByRole("textbox", { name: "线缆编号" });
    fireEvent.change(identifier, { target: { value: "CABLE-UNSAVED-DRAFT" } });
    chooseObject({ kind: "device", id: "device-a" });
    fireEvent.click(within(stage.getByRole("complementary", { name: "选择 A 端接口" })).getByRole("button", { name: "选择接口 DEV-a ETH1" }));
    chooseObject({ kind: "device", id: "device-b" });
    fireEvent.click(within(stage.getByRole("complementary", { name: "选择 B 端接口" })).getByRole("button", { name: "选择接口 DEV-b ETH1" }));
    await workbench.findByText(/端口直连预览/);
    const reserve = workbench.getByRole("spinbutton", { name: "预留长度（m）" });
    fireEvent.change(reserve, { target: { value: "2.5" } });

    fireEvent.click(sidebar.getByRole("button", { name: "工作助手" }));
    const assistant = within(sidebar.getByRole("region", { name: "工作助手" }));
    const checks = within(assistant.getByRole("region", { name: "自动检查结果" }));
    expect(assistant.getByText(/接线草稿已保留/)).toBeInTheDocument();
    for (const button of checks.getAllByRole("button", { name: "配置出入口" })) {
      expect(button).toBeDisabled();
      fireEvent.click(button);
    }
    expect(screen.queryByRole("dialog", { name: /出入口/ })).not.toBeInTheDocument();
    expect(workbenchElement).toBeInTheDocument();
    expect(identifier).toHaveValue("CABLE-UNSAVED-DRAFT");
    expect(reserve).toHaveAttribute("aria-valuenow", "2.5");

    for (const action of ["检查当前场景", "打开对象列表"]) {
      fireEvent.click(sidebar.getByRole("button", { name: /使用指南/ }));
      // rc-util uses the same ID for the form and Drawer title in NODE_ENV=test.
      const guideElement = screen.getByRole("dialog");
      const guide = within(guideElement);
      expect(guide.getByText("3D 工作区教程")).toBeInTheDocument();
      fireEvent.change(guide.getByRole("textbox", { name: "搜索教程" }), { target: { value: action } });
      fireEvent.click(guide.getByRole("button", { name: action }));
      await waitFor(() => expect(guideElement).not.toBeInTheDocument());
      expect(workbenchElement).toBeInTheDocument();
      expect(identifier).toHaveValue("CABLE-UNSAVED-DRAFT");
      expect(reserve).toHaveAttribute("aria-valuenow", "2.5");
      if (action === "检查当前场景") expect(sidebar.getByRole("region", { name: "工作助手" })).toBeInTheDocument();
      else expect(sidebar.getByRole("complementary", { name: "场景对象列表" })).toBeInTheDocument();
    }
    fireEvent.click(sidebar.getByRole("button", { name: "工具箱" }));
    expect(sidebar.getByRole("button", { name: "安装铜缆" })).toHaveAttribute("aria-pressed", "true");
    expect(workbench.getByText("机柜 A / 设备 A / ETH1 · RJ45 · 前侧")).toBeInTheDocument();
    expect(workbench.getByText("机柜 B / 设备 B / ETH1 · RJ45 · 前侧")).toBeInTheDocument();
    expect(workbench.getByRole("textbox", { name: "线缆编号" })).toBe(identifier);
    expect(workbench.getByRole("spinbutton", { name: "预留长度（m）" })).toBe(reserve);
    expect(renderer.data?.paths.some(path => path.draft)).toBe(true);
    expect(previewRequests()).toEqual([{ port_a_id: "copper-a", port_b_id: "copper-b", media_type: "Cat6A copper", excluded_pathway_ids: [] }]);
    expect(request.mock.calls.map(([path]) => path)).toEqual(["/scene", "/scene/routes/preview"]);
  }, 10000);

  it("temporarily hides measurement in the assistant and restores the same two points when returning to the toolbox", async () => {
    await openWorkspace();
    const sidebar = within(screen.getByRole("button", { name: "工具箱" }).closest("aside")!);
    fireEvent.click(sidebar.getByRole("button", { name: "测距工具" }));
    const points: SceneMeasurementPoint[] = [{ locationId: "room-a", point: [1, 0, 1] }, { locationId: "room-a", point: [4, 0, 5] }];
    points.forEach(measure);
    expect(sidebar.getByLabelText("图示总长")).toHaveTextContent("5.000 m");
    fireEvent.click(sidebar.getByRole("button", { name: "工作助手" }));
    expect(sidebar.getByRole("region", { name: "工作助手" })).toBeInTheDocument();
    expect(sidebar.queryByRole("complementary", { name: "折线测距" })).not.toBeInTheDocument();
    expect(renderer.measurement).toHaveBeenLastCalledWith(points);
    fireEvent.click(sidebar.getByRole("button", { name: "工具箱" }));
    expect(sidebar.getByRole("complementary", { name: "折线测距" })).toBeInTheDocument();
    expect(sidebar.getByLabelText("图示总长")).toHaveTextContent("5.000 m");
    expect(renderer.measurement).toHaveBeenLastCalledWith(points);
    expect(request.mock.calls.map(([path]) => path)).toEqual(["/scene"]);
  });
});


it("defers port geometry in overview but keeps both racks pickable while connecting", async () => {
  const input = scene();
  input.racks[1].location_id = "room-a";
  input.racks[1].position_x = 4;
  input.devices.find(device => device.id === "device-b")!.location_id = "room-a";
  request.mockImplementation(async (path: string) => {
    if (path === "/scene") return input;
    throw new Error("Unexpected request " + path);
  });
  await openWorkspace();
  await waitFor(() => expect(renderer.data?.racks).toHaveLength(2));
  const ports = () => renderer.data!.racks.flatMap(rack => rack.devices.flatMap(device => device.ports ?? [])).map(port => port.id);
  expect(ports()).toEqual([]);
  fireEvent.click(screen.getByRole("button", { name: "安装铜缆" }));
  expect(ports()).toEqual(expect.arrayContaining(["copper-a", "copper-b"]));
  chooseObject({ kind: "device", id: "device-a" });
  expect(ports()).toEqual(expect.arrayContaining(["copper-a", "copper-b"]));
  fireEvent.click(screen.getByRole("button", { name: "关闭接线工作区" }));
  expect(ports()).toEqual([]);
});
