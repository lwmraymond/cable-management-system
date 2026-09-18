import { describe, expect, it } from "vitest";
import { buildScene, rackCapacity, segmentPoints } from "./sceneData";
import type { SpatialCable, SpatialDevice, SpatialLocation, SpatialPayload, SpatialPort, SpatialRack, SpatialSegment } from "./sceneData";

function location(id = "room-a", width = 6, depth = 4): SpatialLocation {
  return { id, parent_id: null, identifier: id, name: `Room ${id}`, location_type: "room", dimensions: { width_m: width, depth_m: depth }, coordinates: {}, transform_3d: {} };
}
function rack(id = "rack-a", room = "room-a", x = 1): SpatialRack {
  return { id, location_id: room, rack_identifier: id, name: `Rack ${id}`, height_u: 42, width_mm: 600, depth_mm: 1000, position_x: x, position_y: 1, position_z: 0, rotation: 0, reserved_units: [], status: "active" };
}
function device(id = "device-a", rackId = "rack-a", room = "room-a"): SpatialDevice {
  return { id, rack_id: rackId, location_id: room, identifier: id, name: `Device ${id}`, device_type: "switch", start_u: 1, rack_units: 2, face: "front", status: "active" };
}
function port(id = "port-a", deviceId = "device-a"): SpatialPort {
  return { id, device_id: deviceId, identifier: id, label: id, connector_type: "LC", front_or_rear: "front", position_index: 1, status: "active" };
}
function segment(id: string, coordinates: SpatialSegment["coordinates"]): SpatialSegment {
  return { id, sequence: 1, name: id, length_m: 3, coordinates };
}
function payload(overrides: Partial<SpatialPayload> = {}): SpatialPayload {
  return { scope: { tenant_id: "tenant-a", project_id: "project-a", location_id: null }, locations: [location()], racks: [rack()], devices: [], ports: [], pathways: [], cables: [], truncated: [], ...overrides };
}
function cable(): SpatialCable {
  return {
    id: "cable-a", identifier: "CB-01", media_type: "fiber", construction: "patch", installation_status: "active", length_m: null, test_status: null, route_segment_ids: [], endpoint_scope: "complete",
    terminations: [
      { side: "A", port_id: "port-a", device_id: "device-a", rack_id: "rack-a", location_id: "room-a" },
      { side: "B", port_id: "port-b", device_id: "device-b", rack_id: "rack-b", location_id: "room-a" },
    ],
  };
}
function connectedPayload(): SpatialPayload {
  return payload({ racks: [rack(), rack("rack-b", "room-a", 4)], devices: [device(), device("device-b", "rack-b")], ports: [port(), port("port-b", "device-b")], cables: [cable()] });
}

