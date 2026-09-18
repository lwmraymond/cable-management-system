import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { MemoryRouter } from "react-router-dom";
import type { LocationRecord } from "../types";
import { SpatialLocationPicker, type SpatialLocationPickerProps } from "./SpatialLocationPicker";

beforeAll(() => {
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
beforeEach(() => {
  const getStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation(element => getStyle(element));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
afterAll(() => vi.unstubAllGlobals());

const locations: LocationRecord[] = [
  { id: "campus-a", identifier: "HQ", name: "总部园区", location_type: "campus", parent_id: null },
  { id: "building-a", identifier: "BLD-A", name: "研发楼", location_type: "building", parent_id: "campus-a" },
  { id: "floor-a", identifier: "L02", name: "二层", location_type: "floor", parent_id: "building-a" },
  { id: "room-a", identifier: "TR-201", name: "东侧电信间", location_type: "tr", parent_id: "floor-a" },
  { id: "room-direct", identifier: "ER-A", name: "一楼独立设备间", location_type: "er", parent_id: "building-a" },
  { id: "floor-restricted", identifier: "L05", name: "受限范围五层", location_type: "floor", parent_id: "hidden-building" },
  { id: "room-restricted", identifier: "MDF-501", name: "主配线间", location_type: "mdf", parent_id: "floor-restricted" },
];
function props(overrides: Partial<SpatialLocationPickerProps> = {}): SpatialLocationPickerProps {
  return { locations, loading: false, onSelect: vi.fn(), onEnter: vi.fn(), onRetry: vi.fn(), ...overrides };
}
function ControlledPicker(input: SpatialLocationPickerProps) {
  const [selectedId, setSelectedId] = useState(input.selectedId);
  return <SpatialLocationPicker {...input} selectedId={selectedId} onSelect={id => { setSelectedId(id); input.onSelect(id); }} />;
}
const browser = () => within(screen.getByRole("region", { name: "选择位置" }));
const confirmation = () => within(screen.getByRole("region", { name: "确认进入 3D" }));
const choose = (name: string) => fireEvent.click(browser().getByRole("button", { name }));

describe("3D location entry picker", () => {
  it("navigates real direct relationships without inventing a floor and only enters on explicit confirmation", () => {
    const input = props();
    render(<MemoryRouter><ControlledPicker {...input} /></MemoryRouter>);
    expect(input.onSelect).not.toHaveBeenCalled();
    expect(input.onEnter).not.toHaveBeenCalled();
    expect(confirmation().getByRole("button", { name: "进入3D" })).toBeDisabled();
    expect(screen.getByRole("link", { name: "返回工作台" })).toHaveAttribute("href", "/");
    choose("选择园区：总部园区");
    expect(confirmation().getByRole("button", { name: "进入3D" })).toBeDisabled();
    choose("选择建筑：研发楼");
    expect(browser().getByRole("button", { name: "选择楼层：二层" })).toBeInTheDocument();
    expect(within(browser().getByRole("region", { name: "直属房间" })).getByRole("button", { name: "选择设备间（ER）：一楼独立设备间" })).toBeInTheDocument();
    expect(browser().queryByRole("button", { name: "选择电信间（TR）：东侧电信间" })).not.toBeInTheDocument();
    expect(confirmation().getByRole("button", { name: "进入3D" })).toBeDisabled();
    choose("选择楼层：二层");
    expect(input.onEnter).not.toHaveBeenCalled();
    expect(confirmation().getByText("可进入整个本层，也可在上方继续选择房间。")).toBeInTheDocument();
    fireEvent.click(confirmation().getByRole("button", { name: "进入楼层3D" }));
    expect(input.onEnter).toHaveBeenCalledExactlyOnceWith(locations[2]);
    choose("选择电信间（TR）：东侧电信间");
    expect(input.onEnter).toHaveBeenCalledTimes(1);
    fireEvent.click(confirmation().getByRole("button", { name: "进入房间3D" }));
    expect(input.onEnter).toHaveBeenLastCalledWith(locations[3]);
    choose("上一级");
    expect(input.onSelect).toHaveBeenLastCalledWith("floor-a");
    expect(confirmation().getByRole("heading", { name: "二层" })).toBeInTheDocument();
    expect(input.onEnter).toHaveBeenCalledTimes(2);
  });

  it("searches visible identifiers globally, shows known ancestry and locates a result without entering", () => {
    const input = props();
    render(<MemoryRouter><ControlledPicker {...input} /></MemoryRouter>);
    fireEvent.change(browser().getByRole("textbox", { name: "搜索名称或编号" }), { target: { value: "  tr-201  " } });
    const result = browser().getByRole("button", { name: "选择电信间（TR）：东侧电信间" });
    expect(result).toHaveTextContent("总部园区 / 研发楼 / 二层 / 东侧电信间");
    expect(browser().queryByRole("button", { name: "选择楼层：受限范围五层" })).not.toBeInTheDocument();
    fireEvent.click(result);
    expect(input.onSelect).toHaveBeenCalledExactlyOnceWith("room-a");
    expect(input.onEnter).not.toHaveBeenCalled();
    expect(browser().getByRole("textbox", { name: "搜索名称或编号" })).toHaveValue("");
    expect(screen.getByRole("navigation", { name: "位置层级路径" })).toHaveTextContent("全部可见位置总部园区研发楼二层东侧电信间");
    expect(confirmation().getByRole("button", { name: "进入房间3D" })).toBeEnabled();
    fireEvent.click(within(screen.getByRole("navigation", { name: "位置层级路径" })).getByRole("button", { name: "全部可见位置" }));
    expect(browser().getByRole("button", { name: "选择园区：总部园区" })).toBeInTheDocument();
    expect(confirmation().getByRole("heading", { name: "东侧电信间" })).toBeInTheDocument();
    expect(input.onSelect).toHaveBeenCalledTimes(1);
    fireEvent.change(browser().getByRole("textbox", { name: "搜索名称或编号" }), { target: { value: "不存在的位置" } });
    expect(browser().getByText("没有匹配的位置，请尝试其他名称或编号")).toBeInTheDocument();
    expect(screen.queryByText("当前可见范围暂无位置")).not.toBeInTheDocument();
  });

  it("keeps a scoped floor accessible while revealing only the known part of its ancestry", () => {
    const input = props({ locations: locations.slice(5), contextLabel: "已授权的位置" });
    render(<MemoryRouter><ControlledPicker {...input} /></MemoryRouter>);
    const floor = browser().getByRole("button", { name: "选择楼层：受限范围五层" });
    expect(floor).toHaveTextContent("上级位置未加载");
    fireEvent.click(floor);
    const path = screen.getByRole("navigation", { name: "位置层级路径" });
    expect(path).toHaveTextContent("上级位置未加载");
    expect(path).toHaveTextContent("受限范围五层");
    expect(path).not.toHaveTextContent("hidden-building");
    expect(path).not.toHaveTextContent("总部园区");
    expect(browser().queryByRole("button", { name: "上一级" })).not.toBeInTheDocument();
    expect(browser().getByRole("button", { name: "返回目录" })).toBeEnabled();
    expect(confirmation().getByRole("button", { name: "进入楼层3D" })).toBeEnabled();
    choose("选择主配线间（MDF）：主配线间");
    fireEvent.click(confirmation().getByRole("button", { name: "进入房间3D" }));
    expect(input.onEnter).toHaveBeenCalledExactlyOnceWith(locations[6]);
  });

  it("blocks stale, loading, failed and explicitly disabled entries without silently selecting another location", () => {
    const input = props({ selectedId: "room-a" });
    const view = render(<MemoryRouter><SpatialLocationPicker {...input} /></MemoryRouter>);
    expect(confirmation().getByRole("button", { name: "进入房间3D" })).toBeEnabled();
    view.rerender(<MemoryRouter><SpatialLocationPicker {...input} loading /></MemoryRouter>);
    expect(browser().getByRole("status")).toHaveTextContent("正在加载位置目录");
    fireEvent.click(confirmation().getByRole("button", { name: "进入房间3D" }));
    expect(input.onEnter).not.toHaveBeenCalled();
    view.rerender(<MemoryRouter><SpatialLocationPicker {...input} error="服务暂时不可用" /></MemoryRouter>);
    expect(screen.getByRole("alert")).toHaveTextContent("当前为上次加载的目录");
    expect(confirmation().getByRole("button", { name: "进入房间3D" })).toBeDisabled();
    fireEvent.click(within(screen.getByRole("alert")).getByRole("button", { name: /重\s*试/ }));
    expect(input.onRetry).toHaveBeenCalledExactlyOnceWith();
    view.rerender(<MemoryRouter><SpatialLocationPicker {...input} enterDisabled /></MemoryRouter>);
    expect(confirmation().getByRole("button", { name: "进入房间3D" })).toBeDisabled();
    view.rerender(<MemoryRouter><SpatialLocationPicker {...input} locations={locations.filter(location => location.id !== "room-a")} /></MemoryRouter>);
    expect(confirmation().getByRole("status")).toHaveTextContent("原选择已不在当前可见目录");
    expect(confirmation().getByRole("button", { name: "进入3D" })).toBeDisabled();
    expect(input.onSelect).not.toHaveBeenCalled();
    expect(input.onEnter).not.toHaveBeenCalled();
  });

  it("distinguishes first-load failure from loading and a successfully empty scope", () => {
    const input = props({ locations: [] });
    const view = render(<MemoryRouter><SpatialLocationPicker {...input} loading /></MemoryRouter>);
    expect(browser().getByRole("status")).toHaveTextContent("正在加载位置目录");
    expect(screen.queryByText("当前可见范围暂无位置")).not.toBeInTheDocument();
    view.rerender(<MemoryRouter><SpatialLocationPicker {...input} error="读取失败" /></MemoryRouter>);
    expect(browser().getByRole("status")).toHaveTextContent("位置尚未读取成功");
    expect(screen.queryByText("当前可见范围暂无位置")).not.toBeInTheDocument();
    view.rerender(<MemoryRouter><SpatialLocationPicker {...input} /></MemoryRouter>);
    expect(browser().getByText("当前可见范围暂无位置")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(input.onSelect).not.toHaveBeenCalled();
    expect(input.onEnter).not.toHaveBeenCalled();
  });

  it.each(["room", "tr", "er", "mdf", "mmr", "data_hall", "entrance_facility"])("allows explicit room entry for the supported %s type", locationType => {
    const room: LocationRecord = { id: "room", name: "目标空间", identifier: "SPACE", location_type: locationType, parent_id: null };
    const input = props({ locations: [room], selectedId: room.id });
    render(<MemoryRouter><SpatialLocationPicker {...input} /></MemoryRouter>);
    fireEvent.click(confirmation().getByRole("button", { name: "进入房间3D" }));
    expect(input.onEnter).toHaveBeenCalledExactlyOnceWith(room);
  });

  it("bounds a malformed cyclic ancestry without inventing a root", () => {
    const cyclic: LocationRecord[] = [
      { id: "a", identifier: "A", name: "异常建筑", location_type: "building", parent_id: "b" },
      { id: "b", identifier: "B", name: "异常楼层", location_type: "floor", parent_id: "a" },
    ];
    const input = props({ locations: cyclic });
    render(<MemoryRouter><ControlledPicker {...input} /></MemoryRouter>);
    expect(browser().getByText("位置层级关系待核对")).toBeInTheDocument();
    choose("选择楼层：异常楼层");
    expect(screen.getByRole("navigation", { name: "位置层级路径" })).toHaveTextContent("上级关系存在循环，路径待核对");
    expect(input.onEnter).not.toHaveBeenCalled();
  });
});
