import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { availablePorts, portBlockedReason } from "./sceneCreate";
import { buildScene, type CablePolicy, type SpatialPayload } from "./sceneData";
import { SpatialInspector } from "./SpatialInspector";

afterEach(cleanup);

function payload(policy?: CablePolicy): SpatialPayload {
  return {
    scope: { tenant_id: "tenant", project_id: null, location_id: null },
    locations: [{ id: "room", parent_id: null, identifier: "RM", name: "Room", location_type: "room", dimensions: { width_m: 6, depth_m: 4 }, coordinates: {}, transform_3d: {} }],
    racks: [{ id: "rack", location_id: "room", rack_identifier: "RK", name: "Rack", height_u: 42, width_mm: 600, depth_mm: 1000, position_x: 1, position_y: 1, position_z: 0, rotation: 0, reserved_units: [], status: "active" }],
    devices: [{ id: "device", rack_id: "rack", location_id: "room", identifier: "SW", name: "Switch", device_type: "switch", start_u: 1, rack_units: 1, face: "front", status: "active", ...(policy ? { cable_policy: policy } : {}) }],
    ports: [
      { id: "copper", device_id: "device", identifier: "RJ45-1", label: "", connector_type: "RJ45", media_type: "Cat6A copper", front_or_rear: "front", position_index: 1, status: "available" },
      { id: "fiber", device_id: "device", identifier: "LC-1", label: "", connector_type: "LC", front_or_rear: "front", position_index: 2, status: "available" },
      { id: "occupied", device_id: "device", identifier: "RJ45-2", label: "", connector_type: "RJ45", front_or_rear: "front", position_index: 3, status: "available", occupied: true },
    ],
    pathways: [], cables: [], truncated: [],
  };
}

function inspector(data: SpatialPayload, onConnectPort = vi.fn()) {
  return <SpatialInspector payload={data} selection={{ kind: "device", id: "device" }} onSelect={vi.fn()} onFocus={vi.fn()} onClose={vi.fn()} routeKind="none" onCreate={vi.fn()} onMoveRack={vi.fn()} onConnectPort={onConnectPort} />;
}

describe("port policy admission across the workspace", () => {
  it.each([
    { policy: { allows_cables: false, allowed_media: ["copper", "fiber"] }, expected: ["设备布线策略禁止连接线缆。", "设备布线策略禁止连接线缆。"] },
    { policy: { allows_cables: true, allowed_media: ["fiber"] }, expected: ["设备布线策略不允许铜缆。", undefined] },
    { policy: { allows_cables: true, allowed_media: ["copper"] }, expected: [undefined, "设备布线策略不允许光纤。"] },
  ])("uses the port's own medium to apply $policy", ({ policy, expected }) => {
    const data = payload(policy);
    expect(data.ports.slice(0, 2).map(port => portBlockedReason(data, port))).toEqual(expected);
    expect(availablePorts(data, "device", "Cat6A copper").map(port => port.id)).toEqual(expected[0] ? [] : ["copper"]);
    expect(availablePorts(data, "device", "OS2 fiber").map(port => port.id)).toEqual(expected[1] ? [] : ["fiber"]);
  });

  it("keeps physical occupancy separate and preserves payloads without policy or version metadata", () => {
    const data = payload();
    data.ports[1].status = "disabled";
    const before = structuredClone(data);
    expect(data.ports.map(port => portBlockedReason(data, port))).toEqual([undefined, undefined, undefined]);
    expect(availablePorts(data, "device", "copper").map(port => port.id)).toEqual(["copper"]);
    expect(availablePorts(data, "device", "fiber")).toEqual([]);
    expect(data).toEqual(before);
  });

  it("passes policy blocks to the renderer without changing the actual occupied flag", () => {
    const data = payload({ allows_cables: true, allowed_media: ["fiber"] });
    const before = structuredClone(data);
    expect(buildScene(data).data.racks[0].devices[0].ports).toEqual([
      { id: "copper", identifier: "RJ45-1", mediaType: "Cat6A copper", face: "front", positionIndex: 1, occupied: false, blocked: true },
      { id: "fiber", identifier: "LC-1", mediaType: "LC", face: "front", positionIndex: 2, occupied: false },
      { id: "occupied", identifier: "RJ45-2", mediaType: "RJ45", face: "front", positionIndex: 3, occupied: true, blocked: true },
    ]);
    expect(data).toEqual(before);
  });

  it("prevents forbidden inspector connections and explains the policy while allowing compatible ports", () => {
    const data = payload({ allows_cables: true, allowed_media: ["fiber"] });
    const connect = vi.fn();
    render(inspector(data, connect));
    const copper = screen.getByRole("button", { name: "从 SW RJ45-1 开始连线" });
    const fiber = screen.getByRole("button", { name: "从 SW LC-1 开始连线" });
    expect(copper).toBeDisabled();
    expect(copper).toHaveAttribute("title", expect.stringContaining("设备布线策略不允许铜缆。"));
    expect(fiber).toBeEnabled();
    fireEvent.click(copper); fireEvent.click(fiber);
    expect(connect.mock.calls).toEqual([["fiber"]]);
  });

  it("updates inspector admission after a policy refresh without treating blocked ports as occupied", () => {
    const connect = vi.fn();
    const mounted = render(inspector(payload({ allows_cables: false, allowed_media: [] }), connect));
    const copper = screen.getByRole("button", { name: "从 SW RJ45-1 开始连线" });
    expect(copper).toBeDisabled();
    expect(copper).toHaveAttribute("title", expect.stringContaining("设备布线策略禁止连接线缆。"));
    fireEvent.click(copper);
    expect(connect).not.toHaveBeenCalled();
    mounted.rerender(inspector(payload({ allows_cables: true, allowed_media: ["copper", "fiber"] }), connect));
    expect(copper).toBeEnabled();
    expect(copper).toHaveAttribute("title", expect.stringContaining("可用"));
    expect(screen.getByRole("button", { name: "从 SW RJ45-2 开始连线" })).toBeDisabled();
    fireEvent.click(copper);
    expect(connect.mock.calls).toEqual([["copper"]]);
  });
});
