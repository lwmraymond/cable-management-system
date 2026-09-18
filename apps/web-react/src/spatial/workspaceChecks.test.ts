import { describe, expect, it } from "vitest";
import type { SpatialPayload } from "./sceneData";
import { checkWorkspace } from "./workspaceChecks";

function payload(): SpatialPayload {
  return {
    scope: { tenant_id: "tenant", project_id: "project", location_id: null },
    locations: ["a", "b"].map(id => ({ id, parent_id: null, name: `Room ${id}`, identifier: `ROOM-${id}`, location_type: "room", dimensions: { width_m: 8, depth_m: 6, height_m: 3.2, entrances: [{ id: `door-${id}`, name: "入口", wall: "south", offset_m: 1, width_m: 1, height_m: 2.1 }] }, coordinates: {}, transform_3d: {} })),
    racks: ["1", "2"].map((id, index) => ({ id: `r${id}`, location_id: "a", rack_identifier: `R${id}`, name: `Rack ${id}`, position_x: 1 + index * 2, position_y: 1, position_z: 0, rotation: 0, width_mm: 600, depth_mm: 1000, height_u: 42, reserved_units: [], status: "active" })),
    devices: ["1", "2"].map(id => ({ id: `d${id}`, rack_id: `r${id}`, location_id: "a", identifier: `DEV-${id}`, name: `Device ${id}`, device_type: "switch", start_u: 1, rack_units: 1, face: "front", status: "active" })),
    ports: ["1", "2"].map(id => ({ id: `p${id}`, device_id: `d${id}`, identifier: `PORT-${id}`, label: "", connector_type: "RJ45", media_type: "Cat6A copper", status: "available", occupied: true, front_or_rear: "front", position_index: 1 })),
    pathways: [{ id: "tray-a", identifier: "TRAY-A", name: "Tray A", location_id: "a", type: "basket_tray", segments: [{ id: "s1", name: "First", sequence: 1, length_m: 8, coordinates: [{ x: 1, y: 1, z: 3 }, { x: 5, y: 1, z: 3 }] }] }],
    cables: [{ id: "c1", identifier: "CABLE-1", media_type: "Cat6A copper", construction: "patch_cord", installation_status: "active", length_m: 10, test_status: null, route_segment_ids: ["s1"], endpoint_scope: "complete", terminations: ["1", "2"].map((id, index) => ({ side: index ? "B" : "A", port_id: `p${id}`, device_id: `d${id}`, rack_id: `r${id}`, location_id: "a" })) }],
    truncated: [],
  };
}
const issues = (data: SpatialPayload, code: string, locationId?: string) => checkWorkspace(data, locationId).issues.filter(issue => issue.code === code);

