import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { InstallPalette, installationPreset } from "./InstallPalette";
import type { SpatialPayload } from "./sceneData";

function payload(): SpatialPayload {
  return {
    scope: { tenant_id: "tenant-a", project_id: null, location_id: null },
    locations: ["a", "b"].map(side => ({ id: `room-${side}`, parent_id: null, identifier: `ROOM-${side}`, name: `房间 ${side}`, location_type: "room", dimensions: { width_m: 10, depth_m: 8, height_m: 3.6 }, coordinates: {}, transform_3d: {} })),
    racks: [{ id: "rack-a", location_id: "room-a", rack_identifier: "RACK-A", name: "A 机柜", height_u: 12, width_mm: 600, depth_mm: 1000, position_x: 1, position_y: 1, position_z: 0, rotation: 0, reserved_units: [8], status: "active" }],
    devices: [
      { id: "front-low", rack_id: "rack-a", location_id: "room-a", identifier: "D-1", name: "设备1", device_type: "switch", start_u: 3, rack_units: 2, face: "front", status: "active" },
      { id: "front-high", rack_id: "rack-a", location_id: "room-a", identifier: "D-2", name: "设备2", device_type: "switch", start_u: 6, rack_units: 2, face: "front", status: "active" },
      { id: "rear", rack_id: "rack-a", location_id: "room-a", identifier: "D-3", name: "后侧设备", device_type: "patch_panel", start_u: 1, rack_units: 2, face: "rear", status: "active" },
    ], ports: [], pathways: [], cables: [], truncated: [],
  };
}

describe("palette drop presets", () => {
  it("maps room-local drop coordinates into the rack form without modifying scene data", () => {
    const data = payload(), before = structuredClone(data);
    expect(installationPreset("rack", { locationId: "room-b", positionX: 2.345, positionY: 3.456 }, data)).toEqual({ kind: "rack", preset: { values: { location_id: "room-b", position_x: 2.35, position_y: 3.46 }, hardware: undefined, dropped: true } });
    expect(data).toEqual(before);
  });

  it("finds contiguous front U space nearest the dropped height while respecting occupied and reserved slots", () => {
    const data = payload(), before = structuredClone(data);
    expect(installationPreset("server", { locationId: "room-a", positionX: 1, positionY: 1, rackId: "rack-a", startU: 7 }, data)).toEqual({ kind: "device", preset: { values: { location_id: "room-a", position_x: 1, position_y: 1, rack_id: "rack-a", start_u: 9 }, hardware: "server", dropped: true } });
    expect(data).toEqual(before);
  });

  it("snaps a tray to the exact existing endpoint in the same room, preserving height and sub-centimetre precision", () => {
    const data = payload();
    data.pathways = [
      { id: "path-a", location_id: "room-a", identifier: "TRAY-A", name: "A 桥架", type: "basket_tray", segments: [{ id: "seg-a", name: "A 段", sequence: 1, length_m: 4, coordinates: [{ x: 1, y: 1, z: 2.75 }, { x: 4.1234, y: 2.5678, z: 2.75 }] }] },
      { id: "path-b", location_id: "room-b", identifier: "TRAY-B", name: "B 桥架", type: "basket_tray", segments: [{ id: "seg-b", name: "B 段", sequence: 1, length_m: 1, coordinates: [{ x: 4.2, y: 2.6, z: 3.1 }, { x: 5.2, y: 2.6, z: 3.1 }] }] },
    ];
    const before = structuredClone(data);
    expect(installationPreset("tray", { locationId: "room-a", positionX: 4.2, positionY: 2.6 }, data)).toEqual({ kind: "pathway", preset: { values: { location_id: "room-a", position_x: 4.2, position_y: 2.6, points: [{ x: 4.1234, y: 2.5678, z: 2.75 }, { x: 5.1234, y: 2.5678, z: 2.75 }] }, hardware: undefined, dropped: true } });
    expect(data).toEqual(before);
  });
});


afterEach(cleanup);