describe("spatial scene conversion", () => {
  it("converts millimetres, degrees, Z-up metres and device inventory into renderer data", () => {
    const input = payload({ racks: [{ ...rack(), width_mm: 800, depth_mm: 1200, position_x: 2, position_y: 3, position_z: 0.5, rotation: 90 }], devices: [device()], ports: [port(), { ...port("rear-port"), front_or_rear: "rear" }] });
    const before = structuredClone(input);
    expect(buildScene(input)).toEqual({
      data: {
        rooms: [{ id: "room-a", label: "Room room-a", center: [3, 2], width: 6, depth: 4, height: 3.2, entrances: [] }],
        racks: [{ id: "rack-a", locationId: "room-a", identifier: "rack-a", name: "Rack rack-a", position: [2, 0.5, 3], rotation: Math.PI / 2, width: 0.8, depth: 1.2, heightU: 42,
          devices: [{ id: "device-a", identifier: "device-a", name: "Device device-a", deviceType: "switch", startU: 1, units: 2, face: "front", portCount: 1, ports: [{ id: "port-a", identifier: "port-a", face: "front", mediaType: "LC", positionIndex: 1, occupied: true }, { id: "rear-port", identifier: "rear-port", face: "rear", mediaType: "LC", positionIndex: 1, occupied: true }] }],
        }], paths: [],
      }, warnings: [], routeKind: "none", routeKinds: {}, leadInCableIds: [],
    });
    expect(input).toEqual(before);
  });

  it("counts overlapping device U positions once and excludes occupied positions from reservations", () => {
    const target = { ...rack(), height_u: 6, reserved_units: [2, 4, 4, 6, 0, 7] };
    expect(rackCapacity(target, [device(), { ...device("overlap"), start_u: 2 }, device("elsewhere", "rack-b")])).toEqual({ used: 3, reserved: 2, free: 1, percent: 50 });
  });

  it("clips device occupancy and reservations to the physical rack height", () => {
    const target = { ...rack(), height_u: 4, reserved_units: [-1, 2, 5] };
    expect(rackCapacity(target, [{ ...device(), start_u: 0, rack_units: 2 }, { ...device("top"), start_u: 4, rack_units: 3 }])).toEqual({ used: 2, reserved: 1, free: 1, percent: 50 });
  });

  it("separates rooms schematically and applies the same offset to racks and measured pathways", () => {
    const input = payload({ locations: [location(), location("room-b", 5, 8)], racks: [rack(), rack("rack-b", "room-b", 2)], pathways: [{ id: "path-b", location_id: "room-b", identifier: "PW-B", name: "Tray B", type: "tray", segments: [segment("seg-b", [{ x: 2, y: 1, z: 3 }, { x: 4, y: 1, z: 3 }])] }] });
    const scene = buildScene(input, { showPathways: true });
    expect(scene.data.rooms).toEqual([
      { id: "room-a", label: "Room room-a", center: [3, 2], width: 6, depth: 4, height: 3.2, entrances: [] },
      { id: "room-b", label: "Room room-b", center: [10.5, 4], width: 5, depth: 8, height: 3.2, entrances: [] },
    ]);
    expect(scene.data.racks.map(item => ({ id: item.id, position: item.position }))).toEqual([{ id: "rack-a", position: [1, 0, 1] }, { id: "rack-b", position: [10, 0, 1] }]);
    expect(scene.data.paths).toEqual([{ id: "path-b", identifier: "PW-B", kind: "pathway", pathwayType: "tray", points: [[10, 3, 1], [12, 3, 1]], dimension: { label: "seg-b · 坐标 2.00 m", lengthM: 2, basis: "coordinates" } }]);
    expect(scene.warnings).toEqual([expect.stringMatching(/示意.*不代表.*实测距离/)]);
  });

  it.each([
    { name: "overlapping rack positions", second: rack("rack-b") },
    { name: "non-finite rack position", second: { ...rack("rack-b", "room-a", 4), position_y: Number.NaN } },
  ])("warns about $name and leaves source records unchanged", ({ second }) => {
    const input = payload({ racks: [rack(), second] });
    const before = structuredClone(input);
    const scene = buildScene(input);
    expect(scene.warnings).toEqual([expect.stringMatching(/坐标.*示意.*未修改/)]);
    expect(scene.data.racks.map(item => ({ id: item.id, position: item.position }))).toEqual([{ id: "rack-a", position: [0.8, 0, 1] }, { id: "rack-b", position: [2, 0, 1] }]);
    expect(input).toEqual(before);
  });

  it("warns when an invalid rotation is replaced with a safe pose", () => {
    const input = payload({ racks: [{ ...rack(), rotation: Number.POSITIVE_INFINITY }] });
    const before = structuredClone(input);
    const scene = buildScene(input);
    expect(scene.data.racks[0].rotation).toBe(0);
    expect(scene.warnings.length).toBeGreaterThan(0);
    expect(input).toEqual(before);
  });

  it.each([
    { dimensions: {} },
    { dimensions: { width_m: -6, depth_m: Number.POSITIVE_INFINITY } },
    { dimensions: { width_m: "6", depth_m: 0 } },
  ])("warns when absent or invalid room dimensions require a schematic floor: $dimensions", ({ dimensions }) => {
    const input = payload({ locations: [{ ...location(), dimensions }] });
    const before = structuredClone(input);
    const scene = buildScene(input);
    expect(scene.data.rooms).toEqual([{ id: "room-a", label: "Room room-a", center: [2, 2], width: 4, depth: 4, height: 3.2, entrances: [] }]);
    expect(scene.warnings).toEqual([expect.stringMatching(/尺寸.*示意/)]);
    expect(input).toEqual(before);
  });

  it("shows an explicitly scoped empty room without inventing racks", () => {
    expect(buildScene(payload({ racks: [] }), { locationId: "room-a" })).toEqual({ data: { rooms: [{ id: "room-a", label: "Room room-a", center: [3, 2], width: 6, depth: 4, height: 3.2, entrances: [] }], racks: [], paths: [] }, warnings: [], routeKind: "none", routeKinds: {}, leadInCableIds: [] });
  });
});