describe("visible workspace advisory checks", () => {
  it("accepts normal entrance, occupied connected ports and differing registered/coordinate lengths without changing data", () => {
    const data = payload(), before = structuredClone(data);
    expect(checkWorkspace(data)).toEqual({ issues: [], partial: false, omittedCount: 0, checked: { rooms: 2, racks: 2, devices: 2, cables: 1, pathways: 1 } });
    expect(data).toEqual(before);
  });

  it.each(["missing", "outside", "overlap", "touching", "floor"])("handles %s entrance records without inferring a physical hazard", mode => {
    const data = payload(), room = data.locations[0];
    if (mode === "missing") delete room.dimensions.entrances;
    if (mode === "outside") room.dimensions.entrances = [{ id: "door", name: "入口", wall: "east", offset_m: 5.5, width_m: 1, height_m: 2.1 }];
    if (mode === "overlap" || mode === "touching") room.dimensions.entrances = [
      { id: "one", name: "入口", wall: "south", offset_m: 1, width_m: 1, height_m: 2.1 },
      { id: "two", name: "出口", wall: "south", offset_m: mode === "overlap" ? 1.5 : 2, width_m: 1, height_m: 2.1 },
    ];
    if (mode === "floor") { room.location_type = "floor"; delete room.dimensions.entrances; }
    const found = issues(data, "room_entrance");
    if (["touching", "floor"].includes(mode)) expect(found).toEqual([]);
    else expect(found).toEqual([expect.objectContaining({ severity: mode === "missing" ? "info" : "warning", selection: { kind: "room", id: "a" }, action: "entrances" })]);
  });

  it("does not use schematic dimension defaults to claim a rack is outside a room", () => {
    const data = payload(); data.locations[0].dimensions = {}; data.racks[0].position_x = 500;
    expect(issues(data, "space_dimensions")).toEqual([expect.objectContaining({ severity: "info", action: "view", selection: { kind: "room", id: "a" } })]);
    expect(issues(data, "rack_placement")).toEqual([]);
    expect(issues(data, "room_entrance")[0].action).toBe("view");
  });

  it("checks rotated footprints, legacy minimum dimensions and the rendered top frame", () => {
    const data = payload(); data.racks[0] = { ...data.racks[0], position_x: 0.4, rotation: 90 };
    expect(issues(data, "rack_placement")).toEqual([expect.objectContaining({ selection: { kind: "rack", id: "r1" }, action: "rack-position", severity: "warning" })]);
    data.racks[0] = { ...data.racks[0], rotation: 0, width_mm: 200, position_x: 0.25 };
    expect(issues(data, "rack_placement")).toEqual([]);
    data.racks[0].position_x = 0.249;
    expect(issues(data, "rack_placement")).toHaveLength(1);
    data.locations[0].dimensions.height_m = 2;
    expect(issues(data, "rack_placement")).toHaveLength(2);
    data.locations[0].dimensions.height_m = 42 * 0.04445 + 0.1925; data.racks[0].position_x = 0.25;
    expect(issues(data, "rack_placement")).toEqual([]);
  });

  it("offers position editing for invalid coordinates but only viewing for unsupported invalid specifications", () => {
    const data = payload(); data.racks[0].position_x = Number.NaN;
    expect(issues(data, "rack_placement")[0]).toMatchObject({ severity: "info", action: "rack-position" });
    data.racks[0].width_mm = 0;
    expect(issues(data, "rack_placement")[0]).toMatchObject({ severity: "info", action: "view", actionLabel: "查看规格" });
  });

  it("describes unracked devices and unloaded rack references as information rather than installation failures", () => {
    const data = payload(); data.devices[0].rack_id = null; data.devices[0].device_type = "work_area_outlet"; data.devices[1].rack_id = "not-loaded";
    const result = checkWorkspace(data);
    expect(result.partial).toBe(true);
    expect(result.issues).toEqual(expect.arrayContaining([
      expect.objectContaining({ code: "device_unracked", severity: "info", title: "设备尚未入柜显示", action: "view" }),
      expect.objectContaining({ code: "device_unracked", severity: "info", title: "设备关联机柜未加载", action: "view" }),
    ]));
  });

  it("keeps a route-free patch cord informational and never reports occupied ports as errors", () => {
    const data = payload(); data.cables[0].route_segment_ids = []; data.cables[0].route_scope = "none";
    expect(checkWorkspace(data).issues).toEqual([expect.objectContaining({ code: "cable_route_unloaded", severity: "info", action: "view", detail: expect.stringContaining("不据此判断断线") })]);
    data.cables[0].endpoint_scope = "partial"; data.cables[0].terminations.pop();
    expect(checkWorkspace(data).partial).toBe(true);
    expect(checkWorkspace(data).issues.every(issue => issue.severity === "info")).toBe(true);
  });

  it("checks policy only when a visible cable actually uses the restricted device or tray", () => {
    const data = payload();
    data.devices[0].cable_policy = { allows_cables: false, allowed_media: [] };
    data.pathways[0].cable_policy = { allows_cables: true, allowed_media: ["fiber"] };
    expect(issues(data, "existing_policy_conflict")).toEqual([
      expect.objectContaining({ severity: "warning", selection: { kind: "device", id: "d1" }, action: "properties" }),
      expect.objectContaining({ severity: "warning", selection: { kind: "pathway", id: "tray-a" }, action: "properties" }),
    ]);
    data.cables = [];
    expect(issues(data, "existing_policy_conflict")).toEqual([]);
  });

  it("does not attribute another visible room's records or policies to the selected room", () => {
    const data = payload(); data.devices[1].location_id = "b"; data.racks[1].location_id = "b";
    data.devices[1].cable_policy = { allows_cables: false, allowed_media: [] };
    data.cables[0].terminations[1].location_id = "b";
    data.locations[1].dimensions = {};
    data.pathways.push({ ...data.pathways[0], id: "tray-b", location_id: "b", segments: [{ ...data.pathways[0].segments[0], id: "s2", length_m: -1, coordinates: [] }], cable_policy: { allows_cables: false, allowed_media: [] } });
    data.cables[0].route_segment_ids.push("s2");
    const result = checkWorkspace(data, "a");
    expect(result).toMatchObject({ issues: [], partial: true, checked: { rooms: 1, racks: 1, devices: 1, cables: 1, pathways: 1 } });
    const other = checkWorkspace(data, "b");
    expect(other.issues.every(issue => issue.selection.id !== "a" && issue.selection.id !== "d1" && issue.selection.id !== "tray-a")).toBe(true);
    expect(other.issues.some(issue => issue.code === "existing_policy_conflict")).toBe(true);
  });

  it("includes cables passing through the selected pathway even if their endpoints belong elsewhere", () => {
    const data = payload(); data.pathways[0].location_id = "b"; data.pathways[0].cable_policy = { allows_cables: false, allowed_media: [] };
    expect(checkWorkspace(data, "b")).toMatchObject({ checked: { rooms: 1, racks: 0, devices: 0, cables: 1, pathways: 1 }, issues: [expect.objectContaining({ code: "existing_policy_conflict", selection: { kind: "pathway", id: "tray-a" } })] });
  });

  it("treats missing and truncated geometry as incomplete knowledge", () => {
    const data = payload(); data.cables[0].route_segment_ids.push("unloaded");
    expect(checkWorkspace(data)).toMatchObject({ partial: true, issues: [expect.objectContaining({ code: "route_geometry", severity: "info", action: "view" })] });
    data.cables[0].route_segment_ids = ["s1"]; data.pathways[0].segments[0].coordinates = [{ x: 1, y: 1 }]; data.truncated = ["coordinates"];
    expect(checkWorkspace(data)).toMatchObject({ partial: true, issues: [] });
  });

  it("reports a known same-room recorded gap, accepts 0.5 mm and treats multiple spaces as uncertain", () => {
    const data = payload();
    data.pathways[0].segments.push({ id: "s2", name: "Second", sequence: 2, length_m: 1, coordinates: [{ x: 5.01, y: 1, z: 3 }, { x: 6, y: 1, z: 3 }] });
    data.cables[0].route_segment_ids.push("s2");
    expect(issues(data, "route_geometry")).toEqual([expect.objectContaining({ severity: "warning", title: "登记路径段未连续相接", action: "view" })]);
    data.pathways[0].segments[1].coordinates[0].x = 5.0005;
    expect(issues(data, "route_geometry")).toEqual([]);
    data.pathways.push({ ...data.pathways[0], id: "tray-b", location_id: "b", segments: [data.pathways[0].segments.pop()!] });
    expect(issues(data, "route_geometry")).toEqual([expect.objectContaining({ severity: "info", title: "跨空间路由尚不能完整核对" })]);
  });

  it("reports omitted results explicitly and keeps warnings ahead of advisory notices", () => {
    const data = payload();
    for (let index = 0; index < 105; index++) data.devices.push({ ...data.devices[0], id: `unracked-${index}`, rack_id: null });
    data.devices[0].cable_policy = { allows_cables: false, allowed_media: [] };
    const result = checkWorkspace(data);
    expect(result.issues).toHaveLength(100); expect(result.omittedCount).toBe(6);
    expect(result.issues[0]).toMatchObject({ severity: "warning", code: "existing_policy_conflict" });
    expect(new Set(result.issues.map(issue => issue.id)).size).toBe(100);
  });

  it("identifies affected objects by name and identifier without duplicating labels", () => {
    const data = payload(); delete data.locations[0].dimensions.entrances;
    expect(issues(data, "room_entrance")[0].objectLabel).toBe("Room a · ROOM-a");
    data.racks[0].position_x = Number.NaN; data.racks[0].name = data.racks[0].rack_identifier;
    expect(issues(data, "rack_placement")[0].objectLabel).toBe("R1");
    data.racks[0].name = ""; data.racks[0].rack_identifier = "";
    expect(issues(data, "rack_placement")[0].objectLabel).toBe("r1");
    data.cables[0].length_m = null;
    expect(issues(data, "length_unregistered")[0].objectLabel).toBe("CABLE-1");
  });

  it("treats renderer-out-of-range dimensions and coordinates as unreliable without capping registered cable lengths", () => {
    const data = payload(); data.locations[0].dimensions.width_m = 100000; data.racks[0].position_x = 100000; data.cables[0].length_m = 100000;
    expect(issues(data, "space_dimensions")).toEqual([expect.objectContaining({ severity: "info", selection: { kind: "room", id: "a" } })]);
    expect(issues(data, "rack_placement")).toEqual([expect.objectContaining({ severity: "info", selection: { kind: "rack", id: "r1" } })]);
    expect(issues(data, "length_unregistered")).toEqual([]);
  });

  it("does not reveal other entities when the requested location was not loaded", () => {
    expect(checkWorkspace(payload(), "hidden")).toEqual({ issues: [], partial: true, omittedCount: 0, checked: { rooms: 0, racks: 0, devices: 0, cables: 0, pathways: 0 } });
  });
});


