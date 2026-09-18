import { createRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import type { InfrastructureScene, SceneData, Selection } from "./render/sceneRenderer";
import { buildScene, type SpatialPayload } from "./sceneData";
import { selectionView } from "./selectionView";
import { SpatialCanvas } from "./SpatialCanvas";

const renderer = vi.hoisted(() => ({ created: vi.fn(), disposed: vi.fn(), pick: undefined as ((selection: Selection) => void) | undefined }));
vi.mock("./render/sceneRenderer", () => ({ InfrastructureScene: class {
  constructor(_canvas: HTMLCanvasElement, onSelect: (selection: Selection) => void) { renderer.created(); renderer.pick = onSelect; }
  setData() {} select() {} setLayers() {} setConnectionMode() {} setPlacementMode() {} setPanMode() {} setMeasurementMode() {} setMeasurement() {} focus() {} zoom() {} setView() {}
  dispose() { renderer.disposed(); }
} }));
beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

function connectedScene(crossRoom: boolean): SpatialPayload {
  const roomB = crossRoom ? "room-b" : "room-a";
  const rooms = crossRoom ? ["room-a", "room-b"] : ["room-a"];
  return {
    scope: { tenant_id: "tenant-a", project_id: "project-a", location_id: null },
    locations: rooms.map(id => ({ id, parent_id: null, identifier: id, name: id, location_type: "room", dimensions: { width_m: 6, depth_m: 4 }, coordinates: {}, transform_3d: {} })),
    racks: ["a", "b"].map((side, index) => ({ id: `rack-${side}`, location_id: index ? roomB : "room-a", rack_identifier: `R-${side}`, name: `Rack ${side}`, height_u: 42, width_mm: 600, depth_mm: 1000, position_x: index ? 4 : 1, position_y: 1, position_z: 0, rotation: 0, reserved_units: [], status: "active" })),
    devices: ["a", "b"].map((side, index) => ({ id: `device-${side}`, rack_id: `rack-${side}`, location_id: index ? roomB : "room-a", identifier: `D-${side}`, name: `Device ${side}`, device_type: "switch", start_u: 1, rack_units: 2, face: "front", status: "active" })),
    ports: ["a", "b"].map(side => ({ id: `port-${side}`, device_id: `device-${side}`, identifier: `P-${side}`, label: `Port ${side}`, connector_type: "LC", front_or_rear: "front", position_index: 1, status: "active" })),
    pathways: [],
    cables: [{ id: "cable-ab", identifier: "CB-AB", media_type: "fiber", construction: "patch", installation_status: "active", length_m: null, test_status: null, route_segment_ids: [], endpoint_scope: "complete", terminations: [
      { side: "A", port_id: "port-a", device_id: "device-a", rack_id: "rack-a", location_id: "room-a" },
      { side: "B", port_id: "port-b", device_id: "device-b", rack_id: "rack-b", location_id: roomB },
    ] }], truncated: [],
  };
}

describe("selection and visible scene stay aligned", () => {
  it.each([{ kind: "device", id: "device-b" }, { kind: "rack", id: "rack-b" }] as const)("switches the local room filter when selecting $kind in another loaded room", selection => {
    const payload = connectedScene(true);
    const before = structuredClone(payload);
    const view = selectionView(payload, selection, { locationId: "room-a", mode: "rack" });
    expect(view).toEqual({ locationId: "room-b", mode: "rack", selection });
    const scene = buildScene(payload, { locationId: view!.locationId, rackId: "rack-b" });
    expect(scene.data.racks.map(rack => ({ id: rack.id, devices: rack.devices.map(device => device.id) }))).toEqual([{ id: "rack-b", devices: ["device-b"] }]);
    expect(payload).toEqual(before);
  });

  it("preserves the all-rooms overview when an already-visible device is selected", () => {
    expect(selectionView(connectedScene(true), { kind: "device", id: "device-b" }, { locationId: "", mode: "overview" })).toEqual({ locationId: "", mode: "overview", selection: { kind: "device", id: "device-b" } });
  });

  it("shows both racks when choosing their cable from the second rack inspection", () => {
    const payload = connectedScene(false);
    const view = selectionView(payload, { kind: "cable", id: "cable-ab" }, { locationId: "room-a", mode: "rack" });
    expect(view).toEqual({ locationId: "room-a", mode: "overview", selection: { kind: "cable", id: "cable-ab" } });
    const scene = buildScene(payload, { locationId: view!.locationId, cableId: view!.selection.id });
    expect({ racks: scene.data.racks.map(rack => rack.id), routeKind: scene.routeKind }).toEqual({ racks: ["rack-a", "rack-b"], routeKind: "schematic" });
  });

  it("clears only the UI room filter for a cable whose two authorized endpoints span rooms", () => {
    const payload = connectedScene(true);
    const before = structuredClone(payload);
    const view = selectionView(payload, { kind: "cable", id: "cable-ab" }, { locationId: "room-a", mode: "rack" });
    expect(view).toEqual({ locationId: "", mode: "overview", selection: { kind: "cable", id: "cable-ab" } });
    const scene = buildScene(payload, { locationId: view!.locationId || undefined, cableId: view!.selection.id });
    expect({ rooms: scene.data.rooms.map(room => room.id), routeKind: scene.routeKind }).toEqual({ rooms: ["room-a", "room-b"], routeKind: "schematic" });
    expect(payload).toEqual(before);
  });

  it.each(["termination", "device", "rack", "port"])("does not expand the room filter when the far %s was not returned in the authorized payload", missing => {
    const payload = connectedScene(true);
    if (missing === "termination") payload.cables[0].terminations.pop();
    if (missing === "device") payload.devices.pop();
    if (missing === "rack") payload.racks.pop();
    if (missing === "port") payload.ports.pop();
    payload.cables[0].endpoint_scope = "partial";
    expect(selectionView(payload, { kind: "cable", id: "cable-ab" }, { locationId: "room-a", mode: "rack" })).toEqual({ locationId: "room-a", mode: "overview", selection: { kind: "cable", id: "cable-ab" } });
  });

  it("rejects selections outside the already-loaded payload", () => {
    const payload = connectedScene(true);
    expect(selectionView(payload, { kind: "device", id: "outside-device" }, { locationId: "room-a", mode: "overview" })).toBeNull();
    expect(selectionView(payload, { kind: "cable", id: "outside-cable" }, { locationId: "room-a", mode: "rack" })).toBeNull();
  });
});

describe("canvas callback lifetime", () => {
  it("uses the newest selection callback without destroying and rebuilding the WebGL engine", () => {
    const engine = createRef<InfrastructureScene>();
    const firstSelect = vi.fn(), latestSelect = vi.fn(), firstReady = vi.fn(), latestReady = vi.fn();
    const data: SceneData = { racks: [], rooms: [], paths: [] };
    const props = { data, selection: null, layers: { labels: true, shell: false, pathways: true }, engine };
    const mounted = render(<SpatialCanvas {...props} onSelect={firstSelect} onReady={firstReady} />);
    const firstEngine = engine.current;
    mounted.rerender(<SpatialCanvas {...props} onSelect={latestSelect} onReady={latestReady} />);
    renderer.pick!({ kind: "device", id: "device-b" });
    expect(renderer.created).toHaveBeenCalledTimes(1);
    expect(renderer.disposed).not.toHaveBeenCalled();
    expect(engine.current).toBe(firstEngine);
    expect(firstSelect).not.toHaveBeenCalled();
    expect(latestSelect).toHaveBeenCalledExactlyOnceWith({ kind: "device", id: "device-b" });
    expect(firstReady).toHaveBeenCalledExactlyOnceWith(true);
    mounted.unmount();
    expect(renderer.disposed).toHaveBeenCalledTimes(1);
    expect(latestReady).toHaveBeenCalledExactlyOnceWith(false);
    expect(engine.current).toBeNull();
  });
});
