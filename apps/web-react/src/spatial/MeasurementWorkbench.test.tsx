import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MeasurementWorkbench } from "./MeasurementWorkbench";
import type { SceneMeasurementPoint, SceneRoom } from "./render/sceneRenderer";

afterEach(cleanup);
const room: SceneRoom = { id: "room-b", label: "机房 B", center: [24, 33], width: 8, depth: 6, height: 8 };
function points(): SceneMeasurementPoint[] {
  return [
    { locationId: "room-b", point: [21, 1, 32] },
    { locationId: "room-b", point: [24, 5, 32] },
    { locationId: "room-b", point: [24, 2, 36] },
  ];
}
function callbacks() { return { onUndo: vi.fn(), onClear: vi.fn(), onFinish: vi.fn(), onResume: vi.fn(), onCreatePathway: vi.fn() }; }
function metrics() {
  return Object.fromEntries(["图示总长", "水平投影距离", "累计高差"].map(name => [name, screen.getByLabelText(name).textContent]));
}

describe("polyline measurement workbench", () => {
  it("sums every 3D and horizontal segment, counts both ascent and descent, and displays room-local XYZ", () => {
    const input = points(), before = structuredClone(input);
    render(<MeasurementWorkbench points={input} room={room} {...callbacks()} />);
    expect(metrics()).toEqual({ 图示总长: "10.000 m", 水平投影距离: "7.000 m", 累计高差: "7.000 m" });
    const table = screen.getByRole("table", { name: "房间内坐标（m）" });
    expect(within(table).getAllByRole("row").map(row => Array.from(row.children).map(cell => cell.textContent))).toEqual([
      ["点", "X", "Y", "Z（高）"],
      ["1", "1.000", "2.000", "1.000"],
      ["2", "4.000", "2.000", "5.000"],
      ["3", "4.000", "6.000", "2.000"],
    ]);
    expect(screen.getByRole("button", { name: "用于创建线槽" })).not.toBeDisabled();
    expect(input).toEqual(before);
  });

  it("waits for two points instead of displaying a finished zero-length measurement", () => {
    const actions = callbacks();
    const view = render(<MeasurementWorkbench points={[]} room={room} {...actions} />);
    expect(metrics()).toEqual({ 图示总长: "—", 水平投影距离: "—", 累计高差: "—" });
    expect(screen.getByRole("button", { name: "撤回一点" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "清空" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "完成测距" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "用于创建线槽" })).toBeDisabled();
    view.rerender(<MeasurementWorkbench points={points().slice(0, 1)} room={room} {...actions} />);
    expect(screen.getByText("继续点击第二个点开始测量。")).toBeInTheDocument();
    expect(metrics()).toEqual({ 图示总长: "—", 水平投影距离: "—", 累计高差: "—" });
    expect(screen.getByRole("button", { name: "撤回一点" })).not.toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "完成测距" }));
    fireEvent.click(screen.getByRole("button", { name: "用于创建线槽" }));
    expect(actions.onFinish).not.toHaveBeenCalled();
    expect(actions.onCreatePathway).not.toHaveBeenCalled();
  });

  it("delegates undo, completion, resume, creation and clear while retaining the supplied measurement", () => {
    const input = points(), actions = callbacks();
    const view = render(<MeasurementWorkbench points={input} room={room} {...actions} />);
    fireEvent.click(screen.getByRole("button", { name: "撤回一点" }));
    expect(actions.onUndo).toHaveBeenCalledTimes(1);
    view.rerender(<MeasurementWorkbench points={input.slice(0, 2)} room={room} {...actions} />);
    expect(metrics()).toEqual({ 图示总长: "5.000 m", 水平投影距离: "3.000 m", 累计高差: "4.000 m" });
    fireEvent.click(screen.getByRole("button", { name: "完成测距" }));
    expect(actions.onFinish).toHaveBeenCalledTimes(1);
    view.rerender(<MeasurementWorkbench points={input.slice(0, 2)} room={room} active={false} {...actions} />);
    expect(screen.getByText("测距已完成")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "完成测距" })).not.toBeInTheDocument();
    expect(metrics()).toEqual({ 图示总长: "5.000 m", 水平投影距离: "3.000 m", 累计高差: "4.000 m" });
    fireEvent.click(screen.getByRole("button", { name: "继续取点" }));
    fireEvent.click(screen.getByRole("button", { name: "用于创建线槽" }));
    fireEvent.click(screen.getByRole("button", { name: "清空" }));
    expect(actions.onResume).toHaveBeenCalledTimes(1);
    expect(actions.onCreatePathway).toHaveBeenCalledTimes(1);
    expect(actions.onClear).toHaveBeenCalledTimes(1);
    expect(input).toEqual(points());
  });

  it("does not bridge across an invalid point or expose NaN as a measurement", () => {
    const input = points(); input[1].point[1] = Number.NaN;
    const actions = callbacks();
    render(<MeasurementWorkbench points={input} room={room} {...actions} />);
    expect(screen.getByRole("alert")).toHaveTextContent("测点坐标无效");
    expect(metrics()).toEqual({ 图示总长: "—", 水平投影距离: "—", 累计高差: "—" });
    expect(screen.getByRole("table")).not.toHaveTextContent("NaN");
    expect(screen.getByRole("button", { name: "完成测距" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "用于创建线槽" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "撤回一点" })).not.toBeDisabled();
  });

  it("rejects cross-room segments rather than measuring schematic room separation", () => {
    const input = points(); input[1].locationId = "room-a";
    render(<MeasurementWorkbench points={input} room={room} {...callbacks()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("不能计算跨房间距离");
    expect(metrics()).toEqual({ 图示总长: "—", 水平投影距离: "—", 累计高差: "—" });
    expect(screen.getByRole("button", { name: "完成测距" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "用于创建线槽" })).toBeDisabled();
  });

  it("enforces the 64-point limit and allows recovery by undo without an oversized coordinate table", () => {
    const input: SceneMeasurementPoint[] = Array.from({ length: 64 }, (_, index) => ({ locationId: "room-b", point: [21 + index * 0.01, 1, 32] }));
    const actions = callbacks();
    const view = render(<MeasurementWorkbench points={input} room={room} active={false} {...actions} />);
    expect(screen.getByRole("button", { name: "继续取点" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "用于创建线槽" })).not.toBeDisabled();
    view.rerender(<MeasurementWorkbench points={[...input, { locationId: "room-b", point: [22, 1, 32] }]} room={room} {...actions} />);
    expect(screen.getByRole("alert")).toHaveTextContent("一次测距最多 64 个点");
    expect(screen.getByRole("button", { name: "完成测距" })).toBeDisabled();
    expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(65);
    fireEvent.click(screen.getByRole("button", { name: "撤回一点" }));
    expect(actions.onUndo).toHaveBeenCalledTimes(1);
  });

  it("retains model distances while room coordinates load and blocks conversion until its matching room is available", () => {
    const actions = callbacks();
    const view = render(<MeasurementWorkbench points={points()} {...actions} />);
    expect(metrics()).toEqual({ 图示总长: "10.000 m", 水平投影距离: "7.000 m", 累计高差: "7.000 m" });
    expect(screen.getByText("等待加载测点所属房间后显示本地坐标。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "用于创建线槽" })).toBeDisabled();
    view.rerender(<MeasurementWorkbench points={points()} room={{ ...room, id: "room-a" }} {...actions} />);
    expect(screen.getByRole("button", { name: "用于创建线槽" })).toBeDisabled();
    view.rerender(<MeasurementWorkbench points={points()} room={room} {...actions} />);
    expect(screen.getByRole("button", { name: "用于创建线槽" })).not.toBeDisabled();
  });
});