describe("spatial cable routing", () => {
  it("converts route points to Y-up with room offsets without changing the source", () => {
    const input = segment("seg-a", [{ x: 1, y: 2, z: 3 }, { x: 4, y: 5 }]);
    const before = structuredClone(input);
    expect(segmentPoints(input, [8, 6])).toEqual([[9, 3, 8], [12, 0, 11]]);
    expect(input).toEqual(before);
  });

  it.each([
    { coordinates: [{ x: 0, y: 0, z: 3 }] },
    { coordinates: [{ x: 0, y: 0, z: 3 }, { x: Number.NaN, y: 2 }, { x: 4, y: 4, z: 3 }] },
    { coordinates: [{ x: 0, y: 0 }, { x: 4, y: Number.POSITIVE_INFINITY }] },
    { coordinates: [{ x: 0, y: 0 }, { x: 4, y: 2, z: Number.NaN }] },
  ])("omits incomplete or invalid segments without bridging over invalid points: $coordinates", ({ coordinates }) => {
    expect(segmentPoints(segment("invalid", coordinates))).toEqual([]);
  });

  it("renders recorded segments separately and skips missing or invalid segments", () => {
    const input = payload({ pathways: [{ id: "path-a", location_id: "room-a", identifier: "PW-A", name: "Tray A", type: "tray", segments: [
      segment("first", [{ x: 1, y: 1, z: 3 }, { x: 2, y: 1, z: 3 }]),
      segment("invalid", [{ x: 2, y: 1, z: 3 }, { x: Number.NaN, y: 1, z: 3 }, { x: 4, y: 1, z: 3 }]),
      segment("last", [{ x: 4, y: 1, z: 3 }, { x: 5, y: 1, z: 3 }]),
    ] }], cables: [{ ...cable(), route_segment_ids: ["first", "missing", "invalid", "last"] }] });
    const scene = buildScene(input, { cableId: "cable-a" });
    expect({ routeKind: scene.routeKind, paths: scene.data.paths }).toEqual({ routeKind: "recorded", paths: [
      { id: "cable-a", identifier: "CB-01", kind: "cable", points: [[1, 3, 1], [2, 3, 1]], dimension: { label: "first · 坐标 1.00 m", lengthM: 1, basis: "coordinates" } },
      { id: "cable-a", identifier: "CB-01", kind: "cable", points: [[4, 3, 1], [5, 3, 1]], dimension: { label: "last · 坐标 1.00 m", lengthM: 1, basis: "coordinates" } },
    ] });
  });

  it("uses a solid schematic connection only when both endpoints are visible", () => {
    const input = connectedPayload();
    const before = structuredClone(input);
    const scene = buildScene(input, { cableId: "cable-a" });
    expect({ routeKind: scene.routeKind, paths: scene.data.paths }).toEqual({ routeKind: "schematic", paths: [{
      id: "cable-a", identifier: "CB-01", kind: "cable",
      points: [[1, expect.closeTo(0.15795, 6), 1.453], [1, expect.closeTo(0.15795, 6), 1.653], [4, expect.closeTo(0.15795, 6), 1.653], [4, expect.closeTo(0.15795, 6), 1.453]],
    }] });
    expect(input).toEqual(before);
  });

  it("does not fabricate a second endpoint for a partial cable", () => {
    const input = connectedPayload();
    input.cables[0] = { ...input.cables[0], endpoint_scope: "partial", terminations: input.cables[0].terminations.slice(0, 1) };
    const scene = buildScene(input, { cableId: "cable-a" });
    expect({ routeKind: scene.routeKind, paths: scene.data.paths }).toEqual({ routeKind: "none", paths: [] });
  });

  it("does not connect a complete cable to a rack outside the selected location", () => {
    const input = connectedPayload();
    input.locations.push(location("room-b")); input.racks[1].location_id = "room-b";
    input.devices[1].location_id = "room-b"; input.cables[0].terminations[1].location_id = "room-b";
    const scene = buildScene(input, { cableId: "cable-a", locationId: "room-a" });
    expect({ racks: scene.data.racks.map(item => item.id), routeKind: scene.routeKind, paths: scene.data.paths }).toEqual({ racks: ["rack-a"], routeKind: "none", paths: [] });
  });

  it("isolates the selected rack and hides pathways and external cable endpoints", () => {
    const input = connectedPayload();
    input.pathways = [{ id: "path-a", location_id: "room-a", identifier: "PW-A", name: "Tray A", type: "tray", segments: [segment("seg-a", [{ x: 1, y: 1, z: 3 }, { x: 4, y: 1, z: 3 }])] }];
    input.cables[0].route_segment_ids = ["seg-a"];
    const scene = buildScene(input, { rackId: "rack-a", cableId: "cable-a", showPathways: true });
    expect({ racks: scene.data.racks.map(item => ({ id: item.id, devices: item.devices.map(child => child.id) })), routeKind: scene.routeKind, paths: scene.data.paths }).toEqual({ racks: [{ id: "rack-a", devices: ["device-a"] }], routeKind: "none", paths: [] });
  });
});


