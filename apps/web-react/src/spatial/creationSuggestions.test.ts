import { describe, expect, it } from "vitest";
import { suggestCreationIdentity, suggestRackSlot } from "./creationSuggestions";
import type { SpatialDevice, SpatialPayload, SpatialRack } from "./sceneData";

const rack: SpatialRack = { id: "rack", location_id: "room", rack_identifier: "R1", name: "R1", height_u: 8, reserved_units: [3, 7], width_mm: 600, depth_mm: 1000, position_x: 1, position_y: 1, position_z: 0, rotation: 0, status: "active" };
const device = (face: string, start_u: number, rack_units: number, rack_id = "rack"): SpatialDevice => ({ id: `${face}-${start_u}`, identifier: "DEVICE-001", name: "Device", device_type: "server", rack_id, location_id: "room", start_u, rack_units, face, status: "active" });
const empty: Pick<SpatialPayload, "locations" | "devices" | "pathways"> = { locations: [], devices: [], pathways: [] };

describe("explicit creation suggestions", () => {
  it.each([["room", "ROOM-001", "房间 001"], ["device", "DEVICE-001", "设备 001"], ["pathway", "TRAY-001", "线槽 001"]] as const)("starts the %s namespace independently", (kind, identifier, name) => {
    expect(suggestCreationIdentity(kind, empty)).toEqual({ identifier, name });
  });
  it("finds the first unused identifier gap, handles case/whitespace, and does not mutate visible data", () => {
    const data = { ...empty, devices: [" device-001 ", "DEVICE-003", "OTHER-002"].map(identifier => ({ ...device("front", 1, 1), identifier })) };
    const before = structuredClone(data);
    expect(suggestCreationIdentity("device", data)).toEqual({ identifier: "DEVICE-002", name: "设备 002" });
    expect(data).toEqual(before);
  });
  it("continues beyond three digits without reusing an occupied identifier", () => {
    const data = { ...empty, devices: Array.from({ length: 1000 }, (_, index) => ({ ...device("front", 1, 1), identifier: `DEVICE-${String(index + 1).padStart(3, "0")}` })) };
    expect(suggestCreationIdentity("device", data).identifier).toBe("DEVICE-1001");
  });
  it("uses the smallest contiguous run on the chosen face, honoring reservations on both faces", () => {
    const devices = [device("front", 1, 2), device("rear", 4, 2), device("front", 1, 8, "other-rack")];
    const before = structuredClone({ rack, devices });
    expect(suggestRackSlot(rack, devices, 2, "front")).toEqual({ startU: 4, endU: 5 });
    expect(suggestRackSlot(rack, devices, 2, "rear")).toEqual({ startU: 1, endU: 2 });
    expect(suggestRackSlot(rack, devices, 3, "front")).toEqual({ startU: 4, endU: 6 });
    expect({ rack, devices }).toEqual(before);
  });
  it("reports fragmented or insufficient space instead of falling back to U1", () => {
    expect(suggestRackSlot({ ...rack, reserved_units: [1, 3, 5, 7] }, [], 2, "front")).toEqual({ reason: "前侧没有连续 2U 的空闲位置；请换机柜、安装面或设备模板。" });
    expect(suggestRackSlot(rack, [], 9, "rear")).toEqual({ reason: "模板需要 9U，超过机柜的 8U 容量。" });
  });
  it("does not invent availability when prerequisites, loaded data or coordinates are invalid", () => {
    for (const result of [suggestRackSlot(undefined, [], 1, "front"), suggestRackSlot(rack, [], undefined, "front"), suggestRackSlot(rack, [], 1, undefined), suggestRackSlot(rack, [], 1, "front", false), suggestRackSlot(rack, [device("front", Number.NaN, 1)], 1, "front"), suggestRackSlot({ ...rack, reserved_units: [9] }, [], 1, "front")]) expect(result).toHaveProperty("reason");
  });
});
