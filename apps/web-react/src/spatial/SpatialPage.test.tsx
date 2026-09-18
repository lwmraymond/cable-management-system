import { useCallback, useEffect, useState } from "react";
import { App as AntApp } from "antd";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { InfrastructureContext } from "../api/context";
import type { LocationRecord } from "../types";
import { createApiClient } from "../api/client";
import SpatialPage from "./SpatialPage";

const request = vi.hoisted(() => vi.fn());
vi.mock("../api/client", () => ({ createApiClient: ({ getContext }: { getContext: () => InfrastructureContext }) => ({ request: (path: string, options?: RequestInit) => request(path, options, getContext()) }) }));
// Exercise the real entry, directory and context handoff without starting WebGL.
vi.mock("./SpatialWorkbench", () => ({ SpatialWorkbench: ({ getContext, onBrowse, scopePath }: { getContext: () => InfrastructureContext; onBrowse: (id?: string) => void; scopePath: string }) => {
  const [loaded, setLoaded] = useState("");
  const location = useLocation();
  useEffect(() => { const controller = new AbortController(); void createApiClient({ getContext }).request<string>("/scene", { signal: controller.signal }).then(value => { if (!controller.signal.aborted) setLoaded(value); }); return () => controller.abort(); }, [getContext]);
  return <main aria-label="所选3D场景"><p>{scopePath}</p><output aria-label="场景资料">{loaded}</output><output aria-label="场景地址">{location.search}</output><button onClick={() => onBrowse("building-a")}>切换楼层 / 房间</button></main>;
} }));
const context: InfrastructureContext = { tenantId: "tenant-a", actorId: "actor-a", projectId: "project-a", locationId: "floor-a1" };
const locations: LocationRecord[] = [
  { id: "building-a", parent_id: null, name: "A 栋", identifier: "A", location_type: "building" },
  { id: "floor-a1", parent_id: "building-a", name: "A 栋一层", identifier: "A-F1", location_type: "floor" },
  { id: "floor-a2", parent_id: "building-a", name: "A 栋二层", identifier: "A-F2", location_type: "floor" },
  { id: "room-a1", parent_id: "floor-a1", name: "一层机房", identifier: "A-F1-SR", location_type: "data_hall" },
  { id: "building-b", parent_id: null, name: "B 栋", identifier: "B", location_type: "building" },
  { id: "floor-b1", parent_id: "building-b", name: "B 栋一层", identifier: "B-F1", location_type: "floor" },
];
beforeAll(() => {
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
beforeEach(() => {
  request.mockReset().mockImplementation((path: string, _options: unknown, current: InfrastructureContext) => Promise.resolve(path === "/locations" ? locations : `${current.tenantId}/${current.locationId}`));
  const getStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation(element => getStyle(element));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
afterAll(() => vi.unstubAllGlobals());
function open(url = "/3d", initial = context) {
  function Harness() {
    const [current, setCurrent] = useState(initial);
    const getContext = useCallback(() => current, [current]);
    return <SpatialPage getContext={getContext} onContextChange={setCurrent} />;
  }
  return render(<AntApp><MemoryRouter initialEntries={[url]}><Harness /></MemoryRouter></AntApp>);
}
const scenes = () => request.mock.calls.filter(([path]) => path === "/scene");

describe("3D location entry", () => {
  it("requires explicit entry even with a saved floor, and building navigation never loads scene assets", async () => {
    open();
    const enter = await screen.findByRole("button", { name: "进入楼层3D" });
    await waitFor(() => expect(enter).not.toBeDisabled());
    expect(scenes()).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "上一级" }));
    fireEvent.click(screen.getByRole("button", { name: "选择楼层：A 栋二层" }));
    expect(scenes()).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "进入楼层3D" }));
    await waitFor(() => expect(screen.getByLabelText("场景资料")).toHaveTextContent("tenant-a/floor-a2"));
    expect(scenes().map(([, , current]) => current)).toEqual([{ ...context, locationId: "floor-a2" }]);
  });

  it("opens a building deep link as a directory instead of an aggregate scene", async () => {
    open("/3d?location=building-a");
    await screen.findByRole("button", { name: "选择楼层：A 栋一层" });
    expect(screen.getByRole("button", { name: "进入3D" })).toBeDisabled();
    expect(screen.queryByRole("main", { name: "所选3D场景" })).not.toBeInTheDocument();
    expect(scenes()).toHaveLength(0);
  });

  it.each([
    ["/3d?location=floor-b1", "floor-b1", "B 栋 / B 栋一层"],
    ["/3d?room=room-a1", "room-a1", "A 栋 / A 栋一层 / 一层机房"],
  ])("validates and restores an explicit space link %s before any scene read", async (url, target, path) => {
    open(url);
    await waitFor(() => expect(screen.getByLabelText("场景资料")).toHaveTextContent(`tenant-a/${target}`));
    expect(screen.getByText(path)).toBeInTheDocument();
    expect(scenes().map(([, , current]) => current.locationId)).toEqual([target]);
  });

  it("blocks unknown direct links without quietly entering the saved floor", async () => {
    open("/3d?location=unavailable-room");
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("指定位置不存在、未加载或无权访问"));
    expect(scenes()).toHaveLength(0);
    expect(screen.getByRole("button", { name: "进入楼层3D" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "返回空间选择" }));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(scenes()).toHaveLength(0);
  });

  it("retains a cable target while choosing a floor and returns to directory without expanding the scene", async () => {
    open("/3d?cable=cable-a");
    expect(await screen.findByText("先选择对象所在的楼层或房间")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "进入楼层3D" })).not.toBeDisabled());
    expect(scenes()).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "进入楼层3D" }));
    await waitFor(() => expect(screen.getByLabelText("场景资料")).toHaveTextContent("floor-a1"));
    expect(screen.getByLabelText("场景地址")).toHaveTextContent("cable=cable-a&location=floor-a1");
    fireEvent.click(screen.getByRole("button", { name: "切换楼层 / 房间" }));
    await screen.findByRole("button", { name: "选择楼层：A 栋二层" });
    expect(screen.queryByRole("main", { name: "所选3D场景" })).not.toBeInTheDocument();
    expect(scenes()).toHaveLength(1);
  });

  it("does not load a direct scene until a failed directory verification is retried successfully", async () => {
    request.mockRejectedValueOnce(new Error("位置读取暂时失败"));
    open("/3d?location=floor-a1");
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("位置读取暂时失败"));
    expect(scenes()).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: /重\s*试/ }));
    await waitFor(() => expect(screen.getByLabelText("场景资料")).toHaveTextContent("floor-a1"));
    expect(scenes()).toHaveLength(1);
  });
});