describe("persistent scene inventory", () => {
  it("keeps all visible cables in the scene without requiring a selected cable", () => {
    const input = connectedPayload();
    const scene = buildScene(input);
    expect(scene.data.paths.map(path => ({ id: path.id, kind: path.kind, dashed: path.dashed }))).toEqual([{ id: "cable-a", kind: "cable", dashed: undefined }]);
    expect(scene.routeKinds).toEqual({ "cable-a": "schematic" });
    expect(buildScene(input, { cableId: "cable-a" }).data).toEqual(scene.data);
  });

  it("includes empty server rooms and their pathways in the overview", () => {
    const input = payload({ racks: [], locations: [{ ...location(), location_type: "data_hall", dimensions: { width_m: 8, depth_m: 6, height_m: 4 } }], pathways: [{ id: "path-a", location_id: "room-a", identifier: "TRAY-A", name: "Tray", type: "ladder", segments: [segment("seg-a", [{ x: 1, y: 1, z: 3 }, { x: 5, y: 1, z: 3 }])] }] });
    const scene = buildScene(input, { showPathways: true });
    expect(scene.data.racks).toEqual([]);
    expect(scene.data.rooms).toEqual([{ id: "room-a", label: "Room room-a", center: [4, 3], width: 8, depth: 6, height: 4, entrances: [] }]);
    expect(scene.data.paths).toEqual([{ id: "path-a", identifier: "TRAY-A", kind: "pathway", pathwayType: "ladder", points: [[1, 3, 1], [5, 3, 1]], dimension: { label: "seg-a · 坐标 4.00 m", lengthM: 4, basis: "coordinates" } }]);
  });

  it("does not turn inconsistent port references into visual cable endpoints", () => {
    const input = connectedPayload();
    input.ports[1].device_id = "unrelated-device";
    expect(buildScene(input).data.paths).toEqual([]);
  });
});


