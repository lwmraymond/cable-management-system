import { useCallback, useState } from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { InfrastructureContext } from "../api/context";
import type { LocationRecord } from "../types";
import { LocationsPage } from "./LocationsPage";

const request = vi.hoisted(() => vi.fn());
vi.mock("../api/client", () => ({ createApiClient: () => ({ request }) }));
const context: InfrastructureContext = { tenantId: "tenant-a", actorId: "operator-a", projectId: "project-a", locationId: "previous-scope" };
const getContext = () => context;
const locations: LocationRecord[] = [
  { id: "campus-a", parent_id: null, identifier: "CAMPUS-A", name: "总部园区", location_type: "campus" },
  { id: "building-a", parent_id: "campus-a", identifier: "BLDG-A", name: "主楼", location_type: "building" },
  { id: "room-a", parent_id: "building-a", identifier: "DC-NORTH-01", name: "北区核心机房与运营维护中心", location_type: "data_hall" },
  { id: "room-b", parent_id: "hidden-building", identifier: "TR-B-01", name: "分支电信间", location_type: "tr" },
  { id: "zone-a", identifier: "CUSTOM-ZONE", name: "待核对分区", location_type: "custom_zone" },
];
beforeAll(() => {
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
beforeEach(() => {
  request.mockReset().mockResolvedValue(locations);
  const getStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation(element => getStyle(element));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
afterAll(() => vi.unstubAllGlobals());
function openPage() { return render(<MemoryRouter><LocationsPage getContext={getContext} onContextChange={vi.fn()} /></MemoryRouter>); }
function row(identifier: string) {
  const location = locations.find(item => item.identifier === identifier)!;
  return within(screen.getByRole("button", { name: ["room", "tr", "data_hall", "floor"].includes(location.location_type) ? `在 3D 中查看 ${location.name}` : `选择 ${location.name} 下的楼层或房间` }).closest("tr")!);
}
function destination(current: InfrastructureContext) {
  return function Destination() {
    const location = useLocation();
    return <output aria-label="目标场景">{JSON.stringify({ pathname: location.pathname, search: location.search, context: current })}</output>;
  };
}

describe("locations directory", () => {
  it("combines name/identifier search with type filters and resolves parents from all loaded locations", async () => {
    openPage();
    await screen.findByText("DC-NORTH-01");
    expect(screen.getByText("已加载位置")).toHaveTextContent("5");
    expect(screen.getByText("筛选结果")).toHaveTextContent("5");
    expect(row("CAMPUS-A").getByText("顶层位置")).toBeInTheDocument();
    expect(row("TR-B-01").getByText("上级位置未加载")).toBeInTheDocument();
    expect(row("CUSTOM-ZONE").getByText("上级关系未提供")).toBeInTheDocument();
    expect(row("CUSTOM-ZONE").getByText("其他类型 · custom_zone")).toBeInTheDocument();

    fireEvent.change(screen.getByRole("textbox", { name: "搜索位置" }), { target: { value: "  dc-NORTH-01  " } });
    expect(screen.getByText("筛选结果")).toHaveTextContent("1");
    expect(row("DC-NORTH-01").getByText("主楼")).toBeInTheDocument();
    expect(row("DC-NORTH-01").getByText("BLDG-A")).toBeInTheDocument();
    expect(screen.queryByText("CAMPUS-A")).not.toBeInTheDocument();
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "位置类型" }));
    fireEvent.click(screen.getByText("楼宇"));
    expect(screen.getByText("筛选结果")).toHaveTextContent("0");
    expect(screen.getByText("没有符合条件的位置")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重置筛选" }));
    expect(screen.getByText("筛选结果")).toHaveTextContent("5");
    fireEvent.change(screen.getByRole("textbox", { name: "搜索位置" }), { target: { value: "核心机房" } });
    expect(row("DC-NORTH-01").getByText("北区核心机房与运营维护中心")).toBeInTheDocument();
    expect(screen.getByText("筛选结果")).toHaveTextContent("1");
    expect(request).toHaveBeenCalledTimes(1);
  });

  it.each([
    { id: "room-a", name: "北区核心机房与运营维护中心", search: "?location=room-a", enters: true },
    { id: "building-a", name: "主楼", search: "?browse=building-a", enters: false },
  ])("opens $id as a scene or directory without broadening to a whole building scene", async ({ id, name, search, enters }) => {
    function Harness() {
      const [current, setCurrent] = useState(context);
      const read = useCallback(() => current, [current]);
      const Destination = destination(current);
      return <Routes><Route path="/locations" element={<LocationsPage getContext={read} onContextChange={setCurrent} />} /><Route path="/3d" element={<Destination />} /></Routes>;
    }
    render(<MemoryRouter initialEntries={["/locations"]}><Harness /></MemoryRouter>);
    const action = await screen.findByRole("button", { name: enters ? `在 3D 中查看 ${name}` : `选择 ${name} 下的楼层或房间` });
    await waitFor(() => expect(action).not.toBeDisabled());
    fireEvent.click(action);
    expect(JSON.parse(screen.getByLabelText("目标场景").textContent!)).toEqual({ pathname: "/3d", search, context: enters ? { ...context, locationId: id } : context });
    expect(request.mock.calls.every(([path, options]) => path === "/locations" && (!options.method || options.method === "GET"))).toBe(true);
  });

  it("retries an initial error and blocks stale row actions after a later refresh fails", async () => {
    request.mockRejectedValueOnce(new Error("位置服务暂时不可用")).mockResolvedValueOnce(locations).mockRejectedValueOnce(new Error("刷新连接中断"));
    openPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("位置服务暂时不可用");
    expect(screen.queryByText("当前可见范围暂无位置记录")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /重\s*试/ }));
    await screen.findByText("DC-NORTH-01");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "刷新位置" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("刷新连接中断"));
    expect(screen.getByRole("alert")).toHaveTextContent("当前列表为上次读取的资料");
    expect(screen.getByText("DC-NORTH-01")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "在 3D 中查看 北区核心机房与运营维护中心" })).toBeDisabled();
    expect(request.mock.calls.map(([path]) => path)).toEqual(["/locations", "/locations", "/locations"]);
  });

  it("shows a true empty inventory without inventing a count or parent relationship", async () => {
    request.mockResolvedValueOnce([]);
    openPage();
    await screen.findByText("当前可见范围暂无位置记录");
    expect(screen.getByText("已加载位置")).toHaveTextContent("0");
    expect(screen.getByText("筛选结果")).toHaveTextContent("0");
    expect(screen.queryByText("没有符合条件的位置")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^在 3D 中查看/ })).not.toBeInTheDocument();
  });
});