describe("workspace tool palette", () => {
  it("delegates navigation without opening installation and reflects the active controlled tool", () => {
    const onToolChange = vi.fn(), onInstall = vi.fn();
    const view = render(<InstallPalette disabled={false} activeTool="select" onToolChange={onToolChange} onInstall={onInstall} />);
    const palette = within(screen.getByRole("region", { name: "工具箱" }));
    expect(palette.getByRole("button", { name: "选择工具" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(palette.getByRole("button", { name: "平移工具" }));
    expect(onToolChange.mock.calls).toEqual([["pan"]]);
    expect(onInstall).not.toHaveBeenCalled();
    view.rerender(<InstallPalette disabled={false} activeTool="pan" onToolChange={onToolChange} onInstall={onInstall} />);
    expect(palette.getByRole("button", { name: "平移工具" })).toHaveAttribute("aria-pressed", "true");
    expect(palette.getByRole("button", { name: "选择工具" })).toHaveAttribute("aria-pressed", "false");
    expect(palette.getByRole("status")).toHaveTextContent("当前工具 平移");
    fireEvent.click(palette.getByRole("button", { name: "选择工具" }));
    expect(onToolChange.mock.calls).toEqual([["pan"], ["select"]]);
  });

  it("arms an installation tool and opens parameters only through the separate configure action", () => {
    const onToolChange = vi.fn(), onInstall = vi.fn(), onConfigure = vi.fn();
    const view = render(<InstallPalette disabled={false} activeTool="copper" onToolChange={onToolChange} onInstall={onInstall} onConfigure={onConfigure} />);
    expect(screen.getByRole("status")).toHaveTextContent("点击起点、终点或拖线");
    expect(screen.queryByRole("button", { name: /填写.*参数/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "安装服务器" }));
    expect(onInstall.mock.calls).toEqual([["server"]]);
    expect(onConfigure).not.toHaveBeenCalled();
    expect(onToolChange).not.toHaveBeenCalled();
    view.rerender(<InstallPalette disabled={false} activeTool="server" onToolChange={onToolChange} onInstall={onInstall} onConfigure={onConfigure} />);
    expect(screen.getByRole("button", { name: "安装服务器" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "安装铜缆" })).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(screen.getByRole("button", { name: "填写服务器参数" }));
    expect(onConfigure).toHaveBeenCalledTimes(1);
    expect(onInstall).toHaveBeenCalledTimes(1);
  });

  it("preserves the scene installation drag MIME without triggering click actions", () => {
    const onInstall = vi.fn();
    render(<InstallPalette disabled={false} onInstall={onInstall} />);
    const transfer = { setData: vi.fn(), effectAllowed: "uninitialized" };
    const fiber = screen.getByRole("button", { name: "安装光纤" });
    expect(fiber).toHaveAttribute("draggable", "true");
    fireEvent.dragStart(fiber, { dataTransfer: transfer });
    expect(transfer.setData.mock.calls).toEqual([["application/x-cable-install", "fiber"]]);
    expect(transfer.effectAllowed).toBe("copy");
    expect(onInstall).not.toHaveBeenCalled();
  });

  it("blocks navigation, installation, configuration and drag while a write is pending", () => {
    const onToolChange = vi.fn(), onInstall = vi.fn(), onConfigure = vi.fn();
    render(<InstallPalette disabled activeTool="room" onToolChange={onToolChange} onInstall={onInstall} onConfigure={onConfigure} />);
    const room = screen.getByRole("button", { name: "安装房间" });
    const configure = screen.getByRole("button", { name: "填写房间参数" });
    expect(configure).toBeDisabled();
    expect(room).toHaveAttribute("draggable", "false");
    fireEvent.click(room);
    fireEvent.click(configure);
    fireEvent.click(screen.getByRole("button", { name: "平移工具" }));
    const transfer = { setData: vi.fn(), effectAllowed: "uninitialized" };
    fireEvent.dragStart(room, { dataTransfer: transfer });
    expect(transfer.setData).not.toHaveBeenCalled();
    expect(onInstall).not.toHaveBeenCalled();
    expect(onConfigure).not.toHaveBeenCalled();
    expect(onToolChange).not.toHaveBeenCalled();
  });

  it("selects measurement as a non-installation tool without dragging or exposing the parameter form", () => {
    const onToolChange = vi.fn(), onInstall = vi.fn(), onConfigure = vi.fn();
    const view = render(<InstallPalette disabled={false} activeTool="select" onToolChange={onToolChange} onInstall={onInstall} onConfigure={onConfigure} />);
    const measure = screen.getByRole("button", { name: "测距工具" });
    fireEvent.click(measure);
    expect(onToolChange.mock.calls).toEqual([["measure"]]);
    expect(onInstall).not.toHaveBeenCalled();
    expect(measure).toHaveAttribute("draggable", "false");
    view.rerender(<InstallPalette disabled={false} activeTool="measure" onToolChange={onToolChange} onInstall={onInstall} onConfigure={onConfigure} />);
    expect(measure).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "选择工具" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("status")).toHaveTextContent("当前工具 测距");
    expect(screen.getByRole("status")).toHaveTextContent("连续取点，可撤回最后一点；Esc 退出测距");
    expect(screen.queryByRole("button", { name: /填写.*参数/ })).not.toBeInTheDocument();
    expect(onConfigure).not.toHaveBeenCalled();
    view.rerender(<InstallPalette disabled activeTool="measure" onToolChange={onToolChange} onInstall={onInstall} />);
    fireEvent.click(measure);
    expect(onToolChange).toHaveBeenCalledTimes(1);
  });

});