describe("recorded-route endpoint access leads", () => {
  function routedPayload() {
    const input = connectedPayload();
    input.racks[0].rotation = 90;
    input.ports[1].front_or_rear = "rear";
    input.pathways = [{ id: "tray", identifier: "TRAY", name: "Tray", type: "tray", location_id: "room-a", segments: [
      segment("s1", [{ x: 1.8, y: 1, z: 3 }, { x: 3, y: 1, z: 3 }]),
      segment("s2", [{ x: 4, y: 0.3, z: 3 }, { x: 3, y: 1, z: 3 }]),
    ] }];
    input.cables[0].route_segment_ids = ["s1", "s2"];
    input.cables[0].route_scope = "complete";
    return input;
  }

  it("keeps registered coordinates and adds two access leads using rotated front/rear directions", () => {
    const input = routedPayload();
    const before = structuredClone(input);
    const scene = buildScene(input);
    expect(scene.routeKinds).toEqual({ "cable-a": "recorded" });
    expect(scene.leadInCableIds).toEqual(["cable-a"]);
    expect(scene.data.paths).toEqual([
      { id: "cable-a", identifier: "CB-01", kind: "cable", points: [[1.8, 3, 1], [3, 3, 1]], dimension: { label: "s1 · 坐标 1.20 m", lengthM: 1.2, basis: "coordinates" } },
      { id: "cable-a", identifier: "CB-01", kind: "cable", points: [[4, 3, 0.3], [3, 3, 1]], dimension: { label: "s2 · 坐标 1.221 m", lengthM: expect.closeTo(Math.sqrt(1.49), 12), basis: "coordinates" } },
      { id: "cable-a", identifier: "CB-01", kind: "cable", points: [[1.453, expect.closeTo(0.15795, 6), 1], [1.653, expect.closeTo(0.15795, 6), 1], [1.653, 3, 1], [1.8, 3, 1]] },
      { id: "cable-a", identifier: "CB-01", kind: "cable", points: [[4, 3, 0.3], [4, 3, expect.closeTo(0.347, 6)], [4, expect.closeTo(0.15795, 6), expect.closeTo(0.347, 6)], [4, expect.closeTo(0.15795, 6), expect.closeTo(0.547, 6)]] },
    ]);
    expect(input).toEqual(before);
  });

  it.each(["partial", "missing", "invalid", "disconnected", "missing-port", "missing-device", "missing-rack", "mismatched-rack", "outside-scope", "all-missing"])("does not add leads for %s source data", reason => {
    const input = routedPayload();
    if (reason === "partial") input.cables[0].endpoint_scope = "partial";
    if (reason === "missing") input.cables[0].route_segment_ids.push("missing");
    if (reason === "invalid") input.pathways[0].segments[1].coordinates[0].x = Number.NaN;
    if (reason === "disconnected") input.pathways[0].segments[1].coordinates[1].x = 3.1;
    if (reason === "missing-port") input.ports.pop();
    if (reason === "missing-device") input.devices.pop();
    if (reason === "missing-rack") input.racks.pop();
    if (reason === "mismatched-rack") input.cables[0].terminations[1].rack_id = "unrelated";
    if (reason === "outside-scope") { input.racks[1].location_id = "outside"; input.devices[1].location_id = "outside"; input.cables[0].terminations[1].location_id = "outside"; }
    if (reason === "all-missing") input.pathways = [];
    const before = structuredClone(input);
    const scene = buildScene(input, { locationId: "room-a" });
    expect(scene.leadInCableIds).toEqual([]);
    expect(scene.data.paths.length).toBeLessThanOrEqual(2);
    expect(scene.routeKinds["cable-a"]).not.toBe("schematic");
    expect(input).toEqual(before);
  });
});


it("connects port lead-ins across backend-valid submillimetre joins", () => {
  const input = connectedPayload();
  input.pathways = [{ id: "tray", location_id: "room-a", identifier: "T", name: "Tray", type: "tray", segments: [segment("one", [{ x: 1, y: 1, z: 3 }, { x: 2, y: 1, z: 3 }]), segment("two", [{ x: 2.0005, y: 1, z: 3 }, { x: 4, y: 1, z: 3 }])] }];
  input.cables[0].route_segment_ids = ["one", "two"];
  expect(buildScene(input).leadInCableIds).toEqual(["cable-a"]);
  input.pathways[0].segments[1].coordinates[0].x = 2.002;
  expect(buildScene(input).leadInCableIds).toEqual([]);
});