describe("scoped route check coverage", () => {
  it.each(["no-visible-segments", "continuous-visible-segments", "gap-between-visible-segments"] as const)("treats %s in a partial route as incomplete coverage, not a registered gap", visibility => {
    const data = payload();
    data.cables[0].route_scope = "partial";
    if (visibility === "no-visible-segments") data.cables[0].route_segment_ids = [];
    if (visibility === "gap-between-visible-segments") {
      data.pathways[0].segments.push({ id: "s2", name: "Returned after another floor", sequence: 3, length_m: 2, coordinates: [{ x: 6, y: 3, z: 3 }, { x: 8, y: 3, z: 3 }] });
      data.cables[0].route_segment_ids.push("s2");
    }
    const before = structuredClone(data);
    const result = checkWorkspace(data, "a");
    expect(result.partial).toBe(true);
    expect(result.issues).toEqual([expect.objectContaining({ code: "route_coverage", severity: "info", title: "登记路由仅部分可见", selection: { kind: "cable", id: "c1" }, detail: expect.stringContaining("不检查整条路由的连续性") })]);
    expect(result.issues.some(issue => issue.code === "cable_route_unloaded" || issue.title === "登记路径段未连续相接")).toBe(false);
    expect(data).toEqual(before);
  });

  it("still checks actual policy conflicts on locally visible parts of a partial route", () => {
    const data = payload();
    data.cables[0].route_scope = "partial";
    data.devices[0].cable_policy = { allows_cables: false, allowed_media: [] };
    data.pathways[0].cable_policy = { allows_cables: true, allowed_media: ["fiber"] };
    const result = checkWorkspace(data, "a");
    expect(result.partial).toBe(true);
    expect(result.issues.filter(issue => issue.severity === "warning")).toEqual([
      expect.objectContaining({ code: "existing_policy_conflict", selection: { kind: "device", id: "d1" } }),
      expect.objectContaining({ code: "existing_policy_conflict", selection: { kind: "pathway", id: "tray-a" } }),
    ]);
    expect(result.issues.some(issue => issue.code === "route_coverage" && issue.severity === "info")).toBe(true);
  });
});