describe("scoped registered cable routes", () => {
  it.each(["scene", "rack"] as const)("does not fabricate a direct connection when all registered paths are outside the %s view", view => {
    const input = connectedPayload();
    input.devices[1].rack_id = "rack-a";
    input.devices[1].start_u = 4;
    input.cables[0].terminations[1].rack_id = "rack-a";
    input.cables[0].route_scope = "partial";
    const before = structuredClone(input);
    const scene = buildScene(input, { cableId: "cable-a", ...(view === "rack" ? { rackId: "rack-a" } : {}) });
    expect(scene.data.paths).toEqual([]);
    expect(scene.routeKind).toBe("none");
    expect(scene.leadInCableIds).toEqual([]);
    expect(input).toEqual(before);
  });

  it("draws only returned segments when locally continuous geometry is part of an outside route", () => {
    const input = connectedPayload();
    input.cables[0].route_scope = "partial";
    input.cables[0].route_segment_ids = ["local-one", "local-two"];
    input.pathways = [{ id: "tray", identifier: "TRAY", name: "Tray", type: "tray", location_id: "room-a", segments: [
      segment("local-one", [{ x: 1, y: 1, z: 3 }, { x: 2, y: 1, z: 3 }]),
      segment("local-two", [{ x: 2, y: 1, z: 3 }, { x: 4, y: 1, z: 3 }]),
    ] }];
    const scene = buildScene(input, { cableId: "cable-a" });
    expect(scene.data.paths.map(path => path.points)).toEqual([[[1, 3, 1], [2, 3, 1]], [[2, 3, 1], [4, 3, 1]]]);
    expect(scene.leadInCableIds).toEqual([]);
    expect(scene.routeKind).toBe("recorded");
  });

  it.each(["scene", "rack"] as const)("retains endpoint schematics for explicitly unregistered routes in %s view", view => {
    const input = connectedPayload();
    input.devices[1].rack_id = "rack-a";
    input.devices[1].start_u = 4;
    input.cables[0].terminations[1].rack_id = "rack-a";
    input.cables[0].route_scope = "none";
    const scene = buildScene(input, { cableId: "cable-a", ...(view === "rack" ? { rackId: "rack-a" } : {}) });
    expect(scene.data.paths).toHaveLength(1);
    expect(scene.routeKind).toBe("schematic");
    expect(scene.data.paths[0].dimension).toBeUndefined();
  });

  it("does not treat returned references as proof of complete geometry", () => {
    const input = connectedPayload();
    input.cables[0].route_scope = "complete";
    input.cables[0].route_segment_ids = ["one", "two"];
    input.pathways = [{ id: "tray", identifier: "TRAY", name: "Tray", type: "tray", location_id: "room-a", segments: [
      segment("one", [{ x: 1, y: 1, z: 3 }, { x: 2, y: 1, z: 3 }]),
      segment("two", [{ x: 3, y: 1, z: 3 }, { x: 4, y: 1, z: 3 }]),
    ] }];
    const scene = buildScene(input);
    expect(scene.data.paths).toHaveLength(2);
    expect(scene.leadInCableIds).toEqual([]);
  });
});


it("does not extend a truncated coordinate prefix into complete endpoint leads", () => {
  const input = connectedPayload();
  input.cables[0].route_scope = "complete";
  input.cables[0].route_segment_ids = ["prefix"];
  input.pathways = [{ id: "tray", identifier: "TRAY", name: "Tray", type: "tray", location_id: "room-a", segments: [
    segment("prefix", [{ x: 1, y: 1, z: 3 }, { x: 2, y: 1, z: 3 }]),
  ] }];
  input.truncated = ["coordinates"];
  const scene = buildScene(input);
  expect(scene.data.paths.map(path => path.points)).toEqual([[[1, 3, 1], [2, 3, 1]]]);
  expect(scene.data.paths[0].dimension).toBeUndefined();
  expect(scene.leadInCableIds).toEqual([]);
});


it("omits overview port meshes without moving cable endpoints or losing capacity information", () => {
  const input = connectedPayload();
  const full = buildScene(input);
  const overview = buildScene(input, { portDetailRackIds: [] });
  expect(overview.data.racks.flatMap(r => r.devices.flatMap(d => d.ports ?? []))).toEqual([]);
  expect(overview.data.paths).toEqual(full.data.paths);
  expect(overview.data.racks.map(r => r.devices[0].portCount)).toEqual(full.data.racks.map(r => r.devices[0].portCount));
  const focused = buildScene(input, { portDetailRackIds: ["rack-a"] });
  expect(focused.data.racks[0].devices[0].ports).toHaveLength(1);
  expect(focused.data.racks[1].devices[0].ports).toHaveLength(0);
  expect(focused.data.paths).toEqual(full.data.paths);
  expect(input.ports).toHaveLength(2);
});
